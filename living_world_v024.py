"""World stories add bounded active rewards without changing historical Qi events."""
from copy import deepcopy
from hashlib import sha256
from combat_journey_v024 import utc

CHAPTERS = (
    ('Lantern Caravan', 'mistwood', 'Guide the lantern keepers through the forest.'),
    ('Mountain Seedkeepers', 'stonepeak', 'Help the patient guardians restore a shattered garden.'),
    ('Stormwatch Pilgrimage', 'thunder_rift', 'Follow distant bells between the thunderclouds.'),
    ('Moonlit Herb Gathering', 'mistwood', 'Watch for the white deer beside the herb pools.'),
)
MEMORIES = ('A white deer leads you to an untouched spring.', 'A wandering elder shares a half-forgotten verse.',
            'A lantern spirit bows before disappearing into the mist.', 'An ancient bell answers your heartbeat.')


def chapter(guild, now):
    day = utc(now).date().isoformat()
    index = sha256(f'living-dao-v024:{guild}:{day}'.encode()).digest()[0] % len(CHAPTERS)
    name, region, story = CHAPTERS[index]
    return dict(day=day, name=name, region=region, story=story)


def exploration(state, guild, region, now, roll):
    if type(roll) is not int or not 0 <= roll < 1000:
        raise ValueError('Invalid world encounter roll.')
    s = deepcopy(state)
    info = s.setdefault('world_v024', {})
    event = chapter(guild, now)
    extra, lines = 0, []
    if region == event['region'] and info.get('chapter_day') != event['day']:
        extra += 1
        info['chapter_day'] = event['day']
        info['chapters'] = info.get('chapters', 0) + 1
        lines.append(f'{event["name"]}: {event["story"]} +1 regional material (once per UTC day).')
    if roll < 80 and info.get('rare_day') != event['day']:
        extra += 1
        info['rare_day'] = event['day']
        info['memories'] = info.get('memories', 0) + 1
        info['last_memory'] = MEMORIES[roll % len(MEMORIES)]
        lines.append('Rare trail encounter: ' + info['last_memory'] + ' +1 regional material; one rare reward per UTC day.')
    return s, extra, '\n'.join(lines)


def practice_story(state, root, now, roll, threshold):
    """Three small early epiphanies; no late-realm Qi or new multipliers."""
    p = state.get('player', {})
    info = state.setdefault('living_dao_v024', {})
    lines = []
    if (not p.get('advanced_to_foundation') and not state.get('golden_core')
            and int(p.get('stage', 1)) <= 3 and info.get('epiphanies', 0) < 3):
        state['eternal']['bank_qi'] += 12
        info['epiphanies'] = info.get('epiphanies', 0) + 1
        lines.append(f'Beginner epiphany {info["epiphanies"]}/3: +12 banked Qi. Breakthroughs remain manual.')
    if roll < threshold:
        memory = MEMORIES[roll % len(MEMORIES)]
        info['memories'] = info.get('memories', 0) + 1
        info['last_memory'] = f'{root}: {memory}'
        lines.append('Dao memory: ' + memory)
    return '\n'.join(lines)
