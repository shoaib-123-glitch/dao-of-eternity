"""Optional security telemetry hook. Disabled by default; never alters gameplay.

No environment, file, network or observability import. A caller explicitly passes
an emitter (observability.EventEmitter API) that pseudonymizes actor/guild ids.
Only fixed allowlisted classifications leave this module; never text or messages.
"""
KINDS=frozenset({'authz_failure','unauthorized_command','transaction_replay',
    'command_submitted','command_completed','database_failure','unexpected_error'})
OPERATIONS=frozenset({'command','start','qi','hunt','craft','equip','explore',
    'sect_commission','sect_project','story','npc','technique','bond','guide',
    'companion','cultivation','inventory','adopt','recover'})
OUTCOMES=frozenset({'accepted','denied','duplicate','success','failure'})
REASONS=frozenset({'not_owner','not_guild','missing_permission','unknown_operation',
    'duplicate_interaction','cooldown','rate_limit','timeout','connection_lost',
    'decode','expected','unexpected','disabled'})
_emitter=None


def enable(emitter):
    global _emitter
    if not callable(getattr(emitter,'emit',None)):
        raise TypeError('SECURITY_OBSERVER_EMITTER_REQUIRED')
    _emitter=emitter


def disable():
    global _emitter
    _emitter=None


def enabled():
    return _emitter is not None


def _id(value):
    return value is None or (type(value) is int and value>0)


def _pick(value,choices):
    return type(value) is str and value in choices


def record(kind,*,actor,guild,operation,outcome,reason=None):
    try:
        emitter=_emitter
        if emitter is None or not (_pick(kind,KINDS) and _pick(operation,OPERATIONS)
                and _pick(outcome,OUTCOMES) and (reason is None or _pick(reason,REASONS))
                and _id(actor) and _id(guild)):
            return None
        emitter.emit(kind,actor=actor,guild=guild,operation=operation,outcome=outcome,reason=reason)
    except Exception:
        pass  # Telemetry failure must never change a transaction or reply.
    return None


def classify(error):
    # Fixed type mapping only; the message, args and repr are never inspected.
    try:
        if isinstance(error,TimeoutError):
            return 'timeout'
        if isinstance(error,ConnectionError):
            return 'connection_lost'
        if isinstance(error,UnicodeError):
            return 'decode'
        if isinstance(error,(ValueError,LookupError)):
            return 'expected'
    except Exception:
        pass
    return 'unexpected'
