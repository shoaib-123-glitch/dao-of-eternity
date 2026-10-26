"""Developer console cases 1-6 against a real throwaway PostgreSQL (run_pg_cases_v025.py only).

Not a test_*.py module, so validate_offline.py never discovers or imports it.
Locks are proven with pg_blocking_pids; there are no sleeps. Every case uses its
own guild and deactivates the console afterwards.
"""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import asyncpg

import dev_console_v025 as console
import inventory
import living_dao
import storage
from game import default_state
from postgres_cases import BLOCKING_SQL, ObservedPool, check

DEV, OTHER_DEV, TEST, STRANGER = 1001, 1002, 1003, 1004
CHARACTER_LOCK = 'SELECT state FROM characters'
TIMEOUT = 10


async def create_schema(pool):
    """The same create-on-startup statements the bot runs; the console adds its audit table."""
    async with pool.acquire() as con:
        for schema in (storage.SCHEMA, inventory.SCHEMA, living_dao.SCHEMA):
            await con.execute(schema)


async def _activate(pool, guild, developers=(DEV,)):
    async with pool.acquire() as con:
        database = await con.fetchval('SELECT pg_catalog.current_database()::text')
        system_id = int(await con.fetchval('SELECT system_identifier FROM pg_catalog.pg_control_system()'))
    settings = console.ConsoleSettings(guild, database, system_id, frozenset(developers), frozenset({TEST}))
    await console.activate(SimpleNamespace(pool=pool), settings)


async def _seed(pool, guild, users=(DEV, TEST)):
    now = datetime.now(timezone.utc)
    async with pool.acquire() as con:
        for user in users:
            await con.execute('INSERT INTO characters(guild_id,user_id,state) VALUES($1,$2,$3::jsonb)',
                              guild, user, json.dumps(default_state(now)))


def _run(pool, guild, event, key, params, *, target=DEV, developer=DEV, **hooks):
    repo = SimpleNamespace(pool=ObservedPool(pool, **hooks) if hooks else pool)
    return console.run(repo, guild, developer, event, key, target, params)


async def _snapshot(pool, guild):
    tables = []
    async with pool.acquire() as con:
        for sql in ('SELECT user_id, state::text FROM characters WHERE guild_id=$1 ORDER BY user_id',
                    'SELECT user_id, holdings::text, marks, equipped::text FROM player_inventory WHERE guild_id=$1 ORDER BY user_id',
                    'SELECT event_id, developer_id, target_id, action, params::text, response FROM dev_console_actions '
                    'WHERE guild_id=$1 ORDER BY event_id'):
            rows = await con.fetch(sql, guild)
            tables.append(tuple(tuple(row) for row in rows))
    return tuple(tables)


async def _value(pool, sql, *args):
    async with pool.acquire() as con:
        return await con.fetchval(sql, *args)


async def _character(pool, guild, user):
    return json.loads(await _value(pool, 'SELECT state::text FROM characters WHERE guild_id=$1 AND user_id=$2', guild, user))


async def _marks(pool, guild, user):
    return await _value(pool, 'SELECT marks FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, user)


async def _audit_count(pool, guild):
    return await _value(pool, 'SELECT count(*) FROM dev_console_actions WHERE guild_id=$1', guild)


async def _rejected(job, fragment, label):
    try:
        await job
    except console.ConsoleRejected as error:
        check(fragment in str(error), label)
    else:
        raise AssertionError(label)


class Holder:
    """Pause one real transaction right after it locks its character row."""

    def __init__(self):
        self.held, self.release, self.pid = asyncio.Event(), asyncio.Event(), None

    async def after(self, method, sql, args, con):
        if sql.startswith(CHARACTER_LOCK) and 'FOR UPDATE' in sql and not self.held.is_set():
            self.pid = con.get_server_pid()
            self.held.set()
            await self.release.wait()


class Arrival:
    def __init__(self):
        self.ready, self.pid = asyncio.Event(), None

    async def acquired(self, con):
        self.pid = con.get_server_pid()
        self.ready.set()


async def _signal(event, tasks, label):
    """Wait for a signal; a task finishing (or failing) first is a failure, not a race win."""
    waiter = asyncio.create_task(event.wait())
    try:
        await asyncio.wait((waiter, *tasks), timeout=TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
        check(event.is_set() and not any(task.done() for task in tasks), label)
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def _prove_blocked(pool, task, waiter_pid, holder_pid):
    """Poll the server's lock graph until the waiter is blocked by the holder."""
    async with asyncio.timeout(TIMEOUT):
        async with pool.acquire() as observer:
            while not await observer.fetchval(BLOCKING_SQL, waiter_pid, holder_pid):
                check(not task.done(), 'WAITER_FINISHED_WITHOUT_BLOCKING')


async def _hold_then(pool, first, second):
    """first(holder) locks and pauses; second(arrival) must be proven blocked by it."""
    holder, arrival = Holder(), Arrival()
    tasks = [asyncio.create_task(first(holder))]
    try:
        await _signal(holder.held, tasks, 'HOLDER_DID_NOT_LOCK')
        tasks.append(asyncio.create_task(second(arrival)))
        await _signal(arrival.ready, tasks, 'WAITER_DID_NOT_CONNECT')
        await _prove_blocked(pool, tasks[1], arrival.pid, holder.pid)
        holder.release.set()
        return await asyncio.wait_for(asyncio.gather(*tasks), TIMEOUT)
    finally:
        holder.release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ---- cases -------------------------------------------------------------------

async def same_receipt_twice(pool):
    """1. A repeated event replays the first reply; a changed action or target is refused, no writes."""
    guild = 9101
    await _seed(pool, guild)
    await _activate(pool, guild)
    first = await _run(pool, guild, 1, 'set_marks', {'marks': '50'})
    second = await _run(pool, guild, 1, 'set_marks', {'marks': '50'})
    check(first == second, 'REPLAY_REPLY_DIFFERS')
    check(await _marks(pool, guild, DEV) == 50 and await _audit_count(pool, guild) == 1, 'REPLAY_APPLIED_TWICE')
    check((await _character(pool, guild, DEV))['dev_console_v025']['actions'] == 1, 'REPLAY_TAINT_COUNTED_TWICE')
    before = await _snapshot(pool, guild)
    await _rejected(_run(pool, guild, 1, 'set_material', {'item': 'wolf_fang', 'quantity': '3'}),
                    'again', 'CHANGED_ACTION_NOT_REFUSED')
    await _rejected(_run(pool, guild, 1, 'set_marks', {'marks': '50'}, target=TEST), 'again', 'CHANGED_TARGET_NOT_REFUSED')
    check(await _snapshot(pool, guild) == before, 'REFUSED_REPLAY_WROTE_ROWS')


async def same_receipt_concurrently(pool):
    """2. Two deliveries of one event: the second waits on the row lock, then replays."""
    guild = 9102
    await _seed(pool, guild)
    await _activate(pool, guild)
    replies = await _hold_then(
        pool,
        lambda holder: _run(pool, guild, 2, 'set_marks', {'marks': '40'}, after=holder.after),
        lambda arrival: _run(pool, guild, 2, 'set_marks', {'marks': '40'}, acquired=arrival.acquired))
    check(replies[0] == replies[1], 'CONCURRENT_REPLAY_REPLY_DIFFERS')
    check(await _marks(pool, guild, DEV) == 40 and await _audit_count(pool, guild) == 1, 'CONCURRENT_DUPLICATE_APPLIED_TWICE')
    check((await _character(pool, guild, DEV))['dev_console_v025']['actions'] == 1, 'CONCURRENT_TAINT_COUNTED_TWICE')


async def other_developer_reuses_receipt(pool):
    """3. Another developer's reuse, a changed action and a non-allowlisted actor are refused with no writes."""
    guild = 9103
    await _seed(pool, guild)
    await _activate(pool, guild, developers=(DEV, OTHER_DEV))
    reply = await _run(pool, guild, 3, 'set_marks', {'marks': '7'})
    before = await _snapshot(pool, guild)
    await _rejected(_run(pool, guild, 3, 'set_marks', {'marks': '7'}, developer=OTHER_DEV),
                    'unavailable', 'OTHER_DEVELOPER_REPLAY_NOT_REFUSED')
    await _rejected(_run(pool, guild, 3, 'set_cultivation_qi', {'qi': '5'}), 'again', 'CHANGED_ACTION_NOT_REFUSED')
    acquired = []

    async def count(con):
        acquired.append(con)
    await _rejected(_run(pool, guild, 4, 'set_marks', {'marks': '9'}, developer=STRANGER, acquired=count),
                    'unavailable', 'STRANGER_NOT_REFUSED')
    check(not acquired, 'STRANGER_REACHED_THE_DATABASE')
    check(await _snapshot(pool, guild) == before, 'REFUSED_REQUESTS_WROTE_ROWS')
    check(await _value(pool, 'SELECT response FROM dev_console_actions WHERE guild_id=$1 AND event_id=3', guild) == reply,
          'ORIGINAL_RECEIPT_CHANGED')


async def two_actions_one_character(pool):
    """4. Different actions on one character serialize on its row and both persist."""
    guild = 9104
    await _seed(pool, guild)
    await _activate(pool, guild)
    await _hold_then(
        pool,
        lambda holder: _run(pool, guild, 41, 'set_marks', {'marks': '30'}, after=holder.after),
        lambda arrival: _run(pool, guild, 42, 'set_cultivation_qi', {'qi': '5'}, acquired=arrival.acquired))
    state = await _character(pool, guild, DEV)
    check(await _marks(pool, guild, DEV) == 30 and state['player']['qi'] == 5, 'RACING_ACTION_LOST')
    check(state['dev_console_v025']['actions'] == 2 and await _audit_count(pool, guild) == 2, 'RACING_AUDIT_INCOMPLETE')


async def two_players_in_parallel(pool):
    """5. A lock on one player's rows never blocks another player's action."""
    guild = 9105
    await _seed(pool, guild)
    await _activate(pool, guild)
    holder = Holder()
    first = asyncio.create_task(_run(pool, guild, 51, 'set_marks', {'marks': '11'}, after=holder.after))
    try:
        await _signal(holder.held, [first], 'HOLDER_DID_NOT_LOCK')
        await asyncio.wait_for(_run(pool, guild, 52, 'set_marks', {'marks': '22'}, target=TEST), TIMEOUT)
        check(not first.done(), 'HOLDER_FINISHED_EARLY')
        holder.release.set()
        await asyncio.wait_for(first, TIMEOUT)
    finally:
        holder.release.set()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    check(await _marks(pool, guild, DEV) == 11 and await _marks(pool, guild, TEST) == 22, 'PLAYERS_INTERFERED')
    for user in (DEV, TEST):
        check((await _character(pool, guild, user))['dev_console_v025']['actions'] == 1, 'TAINT_CROSSED_PLAYERS')


async def forced_error_rolls_back(pool):
    """6. A real server error at any write step rolls back rows and audit; the retry applies once."""
    guild = 9106
    await _seed(pool, guild)
    await _activate(pool, guild)
    before = await _snapshot(pool, guild)
    for stage, prefix in (('after', 'UPDATE characters'), ('after', 'UPDATE player_inventory'),
                          ('before', 'INSERT INTO dev_console_actions')):
        async def fail(method, sql, args, con, prefix=prefix):
            if sql.startswith(prefix):
                await con.fetchval('SELECT 1/0')
        try:
            await _run(pool, guild, 6, 'set_marks', {'marks': '60'}, **{stage: fail})
        except asyncpg.DivisionByZeroError:
            pass
        else:
            raise AssertionError('EXPECTED_SERVER_FAILURE')
        check(await _snapshot(pool, guild) == before, 'PARTIAL_WRITE_SURVIVED_' + prefix.split()[-1].upper())
    await _run(pool, guild, 6, 'set_marks', {'marks': '60'})
    check(await _marks(pool, guild, DEV) == 60 and await _audit_count(pool, guild) == 1, 'RETRY_NOT_APPLIED_ONCE')
    check((await _character(pool, guild, DEV))['dev_console_v025']['actions'] == 1, 'RETRY_TAINT_WRONG')


CASES = (
    ('1_same_receipt_twice', same_receipt_twice),
    ('2_same_receipt_concurrently', same_receipt_concurrently),
    ('3_other_developer_reuses_receipt', other_developer_reuses_receipt),
    ('4_two_actions_one_character', two_actions_one_character),
    ('5_two_players_in_parallel', two_players_in_parallel),
    ('6_forced_error_rolls_back', forced_error_rolls_back),
)
