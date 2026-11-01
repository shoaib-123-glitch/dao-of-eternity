import unittest
from dashboard_v020 import render
class DashboardTests(unittest.TestCase):
 def test_png(self):
  content=render({'realm':'Foundation Establishment 2','current':220,'cap':220,'rate':132,'bank':498,'earned':713})
  self.assertEqual(content.read(8),b'\x89PNG\r\n\x1a\n')
 def test_zero_capacity(self):
  self.assertTrue(render({'current':0,'cap':0}).getbuffer().nbytes>1000)
