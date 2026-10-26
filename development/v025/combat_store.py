"""Combat persistence uses its own table, never mutating cultivation character rows."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from combat import begin,turn,auto,assisted,display,ENEMIES
from combat_journey_v024 import training_bonuses, prepare, finish, recover
import inventory

SCHEMA='''CREATE TABLE IF NOT EXISTS combat_sessions (
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,
 battle JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (guild_id,user_id));
CREATE TABLE IF NOT EXISTS combat_actions (
 guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,
 response TEXT NOT NULL,PRIMARY KEY (guild_id,user_id,event_id));'''

async def setup(repo):
    async with repo.pool.acquire() as con: await con.execute(SCHEMA)
    await inventory.setup(repo)

async def current(repo,guild_id,user_id):
    async with repo.pool.acquire() as con:
        raw=await con.fetchval('SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
        return (json.loads(raw) if isinstance(raw,str) else dict(raw)) if raw is not None else None

async def execute(repo,guild_id,user_id,event_id,op,**kwargs):
    async with repo.pool.acquire() as con:
        async with con.transaction():
            exists=await con.fetchval('SELECT 1 FROM characters WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
            if not exists: raise ValueError('Create your cultivator with /start first.')
            # Create a dedicated combat row first so start/turn/resume cannot race.
            await con.execute('INSERT INTO combat_sessions (guild_id,user_id,battle) VALUES ($1,$2,\'{}\'::jsonb) ON CONFLICT DO NOTHING',guild_id,user_id)
            row=await con.fetchrow('SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
            prior=await con.fetchval('SELECT response FROM combat_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild_id,user_id,event_id)
            if prior is not None: return prior
            b=json.loads(row['battle']) if isinstance(row['battle'],str) else dict(row['battle'])
            previous=deepcopy(b)
            now=datetime.now(timezone.utc)
            reply_override=None
            if kwargs.get('expected_battle_id') is not None and kwargs['expected_battle_id']!=b.get('battle_id','legacy'):
                raise ValueError('That battle panel belongs to an earlier hunt. Open /battle_status for your current fight.')
            row_is_victory=b.get('status')=='victory'
            if op=='start':
                if b.get('status')=='active': raise ValueError('Finish your current battle first. Use /battle_status.')
                enemy=kwargs.get('enemy','spirit_wolf')
                if enemy not in ENEMIES: raise ValueError('Unknown spirit beast.')
                root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                # Companion actions lock Living Dao before inventory. Use the same
                # order and keep the snapshot locked through beast settlement.
                raw_living=await con.fetchval('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
                gear=inventory.bonuses(await inventory._locked(con,guild_id,user_id))
                attribution={'weapon':int(gear.get('attack',0)),'companion':0,'sect':0}
                # Companion/sect benefits are applied only at battle start, keeping turns deterministic.
                if raw_living:
                    from living_dao import decode
                    living=decode(raw_living)
                    beast=living.get('companion')
                    bond=max(1,int(living.get('bond',1)))
                    if beast:
                        from beast_v022 import settle as settle_beast
                        living=settle_beast(living,now)
                        await con.execute('UPDATE living_dao_players SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(living))
                        spirit=living.get('beast_cultivation') or {}
                        gear['beast']=beast;gear['beast_bond']=bond
                        gear['beast_evolutions']=int(spirit.get('evolutions',0))
                        gear['hp']+=int(spirit.get('stage',1))//3
                        from companion_bonds_v024 import attuned
                        gear['beast_attuned']=attuned(living,now)
                    if beast=='storm_hawk':
                        gear['attack']+=1+bond
                        gear['opening_qi']=2+(2 if gear.get('beast_attuned') else 0)
                        attribution['companion']=1+bond
                    elif beast=='stone_tortoise':gear['ward']+=1+bond//2+int(gear.get('beast_attuned',False))
                    elif beast=='jade_fox':gear['hp']+=5+bond*2
                    if living.get('sect'):
                        prosperity=await con.fetchval('SELECT contribution FROM living_dao_sects WHERE guild_id=$1 AND name=$2',guild_id,living['sect']) or 0
                        gear['hp']+=min(15,int(prosperity)//20*3)
                        hall_raw=await con.fetchval('SELECT data FROM sect_hall_state WHERE guild_id=$1 AND sect_name=$2',guild_id,living['sect'])
                        from sect_halls import bonuses as hall_bonuses
                        hall=hall_bonuses(hall_raw)
                        gear['attack']+=hall['attack']
                        gear['hp']+=hall['hp']
                        attribution['sect']=hall['attack']
                raw_char=await con.fetchval('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                character=json.loads(raw_char) if isinstance(raw_char,str) else dict(raw_char)
                training=training_bonuses(character)
                from living_world_v025 import technique
                gear['dao_technique']=technique(living if raw_living else {},character,root)
                gear['hp']+=training['hp'];gear['attack']+=training['attack']
                attribution['realm']=training['attack']
                if character.get('golden_core'):
                    gear['hp']+=20+5*int(character.get('golden_stage',1))
                    gear['attack']+=3+int(character.get('golden_stage',1))
                    attribution['golden_core']=3+int(character.get('golden_stage',1))
                b=begin(enemy,root or 'Unawakened',b.get('mastery',{}),gear,character.get('cultivation_path'),character.get('secondary_root'))
                b=prepare(b,previous,now,kwargs.get('mode','manual'),event_id)
                b['bonus_sources']=attribution
            elif op=='turn':
                if not b.get('status'): raise ValueError('No battle. Use /hunt first.')
                b=turn(b,kwargs['action'])
            elif op=='auto':
                if not b.get('status'): raise ValueError('No battle. Use /hunt first.')
                if b['status']!='active': raise ValueError('Battle already finished. Use /hunt for another.')
                b=auto(b)
            elif op=='assist':
                if not b.get('status'): raise ValueError('No battle. Use /hunt first.')
                b['mode']='assisted'
                b=assisted(b)
            elif op=='retreat':
                if b.get('status')!='active':raise ValueError('No active battle to retreat from. /recover prepares your next hunt.')
                b['status']='retreated';b['last_reward']=0;b['loot']=[]
                b['log'].append('You retreat safely. Cultivation and equipment are retained; no Spirit Stones or loot awarded.')
            elif op=='recover':
                reply_override=recover(b,now)
            else: raise ValueError('Unknown combat operation.')
            extra=0
            if previous.get('status')=='active' and b.get('status')!='active':
                extra=finish(b,previous,now)
            # Grant once on the transition to victory, inside the same transaction as
            # combat session/action persistence. Retried Discord interaction IDs do not duplicate loot.
            if op!='start' and b.get('status')=='victory' and not row_is_victory:
                # Living Dao before inventory, the same lock order as battle start.
                raw_goals=await con.fetchval('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
                if raw_goals is not None:
                    from living_dao import decode
                    from goals_v025 import record
                    await con.execute('UPDATE living_dao_players SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',
                                      guild_id,user_id,json.dumps(record(decode(raw_goals),'hunt_win',now)))
                state=await inventory._locked(con,guild_id,user_id)
                state=inventory.award(state,b['enemy'],b['last_reward']+extra)
                b['first_clear_marks']=extra
                b['total_reward']=b['last_reward']+extra
                drops=', '.join(f'{qty}× {inventory.ITEMS[key]}' for key,qty in inventory.DROPS[b['enemy']].items())
                bonus=f' (includes {extra} first-clear)' if extra else ''
                b['payout_summary']=(f'**Saved: +{b["total_reward"]} Spirit Stones{bonus} · {drops}**\n'
                    f'Spirit Stones balance: {state["marks"]} · /inventory · /guide → Hunts and equipment.')
                await inventory._save(con,guild_id,user_id,state)
            reply=reply_override or display(b,now)
            await con.execute('UPDATE combat_sessions SET battle=$3::jsonb,updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(b))
            await con.execute('INSERT INTO combat_actions VALUES ($1,$2,$3,$4)',guild_id,user_id,event_id,reply)
            return reply
