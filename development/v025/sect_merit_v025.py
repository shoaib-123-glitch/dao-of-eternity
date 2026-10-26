"""Economy v1 Sect Merit: a personal sect currency and a small merit shop.

Merit lives in the sect hall JSON under `merit_v025`, keyed by member id, so it is
only changed while the sect hall row is locked. The shop sells materials and
cosmetic titles only; never Qi, cultivation or combat power.
"""
from copy import deepcopy
from datetime import datetime, timezone

EARN = {'commission': 4, 'trial': 2}
SHOP = {
    # key: (label, merit cost, holdings granted, title granted, limit, limit period)
    'gathering_bundle': ('Gathering bundle: 1 Spirit Wolf Fang + 1 Stone Guardian Core', 12,
                         {'wolf_fang': 1, 'stone_core': 1}, None, 1, 'day'),
    'serpent_scale': ('1 Thunder Serpent Scale', 20, {'serpent_scale': 1}, None, 1, 'day'),
    'title_outer': ('Title: Outer Disciple (cosmetic)', 30, {}, 'Outer Disciple', 1, 'ever'),
    'title_inner': ('Title: Inner Disciple (cosmetic)', 90, {}, 'Inner Disciple', 1, 'ever'),
}
CAP = 100000


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Merit time must be timezone aware.')
    return now.astimezone(timezone.utc)


def _member(st, user):
    book = st.get('merit_v025') if isinstance(st.get('merit_v025'), dict) else {}
    raw = book.get(str(user)) if isinstance(book.get(str(user)), dict) else {}
    merit = raw.get('merit')
    bought = raw.get('bought') if isinstance(raw.get('bought'), dict) else {}
    titles = raw.get('titles') if isinstance(raw.get('titles'), list) else []
    return {'merit': merit if type(merit) is int and 0 <= merit <= CAP else 0,
            'bought': {k: v for k, v in bought.items() if k in SHOP and isinstance(v, str)},
            'titles': [t for t in titles if isinstance(t, str)][:10]}


def _store(st, user, member):
    result = deepcopy(st)
    book = result.get('merit_v025') if isinstance(result.get('merit_v025'), dict) else {}
    book = dict(book)
    book[str(user)] = member
    result['merit_v025'] = book
    return result


def earn(st, user, source):
    gain = EARN.get(source, 0)
    member = _member(st, user)
    member['merit'] = min(CAP, member['merit'] + gain)
    return _store(st, user, member), gain


def _period(kind, now):
    return 'ever' if kind == 'ever' else _now(now).date().isoformat()


def buy(st, user, item, now):
    """Returns (state, reply, holdings to add). Caller adds holdings under the inventory lock."""
    if item not in SHOP:
        raise ValueError('Choose an item from the merit shop in /sect_domain.')
    label, price, holdings, title, _, period = SHOP[item]
    member = _member(st, user)
    stamp = _period(period, now)
    if member['bought'].get(item) == stamp:
        raise ValueError(f'{label}: already bought ' + ('— titles are kept forever.' if period == 'ever' else 'today. It returns at 00:00 UTC.'))
    if member['merit'] < price:
        raise ValueError(f'{label} costs {price} Sect Merit; you have {member["merit"]}. '
                         f'Commissions give {EARN["commission"]}, sect trials {EARN["trial"]}. Nothing was spent.')
    member['merit'] -= price
    member['bought'][item] = stamp
    if title and title not in member['titles']:
        member['titles'].append(title)
    reply = f'Bought {label} for {price} Sect Merit. Merit left: {member["merit"]}.'
    return _store(st, user, member), reply, dict(holdings)


def view(st, user, now):
    member = _member(st, user)
    lines = [f'**Sect Merit:** {member["merit"]} · earn {EARN["commission"]} per commission, {EARN["trial"]} per sect trial']
    if member['titles']:
        lines.append('Titles: ' + ', '.join(member['titles']))
    lines.append('Merit shop (/sect_domain buy:<item>) · materials and cosmetics only, never power:')
    for key, (label, price, _, _, _, period) in SHOP.items():
        owned = member['bought'].get(key) == _period(period, now)
        note = ' — owned' if owned and period == 'ever' else ' — bought today' if owned else ''
        lines.append(f'• {label} · {price} merit' + (' · once per day' if period == 'day' else '') + note)
    return '\n'.join(lines)
