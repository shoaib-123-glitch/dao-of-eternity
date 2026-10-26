"""Bounded combat progression and free camp recovery; no cultivation mutations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

FIRST_CLEAR = {'spirit_wolf': 4, 'stone_guardian': 4, 'thunder_serpent': 4}
ADVICE = {
    'spirit_wolf': 'Beginner trail: learn Strike, Thunder Palm and Second Wind here.',
    'stone_guardian': 'Training challenge: Stage 2+ and a Mistwood Blade recommended.',
    'thunder_serpent': 'Dangerous hunt: Stage 3+, a weapon and protective robes recommended.',
    'thunder_dragon': 'Boss challenge: prepare equipment, companions and guarded heavy turns.',
}
REST_COOLDOWN = timedelta(minutes=2)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def training_bonuses(character):
    """Qi-Refining gains stop at +32 HP/+4 attack; higher realms retain that cap."""
    p = character.get('player', {})
    rank = 9 if p.get('advanced_to_foundation') or character.get('golden_core') else int(p.get('stage', 1))
    levels = min(8, max(0, rank - 1))
    return {'hp': 4 * levels, 'attack': levels // 2}


def journey(battle, now):
    info = deepcopy(battle.get('trail_v024') or {})
    if 'hp' not in info:
        # Legacy completed battles imposed no persistent injury. Do not invent one.
        info.update(hp=battle.get('max_hp', 95), maximum=battle.get('max_hp', 95),
                    last_at=utc(now).isoformat())
    info.setdefault('first_clears', {})
    info.setdefault('wins', 0)
    info.setdefault('defeats', 0)
    return info


def heal_over_time(info, now):
    info = deepcopy(info)
    current = utc(now)
    before = utc(info.get('last_at', current))
    units = max(0, int((current - before).total_seconds()) // 5)
    if units:
        info['hp'] = min(info['maximum'], info['hp'] + units)
        info['last_at'] = (before + timedelta(seconds=units * 5)).isoformat()
    return info


def prepare(battle, previous, now, mode, battle_id):
    if mode not in ('manual', 'auto', 'assisted'):
        raise ValueError('Choose Manual, Auto or Assisted combat.')
    info = heal_over_time(journey(previous, now), now)
    info['maximum'] = battle['max_hp']
    info['hp'] = min(info['hp'], info['maximum'])
    # A first hunt or legacy migration begins rested, including newly earned HP.
    if not previous.get('trail_v024'):
        info['hp'] = battle['max_hp']
    if info['hp'] <= 0:
        raise ValueError('You need battle recovery. Use /recover for free healing, or rest (1 HP per 5 seconds).')
    battle['hp'] = info['hp']
    battle['trail_v024'] = info
    battle['mode'] = mode
    battle['battle_id'] = battle_id
    battle['advice'] = ADVICE[battle['enemy']]
    return battle


def finish(battle, previous, now):
    """Called once on active -> finished. Defeat/retreat award no currency."""
    info = journey(previous, now)
    info.update(hp=battle['hp'], maximum=battle['max_hp'], last_at=utc(now).isoformat())
    extra = 0
    if battle['status'] == 'victory':
        info['wins'] += 1
        if battle['enemy'] in FIRST_CLEAR and not info['first_clears'].get(battle['enemy']):
            extra = FIRST_CLEAR[battle['enemy']]
            info['first_clears'][battle['enemy']] = True
    elif battle['status'] == 'defeat':
        info['defeats'] += 1
    battle['trail_v024'] = info
    return extra


def recover(battle, now):
    if battle.get('status') == 'active':
        raise ValueError('Finish or Retreat from this battle first. Second Wind heals once during combat; /battle_status resumes it.')
    if not battle.get('enemy'):
        raise ValueError('You are rested for your first hunt. Try /hunt enemy:spirit_wolf mode:assisted.')
    info = heal_over_time(journey(battle, now), now)
    current = utc(now)
    if info['hp'] >= info['maximum']:
        raise ValueError('You are fully rested. Use /hunt for your next battle.')
    if info.get('rest_next_at') and current < utc(info['rest_next_at']):
        seconds = max(1, int((utc(info['rest_next_at']) - current).total_seconds()))
        raise ValueError(f'Camp breathing is ready in {seconds}s. Natural recovery continues at 1 HP per 5 seconds.')
    restored = min(info['maximum'] - info['hp'], max(1, info['maximum'] * 60 // 100))
    info['hp'] += restored
    info['rest_next_at'] = (current + REST_COOLDOWN).isoformat()
    battle['trail_v024'] = info
    return f'Camp breathing restores {restored} battle HP: {info["hp"]}/{info["maximum"]}. Free; next breathing in 2 minutes. No marks or loot awarded.'


def camp_summary(battle, now):
    info = heal_over_time(journey(battle, now), now)
    if info['hp']>=info['maximum']:
        readiness='Fully rested for your next hunt.'
    elif info.get('rest_next_at') and utc(now)<utc(info['rest_next_at']):
        seconds=max(1,int((utc(info['rest_next_at'])-utc(now)).total_seconds()+0.999))
        readiness=f'Free /recover in {seconds}s.'
    else:readiness='Free /recover is ready.'
    return (f'**Camp HP now: {info["hp"]}/{info["maximum"]}** · {readiness}\n'
            'Natural healing: 1 HP/5s between fights. /guide → Hunts and equipment.')
