"""Earned sect identity and commissions, persisted in existing hall JSON.

These helpers perform no I/O. Renown is separate from contributed prosperity:
free commissions cannot spend or replenish a sect's hall treasury.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

DOCTRINES = {
    'valor': 'Valor — protect travelers and stand against the darkness',
    'wisdom': 'Wisdom — preserve lost scriptures and share understanding',
    'harmony': 'Harmony — nurture spirit gardens and the living world',
}
COMMISSIONS = {
    'patrol': ('Protect the mountain paths', 'valor'),
    'archives': ('Restore the scripture archives', 'wisdom'),
    'gardens': ('Tend the spirit gardens', 'harmony'),
}
RENOWN_TIERS = ((0, 'Novice'), (30, 'Rising'), (90, 'Established'),
                (180, 'Renowned'), (360, 'Heavenly'))
COMMISSION_MARKS = 6
COMMISSION_RENOWN = 5
COMMISSION_LIMIT = 3
COMMISSION_COOLDOWN = timedelta(hours=6)
DOCTRINE_COOLDOWN = timedelta(hours=24)


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def renown_status(state):
    """Return (current renown, tier title, next threshold or None)."""
    renown = max(0, int(state.get('renown', 0)))
    tier = RENOWN_TIERS[0][1]
    next_at = None
    for threshold, name in RENOWN_TIERS:
        if renown >= threshold:
            tier = name
        else:
            next_at = threshold
            break
    return renown, tier, next_at


def complete_commission(state, user, activity, now):
    """Return new hall state, personal marks and renown earned; never mutate input."""
    if activity not in COMMISSIONS:
        raise ValueError('Choose patrol, archives or gardens for your sect commission.')
    now = _utc(now)
    updated = deepcopy(state)
    records = updated.setdefault('commissions', {})
    member = records.get(str(user), {})
    day = now.date().isoformat()
    count = max(0, int(member.get('count', 0))) if member.get('day') == day else 0
    if count >= COMMISSION_LIMIT:
        raise ValueError('You have completed three sect commissions today. The board resets at 00:00 UTC.')
    if member.get('last_at'):
        remaining = COMMISSION_COOLDOWN - (now - _utc(member['last_at']))
        if remaining > timedelta(0):
            minutes = max(1, (int(remaining.total_seconds()) + 59) // 60)
            raise ValueError(f'Your sect commission is recovering. Retry in {minutes} minute(s).')
    earned = COMMISSION_RENOWN + int(updated.get('doctrine') == COMMISSIONS[activity][1])
    updated['renown'] = max(0, int(updated.get('renown', 0))) + earned
    updated['commission_count'] = max(0, int(updated.get('commission_count', 0))) + 1
    records[str(user)] = {**member, 'day': day, 'count': count + 1, 'last_at': now.isoformat()}
    return updated, COMMISSION_MARKS, earned


def choose_doctrine(state, doctrine, now):
    """Apply a founder-authorized doctrine; caller enforces founder identity."""
    if doctrine not in DOCTRINES:
        raise ValueError('Choose valor, wisdom or harmony as the sect doctrine.')
    if state.get('doctrine') == doctrine:
        raise ValueError('Your sect already follows that doctrine.')
    now = _utc(now)
    if state.get('doctrine_changed_at'):
        remaining = DOCTRINE_COOLDOWN - (now - _utc(state['doctrine_changed_at']))
        if remaining > timedelta(0):
            raise ValueError('A sect doctrine can change only once every 24 hours.')
    updated = deepcopy(state)
    updated['doctrine'] = doctrine
    updated['doctrine_changed_at'] = now.isoformat()
    return updated
