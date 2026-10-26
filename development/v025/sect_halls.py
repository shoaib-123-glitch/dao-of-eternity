"""Sect halls & cooperative trials: additive schema, row locks, idempotent writes.
All upgrades are paid with the sect's existing contributed prosperity, which is
interpreted as cumulative contributions; spent total is tracked separately.
"""
import json
from datetime import datetime, timezone, timedelta
from sect_activities_v023 import (COMMISSIONS, DOCTRINES, complete_commission,
                                  choose_doctrine, renown_status)

HALLS = {
    'gathering': ('Spiritual Gathering Hall', 20, '+5% personal passive Qi per hall level (from upgrade time)'),
    'martial': ('Martial Training Hall', 20, 'Combat attack bonus'),
    'sanctuary': ('Guardian Sanctuary', 20, 'Combat max HP bonus'),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS sect_hall_state (
 guild_id BIGINT NOT NULL, sect_name TEXT NOT NULL,
 data JSONB NOT NULL DEFAULT '{}'::jsonb,
 PRIMARY KEY (guild_id,sect_name)
);
CREATE TABLE IF NOT EXISTS sect_hall_actions (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, event_id BIGINT NOT NULL,
 reply TEXT NOT NULL, PRIMARY KEY(guild_id,user_id,event_id)
);
CREATE TABLE IF NOT EXISTS sect_member_roles (
 guild_id BIGINT NOT NULL, sect_name TEXT NOT NULL,user_id BIGINT NOT NULL,
 role TEXT NOT NULL DEFAULT 'Disciple', PRIMARY KEY(guild_id,sect_name,user_id)
);
"""

def unpack(raw):
    o=json.loads(raw) if isinstance(raw,str) else dict(raw or {})
    return {**o, 'spent':max(0,int(o.get('spent',0))),
            'levels':{**o.get('levels',{}), **{k:max(0,int(o.get('levels',{}).get(k,0))) for k in HALLS}},
            'trials':max(0,int(o.get('trials',0))),
            'last_trial':o.get('last_trial')}

def cost(hall,level):
    if hall not in HALLS or level<0:raise ValueError('Unknown hall.')
    return HALLS[hall][1]*(level+1)

def bonuses(state):
    levels=unpack(state)['levels']
    return {'attack':min(10,levels['martial']*2), 'hp':min(30,levels['sanctuary']*6)}

async def setup(repo):
    async with repo.pool.acquire() as con:await con.execute(SCHEMA)

async def overview(repo,guild,user):
    from living_dao import read
    member=await read(repo,guild,user)
    name=member.get('sect')
    if not name:return 'Join or found a sect first. Use /sect.'
    async with repo.pool.acquire() as con:
        sect=await con.fetchrow('SELECT founder_id,contribution FROM living_dao_sects WHERE guild_id=$1 AND name=$2',guild,name)
        if not sect:return 'Your sect was not found; no changes were made.'
        raw=await con.fetchval('SELECT data FROM sect_hall_state WHERE guild_id=$1 AND sect_name=$2',guild,name)
    st=unpack(raw)
    lines=[f'**{name} · Sect Domain**',f'Treasury available: {max(0,sect["contribution"]-st["spent"])} contributed Spirit Stones', 'Founder upgrades halls. Members can run cooperative sect trials.']
    for key,(label,_,desc) in HALLS.items():lines.append(f'• {key} — {label} Lv{st["levels"][key]} (next cost: {cost(key,st["levels"][key])}) · {desc}')
    renown,tier,next_at=renown_status(st)
    lines.extend([f'Sect identity: {tier} · {renown} renown' + (f' · next tier at {next_at}' if next_at else ' · highest tier'),
                  f'Doctrine: {st.get("doctrine") or "Unchosen — founder: /sect_doctrine"}',
                  f'Completed sect trials: {st["trials"]} · Commissions: {st.get("commission_count",0)}',
                  'Commission board: /sect_commission patrol, archives or gardens · +6 personal Spirit Stones, +5 sect renown (+1 matching doctrine).',
                  'Free for every member · 3 commissions per UTC day · 6-hour personal cooldown.'])
    from sect_projects_v024 import board
    lines.append(board(st,datetime.now(timezone.utc)))
    from sect_merit_v025 import view as merit_view
    lines.append(merit_view(st,user,datetime.now(timezone.utc)))
    return '\n'.join(lines)

async def act(repo,guild,user,event_id,operation,argument=None):
    from living_dao import decode
    from inventory import _locked,_save
    async with repo.pool.acquire() as con:
      async with con.transaction():
        old=await con.fetchval('SELECT reply FROM sect_hall_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild,user,event_id)
        if old is not None:return old
        raw=await con.fetchval('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2',guild,user)
        member=decode(raw)
        sect_name=member.get('sect')
        if not sect_name:raise ValueError('Join a sect first.')
        # Contribution already locks inventory before the sect row. Reward
        # operations share that order, including trial, so they cannot form a
        # hall/inventory lock cycle with each other or a contribution.
        inv=await _locked(con,guild,user) if operation in ('trial','commission','project_claim','merit_buy') else None
        await con.execute('INSERT INTO sect_hall_state(guild_id,sect_name) VALUES($1,$2) ON CONFLICT DO NOTHING',guild,sect_name)
        sect=await con.fetchrow('SELECT founder_id,contribution FROM living_dao_sects WHERE guild_id=$1 AND name=$2 FOR UPDATE',guild,sect_name)
        if not sect:raise ValueError('Sect no longer exists.')
        row=await con.fetchrow('SELECT data FROM sect_hall_state WHERE guild_id=$1 AND sect_name=$2 FOR UPDATE',guild,sect_name)
        # The first replay lookup can race with another connection. Recheck
        # after the shared locks serialize requests before awarding anything.
        old=await con.fetchval('SELECT reply FROM sect_hall_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild,user,event_id)
        if old is not None:return old
        st=unpack(row['data'])
        if operation=='upgrade':
            if sect['founder_id']!=user:raise ValueError('Only the sect founder can spend contributed prosperity on halls.')
            if argument not in HALLS:raise ValueError('Unknown sect hall.')
            lv=st['levels'][argument]
            if lv>=5:raise ValueError('This hall has reached level 5.')
            price=cost(argument,lv)
            if sect['contribution']-st['spent']<price:raise ValueError(f'Need {price} available contributed Spirit Stones. Contribute via /sect_contribute.')
            st['spent']+=price;st['levels'][argument]=lv+1
            if argument=='gathering':
                await con.execute('INSERT INTO sect_gathering_history_v022 (guild_id,sect_name,effective_at,level) VALUES($1,$2,$3,$4)',guild,sect_name,datetime.now(timezone.utc),lv+1)
            reply=f'{HALLS[argument][0]} upgraded to Level {lv+1}! Treasury spent {price} contributed Spirit Stones.'
        elif operation=='trial':
            now=datetime.now(timezone.utc)
            if st['last_trial']:
                since=now-datetime.fromisoformat(st['last_trial'])
                if since<timedelta(minutes=30):raise ValueError(f'Sect trial recovering. Retry in {max(1,int((timedelta(minutes=30)-since).total_seconds()//60))} minute(s).')
            # Trial participation rewards the participant; account and sect state are updated atomically.
            inv['marks']+=10
            await _save(con,guild,user,inv)
            st['trials']+=1;st['last_trial']=now.isoformat()
            from sect_merit_v025 import earn
            st,merit=earn(st,user,'trial')
            reply=f'Sect trial completed! {sect_name} recorded trial #{st["trials"]}. You earned 10 Spirit Stones and {merit} Sect Merit. Next trial in 30 minutes.'
        elif operation=='commission':
            now=datetime.now(timezone.utc)
            st,marks,renown=complete_commission(st,user,argument,now)
            from sect_projects_v024 import record_commission
            st,project_reply=record_commission(st,user,argument,now)
            inv['marks']+=marks
            await _save(con,guild,user,inv)
            _,tier,_=renown_status(st)
            reply=(f'{COMMISSIONS[argument][0]} completed! +{marks} Spirit Stones · '
                   f'{sect_name} +{renown} renown ({tier}, {st["renown"]} total). '
                   'Next personal commission in 6 hours; three per UTC day.')
            from sect_merit_v025 import earn
            st,merit=earn(st,user,'commission')
            reply+=f' +{merit} Sect Merit.'
            reply+='\n'+project_reply
        elif operation=='project_claim':
            from sect_projects_v024 import claim,board
            st,marks=claim(st,user)
            inv['marks']+=marks
            await _save(con,guild,user,inv)
            reply=board(st,datetime.now(timezone.utc))
            reply+=f'\nClaimed {marks} earned project Spirit Stones.' if marks else '\nNo unclaimed project rewards. Contribute through /sect_commission.'
        elif operation=='merit_buy':
            from sect_merit_v025 import buy
            st,reply,holdings=buy(st,user,argument,datetime.now(timezone.utc))
            for key,qty in holdings.items():inv['holdings'][key]=inv['holdings'].get(key,0)+qty
            await _save(con,guild,user,inv)
        elif operation=='doctrine':
            if sect['founder_id']!=user:raise ValueError('Only the sect founder can choose its doctrine.')
            st=choose_doctrine(st,argument,datetime.now(timezone.utc))
            reply=f'{sect_name} now follows {DOCTRINES[argument]}. Matching commissions grant +1 shared renown. Next doctrine change in 24 hours.'
        else:raise ValueError('Unknown sect operation.')
        await con.execute('UPDATE sect_hall_state SET data=$3::jsonb WHERE guild_id=$1 AND sect_name=$2',guild,sect_name,json.dumps(st))
        await con.execute('INSERT INTO sect_hall_actions VALUES($1,$2,$3,$4)',guild,user,event_id,reply)
        return reply
