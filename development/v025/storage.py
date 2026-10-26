"""PostgreSQL repository. Row locks and interaction IDs make mutations serial and idempotent."""
import json
from secrets import randbelow
from datetime import datetime, timezone
from eternal import settle
import asyncpg
from game import default_state, apply

SCHEMA = '''
CREATE TABLE IF NOT EXISTS qi_event_settings (guild_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS qi_event_announcements (guild_id BIGINT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY(guild_id,event_id));
CREATE TABLE IF NOT EXISTS fast_sandboxes (guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, state JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (guild_id,user_id));
CREATE TABLE IF NOT EXISTS fast_sandbox_actions (guild_id BIGINT NOT NULL,user_id BIGINT NOT NULL,event_id BIGINT NOT NULL,response TEXT NOT NULL,PRIMARY KEY (guild_id,user_id,event_id));
CREATE TABLE IF NOT EXISTS characters (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 state JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS trial_sandboxes (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 state JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS sandbox_actions (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 event_id BIGINT NOT NULL, response TEXT NOT NULL,
 PRIMARY KEY (guild_id, user_id, event_id)
);
CREATE TABLE IF NOT EXISTS trial_sandboxes (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 state JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS sandbox_actions (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, event_id BIGINT NOT NULL,
 response TEXT NOT NULL, PRIMARY KEY (guild_id, user_id, event_id)
);
CREATE TABLE IF NOT EXISTS character_origins (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 root TEXT NOT NULL, background TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS processed_actions (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 event_id BIGINT NOT NULL,
 response TEXT NOT NULL,
 PRIMARY KEY (guild_id, user_id, event_id)
);
'''

class Repository:
    def __init__(self, pool):
        self.pool = pool

    @classmethod
    async def connect(cls, dsn):
        from contextlib import nullcontext
        from private_playtest import PrivatePlaytestConfig, create_private_pool, startup_stage, StartupStage
        if isinstance(dsn, PrivatePlaytestConfig):
            pool = await create_private_pool(dsn)
        else:
            pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
        try:
            with startup_stage(StartupStage.DATABASE_CORE_SCHEMA) if isinstance(dsn, PrivatePlaytestConfig) else nullcontext():
                async with pool.acquire() as conn:
                    await conn.execute(SCHEMA)
        except BaseException:
            # No repository was returned; do not leave a failed startup pool alive.
            pool.terminate()
            raise
        return cls(pool)

    async def settle_character(self, con, state, now, guild_id, user_id, root):
        from heavens import dt
        from sect_v022 import gathering_windows
        previous=dt(state['eternal']['last_at']) if state.get('eternal') else now
        windows=await gathering_windows(con,guild_id,user_id,previous,now)
        settle(state,now,guild_id,root,sect_windows=windows)
        state['sect_qi_level']=max((lv for left,right,lv in windows if left<=now<=right),default=0)
        # The current hall level also matters for /qi when the settlement period is zero.
        try:
            from sect_v022 import member
            sect,_=await member(con,guild_id,user_id)
            lv=await con.fetchval('SELECT level FROM sect_gathering_history_v022 WHERE guild_id=$1 AND sect_name=$2 ORDER BY effective_at DESC LIMIT 1',guild_id,sect)
            if lv is not None:state['sect_qi_level']=max(0,int(lv))
        except ValueError:pass

    async def close(self):
        await self.pool.close()

    async def start(self, guild_id, user_id, event_id, now):
        async with self.pool.acquire() as con:
            async with con.transaction():
                prior = await con.fetchval('SELECT response FROM processed_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3', guild_id,user_id,event_id)
                if prior is not None:
                    return prior
                await con.execute('INSERT INTO characters (guild_id,user_id,state) VALUES ($1,$2,$3::jsonb) ON CONFLICT DO NOTHING', guild_id,user_id,json.dumps(default_state(now)))
                # Lock the inserted/existing row so concurrent /start events serialize.
                row = await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE', guild_id,user_id)
                state = json.loads(row['state']) if isinstance(row['state'], str) else dict(row['state'])
                root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2', guild_id,user_id)
                await self.settle_character(con,state,now,guild_id,user_id,root)
                await con.execute('UPDATE characters SET state=$3::jsonb WHERE guild_id=$1 AND user_id=$2', guild_id,user_id,json.dumps(state))
                prior = await con.fetchval('SELECT response FROM processed_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3', guild_id,user_id,event_id)
                if prior is not None:
                    return prior
                from onboarding_v024 import next_step
                reply = ('**Welcome to The Living Dao.** Your character is ready; permanent passive Qi is active, even offline.\n'
                         +next_step(state,root)+'\nFirst adventure: /hunt → /recover → /recipes → /craft → /equip → /adopt.\n'
                         'Use /qi for your dashboard and /guide whenever you need a next step. Existing progress is preserved.')
                await con.execute('INSERT INTO processed_actions VALUES ($1,$2,$3,$4)', guild_id,user_id,event_id,reply)
                return reply

    async def execute(self, guild_id, user_id, event_id, op, now, **kwargs):
        async with self.pool.acquire() as con:
            async with con.transaction():
                # Serialize per character. Dup interaction checks must be inside lock.
                row = await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE', guild_id,user_id)
                if row is None:
                    return 'Start by using /start.'
                prior = await con.fetchval('SELECT response FROM processed_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild_id,user_id,event_id)
                if prior is not None:
                    return prior
                current = json.loads(row['state']) if isinstance(row['state'], str) else dict(row['state'])
                root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                await self.settle_character(con,current,now,guild_id,user_id,root)
                if op in ('cultivation_status','omen_seek','cultivation_focus','cultivation_practice','journey'):
                    kwargs['root']=root; kwargs['guild_id']=guild_id
                if op=='omen_seek': kwargs['roll']=randbelow(1000)
                if op=='encounter_choice': kwargs['omen_roll']=randbelow(1000)
                if op=='cultivation_practice': kwargs['roll']=randbelow(1000)
                changed, reply = apply(current,op,now,**kwargs)
                if op == 'cultivation_practice':
                    # Character -> companion lock order; no inventory/household lock.
                    # The character replay check above covers both rewards atomically.
                    living = await con.fetchrow('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
                    if living is not None:
                        from beast_v022 import settle as settle_beast
                        companion = settle_beast(living['data'], now)
                        from goals_v025 import record
                        companion = record(companion, 'practice', now)
                        if companion.get('companion'):
                            beast = companion['beast_cultivation']
                            beast['bank'] += 6
                            beast['earned'] += 6
                            beast['practice_earned'] = beast.get('practice_earned', 0) + 6
                            companion = settle_beast(companion, now)
                            reply += '\nYour spirit beast cultivated alongside you: +6 Beast Qi.'
                        await con.execute('UPDATE living_dao_players SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(companion))
                await con.execute('UPDATE characters SET state=$3::jsonb, updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(changed))
                await con.execute('INSERT INTO processed_actions VALUES ($1,$2,$3,$4)', guild_id,user_id,event_id,reply)
                return reply

    async def get_state(self,guild_id,user_id):
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE', guild_id,user_id)
                if row is None:
                    return None
                state = json.loads(row['state']) if isinstance(row['state'],str) else dict(row['state'])
                root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                await self.settle_character(con,state,datetime.now(timezone.utc),guild_id,user_id,root)
                await con.execute('UPDATE characters SET state=$3::jsonb WHERE guild_id=$1 AND user_id=$2', guild_id,user_id,json.dumps(state))
                return state

    async def sandbox_reset(self, guild_id, user_id, now):
        """Create a disposable test character; never touch characters."""
        state = default_state(now)
        state['player']['stage'] = 9
        state['player']['qi'] = 600
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    'INSERT INTO trial_sandboxes (guild_id,user_id,state) '
                    'VALUES ($1,$2,$3::jsonb) ON CONFLICT (guild_id,user_id) '
                    'DO UPDATE SET state=EXCLUDED.state,updated_at=NOW()',
                    guild_id, user_id, json.dumps(state))
        return 'Sandbox ready: Qi Refining Stage 9, 600/600 Qi. Your real character is unchanged.'

    async def sandbox_get_state(self, guild_id, user_id):
        async with self.pool.acquire() as con:
            raw = await con.fetchval(
                'SELECT state FROM trial_sandboxes WHERE guild_id=$1 AND user_id=$2',
                guild_id, user_id)
            return (json.loads(raw) if isinstance(raw, str) else dict(raw)) if raw is not None else None

    async def sandbox_execute(self, guild_id, user_id, event_id, op, now, **kwargs):
        """Serialize test interactions and record their results independently."""
        if op not in ('trial_start', 'trial_choice', 'trial_abandon', 'profile'):
            raise ValueError('Operation is not available in sandbox mode.')
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    'SELECT state FROM trial_sandboxes WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',
                    guild_id, user_id)
                if row is None:
                    raise ValueError('Use /dev_trial_reset to create a test character first.')
                prior = await con.fetchval(
                    'SELECT response FROM sandbox_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',
                    guild_id,user_id,event_id)
                if prior is not None:
                    return prior
                current = json.loads(row['state']) if isinstance(row['state'], str) else dict(row['state'])
                changed, reply = apply(current, op, now, **kwargs)
                if op != 'profile':
                    await con.execute(
                        'UPDATE trial_sandboxes SET state=$3::jsonb, updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',
                        guild_id,user_id,json.dumps(changed))
                await con.execute(
                    'INSERT INTO sandbox_actions VALUES ($1,$2,$3,$4)',
                    guild_id,user_id,event_id,reply)
                return reply

    async def sandbox_reset(self, guild_id, user_id, now):
        state = default_state(now)
        state['player']['stage'] = 9
        state['player']['qi'] = 600
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    'INSERT INTO trial_sandboxes (guild_id,user_id,state) VALUES ($1,$2,$3::jsonb) '
                    'ON CONFLICT (guild_id,user_id) DO UPDATE SET state=EXCLUDED.state,updated_at=NOW()',
                    guild_id,user_id,json.dumps(state))
        return '[SANDBOX] Ready: Stage 9 with 600 Qi. Your real character remains unchanged.'

    async def sandbox_get_state(self, guild_id, user_id):
        async with self.pool.acquire() as con:
            raw=await con.fetchval('SELECT state FROM trial_sandboxes WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
            return (json.loads(raw) if isinstance(raw,str) else dict(raw)) if raw is not None else None

    async def sandbox_execute(self, guild_id, user_id, event_id, op, now, **kwargs):
        if op not in ('trial_start','trial_choice','trial_abandon','profile'):
            raise ValueError('Not an available sandbox action.')
        async with self.pool.acquire() as con:
            async with con.transaction():
                row=await con.fetchrow('SELECT state FROM trial_sandboxes WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
                if row is None:
                    raise ValueError('Run /dev_trial_reset first.')
                prior=await con.fetchval('SELECT response FROM sandbox_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild_id,user_id,event_id)
                if prior is not None:
                    return prior
                current=json.loads(row['state']) if isinstance(row['state'],str) else dict(row['state'])
                changed, reply=apply(current,op,now,**kwargs)
                if op!='profile':
                    await con.execute('UPDATE trial_sandboxes SET state=$3::jsonb,updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(changed))
                await con.execute('INSERT INTO sandbox_actions VALUES ($1,$2,$3,$4)',guild_id,user_id,event_id,reply)
                return reply

    async def awaken(self, guild_id, user_id, root, background):
        """Exactly-once permanent awakening, separate from existing character state."""
        from awakening import ROOTS, BACKGROUNDS
        if root not in ROOTS or background not in BACKGROUNDS:
            raise ValueError('Invalid root or background.')
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE', guild_id, user_id)
                exists = await con.fetchval('SELECT 1 FROM characters WHERE guild_id=$1 AND user_id=$2', guild_id, user_id)
                if not exists:
                    return 'Use /start first.'
                result = await con.execute('INSERT INTO character_origins (guild_id,user_id,root,background) VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING', guild_id,user_id,root,background)
                if result == 'INSERT 0 0':
                    return 'Already awakened. Your spiritual root and background are permanent.'
                return f'Awakening complete! Root: {root}. Origin: {background}. Your existing realm and resources are unchanged.'

    async def get_origin(self, guild_id, user_id):
        async with self.pool.acquire() as con:
            row = await con.fetchrow('SELECT root,background FROM character_origins WHERE guild_id=$1 AND user_id=$2', guild_id,user_id)
            return dict(row) if row else None

    async def fast_reset(self, guild_id, user_id, now):
        state = default_state(now)
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute('INSERT INTO fast_sandboxes (guild_id,user_id,state) VALUES ($1,$2,$3::jsonb) ON CONFLICT (guild_id,user_id) DO UPDATE SET state=EXCLUDED.state, updated_at=NOW()', guild_id,user_id,json.dumps(state))
        return '[FAST SANDBOX] Stage 1 ready. Your real character is unchanged.'

    async def fast_get_state(self, guild_id, user_id):
        async with self.pool.acquire() as con:
            row = await con.fetchval('SELECT state FROM fast_sandboxes WHERE guild_id=$1 AND user_id=$2', guild_id,user_id)
            return (json.loads(row) if isinstance(row,str) else dict(row)) if row is not None else None

    async def fast_execute(self, guild_id, user_id, event_id, op, now, **kwargs):
        if op not in ('rapid_cultivate', 'minor'):
            raise ValueError('Not a fast sandbox operation.')
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow('SELECT state FROM fast_sandboxes WHERE guild_id=$1 AND user_id=$2 FOR UPDATE', guild_id,user_id)
                if row is None:
                    raise ValueError('Use /dev_fast_reset first.')
                prior = await con.fetchval('SELECT response FROM fast_sandbox_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild_id,user_id,event_id)
                if prior is not None:
                    return prior
                current=json.loads(row['state']) if isinstance(row['state'],str) else dict(row['state'])
                changed,reply=apply(current,op,now,**kwargs)
                await con.execute('UPDATE fast_sandboxes SET state=$3::jsonb,updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(changed))
                await con.execute('INSERT INTO fast_sandbox_actions VALUES ($1,$2,$3,$4)',guild_id,user_id,event_id,reply)
                return reply

    async def choose_root_lottery(self,guild_id,user_id):
        """Once-only, free 3% rare root alternative to ordinary /awaken."""
        from awakening import ROOTS, BACKGROUNDS, RARE_ROOTS
        async with self.pool.acquire() as con:
            async with con.transaction():
                row=await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
                if row is None: raise ValueError('Use /start first.')
                exists=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                if exists:return f'Your permanent root is already {exists}. You cannot reroll it.'
                rare=randbelow(1000)<30
                pool=tuple(RARE_ROOTS if rare else ROOTS)
                root=pool[randbelow(len(pool))]
                await con.execute('INSERT INTO character_origins (guild_id,user_id,root,background) VALUES($1,$2,$3,$4)',guild_id,user_id,root,'Wandering Mortal')
                return f'Your free awakening lottery reveals: {root} Root ({"rare" if rare else "common"}). Origin: Wandering Mortal. This choice is permanent.'

    async def brew_elixir(self,guild_id,user_id,event_id,elixir,now):
        """Atomically pay F2P Spirit Stones and queue exactly one potion."""
        import inventory
        from heavens import ELIXIRS,queue_elixir
        if elixir not in ELIXIRS:raise ValueError('Unknown elixir.')
        async with self.pool.acquire() as con:
            async with con.transaction():
                row=await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
                if row is None:raise ValueError('Use /start first.')
                prior=await con.fetchval('SELECT response FROM processed_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild_id,user_id,event_id)
                if prior is not None:return prior
                current=json.loads(row['state']) if isinstance(row['state'],str) else dict(row['state'])
                root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                await self.settle_character(con,current,now,guild_id,user_id,root)
                stock=await inventory._locked(con,guild_id,user_id)
                name,boost,duration,cost=ELIXIRS[elixir]
                if stock['marks']<cost:raise ValueError(f'{name} costs {cost} Spirit Stones; you have {stock["marks"]}.')
                potion=queue_elixir(current,elixir,now)
                stock['marks']-=cost
                await inventory._save(con,guild_id,user_id,stock)
                await con.execute('UPDATE characters SET state=$3::jsonb,updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(current))
                reply=f'{name} queued! {boost/1000:g}x for {duration} minutes, activates {potion["start_at"]} UTC. Remaining Spirit Stones: {stock["marks"]}.'
                await con.execute('INSERT INTO processed_actions VALUES ($1,$2,$3,$4)',guild_id,user_id,event_id,reply)
                return reply

    async def set_world_channel(self,guild_id,channel_id):
        async with self.pool.acquire() as con:
            await con.execute('INSERT INTO qi_event_settings (guild_id,channel_id) VALUES($1,$2) ON CONFLICT (guild_id) DO UPDATE SET channel_id=excluded.channel_id',guild_id,channel_id)

    async def get_world_channel(self,guild_id):
        async with self.pool.acquire() as con:
            return await con.fetchval('SELECT channel_id FROM qi_event_settings WHERE guild_id=$1',guild_id)

    async def mark_world_announcement(self,guild_id,event_id):
        async with self.pool.acquire() as con:
            value=await con.fetchval('INSERT INTO qi_event_announcements (guild_id,event_id) VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING event_id',guild_id,event_id)
            return value is not None

    async def unmark_world_announcement(self,guild_id,event_id):
        async with self.pool.acquire() as con:
            await con.execute('DELETE FROM qi_event_announcements WHERE guild_id=$1 AND event_id=$2',guild_id,event_id)

    async def choose_dual_root(self,guild_id,user_id,secondary):
        """One-time F2P dual root unlocked from existing dragon rewards. Atomic."""
        import inventory
        from awakening import ROOTS
        if secondary not in ROOTS:raise ValueError('Secondary root must be one of eight common elements.')
        async with self.pool.acquire() as con:
            async with con.transaction():
                row=await con.fetchrow('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
                if row is None:raise ValueError('Use /start first.')
                state=json.loads(row['state']) if isinstance(row['state'],str) else dict(row['state'])
                if state.get('secondary_root'):raise ValueError('Secondary root is already permanently attuned.')
                primary=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                if not primary:raise ValueError('Awaken a primary root first.')
                if primary==secondary:raise ValueError('Choose a different root from your existing primary root.')
                stock=await inventory._locked(con,guild_id,user_id)
                if stock['marks']<80 or stock['holdings'].get('dragon_scale',0)<1 or stock['holdings'].get('heavenly_spark',0)<1:
                    raise ValueError('Dual Root attunement requires 80 Spirit Stones, 1 Thunder Dragon Scale and 1 Heavenly Spark.')
                root=await con.fetchval('SELECT root FROM character_origins WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
                await self.settle_character(con,state,datetime.now(timezone.utc),guild_id,user_id,root)
                stock['marks']-=80
                stock['holdings']['dragon_scale']-=1
                stock['holdings']['heavenly_spark']-=1
                state['secondary_root']=secondary
                await inventory._save(con,guild_id,user_id,stock)
                await con.execute('UPDATE characters SET state=$3::jsonb,updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(state))
                return f'Dual Root awakened: {primary} + {secondary}! The old root, realm, mastery, gear and banked Qi are preserved. Secondary Art is now available.'
