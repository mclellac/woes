from src.main import parse_arguments_and_setup_logging
import logging
from unittest.mock import patch
import unittest
import sys
import os

from unittest.mock import MagicMock

sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
gi_repo_mock = sys.modules["gi.repository"]
setattr(gi_repo_mock, "Adw", MagicMock())
setattr(gi_repo_mock, "Gio", MagicMock())
setattr(gi_repo_mock, "Gtk", MagicMock())
setattr(gi_repo_mock, "GObject", MagicMock())
setattr(gi_repo_mock, "GtkSource", MagicMock())
setattr(gi_repo_mock, "Pango", MagicMock())


current_script_path = os.path.abspath(__file__)
tests_dir = os.path.dirname(current_script_path)
src_dir = os.path.dirname(tests_dir)
project_root = os.path.dirname(src_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestMainAppArgs(unittest.TestCase):
    @patch("src.main.logging.basicConfig")
    def test_no_arguments_logging_info(self, mock_basic_config):
        """Test that logging.INFO is set when no arguments are passed."""
        test_argv = ["main.py"]
        args = parse_arguments_and_setup_logging(test_argv)

        called_with_info = False
        for call_item in mock_basic_config.call_args_list:
            if call_item.kwargs.get("level") == logging.INFO:
                called_with_info = True
                break
        self.assertTrue(called_with_info, "basicConfig was not called with level=logging.INFO")
        self.assertFalse(args.debug)

    @patch("src.main.logging.basicConfig")
    def test_debug_argument_logging_debug(self, mock_basic_config):
        """Test that logging.DEBUG is set when --debug is passed."""
        test_argv = ["main.py", "--debug"]
        args = parse_arguments_and_setup_logging(test_argv)

        called_with_debug = False
        for call_item in mock_basic_config.call_args_list:
            if call_item.kwargs.get("level") == logging.DEBUG:
                called_with_debug = True
                break
        self.assertTrue(called_with_debug, "basicConfig was not called with level=logging.DEBUG")
        self.assertTrue(args.debug)

    @patch("src.main.argparse.ArgumentParser._print_message")
    def test_unknown_argument_system_exit(self, mock_print_message):
        """Test that SystemExit is raised for unknown arguments."""
        test_argv = ["main.py", "--unknown-arg"]
        with self.assertRaises(SystemExit):
            parse_arguments_and_setup_logging(test_argv)


if __name__ == "__main__":
    unittest.main()
