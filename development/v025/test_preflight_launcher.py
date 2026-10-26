"""Test the launcher's embedded Python offline; never launch PowerShell or SQL."""
import ast
import asyncio
from contextlib import redirect_stdout
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import integration_support as support
from test_integration_safety import environment
from test_preflight_port_forwarding import ForwardedConnectionDouble


REVIEWED_DOCKER_CLUSTER_ID = 7699864005918920747


class PreflightLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = (Path(__file__).parent / 'catalog_preflight.ps1').read_text(encoding='utf-8-sig')
        match = re.search(r"\$daoPythonSource = @'\n(.*?)\n'@", text, re.DOTALL)
        if match is None:
            raise AssertionError('Embedded Python source was not found.')
        cls.source = match.group(1)
        cls.code = compile(ast.parse(cls.source), 'catalog_preflight.ps1:embedded_python', 'exec')

    def setUp(self):
        self.config = support.TestDatabaseConfig.from_environment(environment())
        self.report = support.PreflightReport(
            database='dao_eternity_test', role='dao_test_user', host='127.0.0.1',
            port=5432, read_only=True, effective_privileges_verified=True,
            can_create_schema=False, server_major=18, server_port=5432,
            cluster_identity_verified=False)
        self.output = io.StringIO()
        self.api = AsyncMock(return_value=self.report)
        self.config_reader = Mock(return_value=self.config)
        self.original_verifier = support.verify_connection
        self.original_preflight = support.preflight_catalog
        self.namespace = {'__name__': 'offline_launcher_test'}
        exec(self.code, self.namespace)
        for mocked in (
            patch('integration_support.preflight_catalog', self.api),
            patch('integration_support.TestDatabaseConfig.from_environment', self.config_reader),
            patch.object(sys, 'prefix', 'synthetic-venv'),
            patch.object(sys, 'base_prefix', 'synthetic-base'),
            patch.object(sys, 'argv', ['catalog_preflight.ps1', 'direct']),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def run_entrypoint(self):
        with redirect_stdout(self.output):
            code = self.namespace['entrypoint']()
        self.assertIs(support.verify_connection, self.original_verifier)
        return code

    def use_docker_mode(self):
        # Supplied reviewed pins are used only as offline fixtures; no Docker I/O.
        sys.argv[:] = ['catalog_preflight.ps1', 'docker-forwarding']
        self.config = replace(self.config, host='127.0.0.1', port=5433)
        self.config_reader.return_value = self.config
        self.report = replace(self.report, port=5433, cluster_identity_verified=True)
        self.api.return_value = self.report

    def test_docker_mode_passes_complete_independently_pinned_policy(self):
        self.use_docker_mode()
        self.assertEqual(self.run_entrypoint(), 0)
        expected = support.ExpectedServerIdentity('172.17.0.2', 5432, REVIEWED_DOCKER_CLUSTER_ID)
        self.api.assert_awaited_once_with(self.config, allow_connection=True, expected_server=expected)
        result = json.loads(self.output.getvalue())
        self.assertEqual((result['host'], result['port'], result['server_port']),
                         ('127.0.0.1', 5433, 5432))
        self.assertTrue(result['cluster_identity_verified'])
        self.assertNotIn(str(REVIEWED_DOCKER_CLUSTER_ID), self.output.getvalue())

    def test_docker_mode_rejects_unreviewed_client_configuration_before_preflight(self):
        self.use_docker_mode()
        original = self.config
        for field, value in (('host', 'localhost'), ('host', '172.20.0.2'),
                             ('port', 5432), ('database', 'production'), ('user', 'postgres')):
            with self.subTest(field=field, value=value):
                config = replace(original)
                object.__setattr__(config, field, value)
                self.config_reader.return_value = config
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_entrypoint(), 2)
                self.assertIn('[CONFIGURATION]', self.output.getvalue())
                self.assertNotIn('PASSED', self.output.getvalue())
        self.api.assert_not_called()

    def test_incomplete_unknown_or_extra_mode_arguments_reject_before_preflight(self):
        self.use_docker_mode()
        for args in ([], ['unknown'], ['direct', '172.17.0.2'],
                     ['docker-forwarding', '172.17.0.2'],
                     ['docker-forwarding', '172.20.0.2', str(REVIEWED_DOCKER_CLUSTER_ID)]):
            with self.subTest(args=args):
                sys.argv[:] = ['catalog_preflight.ps1', *args]
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_entrypoint(), 2)
                self.assertIn('[CONFIGURATION]', self.output.getvalue())
        self.api.assert_not_called()

    def test_docker_mode_rejects_other_internal_addresses_with_matching_cluster(self):
        self.use_docker_mode()
        for address in ('127.0.0.1', '172.17.0.3', '172.20.0.2', None):
            with self.subTest(address=address):
                con = ForwardedConnectionDouble(address)
                con.server_identity['system_identifier'] = REVIEWED_DOCKER_CLUSTER_ID
                driver = AsyncMock(return_value=con)
                self.output.seek(0)
                self.output.truncate()
                with patch('integration_support.preflight_catalog', self.original_preflight), \
                        patch('integration_support.asyncpg.connect', driver):
                    self.assertEqual(self.run_entrypoint(), 1)
                self.assertIn('[IDENTITY]', self.output.getvalue())
                self.assertNotIn('PASSED', self.output.getvalue())
                driver.assert_awaited_once()
                con.execute.assert_not_awaited()
                con.close.assert_awaited_once()
        self.api.assert_not_called()

    def test_docker_report_must_confirm_client_listener_and_cluster_check(self):
        self.use_docker_mode()
        for field, value in (('host', 'localhost'), ('port', 5432), ('port', '5433'),
                             ('server_port', 5433), ('server_port', '5432'),
                             ('cluster_identity_verified', False),
                             ('cluster_identity_verified', 1)):
            with self.subTest(field=field, value=value):
                self.api.return_value = SimpleNamespace(**{**asdict(self.report), field: value})
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_entrypoint(), 1)
                self.assertIn('[IDENTITY]', self.output.getvalue())
                self.assertNotIn('PASSED', self.output.getvalue())

    def test_docker_launcher_uses_real_preflight_with_only_mocked_driver(self):
        self.use_docker_mode()
        con = ForwardedConnectionDouble('172.17.0.2')
        con.server_identity['system_identifier'] = REVIEWED_DOCKER_CLUSTER_ID
        driver = AsyncMock(return_value=con)
        with patch('integration_support.preflight_catalog', self.original_preflight), \
                patch('integration_support.asyncpg.connect', driver):
            self.assertEqual(self.run_entrypoint(), 0)
        self.api.assert_not_called()
        kwargs = driver.await_args.kwargs
        self.assertEqual((kwargs['host'], kwargs['port'], kwargs['database'], kwargs['user']),
                         ('127.0.0.1', 5433, 'dao_eternity_test', 'dao_test_user'))
        self.assertEqual(kwargs['server_settings']['default_transaction_read_only'], 'on')
        self.assertTrue(json.loads(self.output.getvalue())['cluster_identity_verified'])
        con.execute.assert_not_awaited()
        con.fetchval.assert_not_awaited()
        con.transaction.assert_not_called()
        con.close.assert_awaited_once()

    def test_docker_launcher_rejects_wrong_cluster_or_endpoint_with_no_retry(self):
        self.use_docker_mode()
        for mismatch in ('cluster', 'address', 'port', 'database', 'role', 'session_role'):
            with self.subTest(mismatch=mismatch):
                con = ForwardedConnectionDouble('172.17.0.2')
                con.server_identity['system_identifier'] = REVIEWED_DOCKER_CLUSTER_ID
                if mismatch == 'cluster':
                    con.server_identity['system_identifier'] += 1
                else:
                    key = {'address': 'server_address', 'port': 'server_port',
                           'database': 'database_name', 'role': 'role_name',
                           'session_role': 'session_role'}[mismatch]
                    con.identity[key] = 5433 if mismatch == 'port' else 'unexpected_identity'
                self.output.seek(0)
                self.output.truncate()
                driver = AsyncMock(return_value=con)
                with patch('integration_support.preflight_catalog', self.original_preflight), \
                        patch('integration_support.asyncpg.connect', driver):
                    self.assertEqual(self.run_entrypoint(), 1)
                self.assertIn('[IDENTITY]', self.output.getvalue())
                self.assertNotIn('PASSED', self.output.getvalue())
                driver.assert_awaited_once()
                con.execute.assert_not_awaited()
                con.fetchval.assert_not_awaited()
                con.close.assert_awaited_once()

    def test_pass_uses_only_preflight_entry_and_prints_allowlisted_report(self):
        self.assertEqual(self.run_entrypoint(), 0)
        result = json.loads(self.output.getvalue())
        self.assertEqual(result, dict(status='PASSED', **asdict(self.report)))
        self.api.assert_awaited_once_with(self.config, allow_connection=True)

    def test_privilege_detail_output_requires_explicit_opt_in(self):
        self.api.side_effect = support.IntegrationSafetyError(
            self.config.password, category=support.FailureCategory.PRIVILEGES,
            privilege_stage=support.PrivilegeStage.ROLE_POLICY,
            privilege_flags={'other_memberships': True})
        self.assertEqual(self.run_entrypoint(), 1)
        self.assertEqual(self.output.getvalue(),
                         'PREFLIGHT REJECTED [PRIVILEGES]: connection or safety verification failed.\n')

    def test_requested_privilege_details_print_only_fixed_labels_and_booleans(self):
        self.use_docker_mode()
        sys.argv.append('--privilege-diagnostics')
        sensitive = 'synthetic-DSN SELECT secret_catalog_row ' + self.config.password
        error = support.IntegrationSafetyError(
            sensitive, category=support.FailureCategory.PRIVILEGES,
            privilege_stage=support.PrivilegeStage.EFFECTIVE_PRIVILEGES_POLICY,
            privilege_flags={'other_database_access': True, 'password': sensitive,
                             sensitive: True, 'schema': sensitive})
        error.detail = error.hint = error.query = sensitive
        error.add_note(sensitive)
        self.api.side_effect = error
        self.assertEqual(self.run_entrypoint(), 1)
        lines = self.output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1]), {'privilege_diagnostic': {
            'stage': 'EFFECTIVE_PRIVILEGES_POLICY', 'flags': {'other_database_access': True}}})
        self.assertNotIn(sensitive, self.output.getvalue())
        self.assertNotIn(str(REVIEWED_DOCKER_CLUSTER_ID), self.output.getvalue())
        self.assertNotIn('PASSED', self.output.getvalue())

    def test_nonprivilege_rejections_never_print_privilege_details(self):
        sys.argv.append('--privilege-diagnostics')
        for category in support.FailureCategory:
            if category is support.FailureCategory.PRIVILEGES:
                continue
            with self.subTest(category=category.value):
                self.api.side_effect = support.IntegrationSafetyError(
                    self.config.password, category=category,
                    privilege_stage=support.PrivilegeStage.ROLE_POLICY,
                    privilege_flags={'elevated': True})
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_entrypoint(), 1)
                self.assertNotIn('privilege_diagnostic', self.output.getvalue())

    def test_diagnostic_mode_uses_same_queries_and_pin_without_retry(self):
        self.use_docker_mode()
        sys.argv.append('--privilege-diagnostics')
        con = ForwardedConnectionDouble('172.17.0.2')
        con.identity['version'] = 180006
        con.server_identity['system_identifier'] = REVIEWED_DOCKER_CLUSTER_ID
        con.privileges['other_database_access'] = True
        driver = AsyncMock(return_value=con)
        with patch('integration_support.preflight_catalog', self.original_preflight), \
                patch('integration_support.asyncpg.connect', driver):
            self.assertEqual(self.run_entrypoint(), 1)
        detail = json.loads(self.output.getvalue().splitlines()[1])['privilege_diagnostic']
        self.assertEqual(detail['stage'], 'EFFECTIVE_PRIVILEGES_POLICY')
        self.assertTrue(detail['flags']['other_database_access'])
        self.assertEqual(con.fetchrow.await_count, 3)
        con.fetch.assert_not_awaited()
        con.execute.assert_not_awaited()
        con.close.assert_awaited_once()
        driver.assert_awaited_once()

    def test_diagnostic_mode_does_not_change_successful_report(self):
        self.use_docker_mode()
        sys.argv.append('--privilege-diagnostics')
        self.assertEqual(self.run_entrypoint(), 0)
        self.assertEqual(json.loads(self.output.getvalue()), dict(status='PASSED', **asdict(self.report)))

    def test_repeated_or_misplaced_diagnostic_option_is_configuration_rejection(self):
        for args in (['--privilege-diagnostics'], ['--privilege-diagnostics', 'direct'],
                     ['direct', '--privilege-diagnostics', '--privilege-diagnostics']):
            with self.subTest(args=args):
                sys.argv[:] = ['catalog_preflight.ps1', *args]
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_entrypoint(), 2)
                self.assertIn('[CONFIGURATION]', self.output.getvalue())
        self.api.assert_not_called()

    def test_non18_or_invalid_major_is_rejected_without_success_output(self):
        for major in (15, 16, 17, 19, None, True, 18.0, '18'):
            with self.subTest(major=major):
                self.api.return_value = SimpleNamespace(**{**asdict(self.report), 'server_major': major})
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_entrypoint(), 1)
                self.assertIn('REJECTED', self.output.getvalue())
                self.assertIn('[VERSION]', self.output.getvalue())
                self.assertNotIn('PASSED', self.output.getvalue())

    def test_extra_catalog_and_credential_fields_are_never_printed(self):
        self.api.return_value = SimpleNamespace(
            **asdict(self.report), password=self.config.password,
            raw_catalog_rows='synthetic-private-catalog-value')
        self.assertEqual(self.run_entrypoint(), 0)
        output = self.output.getvalue()
        self.assertEqual(set(json.loads(output)), {'status', *asdict(self.report)})
        self.assertNotIn(self.config.password, output)
        self.assertNotIn('synthetic-private-catalog-value', output)

    def test_permission_or_connection_rejection_returns_one_and_redacts_error(self):
        self.api.side_effect = support.IntegrationSafetyError(self.config.password)
        self.assertEqual(self.run_entrypoint(), 1)
        self.assertIn('[SAFETY]', self.output.getvalue())
        self.assertNotIn(self.config.password, self.output.getvalue())
        self.assertNotIn('PASSED', self.output.getvalue())
        self.api.assert_awaited_once()

    def test_unconfirmed_connection_close_is_rejected_with_recovery_instruction(self):
        error = support.IntegrationSafetyError(
            self.config.password, category=support.FailureCategory.CONNECTION_CLEANUP)
        error.termination_unconfirmed = True
        self.api.side_effect = error
        self.assertEqual(self.run_entrypoint(), 1)
        self.assertIn('[CONNECTION_CLEANUP]', self.output.getvalue())
        self.assertIn('closure is unconfirmed', self.output.getvalue())
        self.assertNotIn(self.config.password, self.output.getvalue())

    def test_cancellation_returns_130_and_never_reports_success(self):
        for error in (KeyboardInterrupt(), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                self.api.side_effect = error
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_entrypoint(), 130)
                self.assertIn('CANCELLED', self.output.getvalue())
                self.assertNotIn('PASSED', self.output.getvalue())

    def test_unexpected_failure_returns_two_without_exception_details(self):
        self.api.side_effect = RuntimeError(self.config.password)
        self.assertEqual(self.run_entrypoint(), 2)
        self.assertIn('[SETUP]', self.output.getvalue())
        self.assertNotIn(self.config.password, self.output.getvalue())
        self.assertNotIn('PASSED', self.output.getvalue())

    def test_configuration_failure_returns_two_before_any_preflight_call(self):
        with patch('integration_support.TestDatabaseConfig.from_environment',
                   side_effect=support.IntegrationSafetyError(self.config.password)):
            self.assertEqual(self.run_entrypoint(), 2)
        self.api.assert_not_awaited()
        self.assertIn('[CONFIGURATION]', self.output.getvalue())
        self.assertNotIn(self.config.password, self.output.getvalue())

    def test_every_reviewed_category_is_printed_without_sensitive_exception_fields(self):
        sensitive = 'synthetic-private DSN SELECT catalog_row ' + self.config.password
        for category in support.FailureCategory:
            with self.subTest(category=category.value):
                self.output.seek(0)
                self.output.truncate()
                error = support.IntegrationSafetyError(sensitive, category=category)
                error.detail = sensitive
                error.hint = sensitive
                error.query = sensitive
                error.add_note(sensitive)
                self.api.side_effect = error
                self.assertEqual(self.run_entrypoint(), 1)
                self.assertEqual(self.output.getvalue(),
                                 f'PREFLIGHT REJECTED [{category.value}]: '
                                 'connection or safety verification failed.\n')
                self.assertNotIn(sensitive, self.output.getvalue())

    def test_mutated_unreviewed_category_is_replaced_by_safe_fallback(self):
        error = support.IntegrationSafetyError(self.config.password)
        error.category = self.config.password
        self.api.side_effect = error
        self.assertEqual(self.run_entrypoint(), 1)
        self.assertIn('[SAFETY]', self.output.getvalue())
        self.assertNotIn(self.config.password, self.output.getvalue())

    def test_cancelled_cleanup_failure_keeps_130_and_safe_recovery_message(self):
        error = asyncio.CancelledError(self.config.password)
        error.termination_unconfirmed = True
        self.api.side_effect = error
        self.assertEqual(self.run_entrypoint(), 130)
        self.assertIn('[CANCELLED]', self.output.getvalue())
        self.assertIn('CONNECTION_CLEANUP:', self.output.getvalue())
        self.assertIn('closure is unconfirmed', self.output.getvalue())
        self.assertNotIn(self.config.password, self.output.getvalue())
        self.assertNotIn('PASSED', self.output.getvalue())

    def test_system_python_is_rejected_before_any_preflight_call(self):
        with patch.object(sys, 'prefix', 'synthetic-base'):
            self.assertEqual(self.run_entrypoint(), 2)
        self.api.assert_not_awaited()

    def test_script_entrypoint_raises_system_exit_with_each_explicit_status(self):
        cases = ((self.report, None, 0), (None, support.IntegrationSafetyError('synthetic'), 1),
                 (None, RuntimeError('synthetic'), 2), (None, asyncio.CancelledError(), 130))
        for report, error, expected in cases:
            with self.subTest(expected=expected):
                self.api.return_value, self.api.side_effect = report, error
                with redirect_stdout(self.output):
                    with self.assertRaises(SystemExit) as raised:
                        exec(self.code, {'__name__': '__main__'})
                self.assertEqual(raised.exception.code, expected)


if __name__ == '__main__':
    unittest.main()
