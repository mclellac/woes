import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
import unittest
import os
import sys

# Add src to sys.path to import application modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from constants import APP_ID # Try to import something simple
    print(f"APP_ID from constants: {APP_ID}")
except Exception as e:
    print(f"Error importing from constants: {e}")


class MinimalTest(unittest.TestCase):
    def test_simple_import(self):
        self.assertTrue(True)
        print("Minimal GI import successful in test_simple_import")

if __name__ == '__main__':
    print("Running minimal_gi_test.py directly")
    unittest.main()
