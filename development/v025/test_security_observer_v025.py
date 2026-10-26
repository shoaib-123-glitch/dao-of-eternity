import ast
import importlib
from pathlib import Path
import sys
import unittest

import security_observer_v025 as observer


HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
SECRET='synthetic-secret-message-do-not-export'


class Recorder:
    def __init__(self):
        self.calls=[]

    def emit(self,kind,**fields):
        self.calls.append((kind,fields))


class Broken:
    def __init__(self):
        self.calls=0

    def emit(self,kind,**fields):
        self.calls+=1
        raise RuntimeError(SECRET)


class SecurityObserverTests(unittest.TestCase):
    def setUp(self):
        observer.disable()
        self.addCleanup(observer.disable)

    def good(self,**extra):
        values=dict(actor=111111111111111111,guild=222222222222222222,
            operation='hunt',outcome='denied',reason='not_owner')
        values.update(extra)
        return values

    def test_disabled_by_default_is_noop(self):
        observer.enable(Recorder())
        fresh=importlib.reload(observer)
        self.assertIsNone(fresh._emitter)
        self.assertFalse(fresh.enabled())
        sink=Recorder()
        fresh.enable(sink)
        fresh.disable()
        fresh.record('unauthorized_command',**self.good())
        self.assertEqual(sink.calls,[])

    def test_enable_and_disable(self):
        sink=Recorder()
        observer.enable(sink)
        self.assertTrue(observer.enabled())
        observer.record('unauthorized_command',**self.good())
        self.assertEqual(sink.calls,[('unauthorized_command',dict(actor=111111111111111111,
            guild=222222222222222222,operation='hunt',outcome='denied',reason='not_owner'))])
        observer.disable()
        self.assertFalse(observer.enabled())
        observer.record('unauthorized_command',**self.good())
        self.assertEqual(len(sink.calls),1)
        with self.assertRaises(TypeError):
            observer.enable(object())
        self.assertFalse(observer.enabled())

    def test_never_raises_when_emitter_throws(self):
        broken=Broken()
        observer.enable(broken)
        observer.record('command_completed',**self.good(outcome='success',reason=None))
        self.assertEqual(broken.calls,1)

    def test_disallowed_values_dropped(self):
        sink=Recorder()
        observer.enable(sink)
        cases=[('free text',{}),('service_start',{}),(None,{}),
            ('unauthorized_command',dict(operation='drop table')),
            ('unauthorized_command',dict(operation=None)),
            ('unauthorized_command',dict(outcome='pwned')),
            ('unauthorized_command',dict(outcome='healthy')),
            ('unauthorized_command',dict(reason=SECRET)),
            ('unauthorized_command',dict(reason=7)),
            ('unauthorized_command',dict(actor='Lin Feng')),
            ('unauthorized_command',dict(guild=True)),
            ('unauthorized_command',dict(actor=-5)),
            ('unauthorized_command',dict(actor=1.5))]
        for kind,extra in cases:
            observer.record(kind,**self.good(**extra))
        self.assertEqual(sink.calls,[])

    def test_no_free_text_passed(self):
        sink=Recorder()
        observer.enable(sink)
        observer.record('unexpected_error',**self.good(outcome='failure',reason=observer.classify(ValueError(SECRET))))
        observer.record('database_failure',**self.good(outcome='failure',reason=observer.classify(RuntimeError(SECRET))))
        self.assertEqual(len(sink.calls),2)
        for kind,fields in sink.calls:
            self.assertEqual(set(fields),{'actor','guild','operation','outcome','reason'})
            for value in (kind,fields['operation'],fields['outcome'],fields['reason']):
                self.assertIn(value,observer.KINDS|observer.OPERATIONS|observer.OUTCOMES|observer.REASONS)
            self.assertNotIn(SECRET,repr(fields))

    def test_classify_never_includes_message(self):
        class Weird(Exception):
            def __str__(self):
                raise AssertionError('message read')
            __repr__=__str__
        cases={ValueError(SECRET):'expected',KeyError(SECRET):'expected',
            TimeoutError(SECRET):'timeout',ConnectionResetError(SECRET):'connection_lost',
            UnicodeDecodeError('utf-8',b'\xff',0,1,SECRET):'decode',
            RuntimeError(SECRET):'unexpected',Weird(SECRET):'unexpected',None:'unexpected'}
        for error,reason in cases.items():
            result=observer.classify(error)
            self.assertEqual(result,reason)
            self.assertIn(result,observer.REASONS)
            self.assertNotIn('synthetic',result)

    def test_module_has_no_os_socket_subprocess_environ_or_open(self):
        tree=ast.parse((HERE/'security_observer_v025.py').read_text(encoding='utf-8'))
        imports=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
        self.assertEqual(imports,[])
        names={n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}
        attrs={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
        banned={'os','socket','subprocess','environ','getenv','open','eval','exec',
            '__import__','print','observability','asyncpg','discord','logging'}
        self.assertFalse(banned&(names|attrs))

    def test_end_to_end_with_real_emitter(self):
        added=str(ROOT) not in sys.path
        if added:
            sys.path.append(str(ROOT))
        try:
            from observability import events
        finally:
            if added:
                sys.path.remove(str(ROOT))
        self.assertLessEqual(observer.KINDS,set(events.KINDS))
        self.assertLessEqual(observer.OPERATIONS,events.OPERATIONS)
        self.assertLessEqual(observer.OUTCOMES,events.OUTCOMES)
        self.assertLessEqual(observer.REASONS,events.REASONS)
        out=[]
        emitter=events.EventEmitter(b'synthetic-observer-test-key-0123456789abcdef',
            sink=out.append,instance_id='observer-test')
        observer.enable(emitter)
        actor,guild=987654321098765432,123456789012345678
        observer.record('unauthorized_command',actor=actor,guild=guild,operation='story',
            outcome='denied',reason='not_owner')
        observer.record('transaction_replay',actor=actor,guild=guild,operation='bond',
            outcome='duplicate',reason='duplicate_interaction')
        observer.record('unexpected_error',actor=actor,guild=guild,operation='hunt',
            outcome='failure',reason=observer.classify(RuntimeError(SECRET)))
        self.assertEqual(len(out),3)
        for event in out:
            text=event.to_json()
            self.assertNotIn(str(actor),text)
            self.assertNotIn(str(guild),text)
            self.assertNotIn(SECRET,text)
            self.assertTrue(event.actor_ref.startswith('actor_'))
            self.assertTrue(event.guild_ref.startswith('guild_'))
            events.SecurityEvent.from_dict(event.to_dict())
        self.assertEqual(out[2].reason,'unexpected')


if __name__=='__main__':
    unittest.main()
