"""Tests for the main application module."""
import unittest
from unittest.mock import patch, MagicMock, ANY
import sys
import os

# Ensure the src directory is in the Python path for imports
# This might be necessary if running tests directly and src is not in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

# Attempt to import main after potentially modifying path
# This structure helps if the test is run from a different working directory.
try:
    from src import main
except ImportError as e:
    print(f"Failed to import src.main: {e}")
    print("Ensure that the src directory is in your PYTHONPATH or accessible.")
    main = None # Ensure main is defined for later checks
    APP_NAME, VERSION, APP_ID = "Woes Test Fallback", "0.0.0-fallback", "com.example.woes.fallback" # Ensure these are defined for later checks

# Fallback mocks if constants cannot be imported
APP_WEBSITE_URL = "http://example.com/woes_test_fallback"
APP_DESCRIPTION = "A fallback test description for Woes."
APP_ISSUES_URL = "http://example.com/woes_test_fallback/issues"
# Mock GI modules before they are used by main.py or its imports
# This needs to be done at the top level of the test module.
# We create MagicMock instances for gi and its submodules/classes.
mock_gi = MagicMock()
mock_gi.require_version = MagicMock()

# Mock specific Gtk/Adw classes that might be instantiated or accessed globally by main
mock_gi.repository.Gtk = MagicMock()
mock_gi.repository.Adw = MagicMock()
mock_gi.repository.Gio = MagicMock()
mock_gi.repository.GLib = MagicMock() # If main uses GLib directly

# Apply the mock to sys.modules BEFORE main.py (or its imports like constants) tries to import them
sys.modules["gi"] = mock_gi
sys.modules["gi.repository"] = mock_gi.repository
sys.modules["gi.repository.Gtk"] = mock_gi.repository.Gtk
sys.modules["gi.repository.Adw"] = mock_gi.repository.Adw
sys.modules["gi.repository.Gio"] = mock_gi.repository.Gio
sys.modules["gi.repository.GLib"] = mock_gi.repository.GLib


import logging # Import the logging module

# APP_LICENSE_TYPE is handled by the mock_gi.repository.Gtk.License mock

class TestMainAppArgs(unittest.TestCase):
    """Test command-line arguments for the main application."""

    @patch("src.main.logging.basicConfig") # Keep this patch for TestMainAppArgs
    def test_no_arguments_logging_info(self, mock_basic_config):
        """Test that logging.INFO is set when no arguments are passed."""
        if not main:
            self.skipTest("src.main module not imported.")
        with patch.object(sys, "argv", ["main.py"]):
            # Patch WoesApplication and its methods to prevent actual app run
            with patch("src.main.WoesApplication") as MockWoesApp:
                mock_app_instance = MockWoesApp.return_value
                mock_app_instance.run.return_value = 0 # Simulate successful run
                main.main()
        mock_basic_config.assert_called_with(level=logging.INFO, format=ANY)

    @patch("src.main.logging.basicConfig")
    def test_debug_argument_logging_debug(self, mock_basic_config):
        """Test that logging.DEBUG is set with --debug."""
        if not main:
            self.skipTest("src.main module not imported.")
        with patch.object(sys, "argv", ["main.py", "--debug"]):
            with patch("src.main.WoesApplication") as MockWoesApp:
                mock_app_instance = MockWoesApp.return_value
                mock_app_instance.run.return_value = 0
                main.main()
        mock_basic_config.assert_called_with(level=logging.DEBUG, format=ANY)

    @patch("src.main.logging.basicConfig")
    def test_verbose_argument_logging_debug(self, mock_basic_config):
        """Test that logging.DEBUG is set with --verbose."""
        if not main:
            self.skipTest("src.main module not imported.")
        with patch.object(sys, "argv", ["main.py", "--verbose"]):
            with patch("src.main.WoesApplication") as MockWoesApp:
                mock_app_instance = MockWoesApp.return_value
                mock_app_instance.run.return_value = 0
                main.main()
        mock_basic_config.assert_called_with(level=logging.DEBUG, format=ANY)

# Patch _load_gresources_early for WoesApplication tests to prevent file system access
@patch("src.main._load_gresources_early", MagicMock())
class TestAppFeatures(unittest.TestCase):
    """Test application features."""

    def setUp(self):
        """Set up for test methods."""
        if not main:
            self.skipTest("src.main module not imported, cannot test WoesApplication.")
        # Ensure that the mocked Adw.Application is used by WoesApplication
        self.mock_app_instance = mock_gi.repository.Adw.Application.return_value
        self.app = main.WoesApplication(application_id="com.example.test", flags=mock_gi.repository.Gio.ApplicationFlags.FLAGS_NONE)

    @patch("src.main.WoesWindow") # Mock WoesWindow
    def test_on_activate_creates_window(self, MockWoesWindow):
        """Test that on_activate creates and presents a WoesWindow."""
        self.app.on_activate()
        MockWoesWindow.assert_called_once_with(application=self.app)
        self.mock_app_instance.add_window.assert_called_once_with(MockWoesWindow.return_value)
        MockWoesWindow.return_value.present.assert_called_once()

    def test_about_action_shows_about_dialog(self):
        """Test that the 'about' action shows an Adw.AboutDialog."""
        # Mock the Adw.AboutDialog and its methods
        mock_about_dialog_instance = mock_gi.repository.Adw.AboutDialog.return_value
        mock_about_dialog_instance.set_modal = MagicMock()
        mock_about_dialog_instance.set_transient_for = MagicMock()
        mock_about_dialog_instance.present = MagicMock()

        # Mock get_active_window to return a mock window
        mock_active_window = MagicMock()
        self.mock_app_instance.get_active_window.return_value = mock_active_window

        self.app.on_about_action(None, None) # Action and parameter are not used

        # Check Adw.AboutDialog was instantiated with correct parameters
        mock_gi.repository.Adw.AboutDialog.assert_called_once()
        # Verify attributes were set. Accessing call_args on the instance, not the class mock.
        # This requires that the instance is the return_value of the class mock.
        setter_calls = {
            "application_name": "Woes Test", # Direct literal
            "version": "0.0.1-test",          # Direct literal
            "developer_name": "Your Name or Organization",
            "website": APP_WEBSITE_URL,     # This should be fine if imported or mocked globally
            "comments": APP_DESCRIPTION,    # This should be fine if imported or mocked globally
            "issue_url": APP_ISSUES_URL,      # This should be fine if imported or mocked globally
            "application_icon": "com.example.woes.test", # Direct literal for APP_ID
            "license_type": mock_gi.repository.Gtk.License.MIT_X11
        }
        for attr, value in setter_calls.items():
            getattr(mock_about_dialog_instance, f"set_{attr}").assert_called_with(value)

        mock_about_dialog_instance.set_modal.assert_called_with(True)
        mock_about_dialog_instance.set_transient_for.assert_called_with(mock_active_window)
        mock_about_dialog_instance.present.assert_called_once()


if __name__ == "__main__":
    unittest.main()
