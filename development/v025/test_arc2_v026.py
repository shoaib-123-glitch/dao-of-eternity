"""Arc II, The Silent Lotus: pure rules over synthetic state; no database."""
from datetime import timedelta
import unittest

import arc2_v026 as arc2
import living_world_v025 as world
from test_living_world_v025 import T, prepare_choice


def arc_one_done():
    state, _, _ = world.choose(prepare_choice(chapter=2), 2, 'reconcile', T)
    return state


def explore_and_choose(state, chapter, choice):
    region = arc2.CHAPTERS[chapter - arc2.OFFSET]['region']
    state, line = arc2.encounter(state, region, T)
    assert line
    return arc2.choose(state, chapter, choice, T + timedelta(minutes=1))


class ArcTwoTests(unittest.TestCase):
    def test_locked_until_arc_one_is_complete(self):
        state = prepare_choice(chapter=1)
        self.assertFalse(arc2.unlocked(state))
        self.assertEqual(arc2.panel(state), world.panel(state))
        self.assertEqual(arc2.encounter(state, 'mistwood', T), (state, ''))
        self.assertEqual(arc2.status(state), world.status(state))
        with self.assertRaises(ValueError):
            arc2.choose(state, 3, 'listen', T)

    def test_chapter_four_needs_mistwood_proof(self):
        state = arc_one_done()
        view = arc2.panel(state)
        self.assertEqual((view['chapter'], view['region'], view['ready']), (3, 'mistwood', False))
        self.assertEqual(arc2.encounter(state, 'stonepeak', T)[1], '')
        with self.assertRaisesRegex(ValueError, 'Explore Mistwood'):
            arc2.choose(state, 3, 'listen', T)
        state, line = arc2.encounter(state, 'mistwood', T)
        self.assertIn('Lanterns Gone Dark', line)
        self.assertEqual(len(arc2.panel(state)['choices']), 3)
        _, again = arc2.encounter(state, 'mistwood', T)
        self.assertIn('already carry the proof', again)

    def test_full_arc_gives_titles_trust_and_no_currency(self):
        state = arc_one_done()
        before = {k: v['trust'] for k, v in world._story(state)['npcs'].items()}
        state, reply, reward = explore_and_choose(state, 3, 'confront')
        self.assertEqual(reward, 0)
        self.assertIn('Mist Walker', reply)
        self.assertIn('trust −1', reply)
        self.assertIn('The Stone That Listens', reply)
        state, _, _ = explore_and_choose(state, 4, 'fair_trade')
        state, reply, reward = explore_and_choose(state, 5, 'hold')
        self.assertEqual(reward, 0)
        self.assertIn('Arc II complete', reply)
        view = arc2.panel(state)
        self.assertTrue(view['complete'])
        self.assertEqual(view['chapter'], 6)
        self.assertEqual(arc2._arc(state)['titles'], ['Mist Walker', 'Listener of Stone', 'Rift Warden'])
        after = {k: v['trust'] for k, v in world._story(state)['npcs'].items()}
        self.assertEqual(after['zhao'], min(world.MAX_TRUST, before.get('zhao', 0) + 3 + 1 + 3))
        self.assertEqual(after['yue'], max(0, before.get('yue', 0) - 1) + 1)
        self.assertIn('Rift Warden', arc2.status(state))

    def test_repeat_and_wrong_chapter_are_refused_without_change(self):
        state, _, _ = explore_and_choose(arc_one_done(), 3, 'listen')
        with self.assertRaisesRegex(ValueError, 'already resolved'):
            arc2.choose(state, 3, 'listen', T)
        with self.assertRaisesRegex(ValueError, 'already resolved'):
            arc2.choose(state, 5, 'seal', T)
        state, _ = arc2.encounter(state, 'stonepeak', T)
        with self.assertRaisesRegex(ValueError, 'current story options'):
            arc2.choose(state, 4, 'listen', T)

    def test_trust_never_drops_below_zero_and_memories_only_on_gains(self):
        state = arc_one_done()
        world._story(state)  # Normalises the traveler records.
        state['living_world_v025']['npcs']['yue'] = {'trust': 0, 'memories': []}
        state, _, _ = explore_and_choose(state, 3, 'confront')
        yue = state['living_world_v025']['npcs']['yue']
        self.assertEqual(yue['trust'], 0)
        self.assertEqual(yue['memories'], [])
        self.assertEqual(state['living_world_v025']['npcs']['zhao']['memories'][-1]['arc'], 2)

    def test_malformed_state_is_tolerated(self):
        state = arc_one_done()
        state['arc2_v026'] = {'choices': {'0': 5, '9': 'x'}, 'titles': 'bad', 'encounter': []}
        view = arc2.panel(state)
        self.assertEqual(view['chapter'], 3)
        self.assertFalse(view['ready'])

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            arc2.encounter(arc_one_done(), 'mistwood', T.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
