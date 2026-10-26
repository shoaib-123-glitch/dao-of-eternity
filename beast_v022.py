"""Spirit-beast cultivation with lazy, permanent Qi accrual and manual evolution."""
from datetime import datetime, timezone, timedelta
from living_dao import decode, COMPANIONS
import json

BEAST_RATE=18  # spirit Qi/hour, independently earned from the owner's Qi

def cap(stage): return 80+40*(stage-1)

def initial_cultivation(now):
    return {'stage':1,'qi':0,'bank':0,'earned':0,'remainder':0,'evolutions':0,'last_at':now.isoformat()}

def settle(state,now):
    s=decode(state)
    if not s.get('companion'):return s
    b=dict(s.get('beast_cultivation') or initial_cultivation(now))
    before=datetime.fromisoformat(b['last_at'])
    before=(before.replace(tzinfo=timezone.utc) if before.tzinfo is None else before.astimezone(timezone.utc))
    current=(now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc))
    elapsed=max(0,int((current-before).total_seconds()))
    if elapsed:
        amount=b['remainder']+elapsed*BEAST_RATE
        earned,b['remainder']=divmod(amount,3600)
        b['bank']+=earned;b['earned']+=earned
        # Retain fractional seconds; repeated dashboard visits must earn the
        # same Qi as a single settlement over the same elapsed time.
        b['last_at']=(before+timedelta(seconds=elapsed)).isoformat()
    if b['stage']<=9:
        moved=min(b['bank'],max(0,cap(b['stage'])-b['qi']))
        b['qi']+=moved;b['bank']-=moved
    s['beast_cultivation']=b
    return s

def advance(s):
    b=s.get('beast_cultivation')
    if not s.get('companion'):raise ValueError('Adopt a spirit companion first.')
    if not b:raise ValueError('Check /beast_cultivation to activate beast cultivation.')
    if b['qi']<cap(b['stage']):raise ValueError(f'Spirit beast needs {cap(b["stage"])} Qi to advance; currently {b["qi"]}.')
    if b['stage']==9:raise ValueError('Spirit beast at Stage 9. Use /beast_evolve.')
    b['stage']+=1;b['qi']=0
    moved=min(b['bank'],cap(b['stage']));b['qi']+=moved;b['bank']-=moved
    return f'{COMPANIONS[s["companion"]][0]} advanced to Spirit Beast Stage {b["stage"]}/9!'

def evolve(s):
    b=s.get('beast_cultivation')
    if not b or b['stage']!=9 or b['qi']<cap(9):raise ValueError('Beast evolution requires Spirit Beast Stage 9 with full Qi.')
    if b['evolutions']>=2:raise ValueError('The beast has reached its highest available evolution.')
    b['stage']=1;b['qi']=0;b['evolutions']+=1
    moved=min(b['bank'],cap(1));b['qi']+=moved;b['bank']-=moved
    return f'{COMPANIONS[s["companion"]][0]} evolved! Evolution Rank {b["evolutions"]}. New passive growth begins immediately.'

def label(s):
    if not s.get('companion'):return 'No bonded spirit beast. Use /adopt.'
    b=s.get('beast_cultivation') or {}
    return (f'{COMPANIONS[s["companion"]][0]} | Bond {s["bond"]}/5 | Evolution {b.get("evolutions",0)}\n'
            f'Spirit Beast Stage {b.get("stage",1)}/9 | {b.get("qi",0)}/{cap(b.get("stage",1))} Beast Qi | Bank {b.get("bank",0)}\n'
            f'Passive beast cultivation: {BEAST_RATE} Qi/hour, never expires. Advance with /beast_advance. Evolution requires Stage 9.')

async def view(repo,guild,user):
    from datetime import datetime,timezone
    async with repo.pool.acquire() as con:
      async with con.transaction():
        row=await con.fetchrow('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,user)
        if not row:return 'Use /start, then /adopt a spirit beast.'
        s=settle(row['data'],datetime.now(timezone.utc))
        await con.execute('UPDATE living_dao_players SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,user,json.dumps(s))
        return label(s)

async def action(repo,guild,user,event_id,op,argument=None):
    from datetime import datetime,timezone
    async with repo.pool.acquire() as con:
      async with con.transaction():
        row=await con.fetchrow('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,user)
        if not row:raise ValueError('Adopt a spirit beast first.')
        prior=await con.fetchval('SELECT response FROM living_dao_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild,user,event_id)
        if prior is not None:return prior
        now=datetime.now(timezone.utc)
        s=settle(row['data'],now)
        if op=='advance':reply=advance(s)
        elif op=='evolve':reply=evolve(s)
        elif op=='bond':
            from secrets import randbelow
            from companion_bonds_v024 import bond_action
            s,reply=bond_action(s,argument,now,randbelow(1000))
            s=settle(s,now)
        else:raise ValueError('Invalid spirit beast action.')
        await con.execute('UPDATE living_dao_players SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,user,json.dumps(s))
        await con.execute('INSERT INTO living_dao_actions VALUES($1,$2,$3,$4)',guild,user,event_id,reply)
        return reply
