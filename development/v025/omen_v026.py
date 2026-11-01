"""Heavenly Omen: one shared weekly goal for the whole server.

Each ISO week the heavens mark one region. Every exploration there adds to the
server's total (each player counts at most PERSONAL_CAP times, so no one can
carry the server alone). When the total crosses a tier, every player who helped
at least once can claim that tier's reward once. Pure rules; each player's share
lives in their own Living Dao JSON (`omen_v026`), and the server total is the sum
of those shares, so no new table is needed.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

PERSONAL_CAP = 15
TIERS = ((20, 5), (60, 10), (120, 15))  # (server total, Spirit Stones)
TITLE = 'Omen Witness'  # Cosmetic, for helping the server reach the last tier.
OMENS = (
    ('Falling Star over Mistwood', 'mistwood',
     'A star fell into Mistwood Vale and the herbs around it glow. Gather its light before it fades.'),
    ('Stone Dragon Stirs', 'stonepeak',
     'Stonepeak trembles at night. Something old shifts beneath the mountain; scouts are needed.'),
    ('Thunder Without Clouds', 'thunder_rift',
     'Lightning strikes the Rift from a clear sky. Each bolt leaves a shard worth recovering.'),
    ('Mist That Remembers', 'mistwood',
     'The mist of Mistwood shows travellers scenes from the Fallen Heaven. Walk it and record what you see.'),
)


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Omen time must be timezone aware.')
    return now.astimezone(timezone.utc)


def week(now):
    year, number, _ = _now(now).isocalendar()
    return f'{year}-W{number:02d}'


def current(guild, now):
    """The same omen for the whole server all week, stable across restarts."""
    key = week(now)
    digest = sha256(f'dao-omen-v1:{int(guild)}:{key}'.encode()).digest()
    name, region, text = OMENS[digest[0] % len(OMENS)]
    return {'week': key, 'name': name, 'region': region, 'text': text}


def _share(state, now):
    raw = state.get('omen_v026') if isinstance(state, dict) and isinstance(state.get('omen_v026'), dict) else {}
    if raw.get('week') != week(now):
        return {'week': week(now), 'count': 0, 'claimed': 0}
    count = raw.get('count') if type(raw.get('count')) is int else 0
    claimed = raw.get('claimed') if type(raw.get('claimed')) is int else 0
    return {'week': week(now), 'count': max(0, min(PERSONAL_CAP, count)), 'claimed': max(0, min(len(TIERS), claimed))}


def record(state, guild, region, now):
    """Returns (state, line). Counts one exploration of this week's omen region."""
    omen = current(guild, now)
    if region != omen['region']:
        return state, ''
    share = _share(state, now)
    if share['count'] >= PERSONAL_CAP:
        return state, f'🌠 {omen["name"]}: you have given your full share this week ({PERSONAL_CAP}). Others can carry it on.'
    result = deepcopy(state)
    share['count'] += 1
    result['omen_v026'] = share
    return result, f'🌠 {omen["name"]}: your exploration adds to the server omen ({share["count"]}/{PERSONAL_CAP} from you).'


def reached(total):
    return sum(1 for need, _ in TIERS if total >= need)


def claim(state, total, now):
    """Returns (state, reply, reward). Pays every reached tier not yet claimed by this player."""
    share = _share(state, now)
    if not share['count']:
        raise ValueError('Explore this week’s omen region at least once to share in its reward.')
    tiers = reached(total)
    if tiers <= share['claimed']:
        need = TIERS[share['claimed']][0] if share['claimed'] < len(TIERS) else None
        raise ValueError('Nothing new to claim yet.' + (f' The server is at {total}/{need}.' if need else ' All tiers are claimed this week.'))
    reward = sum(r for _, r in TIERS[share['claimed']:tiers])
    result = deepcopy(state)
    share['claimed'] = tiers
    result['omen_v026'] = share
    reply = f'Heavenly Omen reward: +{reward} Spirit Stones (tier {tiers}/{len(TIERS)}).'
    if tiers == len(TIERS):
        titles = list(result.get('titles_v026') or []) if isinstance(result.get('titles_v026'), list) else []
        if TITLE not in titles:
            result['titles_v026'] = titles + [TITLE]
            reply += f' Title earned: **{TITLE}** (cosmetic).'
    return result, reply, reward


def view(state, guild, total, now):
    omen = current(guild, now)
    share = _share(state, now)
    from living_world_v025 import region_name
    tiers = ' · '.join(f'{"✓" if total >= need else "○"} {need}: +{reward}' for need, reward in TIERS)
    lines = [f'**Heavenly Omen** · {omen["name"]} (this week)',
             omen['text'],
             f'Explore {region_name(omen["region"])} to help. Server: {total} · you: {share["count"]}/{PERSONAL_CAP}.',
             f'Tiers (Spirit Stones): {tiers}. Last tier also gives the cosmetic title {TITLE}.']
    if share['count'] and reached(total) > share['claimed']:
        lines.append('Reward ready: /quest_claim quest:Heavenly Omen.')
    return '\n'.join(lines)
