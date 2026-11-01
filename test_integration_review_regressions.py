"""Offline reproductions of the seven independent-review findings."""
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import integration_support as infra
from integration_manifest import read_schema_literals
from test_integration_safety import ConnectionDouble, environment


class ReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = infra.TestDatabaseConfig.from_environment(environment())
        self.control = ConnectionDouble()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.driver_patch = patch('integration_support.asyncpg.connect', AsyncMock(return_value=self.control))
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)

    async def create(self):
        return await infra.IsolatedTestDatabase.create(
            self.config, allow_database_writes=True, recovery_directory=self.directory.name)

    async def test_effective_public_access_is_rejected(self):
        self.control.privileges['outside_table_access'] = True
        with self.assertRaises(infra.IntegrationSafetyError):
            await infra.verify_connection(self.control, self.config)

    async def test_no_public_api_returns_a_raw_readonly_connection(self):
        self.assertFalse(hasattr(infra, 'open_readonly_connection'))

    async def test_cleanup_blocks_new_checkouts_before_first_await(self):
        db = await self.create()
        entered, resume = asyncio.Event(), asyncio.Event()
        original = infra.verify_connection

        async def paused(con, config, **kwargs):
            if con is self.control:
                entered.set()
                await resume.wait()
            return await original(con, config, **kwargs)

        self.driver.return_value = self.control.checkout_copy()
        with patch('integration_support.verify_connection', side_effect=paused):
            closing = asyncio.create_task(db.close())
            await asyncio.wait_for(entered.wait(), 2)
            try:
                with self.assertRaises(infra.IntegrationSafetyError):
                    async with db.acquire():
                        pass
            finally:
                resume.set()
                await asyncio.wait_for(closing, 2)

    async def test_cancelled_connection_close_always_releases_active_count(self):
        db = await self.create()
        con = self.control.checkout_copy()
        con.close.side_effect = asyncio.CancelledError()
        self.driver.return_value = con
        with self.assertRaises(asyncio.CancelledError):
            async with db.acquire():
                pass
        self.assertEqual(db._active, 0)

    async def test_ctas_is_rejected_before_connecting(self):
        scripts = read_schema_literals()
        scripts['storage'] = 'CREATE TABLE IF NOT EXISTS copied (payload) AS (SELECT state FROM public.characters);'
        with patch('integration_manifest.read_schema_literals', return_value=scripts):
            with self.assertRaises(infra.IntegrationSafetyError):
                infra._table_definitions()
        self.driver.assert_not_awaited()

    async def test_post_commit_failure_preserves_recovery_metadata(self):
        original = self.control._execute

        async def fail_reset(sql, *args):
            if sql == 'SET default_transaction_read_only = on':
                raise RuntimeError('synthetic post-commit failure')
            return await original(sql, *args)

        self.control.execute.side_effect = fail_reset
        with self.assertRaises(infra.IntegrationSafetyError) as raised:
            await self.create()
        self.assertIsNotNone(getattr(raised.exception, 'recovery', None))
        self.assertEqual(raised.exception.recovery.outcome, 'committed')

    async def test_cleanup_detects_unexpected_dependent_index(self):
        db = await self.create()
        self.control.tables.append({'name': 'unexpected_idx', 'oid': 9999,
                                    'owner': 1000, 'kind': 'i'})
        with self.assertRaises(infra.IntegrationSafetyError):
            await db.close()


if __name__ == '__main__':
    unittest.main()
