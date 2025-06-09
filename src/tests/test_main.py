from src.main import parse_arguments_and_setup_logging
import logging
from unittest.mock import patch
import unittest
import sys
import os

from unittest.mock import MagicMock

# Mock gi repository modules before other src imports that might depend on them
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
gi_repo_mock = sys.modules["gi.repository"]
Adw = MagicMock()
Gio = MagicMock()
Gtk = MagicMock()
GObject = MagicMock() # Added GObject mock explicitly for clarity if needed by WoesApplication init
setattr(gi_repo_mock, "Adw", Adw)
setattr(gi_repo_mock, "Gio", Gio)
setattr(gi_repo_mock, "Gtk", Gtk)
setattr(gi_repo_mock, "GObject", GObject)
setattr(gi_repo_mock, "GtkSource", MagicMock())
setattr(gi_repo_mock, "Pango", MagicMock())

# Mock Gtk.License specifically for APP_LICENSE_TYPE
Gtk.License = MagicMock()
Gtk.License.MIT_X11 = "MIT_X11_Mocked" # Mock the specific license type

# Ensure project root is in sys.path for src imports
current_script_path = os.path.abspath(__file__)
tests_dir = os.path.dirname(current_script_path)
src_dir = os.path.dirname(tests_dir)
project_root = os.path.dirname(src_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Now import WoesApplication and constants after mocks are set up
from src.main import WoesApplication
from src.constants import (
    APP_WEBSITE_URL,
    APP_LICENSE_TYPE, # This will be the mocked Gtk.License.MIT_X11
    APP_DESCRIPTION,
    APP_ISSUES_URL
)


class TestMainAppArgs(unittest.TestCase):
    @patch("src.main.logging.basicConfig") # Keep this patch for TestMainAppArgs
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


# Patch _load_gresources_early for WoesApplication tests to prevent file system access
@patch("src.main._load_gresources_early", MagicMock())
class TestAppFeatures(unittest.TestCase):
    def setUp(self):
        """Set up for test methods."""
        # We need to ensure that the application doesn't try to create actual windows
        # or interact with GTK event loop in tests.
        # The existing mocks for gi.repository.Gtk and Adw should help.
        # WoesApplication.__init__ calls super().__init__ which might try to connect to DBus.
        # We might need to patch Gio.Application.get_default() or similar if it causes issues.
        with patch.object(Gio.Application, "get_default", return_value=None):
             # Mock settings to prevent GSettings access errors
            with patch("src.main.Gio.Settings.new") as mock_settings_new:
                mock_settings_instance = MagicMock()
                mock_settings_new.return_value = mock_settings_instance
                # Add mock methods for any GSettings calls made during WoesApplication init if necessary
                # e.g., mock_settings_instance.get_string.return_value = "some_default_value"
                self.app = WoesApplication()

    def test_about_window_details(self):
        """Test that the About Window has the correct enhanced details."""
        # The _create_about_window method doesn't require an active window to be set on the app
        # for just creating the AboutWindow instance.
        # transient_for would be set by on_about_action if an active_window exists.
        about_window = self.app._create_about_window()

        self.assertIsNotNone(about_window)
        self.assertEqual(about_window.get_website(), APP_WEBSITE_URL)
        # APP_LICENSE_TYPE is Gtk.License.MIT_X11. The mock setup makes Gtk.License.MIT_X11 a string.
        self.assertEqual(about_window.get_license_type(), Gtk.License.MIT_X11)
        self.assertEqual(about_window.get_comments(), APP_DESCRIPTION)
        self.assertEqual(about_window.get_issue_url(), APP_ISSUES_URL)

    def test_page_action_accelerators(self):
        """Test that page-specific actions are registered with correct accelerators."""
        expected_actions = {
            "app.page-action-http-fetch": "<Alt>F",
            "app.page-action-nmap-scan": "<Alt>S",
            "app.page-action-dns-lookup": "<Alt>L",
            "app.page-action-webscan-scan": "<Alt>W",
        }

        for full_action_name, expected_accelerator in expected_actions.items():
            action_name_without_prefix = full_action_name.split(".", 1)[1]
            action = self.app.lookup_action(action_name_without_prefix)
            self.assertIsNotNone(action, f"Action '{action_name_without_prefix}' not found.")

            accels = self.app.get_accels_for_action(full_action_name)
            self.assertIn(expected_accelerator, accels,
                          f"Expected accelerator '{expected_accelerator}' not found for action '{full_action_name}'. Found: {accels}")


if __name__ == "__main__":
    unittest.main()
