"""Offline checks of every throwaway-PostgreSQL refusal rule. No Docker, database or network."""
import fnmatch
import io
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pg_throwaway_v025 as throwaway
import run_pg_cases_v025 as runner

PROJECT = Path(__file__).resolve().parent
TOKEN = '0123456789abcdef0123456789abcdef'
CONTAINER_ID = 'a' * 64
MARKER = 'dao-v025-developer-sandbox'


class FakeDocker:
    """Stands in for subprocess.run; records argv and the password environment only."""
    def __init__(self, port='127.0.0.1:49153\n', logs='PostgreSQL init process complete; ready for start up.'):
        self.calls, self.port, self.logs = [], port, logs

    def __call__(self, arguments, **kwargs):
        self.calls.append((list(arguments), (kwargs.get('env') or {}).get('POSTGRES_PASSWORD')))
        out = {'run': CONTAINER_ID + '\n', 'port': self.port, 'logs': self.logs, 'rm': CONTAINER_ID,
               'restart': CONTAINER_ID}[arguments[1]]
        return SimpleNamespace(returncode=0, stdout=out, stderr='')


def identity(**changes):
    row = dict(database_name='dao_it_' + TOKEN, rolsuper=False, rolcreatedb=False, rolcreaterole=False,
               marker=MARKER, owns_database=True)
    row.update(changes)
    return row


class NameRules(unittest.TestCase):
    def test_container_names(self):
        self.assertEqual(throwaway.check_container_name('dao-it-' + TOKEN), 'dao-it-' + TOKEN)
        for name in ('dao-postgres18-test', 'dao-it-' + TOKEN.upper(), 'dao-it-abc', 'dao-it-' + TOKEN + 'x',
                     'x-dao-it-' + TOKEN, '', None, 5):
            with self.subTest(name=name), self.assertRaises(throwaway.ThrowawayRefused):
                throwaway.check_container_name(name)

    def test_database_and_role_names(self):
        throwaway.check_database_name('dao_it_' + TOKEN)
        throwaway.check_role_name('dao_it_app_' + TOKEN[:12])
        for name in ('dao_playtest_v023_rc1', 'dao_playtest_v023_v025_dev', 'dao_eternity_test', 'postgres',
                     'dao_it_' + TOKEN[:31], 'DAO_IT_' + TOKEN, None):
            with self.subTest(name=name), self.assertRaises(throwaway.ThrowawayRefused):
                throwaway.check_database_name(name)
        for name in ('postgres', 'dao_test_user', 'dao_it_app_' + TOKEN):
            with self.subTest(role=name), self.assertRaises(throwaway.ThrowawayRefused):
                throwaway.check_role_name(name)

    def test_generated_names_follow_the_rules(self):
        cluster = throwaway.ThrowawayCluster()
        throwaway.check_container_name(cluster.name)
        throwaway.check_database_name(cluster.database)
        throwaway.check_role_name(cluster.role)
        self.assertNotIn(cluster.app_password, repr(cluster))


class PortRules(unittest.TestCase):
    def test_only_one_random_loopback_port(self):
        self.assertEqual(throwaway.parse_port('127.0.0.1:49153\n'), 49153)
        for output in ('0.0.0.0:49153', '[::]:49153', ':::49153', '127.0.0.1:49153\n[::1]:49153',
                       '127.0.0.1:5432', '127.0.0.1:5433', '127.0.0.1:80', '127.0.0.1:99999', '', None, 'garbage'):
            with self.subTest(output=output), self.assertRaises(throwaway.ThrowawayRefused):
                throwaway.parse_port(output)


class IdentityRules(unittest.TestCase):
    def test_plain_owner_in_marked_database_is_accepted(self):
        throwaway.check_identity(identity(), 'dao_it_' + TOKEN, MARKER)

    def test_superuser_privileges_wrong_database_marker_or_owner_are_refused(self):
        for changes in (dict(rolsuper=True), dict(rolcreatedb=True), dict(rolcreaterole=True), dict(rolsuper=None),
                        dict(marker=''), dict(marker='other'), dict(owns_database=False),
                        dict(database_name='dao_playtest_v023_v025_dev')):
            with self.subTest(changes=changes), self.assertRaises(throwaway.ThrowawayRefused):
                throwaway.check_identity(identity(**changes), 'dao_it_' + TOKEN, MARKER)
        with self.assertRaises(throwaway.ThrowawayRefused):
            throwaway.check_identity(None, 'dao_it_' + TOKEN, MARKER)
        with self.assertRaises(throwaway.ThrowawayRefused):
            throwaway.check_identity(identity(database_name='postgres'), 'postgres', MARKER)


class DockerRules(unittest.TestCase):
    def test_only_run_port_logs_and_rm_are_allowed(self):
        for arguments in (['docker', 'ps'], ['docker', 'stop', 'dao-postgres18-test'], ['docker', 'exec'],
                          ['sh', 'run'], []):
            with self.subTest(arguments=arguments), patch.object(throwaway.subprocess, 'run') as run, \
                    self.assertRaises(throwaway.ThrowawayRefused):
                throwaway._docker(arguments)
            run.assert_not_called()

    def test_run_arguments_publish_loopback_only_and_never_carry_the_password(self):
        arguments = throwaway.run_arguments('dao-it-' + TOKEN)
        self.assertEqual(arguments[arguments.index('-p') + 1], '127.0.0.1::5432')
        self.assertEqual(arguments[arguments.index('-e') + 1], 'POSTGRES_PASSWORD')
        self.assertEqual(arguments[arguments.index('--label') + 1], 'dao.it=1')
        self.assertFalse(any('=' in part for part in arguments if part != 'dao.it=1'))
        with self.assertRaises(throwaway.ThrowawayRefused):
            throwaway.run_arguments('dao-postgres18-test')

    def test_start_passes_password_by_environment_and_records_the_exact_id(self):
        docker = FakeDocker()
        cluster = throwaway.ThrowawayCluster(TOKEN)
        with patch.object(throwaway.subprocess, 'run', docker), patch.object(throwaway.time, 'sleep'):
            cluster.start()
        (run_argv, run_password), *_ = docker.calls
        self.assertTrue(run_password)
        self.assertFalse(any(run_password in part for part in run_argv))
        self.assertEqual((cluster.container_id, cluster.port), (CONTAINER_ID, 49153))
        self.assertTrue(all(password is None for _, password in docker.calls[1:]))

    def test_start_refuses_a_public_port_and_still_removes_only_its_container(self):
        docker = FakeDocker(port='0.0.0.0:49153')
        cluster = throwaway.ThrowawayCluster(TOKEN)
        with patch.object(throwaway.subprocess, 'run', docker), patch.object(throwaway.time, 'sleep'):
            with self.assertRaises(throwaway.ThrowawayRefused):
                cluster.start()
            self.assertTrue(cluster.close())
        self.assertEqual(docker.calls[-1][0], ['docker', 'rm', '-f', CONTAINER_ID])
        self.assertIsNone(cluster.container_id)

    def test_restart_targets_only_the_created_id_and_rechecks_the_port(self):
        with patch.object(throwaway.subprocess, 'run') as run, self.assertRaises(throwaway.ThrowawayRefused):
            throwaway.ThrowawayCluster(TOKEN).restart()
        run.assert_not_called()
        docker = FakeDocker()
        cluster = throwaway.ThrowawayCluster(TOKEN)
        with patch.object(throwaway.subprocess, 'run', docker), patch.object(throwaway.time, 'sleep'):
            cluster.start()
            docker.port = '127.0.0.1:50001'
            cluster.restart()
            self.assertEqual(cluster.port, 50001)
            docker.port = '0.0.0.0:50002'
            with self.assertRaises(throwaway.ThrowawayRefused):
                cluster.restart()
        self.assertIn(['docker', 'restart', CONTAINER_ID], [argv for argv, _ in docker.calls])
        self.assertIsNone(cluster.port)

    def test_close_without_a_created_container_runs_nothing(self):
        with patch.object(throwaway.subprocess, 'run') as run:
            self.assertTrue(throwaway.ThrowawayCluster(TOKEN).close())
        run.assert_not_called()

    def test_unfinished_initialisation_times_out(self):
        docker = FakeDocker(logs='still starting')
        with patch.object(throwaway.subprocess, 'run', docker), patch.object(throwaway.time, 'sleep'), \
                self.assertRaises(throwaway.ThrowawayRefused):
            throwaway.ThrowawayCluster(TOKEN).start(ready_timeout=-1)


class ProvisionRules(unittest.IsolatedAsyncioTestCase):
    async def test_provision_requires_a_started_container(self):
        with self.assertRaises(throwaway.ThrowawayRefused):
            await throwaway.ThrowawayCluster(TOKEN).provision(MARKER)

    async def test_marker_changes_require_a_started_container_and_text_or_none(self):
        cluster = throwaway.ThrowawayCluster(TOKEN)
        with self.assertRaises(throwaway.ThrowawayRefused):
            await cluster.set_marker(None)
        cluster.port = 49153
        with self.assertRaises(throwaway.ThrowawayRefused):
            await cluster.set_marker(5)


class RunnerRules(unittest.TestCase):
    def test_runner_refuses_without_the_explicit_flag(self):
        with patch.object(throwaway.subprocess, 'run') as run, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(runner.main([]), 2)
            with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                runner.main(['--authorize-docker'])
        run.assert_not_called()
        self.assertIn('Refused', out.getvalue())

    def test_pg_case_files_are_not_discovered_by_the_offline_suite(self):
        for name in ('pg_cases_v025.py', 'run_pg_cases_v025.py', 'pg_throwaway_v025.py'):
            self.assertTrue((PROJECT / name).exists(), name)
            self.assertFalse(fnmatch.fnmatch(name, 'test_*.py'), name)
        source = (PROJECT / 'validate_offline.py').read_text(encoding='utf-8')
        self.assertIn("pattern='test_*.py'", source)
        for path in PROJECT.glob('test_*.py'):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding='utf-8-sig')
            self.assertNotIn('import pg_cases_v025', text)
            self.assertNotIn('from pg_cases_v025', text)


if __name__ == '__main__':
    unittest.main()
