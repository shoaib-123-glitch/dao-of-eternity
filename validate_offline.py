"""Run every unittest under offline guards. No Docker, database or bot startup.

Run with python -B -E -s validate_offline.py. Historical evidence and environment
files are denied even if a regression accidentally attempts to open them.
"""
import ast
import importlib.abc
import logging
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parent
HISTORY = os.path.normcase(str(PROJECT / 'integration_recovery'))
HISTORIES = (HISTORY, os.path.normcase(str(PROJECT / 'fresh_integration')),
             os.path.normcase(str(PROJECT / 'fresh_inspection_records')))
VIOLATIONS = []


def audit(event, args):
    forbidden = event in {'subprocess.Popen', 'os.system', 'os.posix_spawn',
                          'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
                          'socket.sendto', 'socket.sendmsg'}
    if event == 'socket.connect' and sys.platform == 'win32':
        # Windows asyncio needs the stdlib's local wakeup pipe. Admit only its
        # exact socketpair implementation connecting to its own listening socket.
        frame = sys._getframe(1)
        fallback = getattr(socket, '_fallback_socketpair', None)
        listener = frame.f_locals.get('lsock')
        if (fallback is not None and frame.f_code is fallback.__code__
                and isinstance(listener, socket.socket)
                and args[1] == listener.getsockname()
                and args[1][0] == '127.0.0.1' and args[1][1] not in (5432, 5433)):
            forbidden = False
    if event in {'open', 'os.listdir', 'os.scandir', 'os.remove', 'os.rmdir',
                 'os.mkdir', 'os.rename', 'os.chmod', 'os.truncate', 'os.link', 'os.symlink'}:
        # Rename/link/symlink have two paths; examine every path argument.
        for value in args:
            if not isinstance(value, (str, bytes, os.PathLike)):
                continue
            path = os.path.normcase(os.path.abspath(os.fsdecode(value)))
            parts = Path(path).parts
            forbidden |= (any(path == root or path.startswith(root + os.sep) for root in HISTORIES)
                          or any(p == '.env' or p.startswith('.env.') for p in parts))
    if forbidden:
        VIOLATIONS.append(event)  # Never record paths, arguments or credentials.
        raise RuntimeError('OFFLINE_GUARD_REJECTED')


class NoStartup(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'bot', 'config'}:
            VIOLATIONS.append('startup_import')
            raise RuntimeError('OFFLINE_STARTUP_REJECTED')
        return None


async def no_database(*args, **kwargs):
    VIOLATIONS.append('database_connect')
    raise RuntimeError('OFFLINE_DATABASE_REJECTED')


def main():
    sys.dont_write_bytecode = True
    sys.addaudithook(audit)
    sys.meta_path.insert(0, NoStartup())
    os.chdir(PROJECT)
    # Parse/compile only; no imports of config, bot or simulation entry points.
    for path in PROJECT.glob('*.py'):
        compile(ast.parse(path.read_text(encoding='utf-8-sig')), str(path), 'exec')
    # Python 3.15's Windows platform fallback can invoke cmd.exe just to obtain
    # a version string. Supply that metadata from the OS API under the same guard.
    if sys.platform == 'win32':
        version = sys.getwindowsversion()
        metadata = ('Windows', '', f'{version.major}.{version.minor}.{version.build}')
        with patch('platform._syscmd_ver', return_value=metadata):
            import asyncpg
    else:
        import asyncpg
    with patch.object(asyncpg, 'connect', no_database), patch.object(asyncpg, 'create_pool', no_database):
        suite = unittest.defaultTestLoader.discover(str(PROJECT), pattern='test_*.py')
        # Audit checks make asyncio debug timing noisy on Windows; retain errors.
        logging.getLogger('asyncio').setLevel(logging.ERROR)
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
    print('Offline guard violations:', len(VIOLATIONS))
    print('Docker invocations: 0; PostgreSQL connections: 0; database-backed suites: 0')
    return 0 if result.wasSuccessful() and not result.skipped and not VIOLATIONS else 1


if __name__ == '__main__':
    raise SystemExit(main())
