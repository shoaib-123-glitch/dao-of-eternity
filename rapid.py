"""Server-validated, fast Qi Refining actions; no external imports or database writes."""
from datetime import datetime, timedelta, timezone
from math import ceil

CHOICES = {
    'circulate': ('Circulate Qi', 'Restore one Stability'),
    'study': ('Study the Dao', 'Gain one Dao comprehension'),
    'purify': ('Purify Qi', 'Remove two impurities'),
}

def seconds_for(stage):
    if not 1 <= stage <= 9:
        raise ValueError('Invalid Qi Refining stage')
    return 20 if stage <= 3 else 90 if stage <= 6 else 240

def required_actions(stage):
    return 4 if stage == 9 else 3

def foundation_capacity(stage):
    if not 1 <= stage <= 9:
        raise ValueError('Invalid Foundation stage')
    return 180 + 40 * (stage - 1)


def foundation_cultivate(state, player, choice, now):
    if choice not in CHOICES:
        raise ValueError('Invalid cultivation choice.')
    if state.get('pending') or state.get('trial'):
        raise ValueError('Finish existing meditation or trial first.')
    if player.stability < 40 or player.meridian_integrity < 50:
        raise ValueError('Recover Stability (40+) and Meridians (50+) first.')
    stage = state.get('foundation_stage', 1)
    if not 1 <= stage <= 9:
        raise ValueError('Invalid Foundation stage.')
    if stage == 9 and state.get('foundation_peak'):
        raise ValueError('Foundation peak reached; Golden Core is not implemented yet.')
    capacity = foundation_capacity(stage)
    qi = state.get('foundation_qi', 0)
    if qi >= capacity:
        raise ValueError('Foundation Qi full; use /foundation_advance.')
    progress = state.get('foundation_action', {})
    when = progress.get('next_at')
    if when and now < datetime.fromisoformat(when):
        secs = ceil((datetime.fromisoformat(when) - now).total_seconds())
        raise ValueError(f'Foundation circulation cooling down. Try again in {secs}s.')
    gain = min(capacity - qi, ceil(capacity / 4))
    state['foundation_qi'] = qi + gain
    if choice == 'circulate':
        player.stability = min(100, player.stability + 1)
    elif choice == 'study':
        state['dao_insight'] = min(100, state.get('dao_insight', 0) + 1)
    elif choice == 'purify':
        player.impurity = max(0, player.impurity - 2)
    cooldown = 90 if stage <= 3 else 180 if stage <= 6 else 300
    state['foundation_action'] = {'next_at': (now + timedelta(seconds=cooldown)).isoformat()}
    suffix = ' Use /foundation_advance.' if state['foundation_qi'] == capacity else f' Next action in {cooldown}s.'
    return f'Foundation Stage {stage}: +{gain} Qi ({state["foundation_qi"]}/{capacity}).' + suffix


def apply_action(state, player, choice, now):
    if choice not in CHOICES:
        raise ValueError('Invalid cultivation choice.')
    if player.advanced_to_foundation:
        raise ValueError('Foundation cultivation is not implemented yet; your current realm is preserved.')
    if state.get('pending'):
        raise ValueError('Claim your existing meditation with /claim before quick cultivation.')
    if state.get('trial'):
        raise ValueError('Finish your current trial first.')
    if player.stability < 40 or player.meridian_integrity < 50:
        raise ValueError('Recover Stability (40+) and Meridian Integrity (50+) first.')
    progress = state.get('rapid') or {}
    stage = player.stage
    if progress.get('stage') != stage:
        progress = {'stage': stage, 'actions': 0, 'next_at': None}
    next_at = progress.get('next_at')
    if next_at:
        when = datetime.fromisoformat(next_at)
        if now < when:
            seconds = ceil((when - now).total_seconds())
            raise ValueError(f'Qi circulation is cooling down. Try again in {seconds}s.')
    if player.qi >= player.capacity:
        raise ValueError('Your Qi is full. Use /breakthrough to advance.')
    # 3 actions for stages 1-8, 4 actions to prepare Foundation at stage 9.
    remaining = required_actions(stage) - min(progress['actions'], required_actions(stage) - 1)
    gain = min(player.capacity - player.qi, ceil((player.capacity - player.qi) / remaining))
    player.qi += gain
    progress['actions'] += 1
    if choice == 'circulate':
        player.stability = min(100, player.stability + 1)
    elif choice == 'purify':
        player.impurity = max(0, player.impurity - 2)
    elif choice == 'study':
        state['dao_insight'] = min(100, state.get('dao_insight', 0) + 1)
    progress['next_at'] = (now + timedelta(seconds=seconds_for(stage))).isoformat()
    if player.qi == player.capacity:
        if stage == 9:
            msg = f'+{gain} Qi. Foundation trial ready: 600/600. Use /breakthrough.'
        else:
            # Preserve existing /breakthrough and progress rules; do not auto-level.
            msg = f'+{gain} Qi. Stage {stage} is full! Use /breakthrough to reach Stage {stage+1}.'
    else:
        msg = f'+{gain} Qi from {CHOICES[choice][0]}. {player.qi}/{player.capacity} Qi. Next action after {seconds_for(stage)}s.'
    state['rapid'] = progress
    return msg
