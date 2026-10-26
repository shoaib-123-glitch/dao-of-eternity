"""Offline DDL allowlist and persistent recovery-record tests."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integration_manifest import read_schema_literals, reviewed_table_definitions
from integration_recovery import RecoveryJournal, RecoveryRecord
from integration_support import IntegrationSafetyError, _table_definitions


class ManifestRejectionTests(unittest.TestCase):
    def test_unapproved_sql_never_becomes_setup_ddl(self):
        malicious = (
            'CREATE TABLE IF NOT EXISTS copied (payload) AS (SELECT state FROM public.characters);',
            'SELECT state INTO copied FROM public.characters;',
            'CREATE TABLE IF NOT EXISTS copied (id BIGINT REFERENCES public.characters(user_id));',
            'CREATE TABLE IF NOT EXISTS copied (id BIGINT DEFAULT public.run_command());',
            'CREATE TABLE IF NOT EXISTS copied (id BIGINT) INHERITS (public.characters);',
            'CREATE TABLE IF NOT EXISTS copied (id BIGINT); DROP SCHEMA public CASCADE;',
            'DO $$ BEGIN PERFORM public.run_command(); END $$;',
        )
        for sql in malicious:
            with self.subTest(sql_kind=sql.split()[0]):
                scripts = read_schema_literals()
                scripts['storage'] = sql
                with patch('integration_manifest.read_schema_literals', return_value=scripts):
                    with self.assertRaises(IntegrationSafetyError):
                        _table_definitions()

    def test_only_line_endings_and_outer_whitespace_may_change(self):
        expected = reviewed_table_definitions()
        scripts = {name: '\n' + script.replace('\n', '\r\n') + '\n'
                   for name, script in read_schema_literals().items()}
        with patch('integration_manifest.read_schema_literals', return_value=scripts):
            self.assertEqual(reviewed_table_definitions(), expected)
        scripts['storage'] += '\n-- new SQL requires review'
        with patch('integration_manifest.read_schema_literals', return_value=scripts):
            with self.assertRaises(ValueError):
                reviewed_table_definitions()

    def test_source_expressions_are_not_executed_or_imported(self):
        with patch('integration_manifest.Path.read_text', return_value='SCHEMA = run_untrusted_code()'):
            with self.assertRaises(ValueError):
                read_schema_literals()

    def test_manifest_call_returns_an_independent_copy(self):
        first = reviewed_table_definitions()
        first['characters'] = '(malicious text)'
        self.assertNotEqual(first, reviewed_table_definitions())


class RecoveryJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.record = RecoveryRecord('dao_it_' + 'a' * 32)

    def test_record_exists_before_any_future_database_activity(self):
        journal = RecoveryJournal(self.directory.name, self.record)
        data = json.loads(journal.path.read_text())
        self.assertEqual(data['outcome'], 'no_writes')
        self.assertEqual(set(data), {'schema', 'phase', 'outcome', 'database', 'role',
                                    'namespace_oid', 'owner_oid', 'inventory_digest', 'table_oids', 'diagnostic'})

    def test_duplicate_journal_is_never_overwritten(self):
        journal = RecoveryJournal(self.directory.name, self.record)
        before = journal.path.read_bytes()
        with self.assertRaises(FileExistsError):
            RecoveryJournal(self.directory.name, self.record)
        self.assertEqual(journal.path.read_bytes(), before)

    def test_tampering_refuses_updates(self):
        journal = RecoveryJournal(self.directory.name, self.record)
        journal.path.write_text('synthetic changed record')
        with self.assertRaises(ValueError):
            journal.save(replace(self.record, phase='creating_schema'))
        self.assertEqual(journal.path.read_text(), 'synthetic changed record')

    def test_failed_atomic_replace_keeps_previous_record(self):
        journal = RecoveryJournal(self.directory.name, self.record)
        before = journal.path.read_bytes()
        with patch('integration_recovery.os.replace', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                journal.save(replace(self.record, phase='committing_setup', outcome='commit_unknown'))
        self.assertEqual(journal.path.read_bytes(), before)
        self.assertEqual([path.resolve() for path in Path(self.directory.name).iterdir()], [journal.path])

    def test_symlink_guard_refuses_updates(self):
        journal = RecoveryJournal(self.directory.name, self.record)
        before = journal.path.read_bytes()
        with patch('integration_recovery.Path.is_symlink', return_value=True):
            with self.assertRaises(ValueError):
                journal.save(replace(self.record, phase='creating_schema'))
        self.assertEqual(journal.path.read_bytes(), before)

    def test_missing_directory_and_non_generated_identifiers_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            RecoveryJournal(Path(self.directory.name) / 'not-created', self.record)
        for schema in ('public', '../outside', 'dao_it_"; DROP SCHEMA public; --'):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    RecoveryJournal(self.directory.name, replace(self.record, schema=schema))


if __name__ == '__main__':
    unittest.main()
