"""Offline inventory-fetch provenance tests; synthetic exceptions and driver doubles only."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import asyncpg

import integration_support as infra
from integration_catalog import INVENTORY_SQL
from integration_diagnostics import (
    SetupDiagnostic, SetupStage, decode_setup_diagnostic, safe_setup_diagnostic,
)
from inventory_fetch_diagnostics import (
    FetchDiagnostic, FetchFamily, SQLSTATE_CLASSES, SQLSTATE_CODES,
    classify_fetch_failure, decode_fetch_diagnostic, safe_fetch_diagnostic,
)
from test_integration_setup_diagnostics import TransactionTraceDouble
from test_postgres_suite_safety import config


PRIVATE = 'synthetic-password-private-query-parameters-record'


def server_error(state):
    """Use asyncpg's real ErrorResponse factory without a server or protocol."""
    return asyncpg.PostgresError.new(
        {'S': 'ERROR', 'C': state, 'M': PRIVATE, 'D': PRIVATE, 'H': PRIVATE},
        query=PRIVATE)


class HostilePostgresError(asyncpg.PostgresError):
    """Classification may inspect SQLSTATE provenance, never readable detail."""
    def __getattribute__(self, name):
        if name in ('sqlstate', 'args', 'message', 'detail', 'hint', 'query',
                    'parameters', 'password', 'dsn', 'credentials'):
            raise AssertionError('Sensitive attribute was accessed')
        return super().__getattribute__(name)

    def __str__(self):
        raise AssertionError('Exception stringification is forbidden')

    def __repr__(self):
        raise AssertionError('Exception representation is forbidden')


class HostileValue:
    def __str__(self):
        raise AssertionError('Untrusted value was stringified')

    def __repr__(self):
        raise AssertionError('Untrusted value was represented')


class InventoryFetchFamilyTests(unittest.TestCase):
    def assert_family(self, error, family):
        value = classify_fetch_failure(error)
        self.assertIs(value.family, family)
        payload = safe_fetch_diagnostic(value)
        self.assertEqual(decode_fetch_diagnostic(payload), value)
        self.assertNotIn(PRIVATE, json.dumps(payload))
        return payload

    def test_server_rejections_preserve_allowlisted_codes_and_class(self):
        for state in ('0A000', '22023', '23505', '25006', '25P02',
                      '42501', '42601', '42883', '42P18', '53300', 'XX000'):
            with self.subTest(sqlstate=state):
                result = self.assert_family(server_error(state), FetchFamily.SERVER_REJECTION)
                self.assertEqual(result, dict(family='SERVER_REJECTION',
                    sqlstate_class=state[:2], sqlstate_code=state, sqlstate_source='SERVER_FIELD'))

    def test_server_cancellation_does_not_claim_timeout(self):
        result = self.assert_family(server_error('57014'), FetchFamily.SERVER_CANCELLATION)
        self.assertEqual(result['sqlstate_code'], '57014')

    def test_server_timeout_codes_include_transaction_timeout(self):
        for state in ('25P03', '25P04', '57P05'):
            with self.subTest(sqlstate=state):
                self.assert_family(server_error(state), FetchFamily.SERVER_TIMEOUT)

    def test_server_connection_errors_use_server_provenance(self):
        for state in ('08000', '08001', '08003', '08004', '08006', '08007',
                      '08P01', '57P01', '57P02', '57P03', '57P04'):
            with self.subTest(sqlstate=state):
                result = self.assert_family(server_error(state), FetchFamily.SERVER_CONNECTION)
                self.assertEqual(result['sqlstate_source'], 'SERVER_FIELD')

    def test_driver_data_error_class_default_is_not_server_evidence(self):
        # asyncpg's parameter encoder can raise DataError locally with only this default.
        error = asyncpg.DataError(PRIVATE)
        self.assertNotIn('sqlstate', error.__dict__)
        result = self.assert_family(error, FetchFamily.POSTGRES_UNATTRIBUTED)
        self.assertEqual((result['sqlstate_class'], result['sqlstate_code'], result['sqlstate_source']),
                         ('22', '22000', 'CLASS_DEFAULT'))

    def test_same_data_error_with_errorresponse_has_server_provenance(self):
        error = server_error('22000')
        self.assertIsInstance(error, asyncpg.DataError)
        result = self.assert_family(error, FetchFamily.SERVER_REJECTION)
        self.assertEqual(result['sqlstate_source'], 'SERVER_FIELD')

    def test_postgres_base_without_sqlstate_is_unattributed(self):
        result = self.assert_family(asyncpg.PostgresError(PRIVATE), FetchFamily.POSTGRES_UNATTRIBUTED)
        self.assertEqual(result['sqlstate_source'], 'UNAVAILABLE')

    def test_outdated_schema_cache_is_explicit_driver_decode_failure(self):
        self.assert_family(asyncpg.OutdatedSchemaCacheError(PRIVATE), FetchFamily.DRIVER_DECODE)

    def test_protocol_error_precedes_driver_internal_base(self):
        self.assert_family(asyncpg.ProtocolError(PRIVATE), FetchFamily.DRIVER_PROTOCOL)

    def test_driver_internal_error_is_separate(self):
        self.assert_family(asyncpg.InternalClientError(PRIVATE), FetchFamily.DRIVER_INTERNAL)

    def test_interface_error_does_not_claim_network_failure(self):
        self.assert_family(asyncpg.InterfaceError(PRIVATE), FetchFamily.DRIVER_INTERFACE)

    def test_python_and_asyncio_timeout_types(self):
        for error in (TimeoutError(PRIVATE), asyncio.TimeoutError(PRIVATE)):
            with self.subTest(error_type=type(error).__name__):
                self.assert_family(error, FetchFamily.TIMEOUT)

    def test_connection_errors_precede_generic_os_errors(self):
        for kind in (ConnectionError, ConnectionAbortedError, ConnectionRefusedError,
                     ConnectionResetError, BrokenPipeError):
            with self.subTest(error_type=kind.__name__):
                self.assert_family(kind(PRIVATE), FetchFamily.CONNECTION)

    def test_os_error_does_not_claim_network_or_server_provenance(self):
        self.assert_family(OSError(PRIVATE), FetchFamily.OS_ERROR)

    def test_python_errors_do_not_infer_driver_decode_from_text_or_name(self):
        for error in (ValueError(PRIVATE), TypeError(PRIVATE), RuntimeError(PRIVATE),
                      BufferError(PRIVATE), UnicodeDecodeError('utf-8', b'\xff', 0, 1, PRIVATE)):
            with self.subTest(error_type=type(error).__name__):
                self.assert_family(error, FetchFamily.PYTHON_ERROR)

    def test_cancellation_and_keyboard_interrupt_are_fixed_cancellation_family(self):
        for error in (asyncio.CancelledError(PRIVATE), KeyboardInterrupt(PRIVATE)):
            self.assert_family(error, FetchFamily.CANCELLED)

    def test_unrecognized_baseexception_has_unknown_family(self):
        self.assert_family(BaseException(PRIVATE), FetchFamily.UNKNOWN)

    def test_every_fixed_family_has_a_deterministic_reproduction(self):
        samples = (server_error('42501'), server_error('57014'), server_error('25P04'),
                   server_error('08006'), asyncpg.DataError(PRIVATE),
                   asyncpg.OutdatedSchemaCacheError(PRIVATE), asyncpg.ProtocolError(PRIVATE),
                   asyncpg.InterfaceError(PRIVATE), asyncpg.InternalClientError(PRIVATE),
                   TimeoutError(PRIVATE), ConnectionError(PRIVATE), OSError(PRIVATE),
                   RuntimeError(PRIVATE), asyncio.CancelledError(PRIVATE), BaseException(PRIVATE))
        self.assertEqual({classify_fetch_failure(error).family for error in samples}, set(FetchFamily))


class InventoryFetchSanitizationTests(unittest.TestCase):
    def test_unknown_sqlstate_code_keeps_only_allowlisted_class(self):
        result = safe_fetch_diagnostic(classify_fetch_failure(server_error('42999')))
        self.assertEqual(result, dict(family='SERVER_REJECTION', sqlstate_class='42',
                                     sqlstate_code=None, sqlstate_source='SERVER_FIELD'))
        self.assertEqual(decode_fetch_diagnostic(result).sqlstate_class, '42')

    def test_unknown_class_and_code_are_suppressed(self):
        result = safe_fetch_diagnostic(classify_fetch_failure(server_error('ZZ999')))
        self.assertEqual(result, dict(family='SERVER_REJECTION', sqlstate_class=None,
                                     sqlstate_code=None, sqlstate_source='SERVER_FIELD'))

    def test_malformed_or_hostile_instance_state_is_unavailable(self):
        for raw in (None, True, 42883, '42883\n', '42p01', PRIVATE, [], {}, HostileValue()):
            error = asyncpg.PostgresError(PRIVATE)
            error.__dict__['sqlstate'] = raw
            result = safe_fetch_diagnostic(classify_fetch_failure(error))
            self.assertEqual(result, dict(family='POSTGRES_UNATTRIBUTED', sqlstate_class=None,
                                         sqlstate_code=None, sqlstate_source='UNAVAILABLE'))

    def test_state_string_subclasses_are_not_coerced(self):
        class PretendState(str):
            def __str__(self):
                raise AssertionError('No coercion')
        error = asyncpg.PostgresError(PRIVATE)
        error.__dict__['sqlstate'] = PretendState('42883')
        result = safe_fetch_diagnostic(classify_fetch_failure(error))
        self.assertIsNone(result['sqlstate_code'])
        self.assertEqual(result['sqlstate_source'], 'UNAVAILABLE')

    def test_no_message_query_parameters_credentials_or_str_are_accessed(self):
        error = HostilePostgresError(PRIVATE)
        BaseException.__getattribute__(error, '__dict__').update(
            sqlstate='42883', message=HostileValue(), query=HostileValue(),
            detail=HostileValue(), hint=HostileValue(), parameters=HostileValue(),
            password=HostileValue(), dsn=HostileValue(), credentials=HostileValue())
        result = safe_fetch_diagnostic(classify_fetch_failure(error))
        self.assertEqual(result, dict(family='SERVER_REJECTION', sqlstate_class='42',
                                     sqlstate_code='42883', sqlstate_source='SERVER_FIELD'))
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_safe_writer_revalidates_every_field_without_coercion(self):
        hostile = HostileValue()
        result = safe_fetch_diagnostic(FetchDiagnostic(hostile, hostile, hostile, hostile))
        self.assertEqual(result, dict(family='UNKNOWN', sqlstate_class=None,
                                     sqlstate_code=None, sqlstate_source='UNAVAILABLE'))
        self.assertEqual(safe_fetch_diagnostic(hostile), result)

    def test_non_postgres_families_cannot_emit_spurious_sqlstate(self):
        for family in FetchFamily:
            if family.value.startswith('SERVER_') or family is FetchFamily.POSTGRES_UNATTRIBUTED:
                continue
            with self.subTest(family=family):
                result = safe_fetch_diagnostic(FetchDiagnostic(family, '42', '42883', 'SERVER_FIELD'))
                self.assertEqual((result['sqlstate_class'], result['sqlstate_code'], result['sqlstate_source']),
                                 (None, None, 'UNAVAILABLE'))

    def test_mismatched_class_and_code_are_not_emitted(self):
        result = safe_fetch_diagnostic(FetchDiagnostic(FetchFamily.SERVER_REJECTION,
                                                      '22', '42883', 'SERVER_FIELD'))
        self.assertEqual(result['sqlstate_class'], '22')
        self.assertIsNone(result['sqlstate_code'])

    def test_exact_allowlist_members_only_are_emitted(self):
        for state in SQLSTATE_CODES:
            self.assertIn(state[:2], SQLSTATE_CLASSES)
        result = safe_fetch_diagnostic(FetchDiagnostic(FetchFamily.SERVER_REJECTION,
                                                      PRIVATE, PRIVATE, PRIVATE))
        self.assertEqual((result['sqlstate_class'], result['sqlstate_code'], result['sqlstate_source']),
                         (None, None, 'UNAVAILABLE'))

    def test_decoder_rejects_invalid_nested_metadata(self):
        valid = safe_fetch_diagnostic(classify_fetch_failure(server_error('42883')))
        invalid = [None, [], {}, {**valid, 'query': PRIVATE}]
        for key, values in (
            ('family', (PRIVATE, None, True, [])),
            ('sqlstate_class', (PRIVATE, True, '22', 'zz')),
            ('sqlstate_code', (PRIVATE, True, '42p01', '42999', '22023')),
            ('sqlstate_source', (PRIVATE, None, True, 'CLASS_DEFAULT', 'UNAVAILABLE')),
        ):
            for value in values:
                invalid.append({**valid, key: value})
        for index, payload in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises(ValueError):
                decode_fetch_diagnostic(payload)

    def test_decoder_rejects_non_postgres_sqlstate_and_false_server_provenance(self):
        invalid = (
            FetchDiagnostic(FetchFamily.PYTHON_ERROR, '42', '42883', 'SERVER_FIELD'),
            FetchDiagnostic(FetchFamily.SERVER_REJECTION, '42', '42883', 'CLASS_DEFAULT'),
        )
        for value in invalid:
            payload = dict(family=value.family.value, sqlstate_class=value.sqlstate_class,
                           sqlstate_code=value.sqlstate_code, sqlstate_source=value.sqlstate_source)
            with self.assertRaises(ValueError):
                decode_fetch_diagnostic(payload)


class InventoryFetchSetupRoundtripTests(unittest.TestCase):
    def test_legacy_diagnostic_shape_stays_byte_compatible(self):
        value = SetupDiagnostic(stage=SetupStage.INVENTORY_QUERY)
        payload = safe_setup_diagnostic(value)
        self.assertEqual(set(payload), {'stage', 'category', 'statement_ordinal', 'table',
            'rollback', 'rollback_verification', 'connection_cleanup'})
        self.assertNotIn('inventory_fetch', payload)
        self.assertEqual(safe_setup_diagnostic(decode_setup_diagnostic(payload)), payload)
        self.assertIsNone(decode_setup_diagnostic(None))

    def test_new_nested_diagnostic_survives_json_and_strict_reader(self):
        value = SetupDiagnostic(stage=SetupStage.INVENTORY_QUERY,
            inventory_fetch=classify_fetch_failure(server_error('42883')))
        payload = json.loads(json.dumps(safe_setup_diagnostic(value)))
        restored = decode_setup_diagnostic(payload)
        self.assertEqual(restored, value)
        self.assertEqual(safe_setup_diagnostic(restored), payload)

    def test_setup_reader_rejects_invalid_nested_shapes_and_extra_detail(self):
        valid = safe_setup_diagnostic(SetupDiagnostic(stage=SetupStage.INVENTORY_QUERY,
            inventory_fetch=classify_fetch_failure(server_error('42883'))))
        for nested in (None, [], {}, {**valid['inventory_fetch'], 'message': PRIVATE},
                       {**valid['inventory_fetch'], 'sqlstate_code': PRIVATE}):
            payload = deepcopy(valid)
            payload['inventory_fetch'] = nested
            with self.assertRaises(ValueError):
                decode_setup_diagnostic(payload)


class InventoryFetchAdoptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_failure_persists_after_rollback_cleanup_without_retry_or_drop(self):
        con = TransactionTraceDouble()
        original_fetch = con._fetch
        async def fail_inventory(sql, *args):
            if sql == INVENTORY_SQL:
                raise server_error('42883')
            return await original_fetch(sql, *args)
        con.fetch.side_effect = fail_inventory
        with tempfile.TemporaryDirectory(prefix='dao_offline_fetch_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)) as driver:
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
            journal = Path(directory) / (con.schema + '.json')
            before = journal.read_bytes()
            disk = json.loads(before)
            diagnostic = infra.setup_failure_diagnostic(raised.exception)
            self.assertEqual(diagnostic, disk['diagnostic'])
            self.assertEqual(diagnostic['stage'], 'INVENTORY_QUERY')
            self.assertEqual((diagnostic['statement_ordinal'], diagnostic['table']), (0, None))
            self.assertEqual(diagnostic['inventory_fetch'], dict(family='SERVER_REJECTION',
                sqlstate_class='42', sqlstate_code='42883', sqlstate_source='SERVER_FIELD'))
            self.assertEqual((diagnostic['rollback'], diagnostic['rollback_verification'],
                              diagnostic['connection_cleanup']), ('ACKNOWLEDGED', 'UNKNOWN', 'ACKNOWLEDGED'))
            self.assertEqual(decode_setup_diagnostic(diagnostic).inventory_fetch.family,
                             FetchFamily.SERVER_REJECTION)
            self.assertNotIn(PRIVATE, before.decode())
            self.assertEqual((con.attempts, con.rollbacks, con.commits), (27, 1, 0))
            self.assertEqual(sum(call.args[0] == INVENTORY_SQL for call in con.fetch.await_args_list), 1)
            self.assertFalse(any(call.args[0].startswith('DROP ') for call in con.execute.await_args_list))
            con.close.assert_awaited_once()
            driver.assert_awaited_once()
            # Even a synthetic consumed journal blocks a new connection; no reset or reseal.
            with patch('integration_support.asyncpg.connect', AsyncMock()) as replay_driver:
                with self.assertRaises(infra.IntegrationSafetyError):
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
            replay_driver.assert_not_called()
            self.assertEqual(journal.read_bytes(), before)

    async def test_validation_failure_does_not_invent_fetch_exception_metadata(self):
        con = TransactionTraceDouble('inventory_validation')
        with tempfile.TemporaryDirectory(prefix='dao_offline_fetch_') as directory:
            with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)):
                with self.assertRaises(infra.IntegrationSafetyError) as raised:
                    await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                        expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                        allow_database_writes=True, recovery_directory=directory)
        diagnostic = infra.setup_failure_diagnostic(raised.exception)
        self.assertEqual(diagnostic['stage'], 'INVENTORY_VALIDATION')
        self.assertNotIn('inventory_fetch', diagnostic)


if __name__ == '__main__':
    unittest.main()
