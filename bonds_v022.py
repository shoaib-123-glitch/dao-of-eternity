"""Consensual player bonds, fictional NPC households, and non-explicit dual cultivation.

All role changes and rewards use PostgreSQL transactions and Discord interaction IDs.
No relationship is mandatory for solo progression and no bonus scales with partner count.
"""
from datetime import datetime,timezone,timedelta
from heavens import iso,dt
import json

NPCS={
 'lin_yue':('Lin Yue','Water','A patient wandering healer'),
 'mei_xin':('Mei Xin','Fire','An independent alchemist'),
 'ren_qiu':('Ren Qiu','Earth','A thoughtful formation scholar'),
}
ROLES={'spouse','consort','retainer','disciple'}
NPC_QI_RATE=12  # permanent independent cultivation, includes offline time
COSTS={'spouse':15,'consort':15,'retainer':10,'disciple':12}
SCHEMA='''
CREATE TABLE IF NOT EXISTS dao_households (
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,
 data JSONB NOT NULL DEFAULT '{}'::jsonb,PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS dao_household_actions (
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,
 response TEXT NOT NULL,PRIMARY KEY(guild_id,user_id,event_id));
CREATE TABLE IF NOT EXISTS dao_player_bonds (
 guild_id BIGINT NOT NULL, proposer BIGINT NOT NULL, recipient BIGINT NOT NULL,
 role TEXT NOT NULL, status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(guild_id,proposer,recipient),CHECK(proposer<>recipient));
CREATE TABLE IF NOT EXISTS dao_dual_requests (
 guild_id BIGINT NOT NULL,proposer BIGINT NOT NULL,recipient BIGINT NOT NULL,
 status TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(guild_id,proposer,recipient),CHECK(proposer<>recipient));
'''

def normal(raw):
    data=json.loads(raw) if isinstance(raw,str) else dict(raw or {})
    return {'npcs':list(data.get('npcs',[])),'last_dual_at':data.get('last_dual_at'),
            'last_task_at':data.get('last_task_at'),'house_xp':int(data.get('house_xp',0))}

def npc_capacity(stage):return 100+25*(stage-1)

def settle_npcs(data,now):
    """Credit each voluntary NPC's own cultivation since their last interaction."""
    for member in data['npcs']:
        member.setdefault('stage',1);member.setdefault('qi',0);member.setdefault('bank',0)
        member.setdefault('fraction',0);member.setdefault('total',0)
        member.setdefault('last_at',iso(now))
        prev=dt(member['last_at'])
        elapsed=max(0,int((now-prev).total_seconds()))
        if elapsed:
            earned,member['fraction']=divmod(member['fraction']+elapsed*NPC_QI_RATE,3600)
            member['bank']+=earned;member['total']+=earned
            member['last_at']=iso(now)
        if member['stage']<=9:
            transferred=min(member['bank'],max(0,npc_capacity(member['stage'])-member['qi']))
            member['qi']+=transferred;member['bank']-=transferred
    return data

def npc_advance(data,npc):
    member=next((m for m in data['npcs'] if m['id']==npc),None)
    if member is None:raise ValueError('NPC has not joined your household.')
    stage=member.get('stage',1)
    if stage>=9:raise ValueError('NPC has reached current maximum Stage 9.')
    if member.get('qi',0)<npc_capacity(stage):raise ValueError(f'NPC needs {npc_capacity(stage)} Qi to advance.')
    member['stage']=stage+1;member['qi']=0
    transfer=min(member.get('bank',0),npc_capacity(stage+1))
    member['qi']+=transfer;member['bank']-=transfer
    return f'{NPCS[npc][0]} advanced to independent cultivation Stage {stage+1}/9.'

def npc_recruit(data,npc,role,now=None):
    if npc not in NPCS or role not in ROLES:raise ValueError('Unknown NPC or household role.')
    if any(member['id']==npc for member in data['npcs']):raise ValueError('This character is already in your household.')
    if len(data['npcs'])>=6:raise ValueError('Household NPC capacity is 6.')
    if role in ('spouse','consort') and sum(m['role'] in ('spouse','consort') for m in data['npcs'])>=3:
        raise ValueError('Maximum three NPC romantic partners per household.')
    now=now or datetime.now(timezone.utc)
    data['npcs'].append({'id':npc,'role':role,'bond':1,'stage':1,'qi':0,'bank':0,'fraction':0,'total':0,'last_at':iso(now)})
    return f'{NPCS[npc][0]} joined your household as {role}. Relationship progression is fictional and voluntary.'

def can_dual(data,now):
    last=data.get('last_dual_at')
    if last and now-dt(last)<timedelta(hours=4):raise ValueError('Dual cultivation rests for four hours between sessions.')

def dual_boon(character,now):
    # Separate from personal omens; all multipliers share the 6x economy cap.
    character.setdefault('dual_boons',[]).append({'name':'Harmonious Dual Cultivation','mult':1250,
       'start_at':iso(now),'end_at':iso(now+timedelta(hours=1))})

def overview(data,npcs,player_bonds):
    lines=[f'**Cultivation Household** · Renown {data["house_xp"]}',
           f'NPC members: {len(data["npcs"])}/6']
    for member in data['npcs']:
        name,root,desc=npcs[member['id']]
        lines.append(f'• {name} · {member["role"]} · {root} Root · Bond {member["bond"]} · Cultivation {member.get("stage",1)}/9 ({member.get("qi",0)}/{npc_capacity(member.get("stage",1))} Qi) — {desc}')
    lines.extend('• Player bond: '+str(x) for x in player_bonds)
    lines.append('Dual cultivation: 1.25x Qi for 1 hour, 4-hour cooldown; solo alternative: /solo_meditation.')
    return '\n'.join(lines)

async def setup(repo):
    async with repo.pool.acquire() as con:await con.execute(SCHEMA)

async def view(repo,guild,user):
    async with repo.pool.acquire() as con:
      async with con.transaction():
        await con.execute('INSERT INTO dao_households(guild_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING',guild,user)
        raw=await con.fetchval('SELECT data FROM dao_households WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,user)
        rows=await con.fetch('SELECT proposer,recipient,role FROM dao_player_bonds WHERE guild_id=$1 AND status=$2 AND (proposer=$3 OR recipient=$3)',guild,'active',user)
        players=[f'{r["role"]} with <@{r["recipient"] if r["proposer"]==user else r["proposer"]}>' for r in rows]
        data=settle_npcs(normal(raw),datetime.now(timezone.utc))
        await con.execute('UPDATE dao_households SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,user,json.dumps(data))
        return overview(data,NPCS,players)

async def apply(repo,guild,user,event_id,op,*,npc=None,role=None,target=None):
    from inventory import _locked,_save
    from eternal import settle
    now=datetime.now(timezone.utc)
    async with repo.pool.acquire() as con:
      async with con.transaction():
        exists=await con.fetchval('SELECT 1 FROM characters WHERE guild_id=$1 AND user_id=$2',guild,user)
        if not exists:raise ValueError('Use /start first.')
        # Lock an always-existing local row before checking duplicate interaction IDs.
        await con.execute('INSERT INTO dao_households (guild_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING',guild,user)
        row=await con.fetchrow('SELECT data FROM dao_households WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,user)
        prior=await con.fetchval('SELECT response FROM dao_household_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild,user,event_id)
        if prior is not None:return prior
        d=settle_npcs(normal(row['data']),now)
        if op=='recruit':
            price=COSTS.get(role)
            if price is None:raise ValueError('Select spouse, consort, retainer or disciple.')
            if npc not in NPCS:raise ValueError('Unknown household character.')
            # Validate before spending resources.
            if any(m['id']==npc for m in d['npcs']) or len(d['npcs'])>=6:raise ValueError('Cannot recruit this NPC.')
            inv=await _locked(con,guild,user)
            if inv['marks']<price:raise ValueError(f'Recruitment requires {price} earned combat marks.')
            reply=npc_recruit(d,npc,role,now)
            inv['marks']-=price;await _save(con,guild,user,inv)
        elif op=='npc_advance':
            if npc not in NPCS:raise ValueError('Choose a recruited NPC.')
            reply=npc_advance(d,npc)
        elif op=='gift':
            member=next((m for m in d['npcs'] if m['id']==npc),None)
            if member is None:raise ValueError('Recruit the NPC before sending a gift.')
            if member['bond']>=5:raise ValueError('Relationship is at its current maximum bond 5.')
            if member.get('last_gift') and now-dt(member['last_gift'])<timedelta(hours=8):
                raise ValueError('This household member is still reflecting on your last gift (8-hour cooldown).')
            inv=await _locked(con,guild,user)
            if inv['marks']<5:raise ValueError('A meaningful gift costs 5 earned combat marks.')
            inv['marks']-=5;await _save(con,guild,user,inv)
            member['bond']+=1;member['last_gift']=iso(now)
            d['house_xp']+=2
            reply=f'{NPCS[npc][0]} appreciates the thoughtful gift. Bond Level {member["bond"]}/5, household renown +2.'
        elif op=='task':
            if not any(m['role'] in ('retainer','disciple') for m in d['npcs']):
                raise ValueError('Recruit an NPC retainer or disciple first.')
            if d['last_task_at'] and now-dt(d['last_task_at'])<timedelta(hours=4):
                raise ValueError('Household assignment is recovering. Try after four hours.')
            inv=await _locked(con,guild,user)
            inv['marks']+=4
            await _save(con,guild,user,inv)
            d['house_xp']+=1;d['last_task_at']=iso(now)
            reply='Your household completed a voluntary gathering assignment: +4 earned combat marks and +1 household renown.'
        elif op in ('npc_dual','solo'):
            if op=='npc_dual' and not any(m['role'] in ('spouse','consort') for m in d['npcs']):
                raise ValueError('Recruit an NPC spouse or consort, or use /solo_meditation instead.')
            can_dual(d,now)
            character=await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,user)
            c=json.loads(character['state']) if isinstance(character['state'],str) else dict(character['state'])
            root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild,user)
            await repo.settle_character(con,c,now,guild,user,root)
            dual_boon(c,now)
            await con.execute('UPDATE characters SET state=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,user,json.dumps(c))
            d['last_dual_at']=iso(now)
            reply=('NPC dual cultivation' if op=='npc_dual' else 'Solo Dao meditation')+' successful: +25% Qi for 60 minutes. All offline time counts.'
        elif op=='propose':
            if not isinstance(target,int) or target<=0 or target==user:raise ValueError('Choose another player.')
            if role not in ('spouse','consort'):raise ValueError('Partnership role must be spouse or consort.')
            exists=await con.fetchval('SELECT 1 FROM characters WHERE guild_id=$1 AND user_id=$2',guild,target)
            if not exists:raise ValueError('The other player must create a character with /start first.')
            active=await con.fetchval("SELECT 1 FROM dao_player_bonds WHERE guild_id=$1 AND status='active' AND (proposer=$2 OR recipient=$2)",guild,user)
            if active:raise ValueError('End the current player partnership before proposing another.')
            await con.execute('INSERT INTO dao_player_bonds(guild_id,proposer,recipient,role,status) VALUES($1,$2,$3,$4,$5) ON CONFLICT (guild_id,proposer,recipient) DO UPDATE SET role=EXCLUDED.role,status=EXCLUDED.status,changed_at=NOW()',guild,user,target,role,'pending')
            reply=f'Partnership invitation sent to <@{target}> ({role}). They must explicitly accept via /partner_accept.'
        elif op in ('accept','decline'):
            if not isinstance(target,int) or target==user:raise ValueError('Select the player who invited you.')
            invite=await con.fetchrow('SELECT role,status,created_at FROM dao_player_bonds WHERE guild_id=$1 AND proposer=$2 AND recipient=$3 FOR UPDATE',guild,target,user)
            if not invite or invite['status']!='pending':raise ValueError('No pending invitation from that player.')
            if now-invite['created_at']>timedelta(days=7):raise ValueError('Invitation expired after seven days.')
            if op=='accept':
                other=await con.fetchval("SELECT 1 FROM dao_player_bonds WHERE guild_id=$1 AND status='active' AND (proposer IN ($2,$3) OR recipient IN ($2,$3))",guild,user,target)
                if other:raise ValueError('One of you already has an active player partnership.')
            status='active' if op=='accept' else 'declined'
            await con.execute('UPDATE dao_player_bonds SET status=$4,changed_at=NOW() WHERE guild_id=$1 AND proposer=$2 AND recipient=$3',guild,target,user,status)
            reply=f'Partnership {status}. Both players retain the right to end the partnership.'
        elif op=='end':
            if not isinstance(target,int) or target==user:raise ValueError('Select your partner.')
            row=await con.fetchrow("SELECT proposer,recipient FROM dao_player_bonds WHERE guild_id=$1 AND status='active' AND ((proposer=$2 AND recipient=$3) OR (proposer=$3 AND recipient=$2)) FOR UPDATE",guild,user,target)
            if not row:raise ValueError('No active partnership with that player.')
            await con.execute("UPDATE dao_player_bonds SET status='ended',changed_at=NOW() WHERE guild_id=$1 AND proposer=$2 AND recipient=$3",guild,row['proposer'],row['recipient'])
            reply='Partnership ended. Both players retain their characters, equipment and earned cultivation progress.'
        elif op=='dual_offer':
            if not isinstance(target,int) or target==user:raise ValueError('Select your established partner.')
            link=await con.fetchval("SELECT 1 FROM dao_player_bonds WHERE guild_id=$1 AND status='active' AND ((proposer=$2 AND recipient=$3) OR (proposer=$3 AND recipient=$2))",guild,user,target)
            if not link:raise ValueError('Only consenting active player partners can request dual cultivation.')
            can_dual(d,now)
            await con.execute('INSERT INTO dao_dual_requests(guild_id,proposer,recipient,status,expires_at) VALUES($1,$2,$3,$4,$5) ON CONFLICT(guild_id,proposer,recipient) DO UPDATE SET status=EXCLUDED.status,expires_at=EXCLUDED.expires_at',guild,user,target,'pending',now+timedelta(hours=1))
            reply=f'Dual cultivation request sent to <@{target}>. Nothing will activate unless they accept.'
        elif op=='dual_accept':
            if not isinstance(target,int) or target==user:raise ValueError('Select your requesting partner.')
            invitation=await con.fetchrow('SELECT status,expires_at FROM dao_dual_requests WHERE guild_id=$1 AND proposer=$2 AND recipient=$3 FOR UPDATE',guild,target,user)
            if not invitation or invitation['status']!='pending' or invitation['expires_at']<now:raise ValueError('No valid dual cultivation invitation.')
            link=await con.fetchval("SELECT 1 FROM dao_player_bonds WHERE guild_id=$1 AND status='active' AND ((proposer=$2 AND recipient=$3) OR (proposer=$3 AND recipient=$2))",guild,user,target)
            if not link:raise ValueError('The partnership is no longer active.')
            # Serialize both characters in stable order to avoid deadlocks.
            await con.execute('INSERT INTO dao_households (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING',guild,target)
            partner_data=await con.fetchval('SELECT data FROM dao_households WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,target)
            partner_d=normal(partner_data)
            can_dual(d,now);can_dual(partner_d,now)
            for uid in sorted((user,target)):
                row=await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,uid)
                if not row:raise ValueError('Both players need active characters.')
                c=json.loads(row['state']) if isinstance(row['state'],str) else dict(row['state'])
                root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild,uid)
                await repo.settle_character(con,c,now,guild,uid,root);dual_boon(c,now)
                await con.execute('UPDATE characters SET state=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,uid,json.dumps(c))
            partner_d['last_dual_at']=iso(now);d['last_dual_at']=iso(now)
            await con.execute('UPDATE dao_households SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,target,json.dumps(partner_d))
            await con.execute('UPDATE dao_dual_requests SET status=$4 WHERE guild_id=$1 AND proposer=$2 AND recipient=$3',guild,target,user,'accepted')
            reply='Both players agreed: Harmonious Dual Cultivation gives each +25% Qi for one hour, including offline time.'
        else:raise ValueError('Unknown household action.')
        await con.execute('UPDATE dao_households SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,user,json.dumps(d))
        await con.execute('INSERT INTO dao_household_actions VALUES($1,$2,$3,$4)',guild,user,event_id,reply)
        return reply
