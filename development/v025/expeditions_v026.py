"""Companion expeditions: send your spirit beast away for a few hours, collect a small haul.

Pure rules over the Living Dao JSON. One expedition at a time; nothing expires and
nothing is lost if the player returns late. Rewards are modest on purpose (shorter
routes pay more per hour, so short play sessions are not punished). State lives in
`expedition_v026`; the Living Dao action locks the row and the inventory.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

# key: (name, hours, bond needed, spirit stones, materials, material pool)
ROUTES = {
    'mistwood_trail': ('Mistwood Trail', 2, 1, 4, 1, ('wolf_fang',)),
    'stonepeak_caves': ('Stonepeak Caves', 4, 2, 6, 2, ('wolf_fang', 'stone_core')),
    'thunder_ridge': ('Thunder Ridge', 8, 3, 10, 3, ('stone_core', 'serpent_scale')),
}
TALES = {
    'jade_fox': ('came back with a herb tucked behind one ear',
                 'sniffed out a hidden cache beneath old roots',
                 'led a lost disciple home before returning'),
    'storm_hawk': ('circled a storm and dove through its eye',
                   'spotted a glint of scale from the clouds',
                   'raced a thunderbolt and nearly won'),
    'stone_tortoise': ('sat beside a waterfall until it was satisfied',
                       'carried a stubborn stone core all the way home',
                       'outlasted a wolf pack by refusing to move'),
}


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Expedition time must be timezone aware.')
    return now.astimezone(timezone.utc)


def _trip(state):
    raw = state.get('expedition_v026') if isinstance(state, dict) and isinstance(state.get('expedition_v026'), dict) else {}
    route = raw.get('route') if raw.get('route') in ROUTES else None
    try:
        sent = datetime.fromisoformat(raw['sent_at']).astimezone(timezone.utc) if route else None
    except (KeyError, TypeError, ValueError):
        route, sent = None, None
    count = raw.get('count') if type(raw.get('count')) is int and raw.get('count') >= 0 else 0
    return {'route': route, 'sent_at': sent, 'count': count}


def _returns_at(trip):
    return trip['sent_at'] + timedelta(hours=ROUTES[trip['route']][1])


def send(state, route, now):
    """Returns (state, reply). Needs a companion with enough bond and no trip in progress."""
    now = _now(now)
    if route not in ROUTES:
        raise ValueError('Choose Mistwood Trail, Stonepeak Caves or Thunder Ridge.')
    if not state.get('companion'):
        raise ValueError('Adopt a spirit companion first: /adopt beast:Jade Spirit Fox costs 16 Spirit Stones. See all beasts with /companion.')
    name, hours, need, *_ = ROUTES[route]
    bond = state.get('bond') if type(state.get('bond')) is int else 0
    if bond < need:
        raise ValueError(f'{name} needs Bond Level {need}; your companion is Bond Level {bond}. Nothing changed.')
    trip = _trip(state)
    if trip['route']:
        raise ValueError(f'Your companion is already on {ROUTES[trip["route"]][0]}. '
                         'Use /companion action:Welcome your companion home when it returns.')
    result = deepcopy(state)
    result['expedition_v026'] = {'route': route, 'sent_at': now.isoformat(), 'count': trip['count']}
    stamp = int((now + timedelta(hours=hours)).timestamp())
    return result, f'Your companion set out for **{name}**. It returns <t:{stamp}:R>. No need to hurry back; the haul waits for you.'


def welcome(state, now):
    """Returns (state, reply, spirit_stones, holdings). Refused until the trip is over."""
    now = _now(now)
    trip = _trip(state)
    if not trip['route']:
        raise ValueError('Your companion is not on an expedition. Send it with /companion action:<route>.')
    back = _returns_at(trip)
    if now < back:
        raise ValueError(f'Your companion returns from {ROUTES[trip["route"]][0]} <t:{int(back.timestamp())}:R>. Nothing changed.')
    name, _, _, stones, amount, pool = ROUTES[trip['route']]
    holdings = {}
    for index in range(amount):
        key = pool[(trip['count'] + index) % len(pool)]
        holdings[key] = holdings.get(key, 0) + 1
    tales = TALES.get(state.get('companion'), TALES['jade_fox'])
    tale = tales[trip['count'] % len(tales)]
    result = deepcopy(state)
    result['expedition_v026'] = {'route': None, 'sent_at': None, 'count': trip['count'] + 1}
    from inventory import ITEMS
    reply = (f'Your companion {tale} on {name}. Haul: {stones} Spirit Stones, '
             + ', '.join(f'{qty}× {ITEMS[key]}' for key, qty in holdings.items())
             + f'. Expeditions completed: {trip["count"] + 1}.')
    return result, reply, stones, holdings


def view(state, now):
    now = _now(now)
    if not state.get('companion'):
        return ''
    trip = _trip(state)
    bond = state.get('bond') if type(state.get('bond')) is int else 0
    if trip['route']:
        back = _returns_at(trip)
        status = (f'On {ROUTES[trip["route"]][0]}; ' +
                  ('back now, welcome it home.' if now >= back else f'returns <t:{int(back.timestamp())}:R>.'))
    else:
        status = 'Resting at home; ready to set out.'
    lines = [f'**Expeditions** · {status} Completed: {trip["count"]}.']
    for key, (name, hours, need, stones, amount, _) in ROUTES.items():
        lock = '' if bond >= need else f' (needs Bond Level {need})'
        lines.append(f'• {name}: {hours} h · {stones} Spirit Stones + {amount} material{"s" if amount > 1 else ""}{lock}')
    return '\n'.join(lines)
