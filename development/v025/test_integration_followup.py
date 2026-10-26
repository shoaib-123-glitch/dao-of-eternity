"""Offline regressions for the independent review of commit 851ad0a."""
import asyncio
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import integration_support as infra
from test_integration_safety import ConnectionDouble, environment


def pg_settings_access():
    # Normal PUBLIC UPDATE from the view owner, with no column grants or other
    # modification rights. This fixture is catalog metadata, never executable SQL.
    return dict(oid=12000, schema='pg_catalog', name='pg_settings', kind='v',
                is_pg_settings=True, owned=False, other_table_access=False,
                other_column_access=False, table_update=True,
                update_acl_safe=True, explicit_column_update=False)


def prepared_run(version=180000):
    config = infra.TestDatabaseConfig.from_environment(environment())
    con = ConnectionDouble()
    con.identity['version'] = version
    con.schema = 'dao_it_' + 'a' * 32
    con.namespace = dict(oid=2000, owner=1000, acl_safe=True)
    con.tables = [dict(name=name, oid=3000 + i, owner=1000, kind='r')
                  for i, name in enumerate(infra._table_definitions())]
    db = infra.IsolatedTestDatabase(config)
    db.schema = con.schema
    db._namespace_oid, db._owner, db._version = 2000, 1000, version
    db._control, db._journal = con, MagicMock()
    db._state = 'ready'
    return db, con


class FollowupReviewRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_standard_public_pg_settings_update_passes_readonly_preflight(self):
        con = ConnectionDouble()
        con.privileges['system_catalog_writes'] = True
        con.fetch = AsyncMock(return_value=[pg_settings_access()])
        config = infra.TestDatabaseConfig.from_environment(environment())
        with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
            report = await infra.preflight_catalog(config, allow_connection=True)
        self.assertTrue(report.read_only)
        con.fetch.assert_awaited_once()
        con.execute.assert_not_awaited()
        con.close.assert_awaited_once()

    async def test_pg18_inventory_accepts_manifest_not_null_constraints(self):
        db, con = prepared_run()
        rows = con.inventory()
        self.assertTrue(any(r['kind'] == 'constraint' and json.loads(r['definition'])['type'] == 'n'
                            for r in rows))
        db._validate_initial_inventory(rows, infra._table_definitions())

    async def test_checkout_rejects_failure_during_write_enable_and_disposes(self):
        db, control = prepared_run()
        con = control.checkout_copy()
        failing = control.checkout_copy()
        failing.close.side_effect = RuntimeError('synthetic peer close failure')
        failing.terminate.side_effect = RuntimeError('synthetic peer termination failure')
        entered, resume = asyncio.Event(), asyncio.Event()
        original = con._execute

        async def pause_enable(sql, *args):
            if sql == 'SET default_transaction_read_only = off':
                entered.set()
                await resume.wait()
            return await original(sql, *args)

        con.execute.side_effect = pause_enable
        async def checkout():
            async with db.acquire():
                self.fail('A failed run yielded a write-capable connection')

        with patch('integration_support._connect', AsyncMock(side_effect=[failing, con])):
            worker = None
            try:
                with self.assertRaises(infra.IntegrationSafetyError):
                    async with db.acquire():
                        worker = asyncio.create_task(checkout())
                        await asyncio.wait_for(entered.wait(), 2)
                        # Exiting this checkout fails both close and terminate
                        # while the competing checkout remains paused in SET.
                self.assertEqual(db._state, 'failed')
            finally:
                resume.set()
            with self.assertRaises(infra.IntegrationSafetyError):
                await asyncio.wait_for(worker, 2)
        con.close.assert_awaited_once()
        failing.close.assert_awaited_once()
        failing.terminate.assert_called_once()
        self.assertEqual(db._active, 0)

    async def test_failed_close_disposes_control_without_sql_and_preserves_recovery(self):
        db, con = prepared_run()
        db._record_unconfirmed_close()
        recovery = db.recovery
        with self.assertRaises(infra.IntegrationSafetyError):
            await db.close()
        con.close.assert_awaited_once()
        con.execute.assert_not_awaited()
        con.fetchrow.assert_not_awaited()
        self.assertEqual(db.recovery, recovery)


class FollowupHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_pg_settings_exception_rejects_other_objects_and_nonstandard_access(self):
        overrides = (
            dict(oid=20000), dict(oid=True), dict(schema='public'), dict(name='pg_class'),
            dict(kind='r'), dict(is_pg_settings=False), dict(owned=True),
            dict(other_table_access=True), dict(other_column_access=True),
            dict(table_update=False), dict(update_acl_safe=False),
            dict(explicit_column_update=True), dict(update_acl_safe=None),
        )
        config = infra.TestDatabaseConfig.from_environment(environment())
        for changes in overrides:
            with self.subTest(changes=changes):
                con = ConnectionDouble()
                con.privileges['system_catalog_writes'] = True
                row = pg_settings_access()
                row.update(changes)
                con.fetch = AsyncMock(return_value=[row])
                with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                    with self.assertRaises(infra.IntegrationSafetyError):
                        await infra.preflight_catalog(config, allow_connection=True)
                con.execute.assert_not_awaited()
                con.close.assert_awaited_once()

    async def test_pg_settings_exception_rejects_incomplete_extra_or_unreadable_catalog_rows(self):
        config = infra.TestDatabaseConfig.from_environment(environment())
        missing = pg_settings_access()
        del missing['update_acl_safe']
        for result in ([], [missing], [pg_settings_access(), pg_settings_access()], None,
                       RuntimeError('synthetic catalog failure')):
            with self.subTest(result_kind=type(result).__name__):
                con = ConnectionDouble()
                con.privileges['system_catalog_writes'] = True
                con.fetch = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
                with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                    with self.assertRaises(infra.IntegrationSafetyError):
                        await infra.preflight_catalog(config, allow_connection=True)
                con.execute.assert_not_awaited()
                con.close.assert_awaited_once()

    async def test_parameter_privileges_and_other_gates_still_reject_with_pg_settings_exception(self):
        config = infra.TestDatabaseConfig.from_environment(environment())
        for flag in infra.PRIVILEGE_FLAGS:
            if flag == 'system_catalog_writes':
                continue
            with self.subTest(flag=flag):
                con = ConnectionDouble()
                con.privileges['system_catalog_writes'] = con.privileges[flag] = True
                con.fetch = AsyncMock(return_value=[pg_settings_access()])
                with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                    with self.assertRaises(infra.IntegrationSafetyError):
                        await infra.preflight_catalog(config, allow_connection=True)
                con.execute.assert_not_awaited()
                con.close.assert_awaited_once()

    async def test_all_supported_versions_accept_their_constraint_layout(self):
        for version in (150000, 160000, 170000, 180000):
            with self.subTest(version=version):
                db, con = prepared_run(version)
                rows = con.inventory()
                db._validate_initial_inventory(rows, infra._table_definitions())
                not_null = [json.loads(r['definition']) for r in rows if r['kind'] == 'constraint'
                            and json.loads(r['definition'])['type'] == 'n']
                self.assertEqual(bool(not_null), version >= 180000)
                primary = next(json.loads(r['definition']) for r in rows if r['kind'] == 'constraint'
                               and r['name'] == 'characters_pkey')
                self.assertEqual(primary['columns'], [1, 2])

    async def test_pg18_rejects_wrong_missing_duplicate_and_unapproved_constraints(self):
        for mode in ('missing', 'duplicate', 'wrong_column', 'multiple_not_null_columns',
                     'wrong_pk_order', 'wrong_check_columns', 'foreign_key', 'unique',
                     'zero_column', 'boolean_column', 'missing_columns', 'unvalidated',
                     'unenforced', 'deferrable', 'inherited', 'wrong_parent', 'unknown_field'):
            with self.subTest(mode=mode):
                db, con = prepared_run()
                rows = con.inventory()
                not_null = next(r for r in rows if r['kind'] == 'constraint'
                                and json.loads(r['definition'])['type'] == 'n')
                target = not_null
                if mode == 'missing':
                    rows.remove(target)
                elif mode == 'duplicate':
                    rows.append(deepcopy(target))
                else:
                    if mode == 'wrong_pk_order':
                        target = next(r for r in rows if r['kind'] == 'constraint' and r['name'] == 'characters_pkey')
                    elif mode == 'wrong_check_columns':
                        target = next(r for r in rows if r['kind'] == 'constraint' and r['name'] == 'dao_player_bonds_check')
                    details = json.loads(target['definition'])
                    if mode == 'wrong_column':
                        details['columns'] = [2]
                    elif mode == 'multiple_not_null_columns':
                        details['columns'] = [1, 2]
                    elif mode == 'wrong_pk_order':
                        details['columns'].reverse()
                    elif mode == 'wrong_check_columns':
                        details['columns'] = [1, 2]
                    elif mode in ('foreign_key', 'unique'):
                        details['type'] = 'f' if mode == 'foreign_key' else 'u'
                    elif mode in ('zero_column', 'boolean_column'):
                        details['columns'] = [0 if mode == 'zero_column' else True]
                    elif mode == 'missing_columns':
                        del details['columns']
                    elif mode == 'unvalidated':
                        details['validated'] = False
                    elif mode == 'unenforced':
                        details['enforced'] = False
                    elif mode == 'deferrable':
                        details['deferrable'] = True
                    elif mode == 'inherited':
                        details['inherited'] = 1
                    elif mode == 'wrong_parent':
                        target['parent'] = 99999
                    elif mode == 'unknown_field':
                        del details['local']
                    target['definition'] = json.dumps(details)
                with self.assertRaises(infra.IntegrationSafetyError):
                    db._validate_initial_inventory(rows, infra._table_definitions())

    async def test_column_identity_and_nullability_are_checked_against_manifest(self):
        for mode in ('missing', 'extra', 'name', 'address', 'nullable', 'dropped'):
            with self.subTest(mode=mode):
                db, con = prepared_run()
                rows = con.inventory()
                column = next(r for r in rows if r['kind'] == 'column')
                if mode == 'missing':
                    rows.remove(column)
                elif mode == 'extra':
                    rows.append(deepcopy(column))
                elif mode in ('name', 'address'):
                    column['name' if mode == 'name' else 'subid'] = 'unexpected' if mode == 'name' else 99
                else:
                    details = json.loads(column['definition'])
                    details['notnull' if mode == 'nullable' else 'dropped'] = mode != 'nullable'
                    column['definition'] = json.dumps(details)
                with self.assertRaises(infra.IntegrationSafetyError):
                    db._validate_initial_inventory(rows, infra._table_definitions())

    async def test_not_null_constraints_on_pre18_layout_are_rejected(self):
        db, con = prepared_run(170000)
        rows = con.inventory()
        _, pg18 = prepared_run()
        rows.append(next(r for r in pg18.inventory() if r['kind'] == 'constraint'
                         and json.loads(r['definition'])['type'] == 'n'))
        with self.assertRaises(infra.IntegrationSafetyError):
            db._validate_initial_inventory(rows, infra._table_definitions())

    async def test_pg18_not_null_definition_changes_block_cleanup_before_drops(self):
        db, con = prepared_run()
        rows = con.inventory()
        db._validate_initial_inventory(rows, infra._table_definitions())
        db._inventory = infra._fingerprint(rows)
        changed = deepcopy(rows)
        target = next(r for r in changed if r['kind'] == 'constraint'
                      and json.loads(r['definition'])['type'] == 'n')
        details = json.loads(target['definition'])
        details['no_inherit'] = True
        target['definition'] = json.dumps(details)
        self.assertNotEqual(db._inventory, infra._fingerprint(changed))
        con.fetch = AsyncMock(return_value=changed)
        with self.assertRaises(infra.IntegrationSafetyError):
            await db.close()
        self.assertFalse(any(c.args[0].startswith('DROP ') for c in con.execute.await_args_list))
        con.close.assert_awaited_once()

    async def test_pg18_mocked_setup_and_cleanup_include_constraint_fingerprints(self):
        con = ConnectionDouble()
        con.identity['version'] = 180000
        config = infra.TestDatabaseConfig.from_environment(environment())
        with tempfile.TemporaryDirectory() as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                db = await infra.IsolatedTestDatabase.create(
                    config, allow_database_writes=True, recovery_directory=directory)
            self.assertEqual(db._version, 180000)
            self.assertTrue(any(row[0] == 'constraint' and 'NOT NULL' in row[-1] for row in db._inventory))
            await db.close()
        self.assertEqual(db.recovery.outcome, 'cleaned')
        con.close.assert_awaited_once()

    async def test_failed_close_uses_termination_fallback_without_cleanup_or_retry(self):
        db, con = prepared_run()
        db._record_unconfirmed_close()
        recovery = db.recovery
        con.close.side_effect = RuntimeError('synthetic close failure')
        for _ in range(2):
            with self.assertRaises(infra.IntegrationSafetyError):
                await db.close()
        con.close.assert_awaited_once()
        con.terminate.assert_called_once()
        con.execute.assert_not_awaited()
        self.assertEqual(db.recovery, recovery)

    async def test_failed_control_termination_retains_recovery_and_rejects_acquisition(self):
        db, con = prepared_run()
        db._record(phase='ready', outcome='committed', namespace_oid=2000, owner_oid=1000,
                   inventory_digest='synthetic-digest', table_oids=(('characters', 3004),))
        db._state = 'failed'
        con.close.side_effect = RuntimeError('synthetic close failure')
        con.terminate.side_effect = RuntimeError('synthetic termination failure')
        with self.assertRaises(infra.IntegrationSafetyError) as raised:
            await db.close()
        self.assertEqual(raised.exception.recovery.namespace_oid, 2000)
        self.assertEqual(raised.exception.recovery.table_oids, (('characters', 3004),))
        self.assertEqual(raised.exception.recovery.phase, 'connection_close_unconfirmed')
        self.assertEqual(raised.exception.recovery.outcome, 'committed')
        with self.assertRaises(infra.IntegrationSafetyError):
            async with db.acquire():
                self.fail('Failed run acquired a connection')
        con.execute.assert_not_awaited()

    async def test_concurrent_disconnect_and_failed_close_share_disposal_despite_cancellation(self):
        db, con = prepared_run()
        db._record_unconfirmed_close()
        recovery = db.recovery
        entered, resume = asyncio.Event(), asyncio.Event()
        async def paused_close(**kwargs):
            entered.set()
            await resume.wait()
        con.close.side_effect = paused_close
        first = asyncio.create_task(db.disconnect())
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(db.close())
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        resume.set()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await asyncio.wait_for(first, 2)
        with self.assertRaises(infra.IntegrationSafetyError):
            await asyncio.wait_for(second, 2)
        self.assertEqual(raised.exception.recovery, recovery)
        con.close.assert_awaited_once()
        con.execute.assert_not_awaited()

    async def test_disconnect_of_ready_run_blocks_acquisitions_and_preserves_schema(self):
        db, con = prepared_run()
        recovery = db.recovery
        await db.disconnect()
        with self.assertRaises(infra.IntegrationSafetyError):
            async with db.acquire():
                self.fail('Disconnected run acquired a connection')
        self.assertEqual(db.recovery, recovery)
        con.close.assert_awaited_once()
        con.execute.assert_not_awaited()
        con.fetchrow.assert_not_awaited()

    async def test_disconnect_refuses_to_interrupt_running_cleanup(self):
        db, con = prepared_run()
        rows = con.inventory()
        db._validate_initial_inventory(rows, infra._table_definitions())
        db._inventory = infra._fingerprint(rows)
        entered, resume = asyncio.Event(), asyncio.Event()
        original = con._fetchrow
        async def paused_verify(sql, *args):
            if sql == infra.IDENTITY_SQL:
                entered.set()
                await resume.wait()
            return await original(sql, *args)
        con.fetchrow.side_effect = paused_verify
        worker = asyncio.create_task(db.close())
        await asyncio.wait_for(entered.wait(), 2)
        try:
            with self.assertRaises(infra.IntegrationSafetyError):
                await db.disconnect()
            con.close.assert_not_awaited()
        finally:
            resume.set()
            await asyncio.wait_for(worker, 2)
        con.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
