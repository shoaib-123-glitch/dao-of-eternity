"""Fixed inventory-fetch failure metadata. Never read messages, queries or args."""
import asyncio
from dataclasses import dataclass
from enum import Enum
import re

import asyncpg


class FetchFamily(str, Enum):
    SERVER_REJECTION = 'SERVER_REJECTION'
    SERVER_CANCELLATION = 'SERVER_CANCELLATION'
    SERVER_TIMEOUT = 'SERVER_TIMEOUT'
    SERVER_CONNECTION = 'SERVER_CONNECTION'
    POSTGRES_UNATTRIBUTED = 'POSTGRES_UNATTRIBUTED'
    DRIVER_DECODE = 'DRIVER_DECODE'
    DRIVER_PROTOCOL = 'DRIVER_PROTOCOL'
    DRIVER_INTERFACE = 'DRIVER_INTERFACE'
    DRIVER_INTERNAL = 'DRIVER_INTERNAL'
    TIMEOUT = 'TIMEOUT'
    CONNECTION = 'CONNECTION'
    OS_ERROR = 'OS_ERROR'
    PYTHON_ERROR = 'PYTHON_ERROR'
    CANCELLED = 'CANCELLED'
    UNKNOWN = 'UNKNOWN'


# Exact reviewed values, not arbitrary strings that merely resemble SQLSTATEs.
SQLSTATE_CLASSES = frozenset('08 0A 0B 0F 0L 0P 20 21 22 23 24 25 26 27 28 2B 2D 2F 34 38 39 3B 3D 3F 40 42 44 53 54 55 57 58 72 F0 HV P0 XX'.split())
SQLSTATE_CODES = frozenset('''08000 08001 08003 08004 08006 08007 08P01 0A000
22000 22003 22004 22012 22021 22023 22P02 22P03 23000 23502 23503 23505 23514
25000 25001 25006 25P02 25P03 25P04 26000 28000 28P01 3D000 3F000
40000 40001 40P01 42000 42501 42601 42703 42704 42804 42883 42P01 42P02 42P18
53000 53100 53200 53300 53400 54000 54001 54011 54023 55000 55006 55P03
57000 57014 57P01 57P02 57P03 57P04 57P05 58000 58030 58P01 58P02
P0000 P0001 XX000 XX001 XX002'''.split())
SOURCES = ('SERVER_FIELD', 'CLASS_DEFAULT', 'UNAVAILABLE')


@dataclass(frozen=True)
class FetchDiagnostic:
    family: FetchFamily = FetchFamily.UNKNOWN
    sqlstate_class: str | None = None
    sqlstate_code: str | None = None
    sqlstate_source: str = 'UNAVAILABLE'


def safe_fetch_diagnostic(value):
    if type(value) is not FetchDiagnostic:
        value = FetchDiagnostic()
    family = value.family if type(value.family) is FetchFamily else FetchFamily.UNKNOWN
    cls = value.sqlstate_class if type(value.sqlstate_class) is str and value.sqlstate_class in SQLSTATE_CLASSES else None
    code = value.sqlstate_code if type(value.sqlstate_code) is str and value.sqlstate_code in SQLSTATE_CODES else None
    source = value.sqlstate_source if type(value.sqlstate_source) is str and value.sqlstate_source in SOURCES else 'UNAVAILABLE'
    if source == 'UNAVAILABLE' or family not in (
            FetchFamily.SERVER_REJECTION, FetchFamily.SERVER_CANCELLATION, FetchFamily.SERVER_TIMEOUT,
            FetchFamily.SERVER_CONNECTION, FetchFamily.POSTGRES_UNATTRIBUTED):
        cls, code, source = None, None, 'UNAVAILABLE'
    if code is not None and code[:2] != cls:
        code = None
    return dict(family=family.value, sqlstate_class=cls, sqlstate_code=code, sqlstate_source=source)


def decode_fetch_diagnostic(payload):
    if type(payload) is not dict or set(payload) != set(safe_fetch_diagnostic(None)):
        raise ValueError('Invalid inventory-fetch metadata.')
    value = FetchDiagnostic(**{**payload, 'family': FetchFamily(payload['family'])})
    if safe_fetch_diagnostic(value) != payload:
        raise ValueError('Invalid inventory-fetch metadata.')
    # Preserve provenance semantics in durable records as well as in the writer.
    server = value.family in (FetchFamily.SERVER_REJECTION, FetchFamily.SERVER_CANCELLATION,
                             FetchFamily.SERVER_TIMEOUT, FetchFamily.SERVER_CONNECTION)
    if server and value.sqlstate_source != 'SERVER_FIELD':
        raise ValueError('Invalid inventory-fetch provenance.')
    return value


def _state(error):
    # asyncpg's ErrorResponse factory stores the server SQLSTATE on the instance.
    # Its parameter encoder can instead raise DataError with only a class default.
    try:
        fields = BaseException.__getattribute__(error, '__dict__')
        if type(fields) is dict and 'sqlstate' in fields:
            raw, source = fields['sqlstate'], 'SERVER_FIELD'
        else:
            raw, source = getattr(type(error), 'sqlstate', None), 'CLASS_DEFAULT'
    except Exception:
        return None, None, None, 'UNAVAILABLE'
    if type(raw) is not str or not re.fullmatch('[0-9A-Z]{5}', raw):
        return None, None, None, 'UNAVAILABLE'
    return raw, raw[:2] if raw[:2] in SQLSTATE_CLASSES else None, raw if raw in SQLSTATE_CODES else None, source


def classify_fetch_failure(error):
    """Classify only reliable types/provenance; unstructured codec errors stay Python."""
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
        return FetchDiagnostic(FetchFamily.CANCELLED)
    if isinstance(error, TimeoutError):
        return FetchDiagnostic(FetchFamily.TIMEOUT)
    if isinstance(error, asyncpg.OutdatedSchemaCacheError):
        return FetchDiagnostic(FetchFamily.DRIVER_DECODE)
    if isinstance(error, asyncpg.ProtocolError):
        return FetchDiagnostic(FetchFamily.DRIVER_PROTOCOL)
    if isinstance(error, asyncpg.InternalClientError):
        return FetchDiagnostic(FetchFamily.DRIVER_INTERNAL)
    if isinstance(error, asyncpg.PostgresError):
        raw, cls, code, source = _state(error)
        family = FetchFamily.POSTGRES_UNATTRIBUTED
        if source == 'SERVER_FIELD':
            family = FetchFamily.SERVER_REJECTION
            if raw == '57014':  # May be operator cancellation, not a timeout.
                family = FetchFamily.SERVER_CANCELLATION
            elif raw in ('25P03', '25P04', '57P05'):
                family = FetchFamily.SERVER_TIMEOUT
            elif raw[:2] == '08' or raw in ('57P01', '57P02', '57P03', '57P04'):
                family = FetchFamily.SERVER_CONNECTION
        return FetchDiagnostic(family, cls, code, source)
    if isinstance(error, asyncpg.InterfaceError):
        return FetchDiagnostic(FetchFamily.DRIVER_INTERFACE)
    if isinstance(error, ConnectionError):
        return FetchDiagnostic(FetchFamily.CONNECTION)
    if isinstance(error, OSError):
        return FetchDiagnostic(FetchFamily.OS_ERROR)
    return FetchDiagnostic(FetchFamily.PYTHON_ERROR if isinstance(error, Exception) else FetchFamily.UNKNOWN)
