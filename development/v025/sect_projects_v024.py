"""Distinct members complete shared daily projects; earned vouchers never expire."""
from copy import deepcopy
from combat_journey_v024 import utc

ACTIVITIES = frozenset(('patrol', 'archives', 'gardens'))


def record_commission(state, user, activity, now):
    if activity not in ACTIVITIES:
        raise ValueError('Choose patrol, archives or gardens for a sect commission.')
    s = deepcopy(state)
    day = utc(now).date().isoformat()
    project = s.get('project_v024', {})
    if project.get('day') != day:
        project = {'day': day, 'members': [], 'activities': [], 'complete': False}
    s['project_v024'] = project
    if project['complete']:
        return s, ''
    project['members'] = sorted(set(project['members']) | {str(user)})
    project['activities'] = sorted(set(project['activities']) | {activity})
    if len(project['members']) >= 2 and ACTIVITIES.issubset(project['activities']):
        project['complete'] = True
        s['renown'] = max(0, int(s.get('renown', 0))) + 15
        s['projects_completed_v024'] = s.get('projects_completed_v024', 0) + 1
        vouchers = s.setdefault('project_vouchers_v024', {})
        for member in project['members']:
            vouchers[member] = vouchers.get(member, 0) + 2
        return s, ' Shared project completed! +15 sect renown; each contributor earned 2 Spirit Stones to collect with /sect_project.'
    return s, f' Shared project: {len(project["members"])}/2 distinct members, {len(project["activities"])}/3 activities.'


def claim(state, user):
    s = deepcopy(state)
    vouchers = s.setdefault('project_vouchers_v024', {})
    amount = max(0, int(vouchers.get(str(user), 0)))
    vouchers[str(user)] = 0
    return s, amount


def board(state, now):
    p = state.get('project_v024', {})
    current = p.get('day') == utc(now).date().isoformat()
    members, activities = (len(p.get('members', [])), len(p.get('activities', []))) if current else (0, 0)
    missing=sorted(ACTIVITIES-set(p.get('activities',[]))) if current else sorted(ACTIVITIES)
    next_step='All activities covered.' if not missing else 'Still needed: '+', '.join(missing)+'.'
    if members<2:next_step+=' Invite a different player; repeating your own actions cannot fill the second place.'
    return (f'Living Dao project · {"COMPLETE" if current and p.get("complete") else "in progress"}\n'
        f'{members}/2 distinct members · {activities}/3 activities (patrol, archives, gardens). '
        f'{next_step}\nContribute through /sect_commission. Once per UTC day: +15 shared renown and 2 Spirit Stones per contributor. '
        f'Completed projects: {state.get("projects_completed_v024", 0)}. /sect_project collects earned Spirit Stones; they never expire.')
