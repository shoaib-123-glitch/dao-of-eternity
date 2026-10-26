"""Developer console and gameplay cases 1-12 against a real throwaway PostgreSQL (run_pg_cases_v025.py only).

Not a test_*.py module, so validate_offline.py never discovers or imports it.
Locks are proven with pg_blocking_pids; there are no sleeps. Every case uses its
own guild and deactivates the console afterwards.
"""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import asyncpg

import beast_v022
import dev_console_v025 as console
import inventory
import living_dao
import storage
from game import default_state
from postgres_cases import BLOCKING_SQL, ObservedPool, check
from private_playtest import PlaytestRejected

DEV, OTHER_DEV, TEST, STRANGER = 1001, 1002, 1003, 1004
CHARACTER_LOCK = 'SELECT state FROM characters'
INVENTORY_LOCK = 'SELECT holdings,marks,equipped FROM player_inventory'
ROOT, ITEM = 'Fire', 'stormheart_charm'
TIMEOUT = 10


async def create_schema(pool):
    """The same create-on-startup setup the bot runs; the console adds its audit table."""
    import bonds_v022, combat_store, golden_sandbox_v022, sect_halls, sect_v022
    async with pool.acquire() as con:
        await con.execute(storage.SCHEMA)
    repo = SimpleNamespace(pool=pool)
    for setup in (combat_store.setup, living_dao.setup, sect_halls.setup, sect_v022.setup,
                  bonds_v022.setup, golden_sandbox_v022.setup):
        await setup(repo)


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
    """Pause one real transaction right after its first locking read of `prefix`."""

    def __init__(self, prefix=CHARACTER_LOCK):
        self.prefix = prefix
        self.held, self.release, self.pid = asyncio.Event(), asyncio.Event(), None

    async def after(self, method, sql, args, con):
        if sql.startswith(self.prefix) and 'FOR UPDATE' in sql and not self.held.is_set():
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


async def _hold_then(pool, first, second, *, prefix=CHARACTER_LOCK, return_exceptions=False):
    """first(holder) locks and pauses; second(arrival) must be proven blocked by it."""
    holder, arrival = Holder(prefix), Arrival()
    tasks = [asyncio.create_task(first(holder))]
    try:
        await _signal(holder.held, tasks, 'HOLDER_DID_NOT_LOCK')
        tasks.append(asyncio.create_task(second(arrival)))
        await _signal(arrival.ready, tasks, 'WAITER_DID_NOT_CONNECT')
        await _prove_blocked(pool, tasks[1], arrival.pid, holder.pid)
        holder.release.set()
        return await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=return_exceptions), TIMEOUT)
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


async def audit_survives_restart(env):
    """7. Rows and audit survive a fresh pool and a restart of the throwaway container."""
    guild = 9107
    await _seed(env.pool, guild)
    await _activate(env.pool, guild)
    await _run(env.pool, guild, 71, 'set_marks', {'marks': '17'})
    before = await _snapshot(env.pool, guild)
    for restart in (False, True):
        await env.pool.close()
        if restart:
            env.cluster.restart()
            await env.cluster.wait_ready()
        env.pool = await env.cluster.app_pool(env.marker)
        check(await _snapshot(env.pool, guild) == before, 'ROWS_LOST_AFTER_' + ('RESTART' if restart else 'RECONNECT'))
        check('set_marks (marks=17)' in await console.recent(SimpleNamespace(pool=env.pool), guild, DEV),
              'AUDIT_VIEW_MISSING_ACTION')
    await _activate(env.pool, guild)  # The cluster identity check must still match after the restart.
    await _run(env.pool, guild, 72, 'set_marks', {'marks': '18'})
    check(await _audit_count(env.pool, guild) == 2 and await _marks(env.pool, guild, DEV) == 18,
          'CONSOLE_BROKEN_AFTER_RESTART')


async def refusals_touch_nothing(env):
    """8. Non-allowlisted actors never acquire; without the marker run() and activate() refuse."""
    pool, guild = env.pool, 9108
    await _seed(pool, guild)
    await _activate(pool, guild)
    acquired = []

    async def count(con):
        acquired.append(con)
    await _rejected(_run(pool, guild, 81, 'set_marks', {'marks': '5'}, developer=STRANGER, acquired=count),
                    'unavailable', 'STRANGER_DEVELOPER_NOT_REFUSED')
    await _rejected(_run(pool, guild, 82, 'set_marks', {'marks': '5'}, target=STRANGER, acquired=count),
                    'allowlisted', 'STRANGER_TARGET_NOT_REFUSED')
    check(not acquired, 'REFUSED_ACTOR_REACHED_THE_DATABASE')
    before = await _snapshot(pool, guild)
    await env.cluster.set_marker(None)
    try:
        await _rejected(_run(pool, guild, 83, 'set_marks', {'marks': '5'}), 'unavailable', 'UNMARKED_RUN_NOT_REFUSED')
        check(await _snapshot(pool, guild) == before, 'UNMARKED_RUN_WROTE_ROWS')
        try:
            await _activate(pool, guild)
        except PlaytestRejected:
            pass
        else:
            raise AssertionError('UNMARKED_ACTIVATE_NOT_REFUSED')
        check(console.active() is None, 'CONSOLE_LEFT_ACTIVE_WITHOUT_MARKER')
    finally:
        await env.cluster.set_marker(env.marker)
    await _activate(pool, guild)
    await _run(pool, guild, 84, 'set_marks', {'marks': '5'})
    check(await _marks(pool, guild, DEV) == 5 and await _audit_count(pool, guild) == 1, 'RESTORED_MARKER_NOT_WORKING')


async def _seed_inventory(pool, guild, copies):
    recipe = inventory.RECIPES[ITEM]
    holdings = {key: qty * copies for key, qty in recipe['ingredients'].items()}
    async with pool.acquire() as con:
        await con.execute('INSERT INTO player_inventory(guild_id,user_id,holdings,marks) VALUES($1,$2,$3::jsonb,$4)',
                          guild, DEV, json.dumps(holdings), recipe['marks'] * copies)


async def _holdings(pool, guild):
    async with pool.acquire() as con:
        row = await con.fetchrow('SELECT holdings::text, marks FROM player_inventory WHERE guild_id=$1 AND user_id=$2', guild, DEV)
    return json.loads(row['holdings']), row['marks']


async def _receipts(pool, guild):
    return await _value(pool, 'SELECT count(*) FROM processed_actions WHERE guild_id=$1', guild)


def _craft(pool, guild, event, **hooks):
    return inventory.change(SimpleNamespace(pool=ObservedPool(pool, **hooks)), guild, DEV, 'craft', ITEM, event_id=event)


async def concurrent_crafts_spend_once(env):
    """9. Materials for exactly one craft; two concurrent crafts -> one succeeds, spent once."""
    pool, guild = env.pool, 9109
    await _seed(pool, guild, (DEV,))
    await _seed_inventory(pool, guild, 1)
    results = await _hold_then(pool, lambda holder: _craft(pool, guild, 91, after=holder.after),
                               lambda arrival: _craft(pool, guild, 92, acquired=arrival.acquired),
                               prefix=INVENTORY_LOCK, return_exceptions=True)
    check(isinstance(results[0], str) and isinstance(results[1], ValueError), 'CRAFT_OUTCOMES_WRONG')
    holdings, marks = await _holdings(pool, guild)
    check(holdings.get(ITEM) == 1 and marks == 0
          and all(holdings.get(key) == 0 for key in inventory.RECIPES[ITEM]['ingredients']), 'CRAFT_NOT_SPENT_ONCE')
    check(await _receipts(pool, guild) == 1, 'CRAFT_RECEIPTS_WRONG')


async def duplicate_craft_spends_once(env):
    """10. One craft event delivered twice concurrently: spent once, identical reply."""
    pool, guild = env.pool, 9110
    await _seed(pool, guild, (DEV,))
    await _seed_inventory(pool, guild, 2)  # Enough for two, so a double spend would show.
    results = await _hold_then(pool, lambda holder: _craft(pool, guild, 101, after=holder.after),
                               lambda arrival: _craft(pool, guild, 101, acquired=arrival.acquired), prefix=INVENTORY_LOCK)
    check(results[0] == results[1], 'DUPLICATE_CRAFT_REPLY_DIFFERS')
    recipe = inventory.RECIPES[ITEM]
    holdings, marks = await _holdings(pool, guild)
    check(holdings.get(ITEM) == 1 and marks == recipe['marks']
          and all(holdings.get(key) == qty for key, qty in recipe['ingredients'].items()), 'DUPLICATE_CRAFT_SPENT_TWICE')
    check(await _receipts(pool, guild) == 1, 'DUPLICATE_CRAFT_RECEIPTS_WRONG')


async def _seed_origin(pool, guild):
    async with pool.acquire() as con:
        await con.execute('INSERT INTO character_origins(guild_id,user_id,root,background) VALUES($1,$2,$3,$4)',
                          guild, DEV, ROOT, 'integration-fixture')


def _game(pool, guild, event, op, **hooks):
    repo = storage.Repository(ObservedPool(pool, **hooks) if hooks else pool)
    return repo.execute(guild, DEV, event, op, datetime.now(timezone.utc))


async def _cultivation(pool, guild):
    return (await _character(pool, guild, DEV)).get('cultivation_v023', {})


async def reset_races_real_practice(env):
    """11. practice_ready and a real practice serialize in both orders; no mastery is lost."""
    pool, guild = env.pool, 9111
    await _seed(pool, guild, (DEV,))
    await _seed_origin(pool, guild)
    await _activate(pool, guild)
    # The practice holds the row; the reset waits, then clears the fresh cooldown.
    await _hold_then(pool, lambda holder: _game(pool, guild, 111, 'cultivation_practice', after=holder.after),
                     lambda arrival: _run(pool, guild, 112, 'practice_ready', {}, acquired=arrival.acquired))
    info = await _cultivation(pool, guild)
    check(info['mastery'][ROOT] == 4 and 'practice_next_at' not in info, 'RESET_AFTER_PRACTICE_LOST_UPDATE')
    await _game(pool, guild, 113, 'cultivation_practice')
    # The reset holds the row; the practice waits, then succeeds on the cleared cooldown.
    await _hold_then(pool, lambda holder: _run(pool, guild, 114, 'practice_ready', {}, after=holder.after),
                     lambda arrival: _game(pool, guild, 115, 'cultivation_practice', acquired=arrival.acquired))
    info = await _cultivation(pool, guild)
    check(info['mastery'][ROOT] == 12 and info.get('practices') == 3 and info.get('practice_next_at'),
          'PRACTICE_AFTER_RESET_LOST_UPDATE')
    check((await _character(pool, guild, DEV))['dev_console_v025']['actions'] == 2, 'RESET_TAINT_WRONG')


class Gate:
    """Release a round only once every writer holds a connection, so they truly overlap."""

    def __init__(self, size):
        self.size, self.count, self.open = size, 0, asyncio.Event()

    async def arrive(self, con):
        self.count += 1
        if self.count == self.size:
            self.open.set()
        await asyncio.wait_for(self.open.wait(), TIMEOUT)


async def mixed_order_stress(env, rounds=20):
    """12. Console and gameplay writers on one character for 20 rounds: no 40P01, no lost update."""
    pool, guild = env.pool, 9112
    await _seed(pool, guild, (DEV,))
    await _seed_origin(pool, guild)
    living = {**living_dao.initial(), 'companion': 'jade_fox', 'bond': 1,
              'beast_cultivation': beast_v022.initial_cultivation(datetime.now(timezone.utc))}
    async with pool.acquire() as con:
        await con.execute('INSERT INTO living_dao_players(guild_id,user_id,data) VALUES($1,$2,$3::jsonb)',
                          guild, DEV, json.dumps(living))
    await _activate(pool, guild)
    successes = 0
    for index in range(rounds):
        base, gate = 12000 + index * 10, Gate(5)
        results = await asyncio.wait_for(asyncio.gather(
            _game(pool, guild, base + 1, 'cultivation_practice', acquired=gate.arrive),   # character -> companion
            _run(pool, guild, base + 2, 'practice_ready', {}, acquired=gate.arrive),       # character
            _run(pool, guild, base + 3, 'set_beast_qi', {'qi': str(index)}, acquired=gate.arrive),  # character -> companion
            _run(pool, guild, base + 4, 'set_marks', {'marks': str(index)}, acquired=gate.arrive),  # character -> inventory
            _game(pool, guild, base + 5, 'cultivation_status', acquired=gate.arrive),     # character
            return_exceptions=True), TIMEOUT)
        check(not any(isinstance(result, asyncpg.DeadlockDetectedError) for result in results), 'DEADLOCK_40P01')
        check(isinstance(results[0], (str, ValueError)) and all(isinstance(r, str) for r in results[1:]),
              'MIXED_WRITER_FAILED')
        successes += isinstance(results[0], str)  # A cooldown refusal rolls back with no receipt.
    state = await _character(pool, guild, DEV)
    info = state['cultivation_v023']
    beast = json.loads(await _value(pool, 'SELECT data::text FROM living_dao_players WHERE guild_id=$1 AND user_id=$2',
                                    guild, DEV))['beast_cultivation']
    check(successes >= 1, 'NO_PRACTICE_SUCCEEDED')
    check(state['dev_console_v025']['actions'] == 3 * rounds and await _audit_count(pool, guild) == 3 * rounds,
          'CONSOLE_UPDATE_LOST')
    check(info['mastery'][ROOT] == 4 * successes and info['practices'] == successes, 'PRACTICE_UPDATE_LOST')
    check(beast.get('practice_earned') == 6 * successes, 'COMPANION_UPDATE_LOST')
    check(await _receipts(pool, guild) == successes + rounds, 'GAME_RECEIPTS_WRONG')
    check(await _marks(pool, guild, DEV) == rounds - 1, 'LAST_MARKS_WRITE_LOST')


def _pool_only(case):
    async def run(env):
        await case(env.pool)
    return run


CASES = (
    ('1_same_receipt_twice', _pool_only(same_receipt_twice)),
    ('2_same_receipt_concurrently', _pool_only(same_receipt_concurrently)),
    ('3_other_developer_reuses_receipt', _pool_only(other_developer_reuses_receipt)),
    ('4_two_actions_one_character', _pool_only(two_actions_one_character)),
    ('5_two_players_in_parallel', _pool_only(two_players_in_parallel)),
    ('6_forced_error_rolls_back', _pool_only(forced_error_rolls_back)),
    ('7_audit_survives_restart', audit_survives_restart),
    ('8_refusals_touch_nothing', refusals_touch_nothing),
    ('9_concurrent_crafts_spend_once', concurrent_crafts_spend_once),
    ('10_duplicate_craft_spends_once', duplicate_craft_spends_once),
    ('11_reset_races_real_practice', reset_races_real_practice),
    ('12_mixed_order_stress', mixed_order_stress),
)
