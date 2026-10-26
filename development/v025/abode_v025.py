"""Cave abode: the long-term Spirit Stone sink. Pure rules over the Living Dao JSON.

Three rooms, levels 0-10, with quadratic prices. Effects stay modest: a daily
material harvest, a small battle-start HP bonus and cosmetic prestige. State lives
in `abode_v025`; the Living Dao action locks the row and the inventory.
"""
from copy import deepcopy
from datetime import datetime, timezone

MAX_LEVEL = 10
ROOMS = {
    'garden': ('Spirit Herb Garden', 30, 'harvest materials once per UTC day (1 per level)'),
    'training': ('Training Grounds', 40, '+2 max HP per level at the start of each hunt'),
    'hall': ('Hall of Echoes', 25, 'cosmetic prestige; raises your abode rank'),
}
HARVEST = ('wolf_fang', 'stone_core', 'serpent_scale')
RANKS = ((0, 'Bare Cave'), (5, 'Humble Abode'), (12, 'Tranquil Retreat'), (20, 'Hidden Sanctuary'), (30, 'Immortal Cave Abode'))


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Abode time must be timezone aware.')
    return now.astimezone(timezone.utc)


def _abode(state):
    raw = state.get('abode_v025') if isinstance(state, dict) and isinstance(state.get('abode_v025'), dict) else {}
    levels = raw.get('levels') if isinstance(raw.get('levels'), dict) else {}
    harvest = raw.get('harvest_day')
    return {'levels': {room: levels[room] if type(levels.get(room)) is int and 0 <= levels[room] <= MAX_LEVEL else 0
                       for room in ROOMS},
            'harvest_day': harvest if isinstance(harvest, str) else None}


def cost(room, level):
    """Price to go from `level` to `level + 1`."""
    return ROOMS[room][1] * (level + 1) ** 2


def level(state, room):
    return _abode(state)['levels'].get(room, 0)


def battle_hp(state):
    return 2 * level(state, 'training')


def rank(state):
    total = sum(_abode(state)['levels'].values())
    return total, [name for need, name in RANKS if total >= need][-1]


def upgrade(state, room, balance):
    """Returns (state, reply, price). Caller deducts the price under the inventory lock."""
    if room not in ROOMS:
        raise ValueError('Choose Herb Garden, Training Grounds or Hall of Echoes.')
    abode = _abode(state)
    current = abode['levels'][room]
    if current >= MAX_LEVEL:
        raise ValueError(f'{ROOMS[room][0]} is already at level {MAX_LEVEL}.')
    price = cost(room, current)
    if balance < price:
        raise ValueError(f'{ROOMS[room][0]} level {current + 1} costs {price} Spirit Stones; you have {balance}. Nothing was spent.')
    result = deepcopy(state)
    abode['levels'][room] = current + 1
    result['abode_v025'] = abode
    total, title = rank(result)
    reply = (f'{ROOMS[room][0]} raised to level {current + 1} for {price} Spirit Stones. '
             f'Abode rank: {title} ({total}).')
    if current + 1 < MAX_LEVEL:
        reply += f' Next level costs {cost(room, current + 1)}.'
    return result, reply, price


def harvest(state, now):
    """Returns (state, reply, holdings). Once per UTC day; needs a Herb Garden."""
    abode = _abode(state)
    garden = abode['levels']['garden']
    if not garden:
        raise ValueError(f'Build a Spirit Herb Garden first ({cost("garden", 0)} Spirit Stones).')
    day = _now(now).date().isoformat()
    if abode['harvest_day'] == day:
        raise ValueError('Your garden was already harvested today. It regrows at 00:00 UTC.')
    kinds = HARVEST if garden >= 5 else HARVEST[:2]
    holdings = {}
    for index in range(garden):
        key = kinds[index % len(kinds)]
        holdings[key] = holdings.get(key, 0) + 1
    result = deepcopy(state)
    abode['harvest_day'] = day
    result['abode_v025'] = abode
    from inventory import ITEMS
    reply = 'Harvested your Spirit Herb Garden: ' + ', '.join(f'{qty}× {ITEMS[key]}' for key, qty in holdings.items()) + '.'
    return result, reply, holdings


def view(state, now):
    abode = _abode(state)
    total, title = rank(state)
    lines = [f'**Cave Abode** · {title} (rank {total})']
    for room, (name, _, effect) in ROOMS.items():
        current = abode['levels'][room]
        price = f'next: {cost(room, current)} Spirit Stones' if current < MAX_LEVEL else 'max level'
        lines.append(f'• {name} Lv{current}/{MAX_LEVEL} · {effect} · {price}')
    if abode['levels']['garden']:
        ready = abode['harvest_day'] != _now(now).date().isoformat()
        lines.append('Garden: ' + ('ready to harvest.' if ready else 'harvested today; regrows at 00:00 UTC.'))
    lines.append('Use /abode action:<choice> to upgrade a room or harvest.')
    return '\n'.join(lines)
