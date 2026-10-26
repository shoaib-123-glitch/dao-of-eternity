"""Allowlisted recovery-inspection diagnostics; never copy driver text or rows."""
import asyncio
from dataclasses import dataclass, replace
from enum import Enum

import asyncpg

from integration_catalog import ROLE_PRIVILEGE_FLAGS, PRIVILEGE_FLAGS, CATALOG_WRITE_DIAGNOSTIC_FLAGS
from integration_support import FailureCategory, PrivilegeStage, IntegrationSafetyError, failure_category


class InspectionStage(str, Enum):
    UNAVAILABLE = 'UNAVAILABLE'
    AUTHORIZATION = 'AUTHORIZATION'
    CONFIGURATION = 'CONFIGURATION'
    EVIDENCE_REVALIDATION = 'EVIDENCE_REVALIDATION'
    EVENT_LOOP = 'EVENT_LOOP'
    CONNECTION = 'CONNECTION'
    TRANSACTION_BEGIN = 'TRANSACTION_BEGIN'
    SESSION_IDENTITY = 'SESSION_IDENTITY'
    CLUSTER_IDENTITY = 'CLUSTER_IDENTITY'
    EFFECTIVE_PRIVILEGES = 'EFFECTIVE_PRIVILEGES'
    CATALOG_WRITE_POLICY = 'CATALOG_WRITE_POLICY'
    PREPARED_ROLE_POLICY = 'PREPARED_ROLE_POLICY'
    SCHEMA_CATALOG = 'SCHEMA_CATALOG'
    SCHEMA_VERIFICATION = 'SCHEMA_VERIFICATION'
    RECOVERY_CATALOG = 'RECOVERY_CATALOG'
    RECOVERY_METADATA = 'RECOVERY_METADATA'
    DATABASE_IDENTITY = 'DATABASE_IDENTITY'
    RECOVERY_SCHEMA_POLICY = 'RECOVERY_SCHEMA_POLICY'
    SCHEMA_ACCESS_POLICY = 'SCHEMA_ACCESS_POLICY'
    SESSION_INSPECTION = 'SESSION_INSPECTION'
    FINAL_READ_ONLY = 'FINAL_READ_ONLY'
    TRANSACTION_COMMIT = 'TRANSACTION_COMMIT'
    TRANSACTION_ROLLBACK = 'TRANSACTION_ROLLBACK'
    EVIDENCE_RECHECK = 'EVIDENCE_RECHECK'
    REPORT = 'REPORT'
    CONNECTION_CLEANUP = 'CONNECTION_CLEANUP'
    QUERY_ALLOWLIST = 'QUERY_ALLOWLIST'


class InspectionCategory(str, Enum):
    CONFIGURATION = 'CONFIGURATION'
    AUTHENTICATION = 'AUTHENTICATION'
    CONNECTION = 'CONNECTION'
    IDENTITY = 'IDENTITY'
    READ_ONLY = 'READ_ONLY'
    PRIVILEGES = 'PRIVILEGES'
    CATALOG_COMPATIBILITY = 'CATALOG_COMPATIBILITY'
    VERSION = 'VERSION'
    SCHEMA_VERIFICATION = 'SCHEMA_VERIFICATION'
    TRANSACTION = 'TRANSACTION'
    TIMEOUT = 'TIMEOUT'
    CONNECTION_CLEANUP = 'CONNECTION_CLEANUP'
    EVIDENCE = 'EVIDENCE'
    CANCELLED = 'CANCELLED'
    SAFETY = 'SAFETY'


_CHECKS = (
    'query_allowed', 'prepared_database_create_absent', 'server_version_verified',
    'role_oid_verified', 'namespace_present', 'namespace_oid_verified',
    'namespace_owner_verified', 'namespace_acl_private', 'metadata_shape_valid',
    'database_oid_verified', 'namespace_exists', 'namespace_identity_verified',
    'acl_private', 'schema_usage', 'schema_create', 'no_relations',
    'no_namespace_dependencies', 'no_extension_placement', 'no_other_guard_sessions',
    'read_only', 'default_read_only',
)
_PRIVILEGE_FLAGS = (*ROLE_PRIVILEGE_FLAGS, *PRIVILEGE_FLAGS, *CATALOG_WRITE_DIAGNOSTIC_FLAGS)
_TRANSACTION_EXITS = ('NOT_ATTEMPTED', 'BEGIN_RAISED', 'COMMIT_ACKNOWLEDGED',
    'ROLLBACK_ACKNOWLEDGED', 'COMMIT_RAISED', 'ROLLBACK_RAISED', 'UNKNOWN')
_CONNECTION_EXITS = ('NOT_ATTEMPTED', 'ACKNOWLEDGED', 'TERMINATED', 'UNCONFIRMED')


@dataclass(frozen=True)
class InspectionDiagnostic:
    stage: InspectionStage = InspectionStage.UNAVAILABLE
    category: InspectionCategory = InspectionCategory.SAFETY
    failed_checks: tuple = ()
    transaction_exit: str = 'NOT_ATTEMPTED'
    connection_cleanup: str = 'NOT_ATTEMPTED'
    secondary_failures: tuple = ()
    privilege_stage: object = None
    privilege_flags: tuple = ()


def safe_inspection_diagnostic(value):
    if type(value) is not InspectionDiagnostic:
        value = InspectionDiagnostic()
    checks = value.failed_checks if type(value.failed_checks) is tuple and len(value.failed_checks) <= len(_CHECKS) else ()
    checks = tuple(item for item in checks if type(item) is str)
    secondary = []
    if type(value.secondary_failures) is tuple:
        for pair in value.secondary_failures[:2]:
            if (type(pair) is tuple and len(pair) == 2 and type(pair[0]) is InspectionStage
                    and type(pair[1]) is InspectionCategory):
                secondary.append({'stage': pair[0].value, 'category': pair[1].value})
    flags = {}
    if type(value.privilege_flags) is tuple:
        for pair in value.privilege_flags[:len(_PRIVILEGE_FLAGS)]:
            if (type(pair) is tuple and len(pair) == 2 and type(pair[0]) is str
                    and pair[0] in _PRIVILEGE_FLAGS and type(pair[1]) is bool):
                flags[pair[0]] = pair[1]
    return {
        'stage': value.stage.value if type(value.stage) is InspectionStage else 'UNAVAILABLE',
        'category': value.category.value if type(value.category) is InspectionCategory else 'SAFETY',
        'failed_checks': [key for key in _CHECKS if key in checks],
        'transaction_exit': value.transaction_exit if type(value.transaction_exit) is str
            and value.transaction_exit in _TRANSACTION_EXITS else 'UNKNOWN',
        'connection_cleanup': value.connection_cleanup if type(value.connection_cleanup) is str
            and value.connection_cleanup in _CONNECTION_EXITS else 'UNCONFIRMED',
        'secondary_failures': secondary,
        'privilege_stage': value.privilege_stage.value if type(value.privilege_stage) is PrivilegeStage else 'UNAVAILABLE',
        'privilege_flags': flags,
    }


def inspection_diagnostic(error):
    value = getattr(error, 'inspection_diagnostic', None)
    return safe_inspection_diagnostic(value) if type(value) is InspectionDiagnostic else None


def _category(error, stage):
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
        return InspectionCategory.CANCELLED
    if isinstance(error, IntegrationSafetyError) and failure_category(error) is not FailureCategory.SAFETY:
        return InspectionCategory(failure_category(error).value)
    if isinstance(error, asyncpg.PostgresError):
        state = getattr(type(error), 'sqlstate', None)
        if type(state) is str:
            if state.startswith('28'):
                return InspectionCategory.AUTHENTICATION
            if state == '42501':
                return InspectionCategory.PRIVILEGES
            if state == '25006':
                return InspectionCategory.READ_ONLY
            if state in ('57014', '55P03'):
                return InspectionCategory.TIMEOUT
            if state.startswith('08') or state in ('57P01', '57P02', '57P03', '53300', '53400'):
                return InspectionCategory.CONNECTION
            if state == '3D000':
                return InspectionCategory.CONFIGURATION
            if state.startswith(('25', '40')):
                return InspectionCategory.TRANSACTION
            if state.startswith(('42', '22')):
                return InspectionCategory.CATALOG_COMPATIBILITY
    if isinstance(error, TimeoutError):
        return InspectionCategory.TIMEOUT
    if isinstance(error, (OSError, asyncpg.InterfaceError)):
        return InspectionCategory.CONNECTION
    if stage in (InspectionStage.TRANSACTION_BEGIN, InspectionStage.TRANSACTION_COMMIT,
                 InspectionStage.TRANSACTION_ROLLBACK):
        return InspectionCategory.TRANSACTION
    if stage is InspectionStage.CONNECTION_CLEANUP:
        return InspectionCategory.CONNECTION_CLEANUP
    if stage in (InspectionStage.EVIDENCE_REVALIDATION, InspectionStage.EVIDENCE_RECHECK):
        return InspectionCategory.EVIDENCE
    if stage in (InspectionStage.SCHEMA_CATALOG, InspectionStage.RECOVERY_CATALOG,
                 InspectionStage.RECOVERY_METADATA, InspectionStage.SESSION_INSPECTION):
        return InspectionCategory.CATALOG_COMPATIBILITY
    return InspectionCategory.SAFETY


class InspectionFailure(IntegrationSafetyError):
    def __init__(self, diagnostic):
        value = safe_inspection_diagnostic(diagnostic)['category']
        category = FailureCategory(value) if value in FailureCategory._value2member_map_ else FailureCategory.SAFETY
        super().__init__('Read-only recovery inspection failed; details suppressed.', category=category)
        self.inspection_diagnostic = diagnostic


class InspectionTrace:
    def __init__(self, stage=InspectionStage.AUTHORIZATION):
        self.stage = stage
        self.primary = None
        self.transaction_exit = 'NOT_ATTEMPTED'
        self.connection_cleanup = 'NOT_ATTEMPTED'
        self.secondary = ()

    def reject(self, category, *checks):
        raise InspectionFailure(InspectionDiagnostic(self.stage, category, tuple(checks))) from None

    def capture(self, error):
        if self.primary is not None:
            return
        existing = getattr(error, 'inspection_diagnostic', None)
        if type(existing) is InspectionDiagnostic:
            self.primary = existing
            return
        stage = getattr(error, 'privilege_stage', None)
        flags = getattr(error, 'privilege_flags', None)
        pairs = tuple((key, flags[key]) for key in _PRIVILEGE_FLAGS
            if type(flags.get(key)) is bool) if type(flags) is dict else ()
        self.primary = InspectionDiagnostic(self.stage, _category(error, self.stage),
            privilege_stage=stage if type(stage) is PrivilegeStage else None, privilege_flags=pairs)

    def secondary_failure(self, error):
        self.secondary = (*self.secondary, (self.stage, _category(error, self.stage)))[:2]

    def diagnostic(self):
        return replace(self.primary or InspectionDiagnostic(self.stage),
            transaction_exit=self.transaction_exit, connection_cleanup=self.connection_cleanup,
            secondary_failures=self.secondary)
