"""Additive Golden Core progression; never rewrites the historic Qi Refining/Foundation flags.

Golden Core exists as independent fields in the character JSON. All transitions are pure
and committed atomically through Repository.execute, preserving other player records.
"""
from game import unpack_player

PHASES = (
    ('Temper the Dantian', 'foundation'),
    ('Confront the Heart Demon', 'clarity'),
    ('Condense the Core', 'harmony'),
)
CHOICES = {'foundation': 'Stabilize and purify', 'clarity': 'Study the Dao',
           'harmony': 'Align spirit and meridians', 'force': 'Force raw Qi inward'}

def capacity(stage):
    if not isinstance(stage,int) or not 1 <= stage <= 9:
        raise ValueError('Golden Core stage must be between 1 and 9.')
    return 400 + 90 * (stage - 1) + 15 * (stage - 1) ** 2

def realm(state):
    return f'Golden Core Stage {state.get("golden_stage",1)}' if state.get('golden_core') else None

def start(state):
    p=unpack_player(state['player'])
    if state.get('golden_core'):raise ValueError('You have already formed a Golden Core.')
    if not p.advanced_to_foundation or not state.get('foundation_peak'):
        raise ValueError('Reach Foundation Establishment Stage 9 and complete its cultivation first.')
    if state.get('golden_trial'):raise ValueError('Golden Core ritual already active. Use /golden_choice.')
    if p.stability < 60 or p.meridian_integrity < 70 or p.foundation_quality < 50 or state.get('dao_comprehension',0)<50:
        raise ValueError('Prepare Stability 60, Meridian Integrity 70, Foundation Quality 50 and Dao Comprehension 50.')
    state['golden_trial']={'phase':0,'resonance':0,'choices':[]}
    return 'Golden Core ritual begins: Phase 1/3 — Temper the Dantian. Select /golden_choice with foundation, clarity, harmony, or force.'

def choose(state,choice):
    t=state.get('golden_trial')
    if not t:raise ValueError('No ritual active. Begin with /golden_core_trial.')
    if choice not in CHOICES:raise ValueError('Unknown Golden Core ritual decision.')
    phase=t['phase']
    if phase>=3:raise ValueError('The ritual already concluded.')
    preferred=PHASES[phase][1]
    t['resonance']+=3 if choice==preferred else (1 if choice in ('foundation','clarity','harmony') else -2)
    t['choices'].append(choice)
    t['phase']+=1
    if t['phase']<3:
        return f'{PHASES[phase][0]}: {CHOICES[choice]}. Phase {t["phase"]+1}/3 — {PHASES[t["phase"]][0]}. Choose again with /golden_choice.'
    p=unpack_player(state['player'])
    score=t['resonance']+int(p.foundation_quality)//10+int(p.stability)//10+int(p.meridian_integrity)//20+int(state.get('dao_comprehension',50))//15
    # Careful choices guarantee success for a prepared character; risky forcing may fail.
    state['golden_trial']=None
    if score < 20:
        return f'Golden Core condensation dispersed (resonance {score}/20). Foundation remains at peak; stabilize and retry. No realm or equipment was lost.'
    quality='Radiant' if score>=30 else 'Refined' if score>=25 else 'Stable'
    state.update(golden_core=True,golden_stage=1,golden_qi=0,golden_peak=False,golden_quality=quality)
    from eternal import refill_after_advance
    refill_after_advance(state)
    return f'Golden Core formed! {quality} Core · Stage 1/9. Your stored Qi has been transferred toward the new stage. All equipment, bonds and sect memberships are preserved.'

def advance(state):
    if not state.get('golden_core'):raise ValueError('Complete your Golden Core ritual first.')
    stage=int(state.get('golden_stage',1))
    if state.get('golden_peak'):raise ValueError('Golden Core Stage 9 is at its peak. Await the next realm.')
    cap=capacity(stage)
    if int(state.get('golden_qi',0))<cap:
        raise ValueError(f'Need {cap} Golden Core Qi to advance; currently {state.get("golden_qi",0)}.')
    p=unpack_player(state['player'])
    if p.stability < 45 or p.meridian_integrity < 60:
        raise ValueError('Stability 45 and Meridian Integrity 60 required.')
    state['golden_qi']=0
    if stage==9:state['golden_peak']=True;reply='Golden Core Stage 9 perfected! Nascent Soul awaits a later expansion.'
    else:state['golden_stage']=stage+1;reply=f'Golden Core advancement successful! Stage {stage+1}/9.'
    from eternal import refill_after_advance
    refill_after_advance(state)
    return reply
