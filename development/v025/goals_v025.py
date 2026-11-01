"""Economy v1 daily and weekly goals: pure rules over the Living Dao JSON.

Progress is recorded inside transactions that already lock the player's Living
Dao row (explore, hunt victory, cultivation practice). Rewards are claimed through
the normal Living Dao action, which also locks inventory, so a replayed Discord
interaction never pays twice. No schema change: state lives in `goals_v025`.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

KINDS = {'explore': 'Explore a region', 'hunt_win': 'Win a hunt', 'practice': 'Complete cultivation practice'}
# (kind, need, Spirit Stones) options per daily slot; the UTC date picks one of each.
DAILY_SLOTS = (
    (('explore', 2, 4), ('explore', 3, 5)),
    (('hunt_win', 1, 5), ('hunt_win', 2, 8)),
    (('practice', 1, 3),),
)
DAILY_BONUS = 3  # Finishing all three daily goals.
WEEKLY = (('explore', 12, 15), ('hunt_win', 8, 15), ('days', 4, 20))
WEEKLY_LABELS = {'explore': 'Explore {need} times', 'hunt_win': 'Win {need} hunts',
                 'days': 'Finish your daily goals on {need} days'}
CAP = 999


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Goal time must be timezone aware.')
    return now.astimezone(timezone.utc)


def _day(now):
    return _now(now).date().isoformat()


def _week(now):
    year, week, _ = _now(now).isocalendar()
    return f'{year}-W{week:02d}'


def _count(value):
    return value if type(value) is int and 0 <= value <= CAP else 0


def daily_goals(now):
    """Today's three goals, the same for every player on that UTC day."""
    ordinal = _now(now).date().toordinal()
    return tuple(slot[(ordinal + index) % len(slot)] for index, slot in enumerate(DAILY_SLOTS))


def _goals(state, now):
    raw = state.get('goals_v025') if isinstance(state, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    day, week = _day(now), _week(now)
    same_day, same_week = raw.get('day') == day, raw.get('week') == week
    daily = raw.get('daily') if same_day and isinstance(raw.get('daily'), dict) else {}
    weekly = raw.get('weekly') if same_week and isinstance(raw.get('weekly'), dict) else {}
    claimed_d = raw.get('claimed_daily') if same_day and isinstance(raw.get('claimed_daily'), list) else []
    claimed_w = raw.get('claimed_weekly') if same_week and isinstance(raw.get('claimed_weekly'), list) else []
    return {'day': day, 'week': week,
            'daily': {kind: _count(daily.get(kind)) for kind in KINDS},
            'weekly': {kind: _count(weekly.get(kind)) for kind in (*KINDS, 'days')},
            'claimed_daily': sorted({k for k in claimed_d if k in KINDS or k == 'bonus'}),
            'claimed_weekly': sorted({k for k in claimed_w if k in WEEKLY_LABELS})}


def record(state, kind, now):
    """Count one finished activity; returns a new state. Unknown kinds are ignored."""
    result = deepcopy(state) if isinstance(state, dict) else {}
    if kind not in KINDS:
        return result
    goals = _goals(result, now)
    goals['daily'][kind] = min(CAP, goals['daily'][kind] + 1)
    goals['weekly'][kind] = min(CAP, goals['weekly'][kind] + 1)
    result['goals_v025'] = goals
    return result


def _daily_rows(goals, now):
    return [(kind, KINDS[kind] if need == 1 else f'{KINDS[kind]} ×{need}', min(goals['daily'][kind], need), need, reward)
            for kind, need, reward in daily_goals(now)]


def _weekly_rows(goals):
    return [(kind, WEEKLY_LABELS[kind].format(need=need), min(goals['weekly'][kind], need), need, reward)
            for kind, need, reward in WEEKLY]


def view(state, now):
    goals = _goals(state, now)
    tomorrow = (_now(now) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    lines = ['**Daily goals** (reset at 00:00 UTC)']
    for kind, label, done, need, reward in _daily_rows(goals, now):
        flag = 'CLAIMED' if kind in goals['claimed_daily'] else 'READY' if done >= need else f'{done}/{need}'
        lines.append(f'• {label} — {flag} | +{reward} Spirit Stones')
    lines.append(f'• All three — +{DAILY_BONUS} bonus Spirit Stones' + (' (CLAIMED)' if 'bonus' in goals['claimed_daily'] else ''))
    lines.append('**Weekly goals** (reset Monday 00:00 UTC)')
    for kind, label, done, need, reward in _weekly_rows(goals):
        flag = 'CLAIMED' if kind in goals['claimed_weekly'] else 'READY' if done >= need else f'{done}/{need}'
        lines.append(f'• {label} — {flag} | +{reward} Spirit Stones')
    lines.append(f'Claim with /quest_claim quest:Daily goals or Weekly goals. Next daily reset <t:{int(tomorrow.timestamp())}:R>.')
    return '\n'.join(lines)


def claim(state, which, now):
    """Pay every finished, unclaimed goal of one board. Returns (state, reply, reward)."""
    if which not in ('daily', 'weekly'):
        raise ValueError('Choose Daily goals or Weekly goals.')
    result = deepcopy(state) if isinstance(state, dict) else {}
    goals = _goals(result, now)
    reward, paid = 0, []
    if which == 'daily':
        rows = _daily_rows(goals, now)
        for kind, label, done, need, gain in rows:
            if done >= need and kind not in goals['claimed_daily']:
                goals['claimed_daily'].append(kind)
                reward += gain
                paid.append(label)
        if all(kind in goals['claimed_daily'] for kind, *_ in rows) and 'bonus' not in goals['claimed_daily']:
            goals['claimed_daily'].append('bonus')
            reward += DAILY_BONUS
            paid.append('All three daily goals')
            goals['weekly']['days'] = min(CAP, goals['weekly']['days'] + 1)
    else:
        for kind, label, done, need, gain in _weekly_rows(goals):
            if done >= need and kind not in goals['claimed_weekly']:
                goals['claimed_weekly'].append(kind)
                reward += gain
                paid.append(label)
    if not paid:
        raise ValueError('No finished goals to claim yet. /quests shows your progress; nothing was spent.')
    goals['claimed_daily'].sort()
    goals['claimed_weekly'].sort()
    result['goals_v025'] = goals
    return result, f'Goals claimed: {", ".join(paid)}. +{reward} Spirit Stones.', reward
