"""Authored Living World arc; pure rules plus an existing-receipt store adapter.

No background jobs, NPC API, schema, or live connection is created by this module.
Region proof and choices live in the player's existing Living Dao JSON. Story
rewards are deliberately small and independent of random discovery rolls.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import permutations

from living_dao import REGIONS

NPCS = {
    'jian': {'name': 'Jian Wuchen', 'role': 'Wandering Sword Immortal',
             'history': 'A former Heavenly Palace guardian who values integrity.'},
    'yue': {'name': 'Yue Lian', 'role': 'Crimson Moon Wanderer',
            'history': 'A seeker of lost cultivation knowledge with a hidden sect history.'},
    'zhao': {'name': 'Zhao Tianming', 'role': 'Rising Heaven Prodigy',
             'history': 'A proud training rival who can learn to stand beside you.'},
}
CHAPTERS = (
    {'title': 'I · The Broken Jade', 'region': 'mistwood',
     'text': 'At a ruined waystation, Jian recognizes the guardian seal on a jade fragment. '
             'Yue sees an erased map beneath it. Zhao asks who deserves credit for finding it.',
     'choices': (
         ('return_jade', 'Return the guardian seal', 'Trust Jian with its origin; practice integrity.', {'jian': 3, 'yue': 1}, 'The seal is entrusted to its former guardian.'),
         ('trace_script', 'Trace the hidden script', 'Investigate with Yue without destroying the seal.', {'yue': 3, 'jian': 1}, 'An old palace route appears under moonlight.'),
         ('share_credit', 'Share credit with Zhao', 'Treat the rival as a fellow cultivator.', {'zhao': 3, 'jian': 1}, 'Zhao lowers his sword and accepts a shared discovery.'),
     )},
    {'title': 'II · Ashes in the Archive', 'region': 'stonepeak',
     'text': 'The jade leads to a Heavenly Palace archive beneath Stonepeak. Jian admits '
             'he left the Palace to save its apprentices. Yue reveals that her Crimson '
             'Moon forebears were blamed for its fall. Zhao offers to hold the passage.',
     'choices': (
         ('protect_archive', 'Protect the apprentices’ record', 'Preserve names; learn the Patient Edge.', {'jian': 3, 'zhao': 1}, 'The forgotten apprentices will be remembered.'),
         ('follow_yue', 'Investigate Yue’s evidence', 'Compare both histories; learn the Seeking Thread.', {'yue': 3, 'jian': 1}, 'The records implicate ambition rather than a bloodline.'),
         ('train_zhao', 'Train beside Zhao', 'Hold the passage together; learn the Shared Horizon.', {'zhao': 3, 'yue': 1}, 'Your rival matches his breathing to yours.'),
     )},
    {'title': 'III · Echoes of the Fallen Heaven', 'region': 'thunder_rift',
     'text': 'Three jade echoes open a storm-broken Heavenly Palace beacon. Jian would '
             'protect its legacy, Yue would free its knowledge, and Zhao asks whether '
             'the next generation must repeat their rivalry. The beacon can be restored without a sacrifice.',
     'choices': (
         ('restore_beacon', 'Restore the guardian beacon', 'A refuge for every path; Jian teaches a sword form.', {'jian': 3, 'yue': 1, 'zhao': 1}, 'A safe light returns to the mountain roads.'),
         ('open_archive', 'Open the record to all', 'Make the truth accessible; Yue shares her research.', {'yue': 3, 'jian': 1, 'zhao': 1}, 'Knowledge belongs to cultivators, not a price or pedigree.'),
         ('reconcile', 'Found a shared training ground', 'Ask all three to teach; Zhao becomes a willing ally.', {'zhao': 3, 'jian': 2, 'yue': 2}, 'Guardian, wanderer and rival promise to return as teachers.'),
     )},
)
STYLE = {'protect_archive': ('Patient Edge', 'guard'),
         'follow_yue': ('Seeking Thread', 'flow'),
         'train_zhao': ('Shared Horizon', 'strike')}
MAX_TRUST = 12
MAX_MEMORIES = 6
CHAPTER_MARKS = 3
TEACHERS = {
    'jian': ('Sword lesson', 'Patient Edge', 'guard'),
    'yue': ('Archive investigation', 'Seeking Thread', 'flow'),
    'zhao': ('Friendly three-exchange duel', 'Shared Horizon', 'strike'),
}
FORM_EFFECTS = {'guard': 'a ward that reduces the next enemy hit by 2/4/6 damage at rank 1/2/3',
                'flow': 'restores up to 1–3 Combat Qi by rank',
                'strike': 'adds 1–3 damage by rank'}
STANCES = ('rush', 'feint', 'guard')
COUNTERS = {'rush': 'guard', 'feint': 'strike', 'guard': 'feint'}
TELLS = {'rush': 'he plants his back foot and leans in before a rush',
         'feint': 'his shoulders drop a breath too early before a feint',
         'guard': 'he draws his blade close to his chest before he guards'}
# Calm reaction pools, one tuple per chapter; picked deterministically from saved progress.
COMPANION_REACTIONS = {
    'jade_fox': (
        ('Your curious Jade Fox noses a hidden seam in the jade.',
         'Your Jade Fox circles the waystation stones, ears turned toward the fragment.',
         'Your Jade Fox sneezes at the old dust, then settles beside the broken seal.',
         'Your Jade Fox follows a faint scent of incense through the ruins.'),
        ('Your Jade Fox pads between the archive shelves, tail brushing the ash.',
         'Your Jade Fox stops at a scorched scroll case and waits for you.',
         'Your Jade Fox curls near the apprentices’ names, quiet and watchful.'),
        ('Your Jade Fox watches the beacon light flicker in its bright eyes.',
         'Your Jade Fox leaps onto a fallen pillar to see the storm roll past.',
         'Your Jade Fox presses close as thunder speaks over the rift.')),
    'storm_hawk': (
        ('Your proud Storm Hawk keeps watch above the ruins.',
         'Your Storm Hawk circles once over the waystation and lands on a broken lantern.',
         'Your Storm Hawk calls sharply as the mist parts around the jade.'),
        ('Your Storm Hawk perches on the archive gate, feathers still against the ash.',
         'Your Storm Hawk tilts its head at the sealed vault, wary and patient.',
         'Your Storm Hawk shakes soot from its wings and keeps the passage in sight.'),
        ('Your Storm Hawk rides the rift winds, wings steady in the thunder.',
         'Your Storm Hawk answers the storm with a single ringing cry.',
         'Your Storm Hawk lands on the beacon frame as if it has always belonged there.')),
    'stone_tortoise': (
        ('Your patient Stone Tortoise steadies the fractured floor.',
         'Your Stone Tortoise rests its shell against the waystation wall, unhurried.',
         'Your Stone Tortoise blinks slowly at the jade, as if it remembers older roads.'),
        ('Your Stone Tortoise braces the archive doorway while the dust settles.',
         'Your Stone Tortoise walks the cold floor one deliberate step at a time.',
         'Your Stone Tortoise lowers its head beside the apprentices’ record.'),
        ('Your Stone Tortoise plants itself against the rift wind and does not move.',
         'Your Stone Tortoise waits beneath the beacon, calm as the mountain.',
         'Your Stone Tortoise hums faintly as the storm passes overhead.')),
}
DOCTRINE_REACTIONS = {
    'wisdom': (
        ('Yue welcomes your sect’s tradition of careful study.',
         'Yue asks whether your sect’s archivists have seen this palace script before.',
         'Yue notes your sect’s habit of reading twice before judging once.'),
        ('Yue trusts your sect to copy both histories without choosing a winner.',
         'Yue asks your sect’s scholars to keep the margin notes safe.',
         'Yue smiles at your sect’s patience with old, difficult records.'),
        ('Yue hopes your sect will teach what the beacon’s archive reveals.',
         'Yue asks your sect to keep a copy of the record in its library.',
         'Yue says your sect’s questions helped open the beacon.')),
    'harmony': (
        ('Jian asks your sect to safeguard the mountain paths.',
         'Jian bows to your sect’s custom of protecting travelers.',
         'Jian says your sect walks the old guardian road.'),
        ('Jian asks your sect to remember the apprentices as its own.',
         'Jian trusts your sect to keep the archive peaceful.',
         'Jian says your sect’s calm would have suited the old Palace.'),
        ('Jian asks your sect to tend the beacon’s light in hard winters.',
         'Jian says your sect has made the mountain roads safer.',
         'Jian lays his hand on the beacon and names your sect its friend.')),
    'valor': (
        ('Zhao recognizes your sect’s discipline in the storm.',
         'Zhao admits your sect trains harder than his own rivals.',
         'Zhao asks if your sect would spar with him one day.'),
        ('Zhao says your sect would hold this passage well.',
         'Zhao studies your sect’s stance and adjusts his own.',
         'Zhao offers your sect’s disciples a place at his training fire.'),
        ('Zhao says your sect’s courage steadied the beacon’s climb.',
         'Zhao asks your sect to train beside the next generation.',
         'Zhao raises his sword in salute to your sect.')),
    None: (
        ('The three travelers ask your sect to remember the people behind the legend.',
         'The travelers ask your sect what it values most.',
         'The travelers say every sect begins with one shared promise.'),
        ('The travelers ask your sect to carry the archive’s names forward.',
         'The travelers hope your sect will choose a doctrine of its own one day.',
         'The travelers say a sect is remembered by whom it protects.'),
        ('The travelers ask your sect to keep the beacon in its stories.',
         'The travelers thank your sect for the road it helped reopen.',
         'The travelers say your sect’s name now rests beside theirs.')),
}


def region_name(region):
    """Player-facing region name; raw ids stay in saved data and command values only."""
    return REGIONS[region][0] if region in REGIONS else str(region)


def duel_order(now):
    """Zhao's three stances for this UTC day; the same for every player that day."""
    day = _now(now).date().isoformat()
    orders = list(permutations(STANCES))
    return orders[sha256(f'zhao-duel-v0251:{day}'.encode()).digest()[0] % len(orders)]


def _pick(pool, state, salt):
    """Stable choice from saved progress, so a replayed delivery shows the same line."""
    story = state.get('living_world_v025') if isinstance(state.get('living_world_v025'), dict) else {}
    seed = f'{salt}:{state.get("explorations", 0)}:{len(story.get("choices") or {})}'
    return pool[sha256(seed.encode()).digest()[0] % len(pool)]


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Story time must be timezone aware.')
    return now.astimezone(timezone.utc)


def _int(value, low, high):
    """Clamp untrusted saved counters; corrupted values never brick a panel."""
    try:
        value = int(value)
    except OverflowError:
        value = high if value > 0 else low
    except (TypeError, ValueError):
        value = low
    return min(high, max(low, value))


def _chapter(value, choices):
    """Saved chapter if a whole in-range number; otherwise only proven resolved chapters count."""
    done = 0
    while done < len(CHAPTERS) and str(done) in choices:
        done += 1
    try:
        number = float(value) if type(value) in (int, float, str) else None
    except (OverflowError, ValueError):
        number = None
    return int(number) if number is not None and number.is_integer() and 0 <= number <= len(CHAPTERS) else done


def _story(state):
    raw = state.get('living_world_v025') if isinstance(state, dict) else None
    value = deepcopy(raw) if isinstance(raw, dict) else {}
    choices = value.get('choices')
    value['choices'] = {str(k): v for k, v in choices.items() if isinstance(v, str)} if isinstance(choices, dict) else {}
    value['chapter'] = _chapter(value.get('chapter', 0), value['choices'])
    npcs = value.get('npcs')
    npcs = dict(npcs) if isinstance(npcs, dict) else {}
    for key, npc in list(npcs.items()):
        npc = dict(npc) if isinstance(npc, dict) else {}
        npc['trust'] = _int(npc.get('trust', 0), 0, MAX_TRUST)
        memories = npc.get('memories')
        npc['memories'] = [m for m in memories if isinstance(m, dict)][-MAX_MEMORIES:] if isinstance(memories, list) else []
        npcs[key] = npc
    value['npcs'] = npcs
    value['fragments'] = _int(value.get('fragments', 0), 0, value['chapter'])  # one echo per resolved chapter
    # Chapters whose 3 marks were paid. A developer rewind never clears this; any
    # saved choice also counts, so saves from before this record cannot be re-paid.
    paid = value.get('paid')
    paid = {c for c in paid if type(c) is int and 0 <= c < len(CHAPTERS)} if isinstance(paid, list) else set()
    paid |= {int(k) for k in value['choices'] if k.isdigit() and int(k) < len(CHAPTERS)}
    value['paid'] = sorted(paid)
    if not isinstance(value.get('encounter'), dict):
        value.pop('encounter', None)
    return value


def panel(state):
    """Read-only UI data. Old saves remain unchanged until a real action."""
    story = _story(state)
    chapter = story['chapter']
    if chapter == 3:
        return {'chapter': 3, 'title': 'Echoes · Arc complete', 'region': None,
                'complete': True, 'ready': False, 'choices': [],
                'text': story.get('resolution', 'The beacon remembers your choices. No repeat story rewards.')}
    entry = CHAPTERS[chapter]
    proof = story.get('encounter', {})
    ready = proof.get('chapter') == chapter and proof.get('region') == entry['region']
    remembered = remembered_dialogue(story)
    # The chapter's blurb appears once: on the panel where it is chosen, after its proof is found.
    text = entry['text'] + ('\n' + remembered if remembered else '') if ready else ''
    return {'chapter': chapter, 'title': entry['title'], 'region': entry['region'],
            'complete': False, 'ready': ready, 'text': text,
            'choices': [{'key': key, 'label': label, 'description': desc}
                        for key, label, desc, _, _ in entry['choices']] if ready else []}


def relationship(story, npc):
    data = _story({'living_world_v025': story})['npcs'].get(npc, {})
    trust = data.get('trust', 0)
    return ('Trusted ally' if trust >= 6 else 'Growing trust' if trust >= 3 else 'Acquaintance'), trust


def remembered_dialogue(story):
    """Bounded authored callbacks to actual saved choices, never generated text."""
    choices = story.get('choices', {})
    key = choices.get('1') or choices.get('0')
    lines = {
        'return_jade': 'Jian: You returned what you could have sold. I will not forget that integrity.',
        'trace_script': 'Yue: You read beneath the seal before judging its keeper. Help me read the rest.',
        'share_credit': 'Zhao: You gave a rival his due. I will keep the rear path clear for you.',
        'protect_archive': 'Jian: You preserved their names. I will teach your sword to shelter as well as strike.',
        'follow_yue': 'Yue: You heard my evidence without excusing my forebears. Let us seek the whole truth.',
        'train_zhao': 'Zhao: Our breathing held the passage. Next time, I would rather train beside you than race you.',
    }
    return lines.get(key, '')


def status(state):
    view = panel(state)
    story = _story(state)
    lines = ['**Echoes of the Fallen Heaven**', view['title']] + ([view['text']] if view['text'] else [])
    if not view['complete']:
        lines.append('Choose below; no time limit.' if view['ready'] else
                     f'Next: /explore region:{region_name(view["region"])}. The usual 5-minute expedition cooldown applies.')
    for key, npc in NPCS.items():
        label, trust = relationship(story, key)
        lines.append(f'{npc["name"]} · {label} {trust}/{MAX_TRUST}')
    lines.append(f'Jade echoes: {story["fragments"]} / 3 · 3 Spirit Stones per chapter, once only.')
    return '\n'.join(lines)


def _reaction(state, chapter, doctrine=None):
    """One companion line and one sect line from the chapter's pools; flavor only, no rewards."""
    chapter = min(len(CHAPTERS) - 1, max(0, chapter))
    companion = state.get('companion')
    lines = []
    if isinstance(companion, str) and companion in COMPANION_REACTIONS:
        lines.append(_pick(COMPANION_REACTIONS[companion][chapter], state, f'{companion}:{chapter}'))
    if state.get('sect'):
        doctrine = doctrine if isinstance(doctrine, str) and doctrine in DOCTRINE_REACTIONS else None
        lines.append(_pick(DOCTRINE_REACTIONS[doctrine][chapter], state, f'{doctrine}:{chapter}'))
    return ' '.join(lines)


def encounter(state, region, now, roll=None, doctrine=None):
    """Attach one region proof; rare lore has a UTC daily cap and zero loot."""
    now = _now(now)
    if region not in ('mistwood', 'stonepeak', 'thunder_rift'):
        raise ValueError('Unknown story region.')
    if roll is not None and (type(roll) is not int or not 0 <= roll < 1000):
        raise ValueError('Invalid story discovery roll.')
    result = deepcopy(state)
    story = _story(result)
    view = panel(result)
    lines = []
    if not view['complete'] and region == view['region']:
        if view['ready']:
            lines.append(f'You already carry the proof for **{view["title"]}**. Open /guide → Echoes of the Fallen Heaven to choose.')
        else:
            lines.append(f'**{view["title"]}** — a new chapter awaits. Open /guide → Echoes of the Fallen Heaven to read it and choose.')
        story['encounter'] = {'chapter': view['chapter'], 'region': region,
                              'at': now.isoformat()}
    elif view['complete']:
        resolution = story.get('choices', {}).get('2')
        lines.append({'restore_beacon': 'A guardian lantern now guides the road.',
                      'open_archive': 'Travelers exchange copies of the recovered records.',
                      'reconcile': 'A shared training ground welcomes rival schools.'}.get(resolution, 'The mountain remembers the restored beacon.'))
    else:
        lines.append(f'The jade points toward {region_name(view["region"])}; this expedition’s usual rewards are unchanged.')
    today = now.date().isoformat()
    if roll is not None and roll < 50 and story.get('lore_day') != today:
        story['lore_day'] = today
        story['lore_count'] = min(30, _int(story.get('lore_count', 0), 0, 30) + 1)
        story['last_lore'] = ('A palace inscription reads: a Dao shared is not a Dao diminished.'
                              if region == 'mistwood' else
                              'An apprentice’s margin note remembers Jian sheltering the injured.'
                              if region == 'stonepeak' else
                              'The beacon carries three signatures rather than one victor’s name.')
        lines.append('Rare discovery: ' + story['last_lore'] + ' (lore only; at most once per UTC day).')
    story.setdefault('choices', {})
    result['living_world_v025'] = story
    reaction = _reaction(result, view['chapter'], doctrine)
    if reaction:
        lines.append(reaction)
    return result, '\n'.join(lines)


def choose(state, expected_chapter, choice, now, doctrine=None):
    """One bounded, authored transition; caller persists reward and state atomically."""
    now = _now(now)
    if type(expected_chapter) is not int or not 0 <= expected_chapter < len(CHAPTERS):
        raise ValueError('Open the current story panel in /guide.')
    result = deepcopy(state)
    story = _story(result)
    chapter = story['chapter']
    if chapter != expected_chapter or str(chapter) in story['choices']:
        raise ValueError('That story choice is already resolved or belongs to an earlier chapter. Open /guide again; no reward was repeated.')
    entry = CHAPTERS[chapter]
    proof = story.get('encounter', {})
    if proof.get('chapter') != chapter or proof.get('region') != entry['region']:
        raise ValueError(f'Explore {region_name(entry["region"])} first to discover this chapter.')
    option = next((item for item in entry['choices'] if item[0] == choice), None)
    if option is None:
        raise ValueError('Choose one of the current story options.')
    _, _, _, changes, line = option
    for key, gain in changes.items():
        npc = story['npcs'].setdefault(key, {'trust': 0, 'memories': []})
        npc['trust'] = min(MAX_TRUST, npc['trust'] + gain)
        memories = list(npc['memories'])
        memories.append({'chapter': chapter, 'choice': choice, 'at': now.isoformat(),
                         'text': line})
        npc['memories'] = memories[-MAX_MEMORIES:]
    reward = 0 if chapter in story['paid'] else CHAPTER_MARKS
    story['paid'] = sorted(set(story['paid']) | {chapter})
    story['choices'][str(chapter)] = choice
    story['fragments'] = min(3, story['fragments'] + 1)
    story['chapter'] = chapter + 1
    story.pop('encounter', None)
    if chapter == 1:
        story['technique'] = STYLE[choice][0]
        story['technique_style'] = STYLE[choice][1]
    if chapter == 2:
        story['resolution'] = line + ' All three teachers remain available; no essential content is locked.'
    result['living_world_v025'] = story
    payout = (f'+{CHAPTER_MARKS} saved Spirit Stones' if reward else
              'story Spirit Stones for this chapter were already paid once')
    reply = (f'{NPCS[max(changes, key=changes.get)]["name"]}: {line}\n'
             f'Chapter complete · {payout} · Jade echo {story["fragments"]}/3.')
    if chapter == 1:
        reply += (f'\nPersonal Dao form: {story["technique"]} — {FORM_EFFECTS[story["technique_style"]]}. '
                  'Free /cultivation_practice refines it. It takes effect on your first Elemental Art each battle.')
    if chapter == 2:
        reply += '\nArc complete. No repeat payouts; every choice preserves access to training and future arcs.'
    else:
        reply += f'\nNext region: {region_name(CHAPTERS[chapter + 1]["region"])}. Continue whenever you wish.'
    reaction = _reaction(result, chapter, doctrine)
    if reaction:
        reply += '\n' + reaction
    return result, reply, reward


async def choose_story(repo, guild, user, event_id, chapter, choice):
    from living_dao import action
    if type(chapter) is not int:
        raise ValueError('Open the current story panel in /guide.')
    return await action(repo, guild, user, event_id, 'story_choice', f'{chapter}:{choice}')


def training_panel(state, now):
    now = _now(now)
    story = _story(state)
    unlocked = story['chapter'] >= 2
    day = now.date().isoformat()
    available = unlocked and str(story.get('training_day') or '')[:10] < day
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    tells = [TELLS[stance] for stance in duel_order(now)]
    return {'unlocked': unlocked, 'ready': available,
            'next_at': tomorrow.isoformat() if unlocked and not available else None,
            'current_style': story.get('technique') if isinstance(story.get('technique'), str) else None,
            'teachers': [{'key': key, 'name': NPCS[key]['name'], 'lesson': item[0],
                          'form': item[1], 'effect': item[2]} for key, item in TEACHERS.items()],
            'tells': tells,
            'text': ('Complete the Stonepeak archive chapter to meet all three teachers.' if not unlocked else
                     'One free lesson per UTC day. Retune your form; no Spirit Stones, loot or cultivation Qi. '
                     'Zhao’s tells today: ' + '; '.join(f'{n}) {tell}' for n, tell in enumerate(tells, 1))
                     + '. Answer each with guard, strike or feint. This friendly duel cannot injure you.')}


def train(state, teacher, now, tactics=None):
    """Authored optional lessons and a nonlethal tactical duel; no loot payout."""
    now = _now(now)
    if teacher not in TEACHERS:
        raise ValueError('Choose Jian, Yue or Zhao from the current story panel.')
    view = training_panel(state, now)
    if not view['unlocked']:
        raise ValueError('Complete the Stonepeak archive chapter before teacher training.')
    if not view['ready']:
        raise ValueError('Your free teacher lesson returns at the next UTC day. No resources spent.')
    if teacher == 'zhao':
        if (not isinstance(tactics, (list, tuple)) or len(tactics) != 3
                or any(move not in ('guard', 'strike', 'feint') for move in tactics)):
            raise ValueError('Zhao needs three tactics: guard, strike or feint. Read his tells on the story panel.')
    result = deepcopy(state)
    story = _story(result)
    label, name, style = TEACHERS[teacher]
    npc = story['npcs'].setdefault(teacher, {'trust': 0, 'memories': []})
    gain = 1
    if teacher == 'jian':
        lines = ['Jian: A sword can shelter a promise as well as cut a path.',
                 'You practice a restrained draw, a measured parry and a safe recovery.']
        if story.get('choices', {}).get('0') == 'return_jade':
            lines.append('Jian remembers the returned seal and entrusts you with his old guardian form.')
        outcome = 'A guardian sword lesson completed.'
    elif teacher == 'yue':
        lines = ['Yue: The margin notes are another witness; compare them before choosing a side.',
                 'You cross-reference palace and Crimson Moon accounts. Both histories remain available.']
        if story.get('choices', {}).get('0') == 'trace_script':
            lines.append('Yue recognizes the tracing you preserved and shares the unabridged record.')
        outcome = 'Two conflicting records studied without erasing either.'
    else:
        stances = duel_order(now)
        counters = tuple(COUNTERS[stance] for stance in stances)
        lines = ['Zhao: Three exchanges. No injuries, lost equipment or cultivation stakes.']
        wins = 0
        for number, (stance, counter, move) in enumerate(zip(stances, counters, tactics), 1):
            success = move == counter
            wins += success
            lines.append(f'{number}. Zhao {stance.title()} → you {move.title()}: '
                         + ('clean counter.' if success else f'he holds the advantage; try {counter.title()}.'))
        clean = wins >= 2
        gain = 2 if clean else 1
        outcome = f'Friendly duel: {wins}/3 clean counters; ' + ('a shared victory.' if clean else 'a useful lesson, no defeat penalty.')
        lines.append(outcome)
        lines.append('Zhao: Again another day. Until then, we can walk the same road.')
    npc['trust'] = min(MAX_TRUST, npc['trust'] + gain)
    memories = list(npc['memories'])
    memories.append({'kind': 'training', 'teacher': teacher, 'at': now.isoformat(), 'text': outcome})
    npc['memories'] = memories[-MAX_MEMORIES:]
    story['training_day'] = now.date().isoformat()
    story['training_count'] = min(99, _int(story.get('training_count', 0), 0, 99) + 1)
    story['technique'] = name
    story['technique_style'] = style
    result['living_world_v025'] = story
    lines.append(f'{label} · Trust {npc["trust"]}/{MAX_TRUST} · Personal form: {name}. '
                 f'Your first Elemental Art each battle: {FORM_EFFECTS[style]}. '
                 'Original story choices remain saved. Next free lesson: next UTC day.')
    return result, '\n'.join(lines)


async def train_story(repo, guild, user, event_id, teacher, tactics=None):
    from living_dao import action
    return await action(repo, guild, user, event_id, 'story_train',
                        {'teacher': teacher, 'tactics': tactics})


def practice_growth(character, root, now):
    """Called only after existing six-hour practice validation; no Qi multiplier."""
    now = _now(now)
    info = character.get('dao_practice_v025')
    if not isinstance(info, dict):
        info = character['dao_practice_v025'] = {}
    info['practices'] = min(9, _int(info.get('practices', 0), 0, 9) + 1)
    info['root'] = root
    info['last_at'] = now.isoformat()
    return ('Your personal Dao form grows through free practice '
            f'({info["practices"]}/9; form rank 1–3).')


def technique(state, character, root):
    """Small fixed battle snapshot; decisions alter effect, practice bounds rank."""
    from heavens import ALL_ROOTS, relevant_root
    story = _story(state)
    style = story.get('technique_style')
    if style not in ('guard', 'flow', 'strike') or root not in ALL_ROOTS:
        return None
    info = character.get('dao_practice_v025') if isinstance(character, dict) else None
    practices = _int(info.get('practices', 0), 0, 9) if isinstance(info, dict) else 0
    rank = min(3, 1 + practices // 4)
    name = story.get('technique')
    if not isinstance(name, str) or not name:
        name = {'guard': 'Patient Edge', 'flow': 'Seeking Thread', 'strike': 'Shared Horizon'}[style]
    return {'name': f'{root} · {name}', 'root': relevant_root(root),
            'style': style, 'rank': rank,
            'effect': {'guard': 'ward against the next enemy hit', 'flow': 'Combat Qi restored', 'strike': 'extra damage'}[style]}
