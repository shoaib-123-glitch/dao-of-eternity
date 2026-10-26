"""Economy v1: the main currency is shown as Spirit Stones (saved field stays `marks`)."""
import io
from pathlib import Path
import re
import tokenize
import unittest

HERE = Path(__file__).resolve().parent
OLD = re.compile(r'\b([Cc]ombat|[Bb]attle) marks\b|\bMarks balance\b')


class SpiritStonesTests(unittest.TestCase):
    def test_player_facing_text_never_says_combat_marks(self):
        found = []
        for path in sorted(HERE.glob('*.py')):
            if path.name.startswith('test_'):
                continue
            source = path.read_text(encoding='utf-8')
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type in (tokenize.STRING, getattr(tokenize, 'FSTRING_MIDDLE', -1)) and OLD.search(token.string):
                    found.append(f'{path.name}:{token.start[0]}')
        self.assertEqual(found, [])

    def test_inventory_shows_spirit_stones(self):
        import inventory
        self.assertIn('Spirit Stones: 0', inventory.format_inventory(inventory.empty()))
        self.assertIn('Spirit Stones', inventory.format_recipes())


if __name__ == '__main__':
    unittest.main()
