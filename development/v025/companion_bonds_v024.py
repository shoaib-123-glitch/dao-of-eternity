"""Personalities, free bond memories and bounded battle attunement."""
from copy import deepcopy
from datetime import timedelta
from combat_journey_v024 import utc

PROFILES = {
    'jade_fox': ('Curious and mischievous', 'listen', 'The fox steals your ink brush, then draws a map back to you.'),
    'storm_hawk': ('Proud and adventurous', 'roam', 'The hawk circles a hidden ridge and waits for you to follow.'),
    'stone_tortoise': ('Patient and steadfast', 'meditate', 'The tortoise settles beside you until your breathing finds its rhythm.'),
}
ACTIVITIES = {'listen': 'Listen to your companion', 'roam': 'Walk the spirit trails', 'meditate': 'Cultivate quietly together'}
COOLDOWN = timedelta(hours=6)
ATTUNEMENT = timedelta(hours=12)
TRUST_PER_LEVEL = 20


def attuned(state, now):
    last = state.get('companion_v024', {}).get('last_at')
    return bool(last and utc(last) <= utc(now) < utc(last) + ATTUNEMENT)


def bond_action(state, activity, now, roll):
    if not state.get('companion'):
        raise ValueError('Adopt a companion first. A Jade Spirit Fox costs 16 earned marks; /guide shows how.')
    if activity not in ACTIVITIES:
        raise ValueError('Choose listen, roam or meditate for your free companion activity.')
    if type(roll) is not int or not 0 <= roll < 1000:
        raise ValueError('Invalid companion encounter roll.')
    s = deepcopy(state)
    now = utc(now)
    info = s.setdefault('companion_v024', {})
    if info.get('last_at') and now < utc(info['last_at']) + COOLDOWN:
        minutes = max(1, int((utc(info['last_at']) + COOLDOWN - now).total_seconds() + 59) // 60)
        raise ValueError(f'Your companion is content. Another free bond activity is ready in {minutes} minute(s); /beast_cultivation still earns Qi.')
    temperament, favorite, memory = PROFILES.get(s['companion'], ('Gentle and watchful', 'meditate', 'Your companion chooses to rest beside you.'))
    special = not info.get('activities') or roll < 150
    points = 2 + int(activity == favorite) + int(special)
    trust = max(0, int(info.get('trust', 0))) + points
    previous = s.get('bond', 1)
    while trust >= TRUST_PER_LEVEL and s['bond'] < 5:
        trust -= TRUST_PER_LEVEL
        s['bond'] += 1
    info['trust'] = min(TRUST_PER_LEVEL, trust)
    info['activities'] = info.get('activities', 0) + 1
    info['last_at'] = now.isoformat()
    if special:
        info['memories'] = info.get('memories', 0) + 1
        info['last_memory'] = memory
    beast = s.get('beast_cultivation')
    if not beast:
        raise ValueError('Open /beast_cultivation to awaken your companion cultivation first.')
    beast['bank'] += 6
    beast['earned'] += 6
    beast['bond_earned'] = beast.get('bond_earned', 0) + 6
    reply = (f'{ACTIVITIES[activity]} · {temperament}. +{points} trust ({info["trust"]}/{TRUST_PER_LEVEL}), '
        f'+6 Beast Qi. Bond {s["bond"]}/5. Free activity every 6 hours; battle attunement lasts 12 hours.')
    if special:
        reply += '\nBond memory: ' + memory
    if s['bond'] > previous:
        reply += f'\nYour friendship deepens: Bond Level {s["bond"]}!'
    return s, reply


def summary(state, now):
    if not state.get('companion'):
        return 'No spirit companion yet. /adopt beast:jade_fox costs 16 earned marks; /guide shows the route.'
    temperament, favorite, _ = PROFILES.get(state['companion'], ('Gentle and watchful', 'meditate', ''))
    info = state.get('companion_v024', {})
    ready = 'ready now' if not info.get('last_at') or utc(now) >= utc(info['last_at']) + COOLDOWN else f'ready <t:{int((utc(info["last_at"])+COOLDOWN).timestamp())}:R>'
    benefit = {'jade_fox': 'Spirit Beast Art heals 3 extra HP', 'storm_hawk': '+2 extra opening combat Qi',
               'stone_tortoise': '+1 battle ward'}.get(state['companion'], 'quiet companionship')
    memory='\nLast memory: '+info['last_memory'] if info.get('last_memory') else '\nTry a favorite activity to share your first memory.'
    return (f'{temperament} · enjoys {favorite}. Trust {info.get("trust", 0)}/{TRUST_PER_LEVEL}; '
        f'memories {info.get("memories", 0)}. /beast_bond is {ready}.\n'
        f'12-hour attunement: {benefit} ({"active" if attuned(state, now) else "bond to activate"}). '
        'No feeding cost or absence penalty.'+memory)
