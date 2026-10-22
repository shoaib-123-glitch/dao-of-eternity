"""Developer-only Golden Core testing in a separate table, never touches characters."""
import json
from datetime import datetime,timezone
from game import default_state
from golden_core_v022 import start,choose,advance,capacity

SCHEMA='''
CREATE TABLE IF NOT EXISTS golden_sandboxes_v022(
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,state JSONB NOT NULL,
 PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS golden_sandbox_actions_v022(
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,
 response TEXT NOT NULL,PRIMARY KEY(guild_id,user_id,event_id));
'''
async def setup(repo):
    async with repo.pool.acquire() as con:await con.execute(SCHEMA)

async def run(repo,guild,user,event_id,op,choice=None):
    async with repo.pool.acquire() as con:
      async with con.transaction():
        prior=await con.fetchval('SELECT response FROM golden_sandbox_actions_v022 WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild,user,event_id)
        if prior is not None:return prior
        if op=='reset':
            s=default_state(datetime.now(timezone.utc))
            s['player']['advanced_to_foundation']=True
            s['foundation_stage']=9;s['foundation_peak']=True
            s['dao_comprehension']=75
            s['eternal']={'last_at':datetime.now(timezone.utc).isoformat(),'remainder':0,'bank_qi':2000,'total_earned':2000,'scale_version':2}
            await con.execute('INSERT INTO golden_sandboxes_v022 VALUES($1,$2,$3::jsonb) ON CONFLICT (guild_id,user_id) DO UPDATE SET state=EXCLUDED.state',guild,user,json.dumps(s))
            reply='[SANDBOX] Separate Foundation Stage 9 peak character created. Use /dev_golden_trial to begin. Your real character is untouched.'
        else:
            row=await con.fetchrow('SELECT state FROM golden_sandboxes_v022 WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild,user)
            if not row:raise ValueError('Run /dev_golden_prepare first.')
            s=json.loads(row['state']) if isinstance(row['state'],str) else dict(row['state'])
            if op=='trial':reply=start(s)
            elif op=='choice':reply=choose(s,choice)
            elif op=='advance':
                if not s.get('golden_core'):raise ValueError('Finish the ritual first.')
                s['golden_qi']=capacity(s['golden_stage'])
                reply=advance(s)
            else:raise ValueError('Unknown sandbox operation.')
            await con.execute('UPDATE golden_sandboxes_v022 SET state=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild,user,json.dumps(s))
        await con.execute('INSERT INTO golden_sandbox_actions_v022 VALUES($1,$2,$3,$4)',guild,user,event_id,reply)
        return reply
