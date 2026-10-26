"""Run console cases 1-6 in a throwaway PostgreSQL container, then remove it.

    python -B run_pg_cases_v025.py --authorize-throwaway-docker

Without the flag nothing runs. Never uses the bot, Discord, .env or an existing
cluster; output is case names and fixed labels only.
"""
import argparse
import asyncio
import sys

FLAG = '--authorize-throwaway-docker'


def _label(error):
    from dev_console_v025 import ConsoleRejected
    from pg_throwaway_v025 import ThrowawayRefused
    if isinstance(error, (AssertionError, ConsoleRejected, ThrowawayRefused)):
        return f'{type(error).__name__}: {error}'  # Fixed labels and messages only.
    return type(error).__name__  # Driver text can contain data; keep the type only.


async def _run_cases(cluster):
    import dev_console_v025 as console
    from pg_cases_v025 import CASES, create_schema
    await cluster.provision(console.MARKER)
    pool = await cluster.app_pool(console.MARKER)
    results = []
    try:
        await create_schema(pool)
        for name, case in CASES:
            try:
                async with asyncio.timeout(60):
                    await case(pool)
            except Exception as error:
                results.append((name, False, _label(error)))
            else:
                results.append((name, True, ''))
            finally:
                console.deactivate()
    finally:
        await pool.close()
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(FLAG, dest='authorized', action='store_true',
                        help='Allow creating and removing one dao-it-<hex> Docker container.')
    if not parser.parse_args(argv).authorized:
        print(f'Refused: pass {FLAG} to create a throwaway PostgreSQL container.')
        return 2
    from pg_throwaway_v025 import ThrowawayCluster
    cluster = ThrowawayCluster()
    results, setup_error = [], None
    try:
        cluster.start()
        print(f'Container {cluster.name} on 127.0.0.1:{cluster.port}, database {cluster.database}, role {cluster.role}')
        results = asyncio.run(_run_cases(cluster))
    except Exception as error:
        setup_error = _label(error)
    finally:
        removed = cluster.close()
    for name, passed, detail in results:
        print(f'{"PASS" if passed else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))
    if setup_error:
        print(f'SETUP FAILED: {setup_error}')
    print(f'Container removed: {"yes" if removed else "NO - remove it with docker rm -f " + cluster.name}')
    ok = not setup_error and removed and len(results) > 0 and all(passed for _, passed, _ in results)
    print('RESULT:', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
