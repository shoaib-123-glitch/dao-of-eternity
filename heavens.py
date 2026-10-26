"""World Qi calendar, affinity lore, personal omens, and sequential elixir rules.

Deterministic guild/day event selection allows FULL retroactive event-credit after
bot downtime. No background tick or paid game mechanics required.
"""
from __future__ import annotations
from datetime import datetime, date, timedelta, timezone
from hashlib import sha256
from secrets import randbelow
from math import ceil

UTC = timezone.utc
WORLD_EVENTS_LAUNCH = date(2026,10,21)  # No retroactive world-event rewards before first release.
COMMON = {
 'Fire':('Volcanic Spirit Cave','burn, alchemy','Water pressure'),
 'Water':('Moonlit Sacred Lake','heal, cleanse','low burst'),
 'Earth':('Ancient Mountain Heart','shield, formations','low mobility'),
 'Wind':('Sky Feather Canyon','dodge, speed','fragility'),
 'Wood':('Ten Thousand Herb Valley','regenerate, herbs','low burst'),
 'Metal':('Broken Sword Tomb','pierce, forging','low sustain'),
 'Lightning':('Heavenly Thunder Rift','shock, burst','Qi hungry'),
 'Ice':('Frozen Soul Palace','slow, control','lower direct damage'),
}
RARE = ('Light','Shadow','Poison','Blood','Soul','Void','Time','Chaos')
ALL_ROOTS = tuple(COMMON)+RARE
# Rare roots are collectible only through the optional, once-only root lottery.
RARE_CORRESPONDENCE = {'Light':'Fire','Shadow':'Wind','Poison':'Wood','Blood':'Water',
 'Soul':'Ice','Void':'Earth','Time':'Wind','Chaos':'Lightning'}
PATHS = {'Sword':'Weapon techniques: +2 attack', 'Body':'Body tempering: +10 maximum combat HP',
         'Formation':'Defensive formations: -1 incoming damage',
         'Spirit':'Spirit absorption: +10% passive Qi',
         'Alchemy':'Elixir preparation: +10% passive Qi'}

EVENTS = (
 ('Spiritual Rainfall', 1500, 180, 'Water'),
 ('Nine Heavens Qi Surge', 2000, 120, 'Lightning'),
 ('Celestial Convergence', 3000, 60, 'Wind'),
 ('Primordial World Awakening', 5000, 30, 'Fire'),
 ('Verdant Rejuvenation', 1750, 120, 'Wood'),
 ('Mountain Pulse', 1750, 120, 'Earth'),
 ('Sword Starfall', 2000, 75, 'Metal'),
 ('Frost Moon Rising', 2000, 75, 'Ice'),
)
ELIXIRS = {
 'minor':('Minor Qi Elixir',1500,45,12),
 'greater':('Greater Qi Elixir',2000,60,24),
 'celestial':('Celestial Qi Elixir',2500,30,40),
}
OMENS = {
 'rare':('Hidden Spiritual Resonance',2000,120),
 'epic':('Heavenly Dao Enlightenment',3000,60),
 'legendary':('Primordial Elemental Awakening',5000,30),
}


def iso(value): return value.astimezone(UTC).isoformat()
def dt(value): return datetime.fromisoformat(value).astimezone(UTC)

def world_for_day(guild_id: int, day: date):
    """~35% of UTC days have one shared event. Stable across bot restarts."""
    if not guild_id or day < WORLD_EVENTS_LAUNCH: return None
    digest=sha256(f'dao-world-v1:{guild_id}:{day.isoformat()}'.encode()).digest()
    if digest[0]>=90: return None  # 90/256 = 35.15%
    kind=EVENTS[digest[1]%len(EVENTS)]
    name,mult,duration,affinity=kind
    minute=120+(int.from_bytes(digest[2:4], 'big')%1080)  # 02:00-20:00 UTC
    start=datetime.combine(day,datetime.min.time(),tzinfo=UTC)+timedelta(minutes=minute)
    end=start+timedelta(minutes=duration)
    return {'id':f'{day.isoformat()}-{digest[1]}','name':name,'start':start,'end':end,
            'mult':mult,'affinity':affinity}


def events_between(guild_id, start, end):
    if not guild_id or end<=start: return []
    first=start.date()-timedelta(days=1)
    last=end.date()
    result=[]
    day=first
    while day<=last:
        event=world_for_day(guild_id,day)
        if event and event['start']<end and event['end']>start: result.append(event)
        day+=timedelta(days=1)
    return result


def current_event(guild_id,now):
    items=events_between(guild_id,now-timedelta(seconds=1),now+timedelta(seconds=1))
    return next((e for e in items if e['start']<=now<e['end']),None)


def relevant_root(root): return RARE_CORRESPONDENCE.get(root,root)

def add_boon(state,now,quality='rare'):
    if quality not in OMENS: raise ValueError('Unknown omen grade.')
    name,mult,minutes=OMENS[quality]
    existing=state.setdefault('personal_boons',[])
    if any(dt(x['end_at'])>now for x in existing):
        return None  # Never stack free rare boosts against themselves
    state['personal_boons']=[x for x in existing if dt(x['end_at'])>now]
    info={'name':name,'mult':mult,'start_at':iso(now),'end_at':iso(now+timedelta(minutes=minutes))}
    state['personal_boons'].append(info)
    return info


def seek_omen(state,now,root,approach='safe',roll=None):
    if approach not in ('safe','bold'): raise ValueError('Choose safe or bold.')
    until=state.get('omen_next_at')
    if until and now<dt(until):
        secs=ceil((dt(until)-now).total_seconds())
        raise ValueError(f'Your spiritual senses need rest: {secs//60+1} minutes remaining.')
    if any(dt(x['end_at'])>now for x in state.get('personal_boons',[])):
        raise ValueError('A personal Qi omen is active. Visit /qi to see when it expires.')
    if roll is None: roll=randbelow(1000)
    if not 0<=roll<1000: raise ValueError('Invalid omen roll.')
    state['omen_next_at']=iso(now+timedelta(hours=2))
    # 22% safe vs 35% bold. All free; bold has minor impurity consequence on failure.
    threshold=220 if approach=='safe' else 350
    venue=COMMON.get(relevant_root(root),('Wandering Spiritual Rift',))[0]
    if roll<threshold:
        quality='legendary' if roll<12 else ('epic' if roll<65 else 'rare')
        boon=add_boon(state,now,quality)
        label=OMENS[quality][0]
        return (f'{venue}: {label}! {boon["mult"]/1000:g}x personal Qi for '
                f'{OMENS[quality][2]} minutes. All time, even offline, counts.')
    if approach=='bold':
        # The risk is bounded, does not harm combat gear or permanently lower foundation.
        state['omen_bold_failed']=True
    state.setdefault('eternal',{}).setdefault('bank_qi',0)
    state['eternal']['bank_qi']+=5
    return f'{venue}: no rare resonance this time. +5 consolation Qi banked.' + (' A small instability follows your bold approach.' if approach=='bold' else '')


def queue_elixir(state,key,now):
    if key not in ELIXIRS: raise ValueError('Unknown elixir.')
    queue=[x for x in state.get('elixir_queue',[]) if dt(x['end_at'])>now]
    if len(queue)>=5: raise ValueError('Elixir queue is full (5 slots).')
    last=dt(queue[-1]['end_at']) if queue else now
    start=max(now,last)
    name,mult,minutes,_=ELIXIRS[key]
    potion={'key':key,'name':name,'mult':mult,'start_at':iso(start),'end_at':iso(start+timedelta(minutes=minutes))}
    queue.append(potion)
    state['elixir_queue']=queue
    return potion


def active_at(elements,now):
    return next((x for x in elements if dt(x['start_at'])<=now<dt(x['end_at'])),None)


def active_effects(state,guild_id,root,now):
    world=current_event(guild_id,now)
    personal=active_at(state.get('personal_boons',[]),now)
    elixir=active_at(state.get('elixir_queue',[]),now)
    return world,personal,elixir


def multiplier_milli(world,personal,elixir,root,path=None,secondary=None,dual=None):
    """Integer-only factor; max 6x. Everyone benefits from events and root identity."""
    result=1100 if root in ALL_ROOTS else 1000  # equal innate 10% for all awakened roots
    if path in ('Spirit','Alchemy'): result=(result*1100)//1000
    if world:
        result=(result*world['mult'])//1000
        if world['affinity'] in (relevant_root(root),relevant_root(secondary)): result=result*1200//1000
    if personal: result=result*personal['mult']//1000
    if elixir: result=result*elixir['mult']//1000
    if dual: result=result*dual['mult']//1000
    return min(6000,result)


def boost_boundaries(state,guild_id,start,end):
    worlds=events_between(guild_id,start,end)
    marks={start,end}
    for obj in worlds:
        marks.add(max(start,obj['start']));marks.add(min(end,obj['end']))
    for obj in state.get('personal_boons',[])+state.get('elixir_queue',[])+state.get('dual_boons',[]):
        a=dt(obj['start_at']);b=dt(obj['end_at'])
        if a<end and b>start:
            marks.add(max(start,a));marks.add(min(end,b))
    return sorted(marks),worlds


def boost_summary(state,guild_id,root,now):
    world,pers,elix=active_effects(state,guild_id,root,now)
    result=[]
    if root in ALL_ROOTS: result.append('Awakened root: +10% base Qi')
    if state.get('secondary_root'): result.append(f'Dual Root: {root} + {state["secondary_root"]} (either may resonate)')
    if state.get('sect_qi_level'):result.append(f'Sect Gathering Hall Lv{state["sect_qi_level"]}: +{5*state["sect_qi_level"]}% Qi')
    if state.get('cultivation_path') in ('Spirit','Alchemy'): result.append(f'{state["cultivation_path"]} path: +10%')
    from shattered_heavens_v023 import bonus_milli
    cultivation_bonus=bonus_milli(state,now,root)
    if cultivation_bonus>1000:result.append(f'Cultivation focus/mastery: +{(cultivation_bonus-1000)/10:g}% Qi')
    if world: result.append(f'{world["name"]}: {world["mult"]/1000:g}x until {world["end"].strftime("%H:%M UTC")}'+(' (+20% affinity resonance)' if world['affinity'] in (relevant_root(root),relevant_root(state.get('secondary_root'))) else ''))
    if pers: result.append(f'{pers["name"]}: {pers["mult"]/1000:g}x until {dt(pers["end_at"]).strftime("%H:%M UTC")}')
    if elix: result.append(f'{elix["name"]}: {elix["mult"]/1000:g}x until {dt(elix["end_at"]).strftime("%H:%M UTC")}')
    dual=active_at(state.get('dual_boons',[]),now)
    if dual:result.append(f'Dual/Solo Harmony: {dual["mult"]/1000:g}x until {dt(dual["end_at"]).strftime("%H:%M UTC")}')
    if not result: result.append('No active bonuses')
    result.append('Combined boosts capped at 6x')
    return result
