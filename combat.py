"""v0.15 shared deterministic hybrid-combat engine.

Mastery belongs exclusively to the combat session, NEVER to the cultivation record.
"""
from copy import deepcopy

ENEMIES = {
 'spirit_wolf': {'name':'Spirit Wolf','hp':58,'attack':11,'defense':3,'reward':8},
 'stone_guardian': {'name':'Stone Guardian','hp':83,'attack':13,'defense':6,'reward':12},
 'thunder_serpent': {'name':'Thunder Serpent','hp':105,'attack':16,'defense':4,'reward':16},
 'thunder_dragon': {'name':'Heavenly Thunder Dragon','hp':120,'attack':8,'defense':6,'reward':32,'boss':True},
}
ACTIONS = {'strike':'Basic Strike','thunder_palm':'Thunder Palm','lightning_guard':'Lightning Guard',
           'focus_qi':'Focus Qi','thunder_judgment':'Thunder Judgment','elemental_art':'Elemental Art','secondary_art':'Secondary Art','beast_skill':'Spirit Beast Art'}
MAX_TURNS=14
MASTERY_THRESHOLDS=(3,8,20)


def mastery_tier(uses):
    return sum(uses >= t for t in MASTERY_THRESHOLDS)


def unlocked(b, action):
    return action!='thunder_judgment' or b.get('mastery',{}).get('thunder_palm',0)>=6


def begin(enemy='spirit_wolf', root='Unawakened', mastery=None, gear=None, path=None, secondary_root=None):
    if enemy not in ENEMIES: raise ValueError('Unknown spirit beast.')
    beast=ENEMIES[enemy]
    known={k:max(0,int((mastery or {}).get(k,0))) for k in ACTIONS}
    gear = gear or {}
    max_hp=95+max(0,int(gear.get('hp',0)))+(10 if path=='Body' else 0)
    return {'enemy':enemy,'root':root,'hp':max_hp,'max_hp':max_hp,'qi':24,'max_qi':30,
            'gear_attack':max(0,int(gear.get('attack',0)))+(2 if path=='Sword' else 0), 'gear_ward':max(0,int(gear.get('ward',0)))+(1 if path=='Formation' else 0),
            'enemy_hp':beast['hp'],'turn':0,'guard':False,'shock':0,'charge':0,'status':'active',
            'beast':gear.get('beast'), 'beast_bond':gear.get('beast_bond',0), 'beast_evolutions':gear.get('beast_evolutions',0), 'last_beast_art':-99,
            'mastery':known,'log':[f'A {beast["name"]} approaches. Choose your technique.'],
            'last_reward':0,'loot':[],'phase':1,'ward':0,'enraged':False, 'path':path,'secondary_root':secondary_root,'burn':0,'poison':0,'slow':0,'evasion':0}


def enemy_intent(b):
    """Telegraph heavy attacks so players can defend strategically."""
    if b['enemy']=='thunder_dragon':
        if (b['turn']+1)%3==0: return 'TELEGRAPH: Heavenfall Lightning (guard or break the ward)'
        if b.get('ward',0): return 'Storm Ward active: damage reduced until Basic Strike shatters it'
        return 'Dragon Claw next turn'
    return 'HEAVY STRIKE NEXT TURN' if (b['turn']+1)%3==0 else 'Normal attack next turn'


def display(b):
    beast=ENEMIES[b['enemy']]
    headline=f'{beast["name"]} | {b["status"].upper()} | Turn {b["turn"]}/{MAX_TURNS}'
    stats=f'You: {b["hp"]}/{b["max_hp"]} HP, {b["qi"]}/{b["max_qi"]} Combat Qi | Beast: {b["enemy_hp"]}/{beast["hp"]} HP'
    ending='\n'.join(b['log'][-3:])
    intent=f'\nEnemy intent: {enemy_intent(b)}' if b['status']=='active' else ''
    charge=f'\nStorm Charge: {b.get("charge",0)}' if b.get('charge') else ''
    phase=f'\nBoss Phase: {b.get("phase",1)}/3 | Storm Ward: {b.get("ward",0)}' if beast.get('boss') else ''
    loot=f'\nLoot: {", ".join(b.get("loot",[]))} (saved to inventory on victory)' if b.get('loot') else ''
    return f'{headline}\n{stats}{phase}{intent}{charge}\n{ending}{loot}'


def turn(b, action):
    if action not in ACTIONS: raise ValueError('Unknown combat technique.')
    if b['status']!='active': raise ValueError('This battle is finished. Start another with /hunt.')
    if action=='beast_skill' and not b.get('beast'):raise ValueError('Adopt a spirit beast to use this art.')
    if action=='beast_skill' and b['turn']-b.get('last_beast_art',-99)<3:raise ValueError('Spirit Beast Art recovers after three combat turns.')
    if action=='secondary_art' and not b.get('secondary_root'): raise ValueError('Unlock a secondary root before using this art.')
    if not unlocked(b,action): raise ValueError('Thunder Judgment unlocks after 6 uses of Thunder Palm. See /techniques.')
    b=deepcopy(b)
    b.setdefault('mastery', {k:0 for k in ACTIONS})
    b.setdefault('charge',0)
    b.setdefault('phase',1)
    b.setdefault('ward',0)
    b.setdefault('loot',[])
    beast=ENEMIES[b['enemy']]
    root_bonus=4 if b['root']=='Lightning' else 0
    tier=mastery_tier(b['mastery'].get(action,0))
    guard=False
    if action=='beast_skill':
        if b['qi']<6:raise ValueError('Spirit Beast Art requires 6 combat Qi.')
        b['qi']-=6;dmg=8+b.get('beast_bond',1)*3+b.get('beast_evolutions',0)*5
        b['last_beast_art']=b['turn']
        beast_key=b['beast']
        if beast_key=='storm_hawk':b['shock']=1;msg=f'Storm Hawk unleashes Lightning Talon for {dmg} damage and Shock!'
        elif beast_key=='jade_fox':
            heal=min(10,b['max_hp']-b['hp']);b['hp']+=heal;msg=f'Jade Fox heals {heal} HP and attacks for {dmg}!'
        else:guard=True;msg=f'Stone Tortoise shields you and strikes for {dmg}!'
    elif action=='strike':
        dmg=max(1,13+tier-beast['defense']); b['qi']=min(b['max_qi'],b['qi']+4)
        msg=f'Basic Strike deals {dmg} damage; +4 combat Qi.'
        if b['charge']:
            combo=7+2*b['charge']; dmg+=combo; b['charge']=0
            msg+=f' Storm Break combination detonates stored lightning for +{combo} damage!'
    elif action=='thunder_palm':
        if b['qi']<8: raise ValueError('Thunder Palm needs 8 combat Qi. Use Basic Strike or Focus Qi.')
        b['qi']-=8; dmg=max(1,22+root_bonus+tier-beast['defense']); b['shock']=1
        b['charge']=min(2,b['charge']+1)
        msg=f'Thunder Palm deals {dmg} damage and applies Shock! Storm Charge +1.'
    elif action=='lightning_guard':
        if b['qi']<5: raise ValueError('Lightning Guard needs 5 combat Qi.')
        b['qi']-=5; dmg=0; guard=True; msg='Lightning Guard shields you for this enemy attack.'
    elif action=='thunder_judgment':
        if b['qi']<12: raise ValueError('Thunder Judgment needs 12 combat Qi.')
        b['qi']-=12; dmg=max(1,31+root_bonus+tier-beast['defense']); b['shock']=1
        if b['charge']:
            bonus=5*b['charge']; dmg+=bonus; b['charge']=0
            msg=f'Thunder Judgment unleashes {dmg} damage, including {bonus} Storm Charge bonus!'
        else:
            msg=f'Thunder Judgment unleashes {dmg} damage!'
    elif action in ('elemental_art','secondary_art'):
        if b['qi']<6:raise ValueError('Elemental Art needs 6 combat Qi.')
        b['qi']-=6
        element=b.get('secondary_root') if action=='secondary_art' else b['root'];dmg=max(1,18+tier-beast['defense'])
        if element=='Fire':b['burn']=2;msg='Flame Lotus ignites the beast (2 burn ticks)!'
        elif element in ('Water','Light'):
            heal=min(b['max_hp']-b['hp'],8);b['hp']+=heal;dmg=max(1,dmg-5)
            msg=f'Flowing Water restores {heal} HP and cleanses the Qi flow!'
        elif element in ('Earth','Time'):
            guard=True;dmg=max(1,dmg-7);msg='Stone Mantle forms a protective shield!'
        elif element in ('Wind','Shadow','Void'):
            b['evasion']=1;dmg+=2;msg='Gale Steps evade part of the next incoming blow!'
        elif element in ('Wood','Poison'):
            heal=min(b['max_hp']-b['hp'],5);b['hp']+=heal;b['poison']=2;dmg=max(1,dmg-4)
            msg=f'Verdant Bloom restores {heal} HP; vines apply 2 poison ticks!'
        elif element=='Metal':
            dmg+=beast['defense']+2;msg='Sword Qi pierces the beast’s armor!'
        elif element in ('Lightning','Chaos'):
            b['shock']=1;b['charge']=min(2,b['charge']+1)
            msg='Thunder Resonance shocks the beast and gains Storm Charge!'
        elif element in ('Ice','Soul'):
            b['slow']=2;msg='Frost Seal slows the beast for 2 attacks!'
        else:msg='Neutral Elemental Art flows through your meridians.'
        if b['enemy']=='thunder_dragon' and element=='Earth':dmg+=4;msg+=' Grounding counter: +4 damage!'
    else:
        dmg=0; b['qi']=min(b['max_qi'],b['qi']+12); msg='Focus Qi restores up to 12 combat Qi.'
    if beast.get('boss') and b['ward'] and dmg:
        if action=='strike':
            b['ward']=0
            msg+=' Basic Strike shatters the Storm Ward!'
        else:
            blocked=min(dmg,7)
            dmg-=blocked
            msg+=f' Storm Ward absorbs {blocked} damage.'
    if dmg and b.get('gear_attack',0):
        dmg+=b['gear_attack']
        sources=b.get('bonus_sources',{})
        if sources:
            parts=[f'{name} +{amount}' for name,amount in sources.items() if amount]
            remaining=b['gear_attack']-sum(sources.values())
            if remaining:parts.append(f'path +{remaining}')
            msg+=' Combat bonuses: '+', '.join(parts)+'.'
        else:
            msg+=f' Equipment grants +{b["gear_attack"]} damage.'
    b['turn']+=1
    b['mastery'][action]=b['mastery'].get(action,0)+1
    if action=='thunder_palm' and b['mastery'][action]==6:
        msg+=' Thunder Judgment unlocked!'
    b['enemy_hp']=max(0,b['enemy_hp']-dmg)
    if b.get('burn',0) and b['enemy_hp']>0:
        b['enemy_hp']=max(0,b['enemy_hp']-4);b['burn']-=1;msg+=' Burn deals 4 damage.'
    if b.get('poison',0) and b['enemy_hp']>0:
        b['enemy_hp']=max(0,b['enemy_hp']-3);b['poison']-=1;msg+=' Poison deals 3 damage.'
    if b['enemy_hp']==0:
        b['status']='victory'; b['last_reward']=beast['reward']
        if beast.get('boss'):
            b['loot']=['Thunder Dragon Scale','Heavenly Spark']
        b['log'].append(msg+f' Victory! Earned {b["last_reward"]} combat marks (saved to inventory).')
        return b
    if beast.get('boss'):
        new_phase=3 if b['enemy_hp']<=beast['hp']//3 else (2 if b['enemy_hp']<=2*beast['hp']//3 else 1)
        if new_phase>b['phase']:
            b['phase']=new_phase
            b['ward']=1
            msg+=f' Boss enters Phase {new_phase}! Storm Ward rises.'
    attack=beast['attack']+(8 if b['turn']%3==0 else 0)
    if beast.get('boss'):
        if b['phase']==3: attack+=3
        if b['turn']%3==0: attack+=9
    if b.get('slow',0):
        attack=max(1,attack-4);b['slow']-=1
        msg+=' Slow reduces incoming damage.'
    if b.get('evasion',0):
        attack=max(1,attack//2);b['evasion']=0
        msg+=' Gale Steps avoids part of the blow.'
    if b['shock']:
        attack=max(1,attack-5); b['shock']=0
        msg+=' Shock weakens the beast.'
    if guard: attack=max(1,attack-(23 if beast.get('boss') and b['turn']%3==0 else 9)-2*tier)
    attack=max(1,attack-b.get('gear_ward',0))
    b['hp']=max(0,b['hp']-attack)
    msg+=f' The beast deals {attack} damage.'
    if b['turn']%3==0: msg+=(' Heavenfall Lightning!' if beast.get('boss') else ' Heavy Strike!')
    if b['hp']==0: b['status']='defeat'; msg+=' You were defeated; no cultivation resources lost.'
    elif b['turn']>=MAX_TURNS: b['status']='draw'; msg+=' Battle reached turn limit; no cultivation resources lost.'
    b['log'].append(msg)
    return b


def select_auto_action(b):
    if b.get('beast') and b['turn']-b.get('last_beast_art',-99)>=3 and b['qi']>=6 and b['turn']>=1:
        return 'beast_skill'
    if b['enemy']=='thunder_dragon' and b.get('ward'):
        return 'strike'
    if b['enemy']=='thunder_dragon' and (b['turn']+1)%3==0 and b['qi']>=5:
        return 'lightning_guard'
    if b['turn']<MAX_TURNS-1 and (b['turn']+1)%3==0 and b['qi']>=5 and b['hp']<48:
        return 'lightning_guard'
    if b.get('charge') and b['enemy_hp']>12: return 'strike'
    # All root types can use their matching art in auto mode, not just Lightning.
    if b['qi']>=6:
        root=b.get('root')
        if root in ('Fire','Wood','Poison') and b.get('burn',0)==0 and b.get('poison',0)==0 and b['turn']==0:
            return 'elemental_art'
        if root in ('Water','Light','Blood') and b['hp']<b['max_hp']*0.60:return 'elemental_art'
        if root in ('Ice','Soul','Wind','Shadow') and (b['turn']+1)%3==0:return 'elemental_art'
        if root in ('Metal','Void') and b['turn']==0:return 'elemental_art'
    if unlocked(b,'thunder_judgment') and b['qi']>=12: return 'thunder_judgment'
    if b['qi']>=8: return 'thunder_palm'
    return 'strike'


def auto(b):
    b=deepcopy(b)
    while b['status']=='active':
        next_action=select_auto_action(b)
        if b['enemy']=='thunder_dragon' and (b['turn']+2)%3==0 and b['qi']<17:
            next_action='focus_qi'
        b=turn(b,next_action)
    return b


def assisted(b):
    """Play routine turns automatically and stop BEFORE telegraphed dragon attacks.

    The human must choose an action by pressing a Discord button.
    """
    b=deepcopy(b)
    if b['status']!='active':
        raise ValueError('This battle has already finished.')
    while b['status']=='active':
        if b['enemy']=='thunder_dragon' and (b['turn']+1)%3==0:
            return b
        next_action=select_auto_action(b)
        if b['enemy']=='thunder_dragon' and (b['turn']+2)%3==0 and b['qi']<17:
            next_action='focus_qi'
        b=turn(b,next_action)
    return b
