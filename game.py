"""Pure rules facade around the unchanged v0.4/v0.5 engines.
All times are injected UTC-aware server timestamps. No Discord or database imports.
"""
from __future__ import annotations
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
from typing import Any
import secrets
from engine import Cultivator, DAILY_QI_LIMITS
from tribulation import TrialState, CHOICES, start_trial, choose_phase, finish_trial
from rapid import apply_action, foundation_cultivate, foundation_capacity
from encounters import start as start_encounter, resolve as resolve_encounter

UTC = timezone.utc
TRIAL_TIMEOUT = timedelta(minutes=30)
MEDITATION_DURATION = timedelta(minutes=15)

def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None

def utc_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s).astimezone(UTC) if s else None

def pack_player(p: Cultivator) -> dict:
    d = asdict(p)
    for k in ('last_active', 'last_training', 'offline_cursor'):
        d[k] = iso(d[k])
    return d

def unpack_player(d: dict) -> Cultivator:
    d = dict(d)
    for k in ('last_active', 'last_training', 'offline_cursor'):
        d[k] = utc_dt(d.get(k))
    allowed = {f.name for f in fields(Cultivator)}
    return Cultivator(**{k: v for k, v in d.items() if k in allowed})

def default_state(now: datetime) -> dict:
    p = Cultivator()
    p._rollover(now)
    return {'player': pack_player(p), 'pending': None, 'trial': None,
            'trial_started': None, 'body_resilience': 65, 'dao_comprehension': 65}

def prevent_trial(state: dict):
    if state['trial'] is not None:
        raise ValueError('A breakthrough trial is active; finish or abandon it first.')

def apply(state: dict, op: str, now: datetime, **kwargs: Any) -> tuple[dict, str]:
    """Returns a changed copy and result message; caller must commit atomically.
    For duplicate event ids the repository returns its original saved response.
    """
    if now.tzinfo is None:
        raise ValueError('Server time must be timezone aware')
    import copy
    state = copy.deepcopy(state)
    p = unpack_player(state['player'])
    if op not in ('profile', 'trial_choice', 'trial_abandon'):
        prevent_trial(state)
    if op == 'profile':
        return state, format_profile(p, state, now)
    if op == 'cultivation_status':
        from eternal import status
        return state, status(state, now, kwargs.get('guild_id',0), kwargs.get('root'))
    if op == 'omen_seek':
        from heavens import seek_omen
        reply=seek_omen(state,now,kwargs.get('root'),kwargs.get('approach','safe'),kwargs.get('roll'))
        if state.pop('omen_bold_failed',False):
            p.impurity=min(100,p.impurity+1)
            state['player']=pack_player(p)
        # The consolation is banked and applied instantly to available stage space.
        from eternal import refill_after_advance
        refill_after_advance(state)
        return state,reply
    if op == 'path_select':
        from heavens import PATHS
        path=kwargs.get('path')
        if path not in PATHS: raise ValueError('Unknown cultivation path.')
        if state.get('cultivation_path'):
            raise ValueError(f'Cultivation path already set: {state["cultivation_path"]}.')
        state['cultivation_path']=path
        return state,f'Cultivation Path: {path}. {PATHS[path]} Your elemental root remains unchanged.'
    if op == 'encounter_start':
        reply = start_encounter(state, now)
        return state, reply
    if op == 'encounter_choice':
        reply = resolve_encounter(state, p, kwargs.get('choice'), now)
        state['player'] = pack_player(p)
        # Original /encounter choices can very rarely discover an additional personal boost.
        if kwargs.get('omen_roll',1000)<110:
            from heavens import add_boon
            grade='legendary' if kwargs['omen_roll']<5 else ('epic' if kwargs['omen_roll']<30 else 'rare')
            boon=add_boon(state,now,grade)
            if boon: reply+=f' Rare cultivation resonance! {boon["mult"]/1000:g}x Qi for a limited time.'
        return state, reply
    if op == 'foundation_cultivate':
        if not p.advanced_to_foundation:
            raise ValueError('Foundation cultivation requires Foundation Establishment.')
        reply = foundation_cultivate(state, p, kwargs.get('choice'), now)
        state['player'] = pack_player(p)
        return state, reply
    if op in ('golden_core_trial', 'golden_choice', 'golden_advance'):
        from golden_core_v022 import start,choose,advance
        if op == 'golden_core_trial': result=start(state)
        elif op == 'golden_choice': result=choose(state,kwargs.get('choice'))
        else: result=advance(state)
        return state,result
    if op == 'foundation_advance':
        if not p.advanced_to_foundation:
            raise ValueError('Foundation Establishment required.')
        st = state.get('foundation_stage', 1)
        if state.get('foundation_peak') or st > 9:
            raise ValueError('Foundation peak reached.')
        cap = foundation_capacity(st)
        if state.get('foundation_qi', 0) != cap:
            raise ValueError(f'Need {cap} Foundation Qi to advance; current {state.get("foundation_qi", 0)}.')
        if p.stability < 50 or p.meridian_integrity < 60:
            raise ValueError('Stability 50 and Meridian Integrity 60 required.')
        state['foundation_qi'] = 0
        state['foundation_action'] = {}
        if st == 9:
            state['foundation_peak'] = True
            reply = 'Foundation Stage 9 fully cultivated! Await Golden Core expansion.'
        else:
            state['foundation_stage'] = st + 1
            reply = f'Foundation advancement successful! Stage {st+1}/9.'
        state['player'] = pack_player(p)
        from eternal import refill_after_advance
        refill_after_advance(state)
        return state, reply
    if op == 'rapid_cultivate':
        reply = apply_action(state, p, kwargs.get('choice'), now)
        state['player'] = pack_player(p)
        return state, reply
    if op == 'dev_addqi':
        amount = kwargs.get('amount')
        if type(amount) is not int or not 1 <= amount <= 600:
            raise ValueError('Developer Qi amount must be between 1 and 600.')
        if p.advanced_to_foundation:
            raise ValueError('Foundation realm advancement is not implemented yet.')
        gain = min(amount, max(0, p.capacity - p.qi))
        p.qi += gain  # Test grants do not consume daily allowance.
        msg = f'[DEV] Added {gain} Qi. Current: {p.qi}/{p.capacity}.'
    elif op == 'dev_setstage':
        stage = kwargs.get('stage')
        if type(stage) is not int or not 1 <= stage <= 9:
            raise ValueError('Developer stage must be 1–9.')
        if p.advanced_to_foundation:
            raise ValueError('Cannot reset a Foundation character using this command.')
        p.stage = stage
        p.qi = 0  # Reset stored Qi so it never exceeds the new stage cap.
        p.failure_count = 0
        state['pending'] = None
        msg = f'[DEV] Set Qi Refining Stage {stage}; Qi reset to 0.'
    elif op == 'start_meditation':
        if state['pending']:
            raise ValueError('Meditation already running. Use /claim.')
        # Uses original daily slot/cooldown validations, but Qi is credited only on claim.
        p._rollover(now)
        if p.qi >= p.capacity:
            raise ValueError('Qi is full. Use /breakthrough first.')
        p._slot('active', now)
        state['pending'] = iso(now + MEDITATION_DURATION)
        msg = 'Meditation started. Claim after 15 minutes using /claim.'
    elif op == 'claim_meditation':
        ready = utc_dt(state['pending'])
        if not ready:
            raise ValueError('No active meditation. Use /cultivate first.')
        if now < ready:
            mins = int((ready - now).total_seconds() // 60) + 1
            raise ValueError(f'Meditation still running ({mins} minute(s) remaining).')
        p._rollover(now)
        gain = p._gain('active', 20)
        state['pending'] = None
        msg = f'Meditation complete: +{gain} Qi. Current Qi: {p.qi}/{p.capacity}.'
    elif op == 'training':
        gain = p.training(now)
        msg = f'Training complete: +{gain} Qi, +2 Seals, +1 Essence.'
    elif op == 'offline':
        # Last 24h only; settle each whole hour into its actual UTC day.
        p._rollover(now)
        cursor = p.offline_cursor or now
        if cursor > now:
            raise ValueError('Invalid offline clock')
        cursor = max(cursor, now - timedelta(hours=24))
        by_day: dict[str, int] = {}
        while cursor + timedelta(hours=1) <= now:
            day = cursor.date().isoformat()
            by_day[day] = by_day.get(day, 0) + 1
            cursor += timedelta(hours=1)
        # Never pay previous-day quota as today's, and do not replay old days.
        today = now.date().isoformat()
        eligible_hours = by_day.get(today, 0)
        gain = p._gain('offline', eligible_hours * 10)
        p.offline_cursor = now  # discard old / fractional intervals: intentional conservative cap
        msg = f'Offline cultivation: +{gain} Qi (today only, maximum 80 Qi/day).'
    elif op == 'stabilize':
        changed = p.stabilize(now)
        msg = f'Stability restored by {changed} to {p.stability}.'
    elif op == 'heal':
        changed = p.recover_meridians(now)
        msg = f'Meridian Integrity restored by {changed} to {p.meridian_integrity}.'
    elif op == 'purify':
        changed = p.purify(now)
        msg = f'Impurity reduced by {changed} to {p.impurity}.'
    elif op == 'minor':
        if not p.minor_breakthrough():
            raise ValueError('Not eligible: check Qi, stability, meridians or stage.')
        msg = f'Minor breakthrough! Qi Refining Stage {p.stage}. Remaining Qi: {p.qi}.'
        state['player'] = pack_player(p)
        from eternal import refill_after_advance
        refill_after_advance(state)
        p = unpack_player(state['player'])
    elif op == 'meridian':
        if not p.open_meridian():
            raise ValueError('Need enough Seals, stability and meridian integrity.')
        msg = f'Meridian opened! {p.open_meridians}/6.'
    elif op == 'refine':
        if not p.refine_foundation(now):
            raise ValueError('Foundation refinement prerequisites or weekly limit not met.')
        msg = f'Foundation Quality improved to {p.foundation_quality}.'
    elif op == 'trial_start':
        if state['pending']:
            raise ValueError('Claim your pending meditation before entering a trial.')
        prepared = bool(kwargs.get('prepared', True))
        t = start_trial(p, prepared=prepared, protective_aid=False)
        state['trial'] = asdict(t)
        state['trial_started'] = iso(now)
        msg = 'Foundation trial begins. Phase 1: Physical Tribulation. Choose a button.'
    elif op == 'trial_abandon':
        if not state['trial']:
            raise ValueError('No active trial.')
        # No resources charged until resolution; timeout and abandon have no penalty.
        state['trial'] = None
        state['trial_started'] = None
        msg = 'Trial abandoned. No resources spent.'
    elif op == 'trial_choice':
        raw = state['trial']
        if not raw:
            raise ValueError('No active trial. Use /breakthrough.')
        if now - utc_dt(state['trial_started']) > TRIAL_TIMEOUT:
            state['trial'] = None
            state['trial_started'] = None
            msg = 'Trial expired after 30 minutes. No resources spent.'
        else:
            t = TrialState(**raw)
            expected = kwargs.get('phase_index')
            if expected != t.phase_index:
                raise ValueError('This button is outdated. Use the current trial message.')
            choice = kwargs['choice']
            if choice not in CHOICES[t.phase]:
                raise ValueError('Invalid choice for this phase.')
            outcome = choose_phase(t, choice, body_resilience=state['body_resilience'],
                                   dao_comprehension=state['dao_comprehension'])
            if t.phase is None:
                roll = secrets.randbelow(1_000_000) / 10_000
                result = finish_trial(p, t, roll=roll)
                msg = ('BREAKTHROUGH SUCCESS! Foundation Establishment reached.'
                       if result['success'] else 'Breakthrough failed. Recover and prepare again.')
                msg += f" Endurance: {result['endurance']}. Insight: {result['insight']}."
                state['trial'] = None
                state['trial_started'] = None
            else:
                state['trial'] = asdict(t)
                msg = (f"{outcome['phase']}: {choice}. -{outcome['damage']} Endurance; "
                       f"{outcome['endurance']} remaining. Next: {t.phase}.")
    else:
        raise ValueError('Unknown game action')
    state['player'] = pack_player(p)
    return state, msg

def format_profile(p: Cultivator, state: dict, now: datetime) -> str:
    rank = (f'Golden Core Stage {state.get("golden_stage", 1)}' if state.get('golden_core') else f'Foundation Establishment Stage {state.get("foundation_stage", 1)}' if p.advanced_to_foundation else f'Qi Refining Stage {p.stage}')
    stored = state.get('golden_qi',0) if state.get('golden_core') else state.get('foundation_qi', 0) if p.advanced_to_foundation else p.qi
    if state.get('golden_core'):
        from golden_core_v022 import capacity as golden_capacity
        capacity=golden_capacity(state.get('golden_stage',1))
    else:
        capacity = foundation_capacity(state.get('foundation_stage', 1)) if p.advanced_to_foundation else p.capacity
    return (f'**{rank}**\nQi: {stored}/{capacity} | Stability: {p.stability} | '
            f'Foundation: {p.foundation_quality}\nMeridian Integrity: {p.meridian_integrity} | '
            f'Open Meridians: {p.open_meridians}/6 | Impurity: {p.impurity}\n'
            f'Seals: {p.training_seals} | Essences: {p.foundation_essences} | '
            f'Failed major attempts: {p.failure_count}\n'
            f'Meditation pending: {"yes" if state["pending"] else "no"} | '
            f'Trial active: {"yes" if state["trial"] else "no"}')
