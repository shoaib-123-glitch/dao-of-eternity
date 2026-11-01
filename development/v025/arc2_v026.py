"""Arc II, "The Silent Lotus": chapters IV-VI, unlocked after Echoes of the Fallen Heaven.

Same rules as Arc I: explore the chapter's region for proof, then make one
authored choice. Rewards are not currency: trust (which can now also drop by
one), a shared memory and a cosmetic title per chapter. State lives in
`arc2_v026`; trust and memories use the same traveler records as Arc I.
"""
from copy import deepcopy
from datetime import datetime, timezone

import living_world_v025 as arc1

OFFSET = len(arc1.CHAPTERS)  # Arc II chapters are numbered 3-5 in panels and buttons.
CHAPTERS = (
    {'title': 'IV · Lanterns Gone Dark', 'region': 'mistwood', 'honor': 'Mist Walker',
     'text': 'The lanterns of Mistwood have gone dark, and spirit herbs vanish from the gardens overnight. '
             'Villagers whisper of cultivators who make no sound: the Silent Lotus. Zhao wants to confront '
             'them, Yue wants to follow them, and Jian wants to hear the villagers first.',
     'choices': (
         ('confront', 'Confront them openly', 'Stand with Zhao in the lantern light.', {'zhao': 3, 'yue': -1},
          'The Silent Lotus scatter, but one of them drops a healer’s token.'),
         ('follow', 'Follow them in silence', 'Track them with Yue without being seen.', {'yue': 3, 'zhao': -1},
          'Their trail ends at a sickhouse full of coughing disciples.'),
         ('listen', 'Listen to the villagers', 'Let Jian ask what was really taken.', {'jian': 3, 'yue': 1},
          'Only medicinal herbs were taken, and each theft left a coin behind.'),
     )},
    {'title': 'V · The Stone That Listens', 'region': 'stonepeak', 'honor': 'Listener of Stone',
     'text': 'Beneath Stonepeak, a Silent Lotus elder waits beside the old archive. Her disciples suffer a '
             'qi deviation that spreads like fever, and only the Palace records hold the cure. She asks; she '
             'does not threaten. Jian remembers that the Lotus once stole from the Palace.',
     'choices': (
         ('share_cure', 'Share the cure freely', 'Yue copies the remedy for them.', {'yue': 3, 'jian': -1},
          'The elder bows so low her forehead touches the stone.'),
         ('fair_trade', 'Ask for the herbs back first', 'Jian insists on an honest exchange.', {'jian': 3, 'zhao': 1},
          'The herbs return to Mistwood; the remedy goes to the sickhouse.'),
         ('champion', 'Duel their champion for it', 'Zhao fights a silent duel, no killing.', {'zhao': 3, 'jian': -1},
          'Zhao wins on the ninth exchange and helps his opponent stand.'),
     )},
    {'title': 'VI · Where the Thunder Rests', 'region': 'thunder_rift', 'honor': 'Rift Warden',
     'text': 'The fever has a root: a crack in the ley line under Thunder Rift, leaking wild qi into every '
             'valley. It can be sealed, or bent toward healing, but not both. The Silent Lotus offer their '
             'disciples to hold the line while someone works the seal.',
     'choices': (
         ('seal', 'Seal the crack with Jian', 'Use the guardian form; nothing leaks again.', {'jian': 3, 'zhao': 1},
          'The rift falls quiet. The fever fades over a month, not a day.'),
         ('heal', 'Bend it toward healing', 'Yue guides the leak into the sickhouse wells.', {'yue': 3, 'jian': -1},
          'The fever breaks in one night. The rift still hums, softly.'),
         ('hold', 'Let Zhao lead the line', 'Zhao and the Lotus disciples hold while you seal.', {'zhao': 3, 'yue': 1},
          'Rival, Lotus and you hold the storm together until the seal takes.'),
     )},
)


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Story time must be timezone aware.')
    return now.astimezone(timezone.utc)


def _arc(state):
    raw = state.get('arc2_v026') if isinstance(state, dict) and isinstance(state.get('arc2_v026'), dict) else {}
    choices = raw.get('choices') if isinstance(raw.get('choices'), dict) else {}
    choices = {k: v for k, v in choices.items() if k in ('0', '1', '2') and isinstance(v, str)}
    chapter = 0
    while str(chapter) in choices and chapter < len(CHAPTERS):
        chapter += 1
    proof = raw.get('encounter') if isinstance(raw.get('encounter'), dict) else None
    titles = [t for t in raw.get('titles', []) if isinstance(t, str)] if isinstance(raw.get('titles'), list) else []
    return {'chapter': chapter, 'choices': choices, 'encounter': proof, 'titles': titles[:10]}


def unlocked(state):
    return arc1.panel(state)['complete']


def panel(state):
    """Same shape as Arc I's panel; chapter numbers continue at 3."""
    if not unlocked(state):
        return arc1.panel(state)
    arc = _arc(state)
    if arc['chapter'] >= len(CHAPTERS):
        return {'chapter': OFFSET + len(CHAPTERS), 'title': 'The Silent Lotus · Arc complete', 'region': None,
                'text': 'Mistwood’s lanterns burn again, and the Silent Lotus now trade openly in the market. '
                        'Titles earned: ' + (', '.join(arc['titles']) or 'none') + '. More arcs will follow.',
                'ready': False, 'choices': [], 'complete': True}
    entry = CHAPTERS[arc['chapter']]
    proof = arc['encounter'] or {}
    ready = proof.get('chapter') == arc['chapter'] and proof.get('region') == entry['region']
    return {'chapter': OFFSET + arc['chapter'], 'title': entry['title'], 'region': entry['region'],
            'text': entry['text'], 'ready': ready, 'complete': False,
            'choices': [{'key': k, 'label': label, 'description': desc} for k, label, desc, _, _ in entry['choices']] if ready else []}


def encounter(state, region, now):
    """Proof for the current Arc II chapter. Returns (state, line) with an empty line if nothing happened."""
    _now(now)
    if not unlocked(state):
        return state, ''
    arc = _arc(state)
    if arc['chapter'] >= len(CHAPTERS) or CHAPTERS[arc['chapter']]['region'] != region:
        return state, ''
    if panel(state)['ready']:
        return state, (f'You already carry the proof for **{CHAPTERS[arc["chapter"]]["title"]}**. '
                       'Open /guide → Echoes of the Fallen Heaven to choose.')
    result = deepcopy(state)
    raw = dict(result.get('arc2_v026') or {}) if isinstance(result.get('arc2_v026'), dict) else {}
    raw['encounter'] = {'chapter': arc['chapter'], 'region': region, 'at': _now(now).isoformat()}
    result['arc2_v026'] = raw
    return result, (f'**{CHAPTERS[arc["chapter"]]["title"]}**: a new thread of the story stirs. '
                    'Open /guide → Echoes of the Fallen Heaven to read it and choose.')


def choose(state, chapter, choice, now):
    """Returns (state, reply, 0). No currency: trust, a memory and a cosmetic title."""
    now = _now(now)
    if not unlocked(state):
        raise ValueError('Finish Echoes of the Fallen Heaven first.')
    arc = _arc(state)
    index = chapter - OFFSET
    if type(chapter) is not int or index != arc['chapter'] or index >= len(CHAPTERS):
        raise ValueError('That story choice is already resolved or belongs to another chapter. Open /guide again; nothing was repeated.')
    entry = CHAPTERS[index]
    proof = arc['encounter'] or {}
    if proof.get('chapter') != index or proof.get('region') != entry['region']:
        raise ValueError(f'Explore {arc1.region_name(entry["region"])} first to discover this chapter.')
    option = next((item for item in entry['choices'] if item[0] == choice), None)
    if option is None:
        raise ValueError('Choose one of the current story options.')
    _, _, _, changes, line = option
    result = deepcopy(state)
    story = arc1._story(result)
    for key, delta in changes.items():
        npc = story['npcs'].setdefault(key, {'trust': 0, 'memories': []})
        npc['trust'] = max(0, min(arc1.MAX_TRUST, npc['trust'] + delta))
        if delta > 0:
            npc['memories'] = (list(npc['memories']) + [{'arc': 2, 'chapter_title': entry['title'], 'choice': choice,
                                                         'at': now.isoformat(), 'text': line}])[-arc1.MAX_MEMORIES:]
    result['living_world_v025'] = story
    raw = dict(result.get('arc2_v026') or {}) if isinstance(result.get('arc2_v026'), dict) else {}
    raw['choices'] = {**arc['choices'], str(index): choice}
    raw['titles'] = arc['titles'] + ([entry['honor']] if entry['honor'] not in arc['titles'] else [])
    raw.pop('encounter', None)
    result['arc2_v026'] = raw
    lead = max(changes, key=changes.get)
    cooled = [arc1.NPCS[k]['name'] for k, d in changes.items() if d < 0]
    reply = f'{arc1.NPCS[lead]["name"]}: {line}\nTitle earned: **{entry["honor"]}** (cosmetic).'
    if cooled:
        reply += f'\n{", ".join(cooled)} disagreed with this choice (trust −1). Bonds can be mended with lessons.'
    if index + 1 < len(CHAPTERS):
        reply += f'\nNext: {CHAPTERS[index + 1]["title"]} in {arc1.region_name(CHAPTERS[index + 1]["region"])}.'
    else:
        reply += '\nArc II complete: The Silent Lotus.'
    return result, reply, 0


def status(state):
    """Arc I status, followed by Arc II once it unlocks."""
    text = arc1.status(state)
    if not unlocked(state):
        return text
    view = panel(state)
    lines = [text, '', '**Arc II · The Silent Lotus**', view['title'], view['text']]
    if not view['complete']:
        lines.append('Choose below; no time limit.' if view['ready'] else
                     f'Next: /explore and pick {arc1.region_name(view["region"])}.')
    return '\n'.join(lines)
