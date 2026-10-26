"""Living Dao expansion: persistent sects, quests, companions, exploration.
No destructive migrations; all mutations are serialized per player and idempotent by interaction ID.
"""
import json
from datetime import datetime, timezone, timedelta

REGIONS={
 'mistwood':('Mistwood Vale',3,'wolf_fang'),
 'stonepeak':('Stonepeak Sanctuary',6,'stone_core'),
 'thunder_rift':('Thunder Rift Expanse',10,'serpent_scale'),
}
COMPANIONS={
 'jade_fox':('Jade Spirit Fox',16,'Insightful companion; assists exploration'),
 'storm_hawk':('Storm Hawk',24,'Scout companion; adds combat opening Qi'),
 'stone_tortoise':('Stone Tortoise',24,'Guardian companion; reinforces defenses'),
}
QUESTS={
 'scout':('Scout a region', 'explore', 1, 6),
 'adventurer':('Complete three expeditions', 'explore', 3, 12),
 'sect_service':('Contribute to your sect', 'contribute', 1, 8),
}
SCHEMA="""
CREATE TABLE IF NOT EXISTS living_dao_players (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 data JSONB NOT NULL DEFAULT '{}'::jsonb,
 PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS living_dao_actions (
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,
 response TEXT NOT NULL,PRIMARY KEY(guild_id,user_id,event_id));
CREATE TABLE IF NOT EXISTS living_dao_sects (
 guild_id BIGINT NOT NULL, name TEXT NOT NULL,
 founder_id BIGINT NOT NULL, contribution BIGINT NOT NULL DEFAULT 0,
 PRIMARY KEY(guild_id,name));
"""

def initial():
 return {'sect':None,'companion':None,'bond':0,'progress':{},'claimed':{},'last_explore':None,'region':'mistwood','explorations':0}

def decode(raw):
 value=json.loads(raw) if isinstance(raw,str) else dict(raw or {})
 return {**initial(),**value}

def progress(data,kind):
 updated=decode(data)
 updated['progress']=dict(updated['progress']);updated['progress'][kind]=updated['progress'].get(kind,0)+1
 return updated

def available_quests(data):
 return [(key,label,max(0,need-data['progress'].get(metric,0)),reward) for key,(label,metric,need,reward) in QUESTS.items() if not data['claimed'].get(key)]

def rate_limit(last,now,seconds):
 if last and now-datetime.fromisoformat(last)<timedelta(seconds=seconds):
  wait=int((timedelta(seconds=seconds)-(now-datetime.fromisoformat(last))).total_seconds())
  raise ValueError(f'Your spiritual senses are recovering. Try again in {wait}s.')

async def setup(repo):
 async with repo.pool.acquire() as con: await con.execute(SCHEMA)

async def read(repo,guild,user):
 async with repo.pool.acquire() as con:
  raw=await con.fetchval('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2',guild,user)
  return decode(raw)

async def action(repo,guild,user,event_id,op,argument=None):
 from inventory import _locked,_save
 now=datetime.now(timezone.utc)
 async with repo.pool.acquire() as con:
  async with con.transaction():
   if not await con.fetchval('SELECT 1 FROM characters WHERE guild_id=$1 AND user_id=$2',guild,user):
    raise ValueError('Use /start to create a character first.')
   await con.execute('INSERT INTO living_dao_players(guild_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING',guild,user)
   row=await con.fetchrow('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,user)
   old=await con.fetchval('SELECT response FROM living_dao_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild,user,event_id)
   if old is not None:return old
   from beast_v022 import settle,initial_cultivation
   state=settle(row['data'],now);inv=None
   doctrine=None
   if op in ('explore','story_choice') and state.get('sect'):
    raw_hall=await con.fetchval('SELECT data FROM sect_hall_state WHERE guild_id=$1 AND sect_name=$2',guild,state['sect'])
    hall=json.loads(raw_hall) if isinstance(raw_hall,str) else dict(raw_hall or {})
    doctrine=hall.get('doctrine') if isinstance(hall,dict) else None
   if op=='explore':
    if argument not in REGIONS:raise ValueError('Unknown region.')
    rate_limit(state['last_explore'],now,300)
    title,marks,item=REGIONS[argument]
    inv=await _locked(con,guild,user)
    inv['marks']+=marks
    inv['holdings'][item]=inv['holdings'].get(item,0)+1
    state['region']=argument;state['last_explore']=now.isoformat();state['explorations']+=1
    state=progress(state,'explore')
    from goals_v025 import record
    state=record(state,'explore',now)
    reply=f'Explored {title}! Earned {marks} Spirit Stones and 1 material. Expeditions: {state["explorations"]}. Next expedition in 5 minutes.'
    from secrets import randbelow
    from living_world_v024 import exploration
    encounter_roll=randbelow(1000)
    state,extra,stories=exploration(state,guild,argument,now,encounter_roll)
    inv['holdings'][item]+=extra
    if stories:reply+='\n'+stories
    from living_world_v025 import encounter
    state,scene=encounter(state,argument,now,encounter_roll,doctrine)
    reply+='\n'+scene
   elif op=='story_choice':
    from living_world_v025 import choose
    try:
     chapter_text,choice=str(argument).split(':',1)
     chapter=int(chapter_text)
    except (ValueError,TypeError):
     raise ValueError('Open the current story panel in /guide.') from None
    state,reply,reward=choose(state,chapter,choice,now,doctrine)
    inv=await _locked(con,guild,user)
    inv['marks']+=reward
   elif op=='story_train':
    from living_world_v025 import train
    if not isinstance(argument,dict):raise ValueError('Open the teacher panel in /guide.')
    state,reply=train(state,argument.get('teacher'),now,argument.get('tactics'))
   elif op=='quest' and argument in ('daily','weekly'):
    from goals_v025 import claim
    state,reply,reward=claim(state,argument,now)
    inv=await _locked(con,guild,user);inv['marks']+=reward
   elif op=='quest':
    if argument not in QUESTS:raise ValueError('Unknown quest.')
    label,metric,needed,reward=QUESTS[argument]
    if state['claimed'].get(argument):raise ValueError('This quest has already been claimed.')
    if state['progress'].get(metric,0)<needed:raise ValueError(f'{label}: {state["progress"].get(metric,0)}/{needed} completed.')
    inv=await _locked(con,guild,user);inv['marks']+=reward
    state['claimed']=dict(state['claimed']);state['claimed'][argument]=True
    reply=f'Quest complete: {label}! +{reward} Spirit Stones.'
   elif op=='adopt':
    if argument not in COMPANIONS:raise ValueError('Unknown companion.')
    if state['companion']:raise ValueError('You already have a bonded companion.')
    name,cost,_=COMPANIONS[argument];inv=await _locked(con,guild,user)
    if inv['marks']<cost:raise ValueError(f'{name} costs {cost} earned Spirit Stones.')
    inv['marks']-=cost;state['companion']=argument;state['bond']=1
    state['beast_cultivation']=initial_cultivation(now)
    reply=f'You bonded with {name}! Bond Level 1. Your beast now cultivates permanently at 18 Qi/hour alongside you. Try the free /beast_bond activity and /beast_cultivation. /bond remains optional Spirit-Stone-funded training.'
   elif op=='bond':
    if not state['companion']:raise ValueError('Adopt a spirit companion first.')
    if state['bond']>=5:raise ValueError('Your companion is already at maximum bond level 5.')
    inv=await _locked(con,guild,user);cost=8*state['bond']
    if inv['marks']<cost:raise ValueError(f'Bond training requires {cost} Spirit Stones.')
    inv['marks']-=cost;state['bond']+=1
    reply=f'Your companion reached Bond Level {state["bond"]}!'
   elif op=='sect_create':
    name=(argument or '').strip()
    if not (3<=len(name)<=30) or not all(c.isalnum() or c in ' -_' for c in name):
     raise ValueError('Sect name must be 3–30 letters/numbers/spaces, hyphens or underscores.')
    if state['sect']:raise ValueError('Leave your existing sect before creating another.')
    exists=await con.fetchval('SELECT 1 FROM living_dao_sects WHERE guild_id=$1 AND name=$2',guild,name)
    if exists:raise ValueError('That sect already exists. Use /sect_join.')
    inv=await _locked(con,guild,user)
    if inv['marks']<20:raise ValueError('Founding a sect requires 20 earned Spirit Stones.')
    inv['marks']-=20
    await con.execute('INSERT INTO living_dao_sects VALUES($1,$2,$3,0)',guild,name,user)
    state['sect']=name
    await con.execute('INSERT INTO sect_member_join_v022 (guild_id,user_id,sect_name) VALUES($1,$2,$3) ON CONFLICT(guild_id,user_id) DO UPDATE SET sect_name=EXCLUDED.sect_name,joined_at=NOW()',guild,user,name)
    reply=f'Founded the sect {name}! Other players can join with /sect_join.'
   elif op=='sect_join':
    if state['sect']:raise ValueError('You already belong to a sect.')
    name=(argument or '').strip()
    found=await con.fetchval('SELECT name FROM living_dao_sects WHERE guild_id=$1 AND lower(name)=lower($2)',guild,name)
    if not found:raise ValueError('No sect found with that name.')
    state['sect']=found
    await con.execute('INSERT INTO sect_member_join_v022 (guild_id,user_id,sect_name) VALUES($1,$2,$3) ON CONFLICT(guild_id,user_id) DO UPDATE SET sect_name=EXCLUDED.sect_name,joined_at=NOW()',guild,user,found)
    reply=f'Joined {found}! Contribute earned Spirit Stones to strengthen your sect.'
   elif op=='contribute':
    if not state['sect']:raise ValueError('Join a sect first.')
    amount=int(argument or 0)
    if amount<1 or amount>100:raise ValueError('Contribute between 1 and 100 Spirit Stones at once.')
    inv=await _locked(con,guild,user)
    if inv['marks']<amount:raise ValueError('Not enough Spirit Stones to contribute.')
    inv['marks']-=amount
    await con.execute('UPDATE living_dao_sects SET contribution=contribution+$3 WHERE guild_id=$1 AND name=$2',guild,state['sect'],amount)
    state=progress(state,'contribute')
    reply=f'Contributed {amount} Spirit Stones to {state["sect"]}. Sect prosperity increased!'
   else:raise ValueError('Unknown action.')
   if inv is not None:await _save(con,guild,user,inv)
   await con.execute('UPDATE living_dao_players SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,user,json.dumps(state))
   await con.execute('INSERT INTO living_dao_actions VALUES($1,$2,$3,$4)',guild,user,event_id,reply)
   return reply

async def view_sect(repo,guild,user):
 state=await read(repo,guild,user)
 async with repo.pool.acquire() as con:
  if not state['sect']:
   allrows=await con.fetch('SELECT name,contribution FROM living_dao_sects WHERE guild_id=$1 ORDER BY contribution DESC LIMIT 10',guild)
   return 'No sect joined. Available sects:\n'+('\n'.join(f'• {r["name"]} — prosperity {r["contribution"]}' for r in allrows) or 'No sects founded yet.')
  row=await con.fetchrow('SELECT contribution FROM living_dao_sects WHERE guild_id=$1 AND name=$2',guild,state['sect'])
  return f'Sect: {state["sect"]} | Shared prosperity: {row["contribution"] if row else 0} | Membership retained across sessions.'

async def view_quests(repo,guild,user):
 state=await read(repo,guild,user)
 lines=['**Living Dao · Quest Board**']
 for key,(label,metric,need,reward) in QUESTS.items():
  done=state['progress'].get(metric,0)
  flag='CLAIMED' if state['claimed'].get(key) else 'READY' if done>=need else f'{done}/{need}'
  lines.append(f'• {key}: {label} — {flag} | +{reward} Spirit Stones')
 from goals_v025 import view
 lines.append(view(state,datetime.now(timezone.utc)))
 return '\n'.join(lines)

async def view_companion(repo,guild,user):
 s=await read(repo,guild,user)
 if s['companion']:
  from companion_bonds_v024 import summary
  return (f'{COMPANIONS[s["companion"]][0]} · Bond Level {s["bond"]}/5.\n'
          +summary(s,datetime.now(timezone.utc))+'\n/beast_cultivation · /beast_advance · /beast_evolve\n'
          +('Optional /bond training costs '+str(8*s['bond'])+' earned Spirit Stones.' if s['bond']<5 else 'Maximum bond reached; shared activities remain free.'))
 return 'No bonded beast yet. ' + '; '.join(f'{k}: {v[0]} ({v[1]} Spirit Stones)' for k,v in COMPANIONS.items())
