"""Pure, additive cultivation rules for The Shattered Heavens v0.23.

All new progression lives in the existing character JSON. Callers hold the
character lock and settle Eternal Qi before changing focus or mastery. There is
no background tick, purchase, root reroll, expiry, or automatic breakthrough.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from secrets import randbelow

from heavens import ALL_ROOTS, RARE_CORRESPONDENCE, add_boon, dt, iso, relevant_root

FOCUSES = {
    'gather': 'Gathering: +10% passive Qi',
    'insight': 'Insight: +2 mastery per practice',
    'resonance': 'Resonance: rare practice encounters improve from 8% to 13%',
}
PRACTICE_COOLDOWN = timedelta(hours=6)
FOCUS_COOLDOWN = timedelta(hours=6)
MAX_MASTERY = 100
ROOT_UTILITIES = {
    'Fire': ('Ember Refinement', 'cleanse 2 impurity and bank 4 Qi'),
    'Water': ('Moonwell Recovery', 'restore 4 meridian integrity'),
    'Earth': ('Mountain Resolve', 'restore 4 stability'),
    'Wind': ('Cloud Insight', 'gain 1 additional mastery'),
    'Wood': ('Verdant Renewal', 'restore 2 integrity and 2 stability'),
    'Metal': ('Sword Discipline', 'earn 1 training seal'),
    'Lightning': ('Thunder Absorption', 'bank 12 Qi'),
    'Ice': ('Stillwater Clarity', 'cleanse 4 impurity'),
}
RARE_TITLES = {
    'Light': 'Dawn Refinement', 'Shadow': 'Veiled Insight',
    'Poison': 'Venom Renewal', 'Blood': 'Crimson Recovery',
    'Soul': 'Soul Clarity', 'Void': 'Hollow Resolve',
    'Time': 'Timeless Insight', 'Chaos': 'Primordial Absorption',
}


def _now(value):
    if value.tzinfo is None:
        raise ValueError('Server time must be timezone aware.')
    return value.astimezone(timezone.utc)


def _root(root):
    if root not in ALL_ROOTS:
        raise ValueError('Awaken an elemental root with /awaken first.')
    return root


def _info(state):
    return state.setdefault('cultivation_v023', {'mastery': {}, 'windows': []})


def _ready(info, key, now, label):
    until = info.get(key)
    if until and now < dt(until):
        minutes = int((dt(until) - now).total_seconds() // 60) + 1
        raise ValueError(f'{label} will be ready in {minutes} minute(s).')


def _snapshot(info, now):
    """Record the exact start of new bonuses, including fractional seconds.

    The old window covers an unsettled fraction of a second. Eternal settlement
    discards closed windows only after that fraction has been credited.
    """
    windows = info.setdefault('windows', [])
    if windows and windows[-1].get('end_at') is None:
        windows[-1]['end_at'] = iso(now)
    windows.append({'start_at': iso(now), 'end_at': None,
                    'focus': info.get('focus'), 'mastery': dict(info.get('mastery', {}))})


def focus(state, choice, now):
    now = _now(now)
    if choice not in FOCUSES:
        raise ValueError('Choose gather, insight or resonance.')
    info = _info(state)
    if info.get('focus') == choice:
        raise ValueError('That cultivation focus is already active.')
    _ready(info, 'focus_next_at', now, 'Focus changes')
    info['focus'] = choice
    info['focus_next_at'] = iso(now + FOCUS_COOLDOWN)
    _snapshot(info, now)
    return f'{FOCUSES[choice]}. You may change focus again in 6 hours. No resources spent.'


def bonus_milli(state, when, root):
    """Optional focus/mastery multiplier; old characters retain their old rate."""
    when = _now(when)
    info = state.get('cultivation_v023', {})
    window = next((x for x in reversed(info.get('windows', []))
                   if dt(x['start_at']) <= when
                   and (x.get('end_at') is None or when < dt(x['end_at']))), None)
    if window is None:
        return 1000
    mastery = min(MAX_MASTERY, max(0, int(window.get('mastery', {}).get(root, 0))))
    return 1000 + (100 if window.get('focus') == 'gather' else 0) + (mastery // 20) * 10


def bonus_boundaries(state, start, end):
    marks = set()
    for item in state.get('cultivation_v023', {}).get('windows', []):
        for key in ('start_at', 'end_at'):
            if item.get(key):
                value = dt(item[key])
                if start < value < end:
                    marks.add(value)
    return marks


def prune_windows(state, settled_through):
    info = state.get('cultivation_v023')
    if info:
        info['windows'] = [x for x in info.get('windows', [])
                           if x.get('end_at') is None or dt(x['end_at']) > settled_through]


def root_utility(root):
    _root(root)
    base = relevant_root(root)
    title, effect = ROOT_UTILITIES[base]
    return RARE_TITLES.get(root, title), effect


def _bank(state, now, amount):
    if not state.get('eternal'):
        from eternal import settle
        settle(state, now)
    state['eternal']['bank_qi'] += amount


def _utility(state, root, now):
    from game import unpack_player, pack_player
    p = unpack_player(state['player'])
    base = relevant_root(root)
    if base == 'Fire':
        p.impurity = max(0, p.impurity - 2)
        _bank(state, now, 4)
    elif base == 'Water':
        p.meridian_integrity = min(100, p.meridian_integrity + 4)
    elif base == 'Earth':
        p.stability = min(100, p.stability + 4)
    elif base == 'Wood':
        p.meridian_integrity = min(100, p.meridian_integrity + 2)
        p.stability = min(100, p.stability + 2)
    elif base == 'Metal':
        p.training_seals += 1
    elif base == 'Lightning':
        _bank(state, now, 12)
    elif base == 'Ice':
        p.impurity = max(0, p.impurity - 4)
    state['player'] = pack_player(p)


def practice(state, root, now, roll=None):
    """One free six-hour practice; randomness comes from the server only."""
    now = _now(now)
    _root(root)
    if roll is None:
        roll = randbelow(1000)
    if type(roll) is not int or not 0 <= roll < 1000:
        raise ValueError('Invalid cultivation encounter roll.')
    info = _info(state)
    _ready(info, 'practice_next_at', now, 'Cultivation practice')
    points = 4 + (2 if info.get('focus') == 'insight' else 0) + (1 if relevant_root(root) == 'Wind' else 0)
    mastery = info.setdefault('mastery', {})
    previous = mastery.get(root, 0)
    mastery[root] = min(MAX_MASTERY, previous + points)
    info['practice_next_at'] = iso(now + PRACTICE_COOLDOWN)
    info['practices'] = info.get('practices', 0) + 1
    _utility(state, root, now)
    _snapshot(info, now)
    title, effect = root_utility(root)
    reply = (f'{root} practice complete: +{mastery[root] - previous} mastery '
             f'({mastery[root]}/{MAX_MASTERY}). {title}: {effect}. '
             'Recovery is capped at full health; no resources spent. Next practice in 6 hours.')
    threshold = 130 if info.get('focus') == 'resonance' else 80
    if roll < threshold:
        grade = 'legendary' if roll < 3 else 'epic' if roll < 18 else 'rare'
        boon = add_boon(state, now, grade)
        if boon:
            info['discoveries'] = info.get('discoveries', 0) + 1
            reply += (f' Rare encounter: {boon["name"]}! '
                      f'{boon["mult"]/1000:g}x personal Qi until {boon["end_at"]}. '
                      'The boost also counts while offline.')
        else:
            reply += ' A rare encounter stirs; your existing personal boon remains unchanged.'
    from living_world_v024 import practice_story
    stories=practice_story(state,root,now,roll,threshold)
    if stories:reply+='\n'+stories
    from eternal import refill_after_advance
    refill_after_advance(state)
    return reply


def cultivation_summary(state, root, now):
    """Read-only dashboard lines, including legacy characters with no new state."""
    now = _now(now)
    info = state.get('cultivation_v023', {})
    choice = info.get('focus')
    lines = [FOCUSES.get(choice, 'Focus: unchosen; choose gather, insight or resonance')]
    if root in ALL_ROOTS:
        title, effect = root_utility(root)
        mastery = info.get('mastery', {}).get(root, 0)
        lines.append(f'{root} mastery: {mastery}/{MAX_MASTERY} (+{min(5, mastery // 20)}% passive Qi)')
        lines.append(f'{title}: {effect} with each practice')
    until = info.get('practice_next_at')
    lines.append(f'Practice ready <t:{int(dt(until).timestamp())}:R>'
                 if until and now < dt(until) else 'Cultivation practice: ready (free; every 6 hours)')
    lines.append(f'Practices: {info.get("practices", 0)} | Rare encounters: {info.get("discoveries", 0)}')
    return lines


def journey(state, root, now):
    """A freely readable field journal, never a reward or random roll."""
    return ('The Shattered Heavens — Cultivation Journal\n'
            + '\n'.join(cultivation_summary(state, root, now))
            + '\nEternal Qi never expires. Mastery survives focus changes; '
              'rare roots use the same power budget as their common counterparts. '
              'Breakthroughs remain manual.')
