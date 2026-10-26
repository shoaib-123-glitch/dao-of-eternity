"""Authored Living World arc; pure rules plus an existing-receipt store adapter.

No background jobs, NPC API, schema, or live connection is created by this module.
Region proof and choices live in the player's existing Living Dao JSON. Story
rewards are deliberately small and independent of random discovery rolls.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

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
    return {'chapter': chapter, 'title': entry['title'], 'region': entry['region'],
            'complete': False, 'ready': ready, 'text': entry['text'] + ('\n' + remembered if remembered else ''),
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
    lines = ['**Echoes of the Fallen Heaven**', view['title'], view['text']]
    if not view['complete']:
        lines.append('Choose below; no time limit.' if view['ready'] else
                     f'Next: /explore region:{view["region"]}. The usual 5-minute expedition cooldown applies.')
    for key, npc in NPCS.items():
        label, trust = relationship(story, key)
        lines.append(f'{npc["name"]} · {label} {trust}/{MAX_TRUST}')
    lines.append(f'Jade echoes: {story["fragments"]} / 3 · 3 marks per chapter, once only.')
    return '\n'.join(lines)


def _reaction(state, doctrine=None):
    companion = state.get('companion')
    beasts = {'jade_fox': 'Your curious Jade Fox noses a hidden seam in the jade.',
              'storm_hawk': 'Your proud Storm Hawk keeps watch above the ruins.',
              'stone_tortoise': 'Your patient Stone Tortoise steadies the fractured floor.'}
    lines = [beasts[companion]] if isinstance(companion, str) and companion in beasts else []
    if state.get('sect'):
        doctrine = doctrine if isinstance(doctrine, str) else None
        messages = {'wisdom': 'Yue welcomes your sect’s tradition of careful study.',
                    'harmony': 'Jian asks your sect to safeguard the mountain paths.',
                    'valor': 'Zhao recognizes your sect’s discipline in the storm.'}
        lines.append(messages.get(doctrine, 'The three travelers ask your sect to remember the people behind the legend.'))
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
        story['encounter'] = {'chapter': view['chapter'], 'region': region,
                              'at': now.isoformat()}
        lines.append(f'**{view["title"]}** — {view["text"]} Open /guide and choose Echoes of the Fallen Heaven.')
    elif view['complete']:
        resolution = story.get('choices', {}).get('2')
        lines.append({'restore_beacon': 'A guardian lantern now guides the road.',
                      'open_archive': 'Travelers exchange copies of the recovered records.',
                      'reconcile': 'A shared training ground welcomes rival schools.'}.get(resolution, 'The mountain remembers the restored beacon.'))
    else:
        lines.append(f'The jade points toward {view["region"]}; this expedition’s usual rewards are unchanged.')
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
    reaction = _reaction(result, doctrine)
    if reaction:
        lines.append(reaction)
    story.setdefault('choices', {})
    result['living_world_v025'] = story
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
        raise ValueError(f'Explore {entry["region"]} first to discover this chapter.')
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
    reply = (f'{NPCS[max(changes, key=changes.get)]["name"]}: {line}\n'
             f'Chapter complete · +{CHAPTER_MARKS} saved marks · Jade echo {story["fragments"]}/3.')
    if chapter == 1:
        reply += ('\nPersonal Dao form: ' + story['technique']
                  + '. Free /cultivation_practice refines it. Your first Elemental Art gets one modest effect per battle.')
    if chapter == 2:
        reply += '\nArc complete. No repeat payouts; every choice preserves access to training and future arcs.'
    else:
        reply += f'\nNext region: {CHAPTERS[chapter + 1]["region"]}. Continue whenever you wish.'
    reaction = _reaction(result, doctrine)
    if reaction:
        reply += '\n' + reaction
    return result, reply, CHAPTER_MARKS


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
    return {'unlocked': unlocked, 'ready': available,
            'next_at': tomorrow.isoformat() if unlocked and not available else None,
            'current_style': story.get('technique') if isinstance(story.get('technique'), str) else None,
            'teachers': [{'key': key, 'name': NPCS[key]['name'], 'lesson': item[0],
                          'form': item[1], 'effect': item[2]} for key, item in TEACHERS.items()],
            'text': ('Complete the Stonepeak archive chapter to meet all three teachers.' if not unlocked else
                     'One free lesson per UTC day. Retune your form; no marks, loot or cultivation Qi. '
                     'Zhao telegraphs Rush → Feint → Guard; answer with Guard → Strike → Feint. '
                     'This friendly duel cannot injure you.')}


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
            raise ValueError('Zhao needs three tactics: guard, strike or feint. He telegraphs Rush, Feint, Guard.')
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
        stances = ('rush', 'feint', 'guard')
        counters = ('guard', 'strike', 'feint')
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
                 'Your first Elemental Art gets this form’s effect once per battle (cap 3). '
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
            f'({info["practices"]}/9; combat effect capped at 3).')


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
            'effect': {'guard': 'HP restored', 'flow': 'Combat Qi restored', 'strike': 'extra damage'}[style]}
