"""Eternal Qi v0.19: efficient precise offline settlement with bounded multiplier windows.

All arithmetic uses integer Qi-second-thousandths, permitting *indefinite* offline
accrual, persistent stage overflow, and offline guild event / elixir coverage.
"""
from datetime import datetime, timezone, timedelta
from game import unpack_player,pack_player
from rapid import foundation_capacity
from heavens import boost_boundaries,active_at,multiplier_milli,world_for_day,active_effects,boost_summary,dt

RATES_PER_HOUR={'qi_refining':40,'foundation':60,'golden':85}
DENOM=3_600_000


def _time(now):
    if now.tzinfo is None: raise ValueError('Expected aware timestamp')
    return now.astimezone(timezone.utc)


def _credit_bank(state):
    p=unpack_player(state['player']);info=state['eternal'];bank=info['bank_qi']
    if bank<=0:return
    if state.get('golden_core'):
        from golden_core_v022 import capacity
        if state.get('golden_peak'):return
        current=state.get('golden_qi',0);cap=capacity(state.get('golden_stage',1))
        moved=min(bank,max(0,cap-current));state['golden_qi']=current+moved
    elif p.advanced_to_foundation:
        if state.get('foundation_peak'):return
        current=state.get('foundation_qi',0);cap=foundation_capacity(state.get('foundation_stage',1))
        moved=min(bank,max(0,cap-current));state['foundation_qi']=current+moved
    else:
        moved=min(bank,max(0,p.capacity-p.qi));p.qi+=moved;state['player']=pack_player(p)
    info['bank_qi']-=moved


def settle(state,now,guild_id=0,root=None,sect_windows=None):
    """Mutate a locked character record; idempotent, safe on repeated reads."""
    now=_time(now)
    info=state.get('eternal')
    if info is None:
        state['eternal']={'last_at':now.isoformat(),'remainder':0,'bank_qi':0,'total_earned':0,'scale_version':2}
        return 0
    if info.get('scale_version')!=2:
        info['remainder']=int(info.get('remainder',0))*1000
        info['scale_version']=2
    prev=dt(info['last_at'])
    if now<=prev:
        _credit_bank(state);return 0
    seconds=int((now-prev).total_seconds())
    if seconds<=0:
        _credit_bank(state);return 0
    end=prev+timedelta(seconds=seconds)
    if end<=prev:end=now
    marks,worlds=boost_boundaries(state,guild_id,prev,end)
    for left,right,level in (sect_windows or []):
        if prev < right and left < end:
            marks=sorted(set(marks)|{max(prev,left),min(end,right)})
    p=unpack_player(state['player'])
    rate=RATES_PER_HOUR['golden' if state.get('golden_core') else 'foundation' if p.advanced_to_foundation else 'qi_refining']
    units=int(info['remainder'])
    for a,b in zip(marks,marks[1:]):
        span=int((b-a).total_seconds())
        if span<=0:continue
        middle=a+(b-a)/2
        world=next((e for e in worlds if e['start']<=middle<e['end']),None)
        personal=active_at(state.get('personal_boons',[]),middle)
        elixir=active_at(state.get('elixir_queue',[]),middle)
        dual=active_at(state.get('dual_boons',[]),middle)
        factor=multiplier_milli(world,personal,elixir,root,state.get('cultivation_path'),state.get('secondary_root'),dual)
        active_hall=max((lv for left,right,lv in (sect_windows or []) if left<=middle<right),default=0)
        factor=min(6000,factor*(1000+50*active_hall)//1000)
        units+=span*rate*factor
    gained,info['remainder']=divmod(units,DENOM)
    info['last_at']=end.isoformat()
    info['bank_qi']+=gained
    info['total_earned']+=gained
    _credit_bank(state)
    # Drop ended buffs ONLY after interval settlement to preserve offline rewards.
    state['personal_boons']=[x for x in state.get('personal_boons',[]) if dt(x['end_at'])>end]
    state['elixir_queue']=[x for x in state.get('elixir_queue',[]) if dt(x['end_at'])>end]
    state['dual_boons']=[x for x in state.get('dual_boons',[]) if dt(x['end_at'])>end]
    return gained


def refill_after_advance(state):
    if state.get('eternal'):_credit_bank(state)


def rate_now(state,now,guild_id=0,root=None):
    p=unpack_player(state['player'])
    base=RATES_PER_HOUR['golden' if state.get('golden_core') else 'foundation' if p.advanced_to_foundation else 'qi_refining']
    world,pers,elix=active_effects(state,guild_id,root,_time(now))
    dual=active_at(state.get('dual_boons',[]),_time(now))
    factor=multiplier_milli(world,pers,elix,root,state.get('cultivation_path'),state.get('secondary_root'),dual)
    factor=min(6000,factor*(1000+50*int(state.get('sect_qi_level',0)))//1000)
    return base, factor, base*factor/1000


def status(state,now=None,guild_id=0,root=None):
    now=_time(now or datetime.now(timezone.utc))
    info=state.get('eternal',{});p=unpack_player(state['player'])
    if state.get('golden_core'):
        from golden_core_v022 import capacity
        current=state.get('golden_qi',0);cap=capacity(state.get('golden_stage',1))
    else:
        current=state.get('foundation_qi',0) if p.advanced_to_foundation else p.qi
        cap=foundation_capacity(state.get('foundation_stage',1)) if p.advanced_to_foundation else p.capacity
    base,factor,rate=rate_now(state,now,guild_id,root)
    remaining=max(0,cap-current-info.get('bank_qi',0))
    eta='Ready to advance' if current>=cap else f'~{remaining/max(rate,0.001):.1f} hours at current rate (boosts may expire)'
    return (f'Eternal Cultivation: ACTIVE (never expires)\n'
            f'Passive rate: {rate:g} Qi/hour (base {base}, {factor/1000:g}x) | Stage Qi: {current}/{cap}\n'
            f'Banked overflow: {info.get("bank_qi",0)} Qi | Total earned passively: {info.get("total_earned",0)} Qi\n'
            f'Next breakthrough: {eta}\n'
            'Qi beyond a full stage waits in the bank. Breakthroughs remain manual.')
