"""Tests for the webscan_page module."""
import unittest
from unittest.mock import patch, MagicMock #,create_autospec
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio # pylint: disable=wrong-import-position
import sys
import os

# Append project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.webscan_page import WebScanPage # pylint: disable=wrong-import-position

# It's good practice to ensure Gtk is initialized for tests involving UI components,
# though with heavy mocking, it might not always be strictly necessary.
Gtk.init() # Ensure GTK is initialized

def process_gtk_events():
    """Process all pending GTK events."""
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)

class TestWebScanPage(unittest.TestCase):
    """Test cases for the WebScanPage class."""

    @classmethod
    def setUpClass(cls):
        """Set up resources before all tests in this class."""
        cls.app = Adw.Application(application_id="com.example.test.woes")
        cls.app.register()
        cls.window = Gtk.Window(application=cls.app) # Create a dummy window

    @classmethod
    def tearDownClass(cls):
        """Tear down resources after all tests in this class have run."""
        cls.app.quit()

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.page = WebScanPage()
        self.window.set_content(self.page) # Add page to window to realize it
        # process_gtk_events() # Process events to ensure UI is somewhat set up

    def tearDown(self):
        """Tear down test fixtures after each test method."""
        self.window.set_content(None)
        self.page = None
        # process_gtk_events() # Clean up events

    @patch('src.webscan_page.subprocess.Popen')
    def test_scan_button_clicked_valid_url(self, mock_popen):
        """Test scan button click with a valid URL."""
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"Nikto output", b"")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        self.page.url_entry.set_text("http://example.com")
        self.page.on_scan_button_clicked(self.page.scan_button)

        # Check that Popen was called with 'nikto' and the URL
        mock_popen.assert_called_once()
        args, _ = mock_popen.call_args
        self.assertIn('nikto', args[0])
        self.assertIn('-h', args[0])
        self.assertTrue(any(arg.endswith("example.com") for arg in args[0]))

        # Check that the source_view is updated (eventually, via GLib.idle_add)
        # This requires simulating the GLib.idle_add callback.
        # For simplicity, we'll check the initial text set.
        # A more complex test would involve patching GLib.idle_add.
        # For now, let's assume the _on_scan_task_done is called by the mocked task.

        # Simulate task completion by directly calling the callback logic if possible,
        # or by checking side effects that occur before async parts.
        # The current structure makes direct check of final output hard without deeper mocking.
        # Let's check the initial text buffer content.
        buffer = self.page.source_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        self.assertIn("Scanning http://example.com...", text)

    def test_scan_button_clicked_empty_url(self):
        """Test scan button click with an empty URL."""
        self.page.url_entry.set_text("")
        # Mock show_global_toast to check if it's called
        with patch('src.webscan_page.show_global_toast') as mock_toast:
            self.page.on_scan_button_clicked(self.page.scan_button)
            mock_toast.assert_called_with(self.page, "Target URL cannot be empty.")
        self.assertTrue(self.page.scan_button.get_sensitive()) # Button should be re-enabled

    def test_scan_button_clicked_invalid_url(self):
        """Test scan button click with an invalid URL."""
        self.page.url_entry.set_text("invalid-url")
        with patch('src.webscan_page.show_global_toast') as mock_toast:
            self.page.on_scan_button_clicked(self.page.scan_button)
            # The URL becomes "https://invalid-url" due to prepending logic
            # but is_valid_url should still catch "invalid-url" part if it's strict.
            # If is_valid_url is very lenient, this test might need adjustment.
            mock_toast.assert_called_with(self.page, "Invalid URL format. Please enter a valid URL.")
        self.assertTrue(self.page.scan_button.get_sensitive())

    @patch('src.webscan_page.subprocess.Popen')
    def test_nikto_command_construction_all_options(self, mock_popen):
        """Test Nikto command construction with all options enabled."""
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"Nikto output", b"")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        self.page.url_entry.set_text("http://example.com")
        self.page.force_ssl_switch.set_active(True)
        self.page.cgi_vulns_switch.set_active(True) # Tuning '2'
        self.page.interesting_content_switch.set_active(True) # Tuning '1'
        self.page.evasion_switch.set_active(True) # Evasion '1'
        self.page.mutate_switch.set_active(True) # Mutate '1'
        self.page.maxtime_entry_row.set_text("60")
        self.page.no404_switch.set_active(True)
        self.page.auth_bypass_switch.set_active(True) # Tuning '9'

        # Mock the format ComboBox to select a specific format, e.g., XML
        # This requires knowing the model structure or mocking get_selected_item().get_string()
        mock_selected_item = MagicMock()
        mock_selected_item.get_string.return_value = "xml" # Simulate XML format selected
        self.page.nikto_format_combo_row.get_selected_item = MagicMock(return_value=mock_selected_item)
        self.page.nikto_output_file_row.set_text("/tmp/nikto_report.xml")


        # Simulate Gio.Task and its callback for command construction check
        # We need to capture the command passed to Popen
        # The actual task execution is complex, so we focus on command parts
        with patch.object(Gio, 'Task', MagicMock()) as mock_task_class:
            mock_task_instance = mock_task_class.return_value
            # Ensure run_in_thread calls the function directly for easier arg capture
            def simple_run_in_thread(func):
                # We need to simulate the _current_webscan_params being set
                # This would normally happen in on_scan_button_clicked before run_in_thread
                self.page._current_webscan_params = self.page._current_webscan_params # Already set by on_scan_button_clicked
                func(mock_task_instance, self.page, None, Gio.Cancellable())
                return None # Must return None
            mock_task_instance.run_in_thread = simple_run_in_thread

            self.page.on_scan_button_clicked(self.page.scan_button)


        mock_popen.assert_called_once()
        args, _ = mock_popen.call_args
        command_list = args[0]

        self.assertIn("nikto", command_list)
        self.assertTrue(any(arg.startswith("https://example.com") for arg in command_list)) # SSL forced
        self.assertIn("-ssl", command_list)
        self.assertIn("-Tuning", command_list)
        # Order of tuning options might vary due to set, so check for presence of combined string parts
        self.assertTrue(any("1" in arg and "2" in arg and "9" in arg for arg in command_list if arg.startswith(tuple("0123456789"))))
        self.assertIn("-evasion", command_list)
        self.assertIn("1", command_list[command_list.index("-evasion") + 1])
        self.assertIn("-mutate", command_list)
        self.assertIn("1", command_list[command_list.index("-mutate") + 1])
        self.assertIn("-maxtime", command_list)
        self.assertIn("60s", command_list)
        self.assertIn("-Format", command_list)
        self.assertIn("xml", command_list)
        self.assertIn("-o", command_list)
        self.assertIn("/tmp/nikto_report.xml", command_list)
        self.assertIn("-no404", command_list)


if __name__ == '__main__':
    unittest.main()
