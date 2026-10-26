"""Real PostgreSQL cases, invoked only by the explicitly enabled runner.

No unittest discovery entry points or connection code. SQL results, handlers,
transactions and locks are real; wrappers only coordinate/fault at query edges.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

import asyncpg

import beast_v022
import bonds_v022
import combat
import combat_store
from game import default_state
import sect_halls
from storage import Repository


HOUSEHOLD_LOCK_SQL = 'SELECT data FROM dao_households WHERE guild_id=$1 AND user_id=$2 FOR UPDATE'
COMBAT_LOCK_SQL = 'SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2 FOR UPDATE'
BLOCKING_SQL = 'SELECT $2::int = ANY(pg_catalog.pg_blocking_pids($1::int))'
SCHEDULE_TIMEOUT = 10
BLOCKING_TIMEOUT = 2


def check(condition, label):
    if not condition:
        raise AssertionError(label)  # Fixed case label only; never catalog/driver data.


def decode(raw):
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


class ObservedConnection:
    """Forward actual calls; hooks cannot substitute returned SQL results."""
    def __init__(self, con, after, *, before=None):
        self.con, self.after, self.before = con, after, before

    def __getattr__(self, name):
        return getattr(self.con, name)

    async def _call(self, method, sql, *args):
        if self.before is not None:
            await self.before(method, sql, args, self.con)
        result = await getattr(self.con, method)(sql, *args)
        if self.after is not None:
            await self.after(method, sql, args, self.con)
        return result

    async def execute(self, sql, *args):
        return await self._call('execute', sql, *args)

    async def fetchrow(self, sql, *args):
        return await self._call('fetchrow', sql, *args)

    async def fetchval(self, sql, *args):
        return await self._call('fetchval', sql, *args)

    async def fetch(self, sql, *args):
        return await self._call('fetch', sql, *args)


class ObservedPool:
    def __init__(self, db, *, acquired=None, after=None, before=None):
        self.db, self.acquired, self.after, self.before = db, acquired, after, before

    @asynccontextmanager
    async def acquire(self):
        async with self.db.acquire() as con:
            if self.acquired is not None:
                await self.acquired(con)
            yield ObservedConnection(con, self.after, before=self.before)


def _require_pending(tasks, completed=()):
    check(all(not task.done() for task in tasks) and not any(event.is_set() for event in completed),
          'REQUEST_FINISHED_BEFORE_LOCK_PROOF')


async def _await_signal(event, tasks):
    """A worker finishing before the required signal is a failure, not a race win."""
    signal = asyncio.create_task(event.wait())
    try:
        await asyncio.wait((signal, *tasks), timeout=SCHEDULE_TIMEOUT,
                           return_when=asyncio.FIRST_COMPLETED)
        _require_pending(tasks)
        check(event.is_set(), 'LOCK_SCHEDULE_INCOMPLETE')
    finally:
        signal.cancel()
        await asyncio.gather(signal, return_exceptions=True)


async def _prove_blocked(db, waiter, holder, tasks, *, completed=(), via=()):
    """Prove a path to the held lock, allowing only known competing sessions.

    PostgreSQL may queue one row-lock waiter behind another waiter. An unrelated
    blocker or a cycle without the independent holder cannot satisfy the proof.
    """
    check(waiter != holder, 'LOCK_PROOF_NEEDS_INDEPENDENT_SESSIONS')
    async with asyncio.timeout(BLOCKING_TIMEOUT):
        async with db.acquire() as observer:
            while True:
                pending, visited = [waiter], set()
                while pending:
                    current = pending.pop()
                    if current in visited:
                        continue
                    visited.add(current)
                    for blocker in (holder, *via):
                        if blocker in visited:
                            continue
                        _require_pending(tasks, completed)
                        blocked = await observer.fetchval(BLOCKING_SQL, current, blocker)
                        _require_pending(tasks, completed)
                        if blocked is True:
                            if blocker == holder:
                                return
                            pending.append(blocker)
                await asyncio.sleep(0.01)  # Poll actual lock state; sleep is not evidence.


async def _blocked_pair(db, first_job, second_job, lock_match, *,
                        first_after=None, second_after=None):
    held, release, second_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pids = {}

    async def hold(method, sql, args, con):
        if first_after is not None:
            await first_after(method, sql, args, con)
        if not held.is_set() and lock_match(method, sql, args):
            pids['holder'] = con.get_server_pid()
            held.set()
            await release.wait()

    async def arrived(con):
        pids['waiter'] = con.get_server_pid()
        second_ready.set()

    tasks = [asyncio.create_task(first_job(Repository(ObservedPool(db, after=hold))))]
    try:
        await _await_signal(held, tasks)
        tasks.append(asyncio.create_task(second_job(Repository(
            ObservedPool(db, acquired=arrived, after=second_after)))))
        await _await_signal(second_ready, tasks)
        await _prove_blocked(db, pids['waiter'], pids['holder'], tasks)
        release.set()
        return await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), SCHEDULE_TIMEOUT)
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _household_pair(db, guild, first_users, second_users, first_job, second_job):
    """Record completed production locking reads for BOTH requests, including the loser."""
    expected = (tuple(sorted(first_users)), tuple(sorted(second_users)))
    acquired = ([], [])

    def recorder(index):
        async def record(method, sql, args, con):
            if method == 'fetchrow' and sql == HOUSEHOLD_LOCK_SQL:
                check(len(args) == 2 and args[0] == guild, 'HOUSEHOLD_LOCK_TARGET')
                acquired[index].append(args[1])  # Only after the real locking query completes.
                check(tuple(acquired[index]) == expected[index][:len(acquired[index])],
                      'HOUSEHOLD_GLOBAL_LOCK_ORDER')
        return record

    common = min(set(first_users) & set(second_users))
    results = await _blocked_pair(
        db, first_job, second_job,
        lambda method, sql, args: method == 'fetchrow' and sql == HOUSEHOLD_LOCK_SQL
            and args == (guild, common),
        first_after=recorder(0), second_after=recorder(1))
    check(tuple(tuple(order) for order in acquired) == expected, 'HOUSEHOLD_LOCKS_INCOMPLETE')
    check(isinstance(results[0], str) and isinstance(results[1], ValueError), 'HOUSEHOLD_REQUEST_OUTCOMES')
    return results


async def _combat_requests(db, guild, user, *jobs):
    """An independent transaction holds the real row until every action proves contention.

    A completed locking read, a skipped read, or blocking at a later UPDATE
    cannot satisfy this proof. All production requests must then finish normally.
    """
    check(bool(jobs), 'COMBAT_REQUESTS_REQUIRED')
    attempted = [asyncio.Event() for _ in jobs]
    acquired = [asyncio.Event() for _ in jobs]
    pids, tasks = {}, []

    def hook(index, events):
        async def observe(method, sql, args, con):
            if method == 'fetchrow' and sql == COMBAT_LOCK_SQL and args == (guild, user):
                check(not events[index].is_set(), 'COMBAT_LOCK_READ_REPEATED')
                pids[index] = con.get_server_pid()
                events[index].set()
        return observe

    try:
        async with db.acquire() as holder:
            async with holder.transaction():
                check(await holder.fetchrow(COMBAT_LOCK_SQL, guild, user) is not None,
                      'COMBAT_LOCK_ROW_REQUIRED')
                for index, job in enumerate(jobs):
                    repo = Repository(ObservedPool(db, before=hook(index, attempted),
                                                   after=hook(index, acquired)))
                    tasks.append(asyncio.create_task(job(repo)))
                for event in attempted:
                    await _await_signal(event, tasks)
                check(len(set(pids.values()) | {holder.get_server_pid()}) == len(jobs) + 1,
                      'COMBAT_SESSIONS_MUST_BE_INDEPENDENT')
                for index in range(len(jobs)):
                    await _prove_blocked(db, pids[index], holder.get_server_pid(), tasks,
                                         completed=acquired,
                                         via=tuple(pid for other, pid in pids.items() if other != index))
            # Release only after the observer proved every production SELECT was blocked.
        results = await asyncio.wait_for(asyncio.gather(*tasks), SCHEDULE_TIMEOUT)
        check(all(event.is_set() for event in acquired) and all(isinstance(r, str) for r in results),
              'COMBAT_REQUESTS_INCOMPLETE')
        return results
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _together(db, *jobs):
    gate = asyncio.Event()
    arrived = 0

    async def ready(con):
        nonlocal arrived
        arrived += 1
        if arrived == len(jobs):
            gate.set()
        await gate.wait()

    tasks = [asyncio.create_task(job(Repository(ObservedPool(db, acquired=ready)))) for job in jobs]
    try:
        return await asyncio.wait_for(asyncio.gather(*tasks), 10)
    finally:
        gate.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _seed_characters(db, guild, users, *, root='Unawakened', now=None):
    now = now or datetime.now(timezone.utc)
    async with db.acquire() as con:
        async with con.transaction():
            for uid in users:
                state = default_state(now)
                await con.execute('INSERT INTO characters(guild_id,user_id,state) VALUES($1,$2,$3::jsonb)',
                                  guild, uid, json.dumps(state))
                await con.execute(
                    'INSERT INTO character_origins(guild_id,user_id,root,background) VALUES($1,$2,$3,$4)',
                    guild, uid, root, 'integration-fixture')


async def _value(db, sql, *args):
    async with db.acquire() as con:
        return await con.fetchval(sql, *args)


async def combat_companion(db):
    guild, user = 7001, 101
    await _seed_characters(db, guild, (user, 202))
    original_character = decode(await _value(db, 'SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2', guild, user))
    living = beast_v022.settle({'companion': 'storm_hawk', 'bond': 2}, datetime.now(timezone.utc))
    living['beast_cultivation']['qi'] = beast_v022.cap(1)
    async with db.acquire() as con:
        await con.execute('INSERT INTO living_dao_players(guild_id,user_id,data) VALUES($1,$2,$3::jsonb)',
                          guild, user, json.dumps(living))
    replies = await _blocked_pair(
        db, lambda repo: combat_store.execute(repo, guild, user, 1001, 'start'),
        lambda repo: beast_v022.action(repo, guild, user, 1002, 'advance'),
        lambda method, sql, args: method == 'fetchval' and 'living_dao_players' in sql and 'FOR UPDATE' in sql)
    check(all(isinstance(reply, str) for reply in replies), 'COMBAT_COMPANION_COMPLETED')
    await _combat_requests(
        db, guild, user,
        lambda repo: combat_store.execute(repo, guild, user, 1003, 'turn', action='strike'))
    saved = decode(await _value(db, 'SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2', guild, user))
    check(saved['beast_cultivation']['stage'] == 2 and saved['bond'] == 2, 'COMPANION_PROGRESS_PRESERVED')
    battle = decode(await _value(db, 'SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2', guild, user))
    check(battle['status'] == 'active' and battle['turn'] == 1, 'COMBAT_START_AND_TURN_PERSISTED')
    check(decode(await _value(db, 'SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2', guild, user))
          == original_character, 'COMBAT_CULTIVATION_STATE_UNCHANGED')

    async with db.acquire() as con:
        await con.execute('INSERT INTO combat_sessions(guild_id,user_id,battle) VALUES($1,$2,$3::jsonb)',
                          guild, 202, json.dumps(combat.begin()))
    await _combat_requests(db, guild, 202, *(
        lambda repo, event=event: combat_store.execute(repo, guild, 202, event, 'turn', action='strike')
        for event in (2001, 2002)))
    battle = decode(await _value(db, 'SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2', guild, 202))
    check((battle['turn'], battle['mastery']['strike'], battle['enemy_hp']) == (2, 2, 38),
          'COMBAT_BOTH_TURNS_PRESERVED')
    replies = await _combat_requests(db, guild, 202, *(
        lambda repo: combat_store.execute(repo, guild, 202, 2003, 'auto') for _ in range(2)))
    check(replies[0] == replies[1], 'COMBAT_DUPLICATE_REPLY')
    async with db.acquire() as con:
        stock = await con.fetchrow('SELECT marks,holdings FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, 202)
        count = await con.fetchval('SELECT count(*) FROM combat_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3', guild, 202, 2003)
    check(stock['marks'] == 12 and decode(stock['holdings']) == {'wolf_fang': 1} and count == 1,
          'COMBAT_VICTORY_PAID_ONCE')
    battle = decode(await _value(db, 'SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2', guild, 202))
    check(battle['status'] == 'victory', 'COMBAT_VICTORY_PERSISTED')


async def partnership_acceptance(db):
    variants = (((101, 202), (101, 303), False), ((101, 303), (202, 303), True),
                ((101, 202), (202, 101), True), ((101, 202), (101, 303), True))
    for variant, (first, second, existing) in enumerate(variants):
        guild = 7100 + variant
        users = sorted(set(first + second))
        await _seed_characters(db, guild, users)
        async with db.acquire() as con:
            async with con.transaction():
                for proposer, recipient in (first, second):
                    await con.execute(
                        'INSERT INTO dao_player_bonds(guild_id,proposer,recipient,role,status) VALUES($1,$2,$3,$4,$5)',
                        guild, proposer, recipient, 'spouse', 'pending')
                if existing:
                    for uid in users:
                        await con.execute('INSERT INTO dao_households(guild_id,user_id) VALUES($1,$2)', guild, uid)
        results = await _household_pair(
            db, guild, first, second,
            lambda repo: bonds_v022.apply(repo, guild, first[1], 3001, 'accept', target=first[0]),
            lambda repo: bonds_v022.apply(repo, guild, second[1], 3002, 'accept', target=second[0]))
        check(isinstance(results[0], str) and isinstance(results[1], ValueError), 'ONE_PARTNERSHIP_ACCEPTED')
        active = await _value(db, "SELECT count(*) FROM dao_player_bonds WHERE guild_id=$1 AND status='active'", guild)
        actions = await _value(db, 'SELECT count(*) FROM dao_household_actions WHERE guild_id=$1', guild)
        winning_status = await _value(db, 'SELECT status FROM dao_player_bonds WHERE guild_id=$1 AND proposer=$2 AND recipient=$3',
                                     guild, first[0], first[1])
        losing_status = await _value(db, 'SELECT status FROM dao_player_bonds WHERE guild_id=$1 AND proposer=$2 AND recipient=$3',
                                    guild, second[0], second[1])
        replay = await bonds_v022.apply(Repository(db), guild, first[1], 3001, 'accept', target=first[0])
        check(active == 1 and actions == 1 and winning_status == 'active' and losing_status == 'pending'
              and replay == results[0], 'PARTNERSHIP_CONSENT_AND_REPLAY')


async def opposing_dual(db):
    guild = 7200
    await _seed_characters(db, guild, (101, 202))
    async with db.acquire() as con:
        async with con.transaction():
            await con.execute(
                'INSERT INTO dao_player_bonds(guild_id,proposer,recipient,role,status) VALUES($1,$2,$3,$4,$5)',
                guild, 101, 202, 'spouse', 'active')
            for proposer, recipient in ((101, 202), (202, 101)):
                await con.execute('INSERT INTO dao_households(guild_id,user_id) VALUES($1,$2)', guild, proposer)
                await con.execute(
                    'INSERT INTO dao_dual_requests(guild_id,proposer,recipient,status,expires_at) VALUES($1,$2,$3,$4,$5)',
                    guild, proposer, recipient, 'pending', datetime.now(timezone.utc) + timedelta(days=1))
    results = await _household_pair(
        db, guild, (101, 202), (202, 101),
        lambda repo: bonds_v022.apply(repo, guild, 202, 4001, 'dual_accept', target=101),
        lambda repo: bonds_v022.apply(repo, guild, 101, 4002, 'dual_accept', target=202))
    check(isinstance(results[0], str) and isinstance(results[1], ValueError), 'ONE_DUAL_SESSION_ACCEPTED')
    cooldowns = []
    async with db.acquire() as con:
        for uid in (101, 202):
            character = decode(await con.fetchval('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2', guild, uid))
            house = decode(await con.fetchval('SELECT data FROM dao_households WHERE guild_id=$1 AND user_id=$2', guild, uid))
            check(len(character['dual_boons']) == 1 and character['dual_boons'][0]['mult'] == 1250,
                  'DUAL_BOON_ONCE_PER_PLAYER')
            boon = character['dual_boons'][0]
            check(datetime.fromisoformat(boon['end_at']) - datetime.fromisoformat(boon['start_at'])
                  == timedelta(hours=1) and boon['start_at'] == house['last_dual_at'], 'DUAL_DURATION_AND_COOLDOWN')
            cooldowns.append(house['last_dual_at'])
        accepted = await con.fetchval("SELECT count(*) FROM dao_dual_requests WHERE guild_id=$1 AND status='accepted'", guild)
        actions = await con.fetchval('SELECT count(*) FROM dao_household_actions WHERE guild_id=$1', guild)
        first_status = await con.fetchval('SELECT status FROM dao_dual_requests WHERE guild_id=$1 AND proposer=$2 AND recipient=$3', guild, 101, 202)
        second_status = await con.fetchval('SELECT status FROM dao_dual_requests WHERE guild_id=$1 AND proposer=$2 AND recipient=$3', guild, 202, 101)
    check(cooldowns[0] == cooldowns[1] and cooldowns[0] is not None and accepted == actions == 1,
          'DUAL_COOLDOWNS_AND_ACTION_ATOMIC')
    check(first_status == 'accepted' and second_status == 'pending', 'DUAL_REQUESTS_PRESERVED')


async def rollback_retry(db):
    guild, user = 7300, 101
    await _seed_characters(db, guild, (user,))
    house = bonds_v022.normal(None)
    bonds_v022.npc_recruit(house, 'ren_qiu', 'retainer')
    async with db.acquire() as con:
        await con.execute('INSERT INTO dao_households(guild_id,user_id,data) VALUES($1,$2,$3::jsonb)',
                          guild, user, json.dumps(house))
        await con.execute('INSERT INTO player_inventory(guild_id,user_id) VALUES($1,$2)', guild, user)

    async def fail_after_inventory(method, sql, args, con):
        if method == 'execute' and sql.startswith('UPDATE player_inventory'):
            await con.fetchval('SELECT 1/0')  # Real server error aborts the real transaction.

    try:
        await bonds_v022.apply(Repository(ObservedPool(db, after=fail_after_inventory)),
                               guild, user, 5001, 'task')
    except asyncpg.DivisionByZeroError:
        pass
    else:
        raise AssertionError('EXPECTED_SERVER_FAILURE')
    async with db.acquire() as con:
        marks = await con.fetchval('SELECT marks FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, user)
        saved_house = decode(await con.fetchval('SELECT data FROM dao_households WHERE guild_id=$1 AND user_id=$2', guild, user))
        actions = await con.fetchval('SELECT count(*) FROM dao_household_actions WHERE guild_id=$1', guild)
    check(marks == 0 and saved_house == house and actions == 0, 'RESOURCE_AND_ACTION_ROLLBACK')
    reply = await bonds_v022.apply(Repository(db), guild, user, 5001, 'task')
    replay = await bonds_v022.apply(Repository(db), guild, user, 5001, 'task')
    marks = await _value(db, 'SELECT marks FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, user)
    saved_house = decode(await _value(db, 'SELECT data FROM dao_households WHERE guild_id=$1 AND user_id=$2', guild, user))
    actions = await _value(db, 'SELECT count(*) FROM dao_household_actions WHERE guild_id=$1 AND user_id=$2', guild, user)
    check(reply == replay and marks == 4 and saved_house['house_xp'] == 1 and actions == 1,
          'RETRY_INTERACTION_PAID_ONCE')

    # PostgreSQL-only demonstration, NOT a production automatic retry mechanism.
    # Establish an old SERIALIZABLE snapshot, commit a competing update, then
    # force a 40001. The test explicitly retries; the gameplay handlers do not.
    # The losing transaction's action insert must roll back too.
    async with db.acquire() as first, db.acquire() as second:
        try:
            async with second.transaction(isolation='serializable'):
                await second.fetchval('SELECT marks FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, user)
                await second.execute('INSERT INTO processed_actions VALUES($1,$2,$3,$4)', guild, user, 5002, 'retry-fixture')
                async with first.transaction(isolation='serializable'):
                    await first.execute('UPDATE player_inventory SET marks=marks+1 WHERE guild_id=$1 AND user_id=$2', guild, user)
                await second.execute('UPDATE player_inventory SET marks=marks+1 WHERE guild_id=$1 AND user_id=$2', guild, user)
        except asyncpg.SerializationError:
            pass
        else:
            raise AssertionError('EXPECTED_SERIALIZATION_FAILURE')
    async with db.acquire() as con:
        check(await con.fetchval('SELECT count(*) FROM processed_actions WHERE guild_id=$1 AND event_id=$2', guild, 5002) == 0,
              'SERIALIZATION_ACTION_ROLLED_BACK')
        async with con.transaction(isolation='serializable'):
            # Retry the whole decision/read/write transaction from a fresh snapshot.
            marks = await con.fetchval('SELECT marks FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, user)
            check(marks == 5, 'SERIALIZATION_FRESH_SNAPSHOT')
            await con.execute('UPDATE player_inventory SET marks=$3 WHERE guild_id=$1 AND user_id=$2', guild, user, marks + 1)
            await con.execute('INSERT INTO processed_actions VALUES($1,$2,$3,$4)', guild, user, 5002, 'retry-fixture')
    check(await _value(db, 'SELECT marks FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, user) == 6,
          'WHOLE_SERIALIZABLE_TRANSACTION_RETRIED')


def _clock(instant):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)
    return Clock


async def gathering_hall(db):
    start = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    # Guild 7 at this fixed interval has no world event; independent arithmetic:
    # 40 Qi/hour * 1.1 root bonus, then 5% per active Gathering Hall level.
    for variant, (join_minutes, levels, earned, remainder) in enumerate((
            (0, 1, 90, 720000), (90, 1, 89, 360000), (0, 2, 91, 1080000))):
        guild, founder, user = 7, 800 + variant * 2, 801 + variant * 2
        sect = 'IntegrationAzure' + str(variant)
        await _seed_characters(db, guild, (founder, user), root='Lightning', now=start)
        state = default_state(start)
        state['player']['qi'] = 95
        state['legacy_feature'] = {'preserve': True}
        state['eternal'] = {'last_at': start.isoformat(), 'bank_qi': 0, 'remainder': 0,
                            'total_earned': 0, 'scale_version': 2}
        async with db.acquire() as con:
            async with con.transaction():
                await con.execute('UPDATE characters SET state=$3::jsonb WHERE guild_id=$1 AND user_id=$2',
                                  guild, user, json.dumps(state))
                await con.execute('INSERT INTO living_dao_sects(guild_id,name,founder_id,contribution) VALUES($1,$2,$3,$4)',
                                  guild, sect, founder, 1000)
                for uid in (founder, user):
                    await con.execute('INSERT INTO living_dao_players(guild_id,user_id,data) VALUES($1,$2,$3::jsonb)',
                                      guild, uid, json.dumps({'sect': sect}))
                await con.execute('INSERT INTO sect_member_join_v022(guild_id,user_id,sect_name,joined_at) VALUES($1,$2,$3,$4)',
                                  guild, user, sect, start + timedelta(minutes=join_minutes))
        for level in range(1, levels + 1):
            instant = start + timedelta(minutes=60 + (level - 1) * 30)
            with patch.object(sect_halls, 'datetime', _clock(instant)):
                await sect_halls.act(Repository(db), guild, founder, 6000 + level, 'upgrade', 'gathering')
        now = start + timedelta(hours=2)
        repo = Repository(db)
        reply = await repo.execute(guild, user, 6100, 'profile', now)
        replay = await repo.execute(guild, user, 6100, 'profile', now)
        await repo.execute(guild, user, 6101, 'profile', now)
        saved = decode(await _value(db, 'SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2', guild, user))
        check(saved['eternal']['total_earned'] == earned and saved['eternal']['remainder'] == remainder,
              'GATHERING_UPGRADE_JOIN_BOUNDARIES')
        check(saved['player']['qi'] == 100 and saved['eternal']['bank_qi'] == earned - 5,
              'GATHERING_QI_OVERFLOW')
        check(saved['sect_qi_level'] == levels and saved['legacy_feature'] == state['legacy_feature']
              and reply == replay, 'GATHERING_ZERO_PERIOD_AND_REPLAY')
        hall = decode(await _value(db, 'SELECT data FROM sect_hall_state WHERE guild_id=$1 AND sect_name=$2', guild, sect))
        check(hall['spent'] == (20 if levels == 1 else 60), 'GATHERING_UPGRADE_COST')


CASES = (
    ('combat_companion', combat_companion),
    ('partnership_acceptance', partnership_acceptance),
    ('opposing_dual', opposing_dual),
    ('rollback_retry', rollback_retry),
    ('gathering_hall', gathering_hall),
)
