"""Fixed setup diagnostics. Never inspect exception messages or SQL fields."""
import asyncio
from dataclasses import dataclass
from enum import Enum

import asyncpg

from integration_manifest import TABLE_DEFINITIONS
from inventory_fetch_diagnostics import safe_fetch_diagnostic, decode_fetch_diagnostic


class SetupStage(str, Enum):
    UNAVAILABLE = 'UNAVAILABLE'
    JOURNAL_PERSISTENCE = 'JOURNAL_PERSISTENCE'
    CONNECTION = 'CONNECTION'
    IDENTITY_VERIFICATION = 'IDENTITY_VERIFICATION'
    NAMESPACE_VERIFICATION = 'NAMESPACE_VERIFICATION'
    ENABLE_WRITES = 'ENABLE_WRITES'
    BEGIN_SETUP = 'BEGIN_SETUP'
    CREATE_SCHEMA = 'CREATE_SCHEMA'
    SEARCH_PATH_SETUP = 'SEARCH_PATH_SETUP'
    CREATE_TABLE = 'CREATE_TABLE'
    INVENTORY_QUERY = 'INVENTORY_QUERY'
    INVENTORY_VALIDATION = 'INVENTORY_VALIDATION'
    COMMIT_SETUP = 'COMMIT_SETUP'
    RESTORE_READ_ONLY = 'RESTORE_READ_ONLY'
    CONNECTION_CLEANUP = 'CONNECTION_CLEANUP'


class SetupCategory(str, Enum):
    UNAVAILABLE = 'UNAVAILABLE'
    CONFIGURATION = 'CONFIGURATION'
    AUTHENTICATION = 'AUTHENTICATION'
    IDENTITY = 'IDENTITY'
    VERSION = 'VERSION'
    CATALOG_COMPATIBILITY = 'CATALOG_COMPATIBILITY'
    NONE = 'NONE'
    SAFETY = 'SAFETY'
    CANCELLED = 'CANCELLED'
    JOURNAL_IO = 'JOURNAL_IO'
    PRIVILEGES = 'PRIVILEGES'
    READ_ONLY = 'READ_ONLY'
    SQL_COMPATIBILITY = 'SQL_COMPATIBILITY'
    TRANSACTION = 'TRANSACTION'
    TIMEOUT = 'TIMEOUT'
    CONNECTION = 'CONNECTION'
    CONNECTION_CLEANUP = 'CONNECTION_CLEANUP'


TABLE_NAMES = tuple(name for name, _ in TABLE_DEFINITIONS)
ROLLBACK_STATES = ('NOT_ATTEMPTED', 'UNKNOWN', 'ACKNOWLEDGED', 'RAISED')
CLEANUP_STATES = ('NOT_ATTEMPTED', 'UNKNOWN', 'ACKNOWLEDGED', 'TERMINATED', 'UNCONFIRMED')


@dataclass(frozen=True)
class SetupDiagnostic:
    stage: SetupStage = SetupStage.UNAVAILABLE
    category: SetupCategory = SetupCategory.NONE
    statement_ordinal: int = 0
    table: str | None = None
    rollback: str = 'NOT_ATTEMPTED'
    rollback_verification: str = 'UNKNOWN'
    connection_cleanup: str = 'NOT_ATTEMPTED'
    inventory_fetch: object = None


def safe_setup_diagnostic(value):
    """Revalidate every output field; arbitrary attributes never reach output."""
    if type(value) is not SetupDiagnostic:
        value = SetupDiagnostic(category=SetupCategory.UNAVAILABLE, rollback='UNKNOWN', connection_cleanup='UNKNOWN')
    stage = value.stage if type(value.stage) is SetupStage else SetupStage.UNAVAILABLE
    category = value.category if type(value.category) is SetupCategory else SetupCategory.SAFETY
    ordinal = value.statement_ordinal
    table = value.table
    if (type(ordinal) is not int or not 1 <= ordinal <= len(TABLE_NAMES)
            or type(table) is not str or table != TABLE_NAMES[ordinal - 1]):
        ordinal, table = 0, None
    result = dict(stage=stage.value, category=category.value,
                statement_ordinal=ordinal, table=table,
                rollback=value.rollback if type(value.rollback) is str and value.rollback in ROLLBACK_STATES else 'UNKNOWN',
                rollback_verification='UNKNOWN',
                connection_cleanup=value.connection_cleanup if type(value.connection_cleanup) is str
                and value.connection_cleanup in CLEANUP_STATES else 'UNCONFIRMED')
    if value.inventory_fetch is not None:
        result['inventory_fetch'] = safe_fetch_diagnostic(value.inventory_fetch)
    return result


def decode_setup_diagnostic(payload):
    """Strict reader for new journals; legacy journals have no diagnostic field."""
    if payload is None:
        return None
    keys = set(safe_setup_diagnostic(None))
    if type(payload) is not dict or set(payload) not in (keys, keys | {'inventory_fetch'}):
        raise ValueError('Invalid diagnostic metadata.')
    if type(payload['statement_ordinal']) is not int:
        raise ValueError('Invalid diagnostic metadata.')
    fields = dict(payload)
    if 'inventory_fetch' in fields:
        fields['inventory_fetch'] = decode_fetch_diagnostic(fields['inventory_fetch'])
    value = SetupDiagnostic(**{**fields, 'stage': SetupStage(payload['stage']),
                               'category': SetupCategory(payload['category'])})
    if safe_setup_diagnostic(value) != payload:
        raise ValueError('Invalid diagnostic metadata.')
    return value


def setup_category(error, stage):
    if isinstance(error, asyncio.CancelledError):
        return SetupCategory.CANCELLED
    if stage is SetupStage.JOURNAL_PERSISTENCE:
        return SetupCategory.JOURNAL_IO
    if stage is SetupStage.CONNECTION_CLEANUP:
        return SetupCategory.CONNECTION_CLEANUP
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return SetupCategory.TIMEOUT
    if isinstance(error, asyncpg.PostgresError):
        state = getattr(type(error), 'sqlstate', None)
        if type(state) is str:
            if state == '42501':
                return SetupCategory.PRIVILEGES
            if state == '25006':
                return SetupCategory.READ_ONLY
            if state in ('57014', '55P03'):
                return SetupCategory.TIMEOUT
            if state.startswith('42'):
                return SetupCategory.SQL_COMPATIBILITY
            if state.startswith(('25', '40')):
                return SetupCategory.TRANSACTION
            if state.startswith('28'):
                return SetupCategory.AUTHENTICATION
            if state.startswith(('08', '57')):
                return SetupCategory.CONNECTION
    if isinstance(error, (OSError, asyncpg.InterfaceError)):
        return SetupCategory.CONNECTION
    return SetupCategory.SAFETY
