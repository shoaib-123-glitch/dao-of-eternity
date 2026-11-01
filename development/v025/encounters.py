"""Deterministic, bounded cultivation encounters with explicit server-side choices."""
from datetime import timedelta, datetime
from math import ceil

ENCOUNTERS = {
    'vortex': ('Unstable Spiritual Vortex', 'A turbulent Qi vortex opens beside your cultivation mat.', {
        'stabilize': ('Stabilize the Vortex', 'stability'),
        'absorb': ('Absorb the Wild Qi', 'impurity'),
        'study': ('Observe the Energy Flow', 'insight'),
    }),
    'tablet': ('Ancient Dao Tablet', 'A worn tablet contains a half-erased cultivation principle.', {
        'study': ('Decipher the Inscription', 'insight'),
        'cleanse': ('Cleanse Its Spirit Dust', 'purity'),
        'reinforce': ('Reinforce Your Meridians', 'meridian'),
    }),
    'herb': ('Spirit Herb Garden', 'You discover herbs growing around a forgotten spring.', {
        'harvest': ('Harvest Carefully', 'essence'),
        'rest': ('Rest by the Spring', 'stability'),
        'purify': ('Purify at the Spring', 'purity'),
    }),
}
ORDER = tuple(ENCOUNTERS)
COOLDOWN = timedelta(minutes=10)
EXPIRE = timedelta(minutes=15)

def start(state, now):
    if state.get('trial') or state.get('pending'):
        raise ValueError('Finish the active breakthrough or meditation first.')
    if state.get('encounter'):
        old = state['encounter']
        if now < datetime.fromisoformat(old['expires_at']):
            raise ValueError('An encounter is already open. Use /encounter_resume.')
        state['encounter'] = None
    next_at=state.get('encounter_next_at')
    if next_at and now < datetime.fromisoformat(next_at):
        secs=ceil((datetime.fromisoformat(next_at)-now).total_seconds())
        raise ValueError(f'Your spiritual senses need rest. Try in {secs}s.')
    index=state.get('encounter_counter',0)
    key=ORDER[index%len(ORDER)]
    state['encounter_counter']=index+1
    state['encounter']={'key':key, 'expires_at':(now+EXPIRE).isoformat()}
    return f'{ENCOUNTERS[key][0]}: {ENCOUNTERS[key][1]} Choose your response.'

def resolve(state, player, choice, now):
    pending=state.get('encounter')
    if not pending:
        raise ValueError('No active encounter. Use /encounter first.')
    if now >= datetime.fromisoformat(pending['expires_at']):
        state['encounter']=None
        raise ValueError('The encounter expired. Use /encounter after the cooldown.')
    key=pending['key']
    options=ENCOUNTERS[key][2]
    if choice not in options:
        raise ValueError('This option is not available for the current encounter.')
    label, reward=options[choice]
    if reward=='stability':
        before=player.stability; player.stability=min(100,before+3); result=f'Stability +{player.stability-before}'
    elif reward=='impurity':
        before=player.impurity; player.impurity=min(100,before+4)
        state['dao_insight']=min(100,state.get('dao_insight',0)+2)
        result=f'Wild Qi insight +2 (capped); Impurity +{player.impurity-before}'
    elif reward=='insight':
        before=state.get('dao_insight',0); state['dao_insight']=min(100,before+2); result=f'Dao Insight +{state["dao_insight"]-before}'
    elif reward=='purity':
        before=player.impurity; player.impurity=max(0,before-4); result=f'Impurity -{before-player.impurity}'
    elif reward=='meridian':
        before=player.meridian_integrity; player.meridian_integrity=min(100,before+3); result=f'Meridian Integrity +{player.meridian_integrity-before}'
    elif reward=='essence':
        # Small, bounded event prize; full economy balancing remains future work.
        before=player.foundation_essences; player.foundation_essences=min(30,before+1)
        result=f'Foundation Essence +{player.foundation_essences-before}'
    state['encounter']=None
    state['encounter_next_at']=(now+COOLDOWN).isoformat()
    return f'{ENCOUNTERS[key][0]} — {label}. {result}. Next encounter in 10 minutes.'
