"""Sect identity/ranks and real shared raid encounters. Every raid strike is a member action.

Treasury remains in the existing sect contribution+sect_hall tables. Raid rewards
are earned by contribution and awarded atomically on the finishing strike.
"""
import json
from datetime import datetime,timezone,timedelta
from living_dao import decode

SCHEMA='''
CREATE TABLE IF NOT EXISTS sect_profiles_v022(
 guild_id BIGINT NOT NULL,sect_name TEXT NOT NULL,
 banner TEXT NOT NULL DEFAULT 'Unfurl your banner and rise toward the heavens',
 PRIMARY KEY(guild_id,sect_name));
CREATE TABLE IF NOT EXISTS sect_raid_v022(
 guild_id BIGINT NOT NULL,sect_name TEXT NOT NULL, data JSONB NOT NULL DEFAULT '{}'::jsonb,
 PRIMARY KEY(guild_id,sect_name));
CREATE TABLE IF NOT EXISTS sect_action_v022(
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,
 response TEXT NOT NULL,PRIMARY KEY(guild_id,user_id,event_id));
CREATE TABLE IF NOT EXISTS sect_member_join_v022(
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, sect_name TEXT NOT NULL,
 joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS sect_gathering_history_v022(
 guild_id BIGINT NOT NULL, sect_name TEXT NOT NULL,
 effective_at TIMESTAMPTZ NOT NULL, level INT NOT NULL,
 PRIMARY KEY(guild_id,sect_name,effective_at));
'''
ROLES=('Outer Disciple','Inner Disciple','Elder')

def raid_default():return {'status':'idle','hp':0,'max_hp':150,'members':{},'last_at':None,'cleared':0}

def raid_read(raw):
    val=json.loads(raw) if isinstance(raw,str) else dict(raw or {})
    return {**raid_default(),**val}

def raid_join(r,user):
    if r['status']!='recruiting':raise ValueError('No recruiting raid. Ask your founder or Elder to start one.')
    if str(user) not in r['members'] and len(r['members'])>=8:raise ValueError('Raid roster is full (8).')
    r['members'].setdefault(str(user),{'damage':0,'last_at':None})
    return f'Joined the sect raid: {len(r["members"])}/8 cultivators. Launch requires at least two.'

def raid_launch(r):
    if r['status']!='recruiting':raise ValueError('Raid is not recruiting.')
    if len(r['members'])<2:raise ValueError('A cooperative raid requires at least two distinct sect members.')
    r['status']='active';r['max_hp']=100+55*len(r['members']);r['hp']=r['max_hp']
    return f'Cooperative sect raid begins! {len(r["members"])} cultivators vs {r["hp"]} HP Ancient Wraith. Each player contributes damage.'

def raid_strike(r,user,now,damage):
    if r['status']!='active':raise ValueError('No active sect raid.')
    player=r['members'].get(str(user))
    if player is None:raise ValueError('Join the raid before attacking.')
    prior=player['last_at']
    if prior and now-datetime.fromisoformat(prior)<timedelta(seconds=30):raise ValueError('Raid technique cooling down (30 seconds).')
    dealt=min(r['hp'],max(1,damage));r['hp']-=dealt
    player['damage']+=dealt;player['last_at']=now.isoformat()
    if r['hp']==0:r['status']='victory';r['cleared']=r.get('cleared',0)+1
    return dealt

def contributor_rewards(r):
    if r['status']!='victory':raise ValueError('Raid must be cleared before rewards.')
    return {int(uid):6+min(18,int(info['damage'])//15) for uid,info in r['members'].items() if info['damage']>0}

async def setup(repo):
    async with repo.pool.acquire() as con:
        await con.execute(SCHEMA)
        # Migrate previously purchased (v0.21) Gathering Halls forward from first
        # v0.22 startup. Never grant unearned retroactive Qi before deployment.
        await con.execute("""
            INSERT INTO sect_gathering_history_v022(guild_id,sect_name,effective_at,level)
            SELECT h.guild_id,h.sect_name,NOW(),
                   LEAST(5,GREATEST(0,(h.data #>> '{levels,gathering}')::int))
            FROM sect_hall_state h
            WHERE COALESCE((h.data #>> '{levels,gathering}')::int,0)>0
              AND NOT EXISTS (
                SELECT 1 FROM sect_gathering_history_v022 gh
                WHERE gh.guild_id=h.guild_id AND gh.sect_name=h.sect_name
              )
        """)

async def member(con,guild,user):
    raw=await con.fetchval('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2',guild,user)
    s=decode(raw)
    if not s.get('sect'):raise ValueError('Join or create a sect first.')
    sect=s['sect'];founder=await con.fetchval('SELECT founder_id FROM living_dao_sects WHERE guild_id=$1 AND name=$2',guild,sect)
    if not founder:raise ValueError('This sect no longer exists.')
    role=('Founder' if user==founder else await con.fetchval('SELECT role FROM sect_member_roles WHERE guild_id=$1 AND sect_name=$2 AND user_id=$3',guild,sect,user) or 'Outer Disciple')
    return sect,role

async def gathering_windows(con,guild,user,prev,end):
    """Historical ledger credits upgrades only from their real activation time (no backdating)."""
    try:sect,_=await member(con,guild,user)
    except ValueError:return []
    joined=await con.fetchval('SELECT joined_at FROM sect_member_join_v022 WHERE guild_id=$1 AND user_id=$2 AND sect_name=$3',guild,user,sect)
    if joined:prev=max(prev,joined)
    if end<=prev:return []
    rows=await con.fetch('SELECT effective_at,level FROM sect_gathering_history_v022 WHERE guild_id=$1 AND sect_name=$2 AND effective_at<$3 ORDER BY effective_at',guild,sect,end)
    out=[]
    for i,r in enumerate(rows):
        left=max(r['effective_at'],prev)
        right=min(end,rows[i+1]['effective_at'] if i+1<len(rows) else end)
        if left<right:out.append((left,right,min(5,max(0,int(r['level'])))))
    return out

async def overview(repo,guild,user):
    async with repo.pool.acquire() as con:
        sect,role=await member(con,guild,user)
        banner=await con.fetchval('SELECT banner FROM sect_profiles_v022 WHERE guild_id=$1 AND sect_name=$2',guild,sect)
        roster=await con.fetch('SELECT user_id,data FROM living_dao_players WHERE guild_id=$1',guild)
        members=[int(r['user_id']) for r in roster if decode(r['data']).get('sect')==sect]
        raid=raid_read(await con.fetchval('SELECT data FROM sect_raid_v022 WHERE guild_id=$1 AND sect_name=$2',guild,sect))
        return (f'**{sect}** · Your rank: {role}\nBanner: {banner or "Not set"}\nMembers: {len(members)} | '
                f'Raid: {raid["status"]} ({raid["hp"]}/{raid["max_hp"]} HP) · Victories: {raid["cleared"]}\n'
                'Founder promotes members and changes banner; Elders can lead shared raids. /sect_domain shows buildings.')

async def act(repo,guild,user,event_id,op,argument=None,target=None):
    from inventory import _locked,_save
    now=datetime.now(timezone.utc)
    async with repo.pool.acquire() as con:
      async with con.transaction():
        sect,role=await member(con,guild,user)
        old=await con.fetchval('SELECT response FROM sect_action_v022 WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild,user,event_id)
        if old is not None:return old
        await con.execute('INSERT INTO sect_profiles_v022 (guild_id,sect_name) VALUES($1,$2) ON CONFLICT DO NOTHING',guild,sect)
        await con.execute('INSERT INTO sect_raid_v022 (guild_id,sect_name) VALUES($1,$2) ON CONFLICT DO NOTHING',guild,sect)
        row=await con.fetchrow('SELECT data FROM sect_raid_v022 WHERE guild_id=$1 AND sect_name=$2 FOR UPDATE',guild,sect)
        raid=raid_read(row['data'])
        if op=='banner':
            if role!='Founder':raise ValueError('Only the sect founder can change its banner.')
            title=(argument or '').strip()
            if not 5<=len(title)<=100:raise ValueError('Banner motto must be between 5 and 100 characters.')
            await con.execute('UPDATE sect_profiles_v022 SET banner=$3 WHERE guild_id=$1 AND sect_name=$2',guild,sect,title)
            reply='Sect banner updated: '+title
        elif op=='rank':
            if role!='Founder':raise ValueError('Only the founder can assign sect ranks.')
            if argument not in ROLES:raise ValueError('Choose Outer Disciple, Inner Disciple or Elder.')
            if not target or target==user:raise ValueError('Choose another sect member, not yourself.')
            their,_=await member(con,guild,target)
            if their!=sect:raise ValueError('That player belongs to another sect.')
            await con.execute('INSERT INTO sect_member_roles (guild_id,sect_name,user_id,role) VALUES($1,$2,$3,$4) ON CONFLICT(guild_id,sect_name,user_id) DO UPDATE SET role=EXCLUDED.role',guild,sect,target,argument)
            reply=f'<@{target}> appointed {argument} of {sect}.'
        elif op=='raid_start':
            if role not in ('Founder','Elder'):raise ValueError('Founder or Elder rank required to start a raid.')
            if raid['status'] in ('recruiting','active'):raise ValueError('An unfinished sect raid already exists.')
            if raid.get('last_at') and now-datetime.fromisoformat(raid['last_at'])<timedelta(hours=2):raise ValueError('Sect raid requires two hours of preparation after the previous raid.')
            cleared=raid['cleared']
            raid={**raid_default(),'status':'recruiting','members':{str(user):{'damage':0,'last_at':None}},'cleared':cleared}
            reply='Ancient Wraith raid opened. At least one other member must /sect_raid_join before launch.'
        elif op=='raid_join':reply=raid_join(raid,user)
        elif op=='raid_launch':
            if role not in ('Founder','Elder'):raise ValueError('Founder or Elder required to launch the raid.')
            reply=raid_launch(raid)
        elif op=='raid_strike':
            # Only actual participant actions damage the shared boss. No fake co-op.
            raw=await con.fetchval('SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2',guild,user)
            battle=json.loads(raw) if isinstance(raw,str) else dict(raw or {})
            damage=15+min(10,int(battle.get('mastery',{}).get('thunder_palm',0))//3)
            dealt=raid_strike(raid,user,now,damage)
            reply=f'<@{user}> struck the Ancient Wraith for {dealt} damage! HP: {raid["hp"]}/{raid["max_hp"]}.'
            if raid['status']=='victory':
                rewards=contributor_rewards(raid)
                for uid,marks in sorted(rewards.items()):
                    inv=await _locked(con,guild,uid);inv['marks']+=marks;await _save(con,guild,uid,inv)
                raid['last_at']=now.isoformat()
                reply+=' Raid cleared! Rewards: '+', '.join(f'<@{uid}> +{marks} marks' for uid,marks in rewards.items())
        else:raise ValueError('Unknown sect activity.')
        await con.execute('UPDATE sect_raid_v022 SET data=$3::jsonb WHERE guild_id=$1 AND sect_name=$2',guild,sect,json.dumps(raid))
        await con.execute('INSERT INTO sect_action_v022 VALUES($1,$2,$3,$4)',guild,user,event_id,reply)
        return reply
