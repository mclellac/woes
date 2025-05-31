import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import sys

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio

# Ensure the src directory is in the Python path for imports
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Import the class to be tested
from webscan_page import WebScanPage

# Helper to process GTK events
def process_gtk_events():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)

class TestWebScanPage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Initialize GTK application context for testing
        # Needed if your widget interacts with Adw.Application or similar
        cls.app = Adw.Application(application_id="com.example.test.woes")
        cls.app.register() # Important for Adw.Application based widgets or features

        # It's good practice to have a window for pages, even if not shown
        cls.window = Adw.Window()
        cls.app.add_window(cls.window)


    @classmethod
    def tearDownClass(cls):
        cls.app.quit()


    def setUp(self):
        # Create an instance of the WebScanPage
        # Ensure that the GType is registered if WebScanPage is in a separate module
        # This should be handled by `from webscan_page import WebScanPage` if __init__.py is correct
        self.page = WebScanPage()
        self.window.set_content(self.page) # Add page to window to ensure it's fully initialized
        process_gtk_events()


    def tearDown(self):
        self.window.set_content(None)
        self.page = None
        process_gtk_events()


    def test_01_initialization(self):
        """Test that the WebScanPage initializes correctly."""
        self.assertIsNotNone(self.page)
        self.assertIsInstance(self.page, WebScanPage)
        self.assertTrue(self.page.get_visible())


    def test_02_ui_elements_present(self):
        """Test that essential UI elements are present."""
        self.assertIsNotNone(self.page.url_entry, "URL entry row should exist")
        self.assertIsInstance(self.page.url_entry, Adw.EntryRow, "URL entry should be AdwEntryRow")

        self.assertIsNotNone(self.page.scan_button, "Scan button should exist")
        self.assertIsInstance(self.page.scan_button, Gtk.Button, "Scan button should be GtkButton")

        self.assertIsNotNone(self.page.results_textview, "Results text view should exist")
        self.assertIsInstance(self.page.results_textview, Gtk.TextView, "Results text view should be GtkTextView")

    def test_03_empty_url_shows_error(self):
        """Test that clicking scan with an empty URL shows an error and does not run nikto."""
        initial_text = self.page.results_textview.get_buffer().get_text(
            self.page.results_textview.get_buffer().get_start_iter(),
            self.page.results_textview.get_buffer().get_end_iter(),
            False
        )

        with patch('webscan_page.subprocess.Popen') as mock_popen:
            # Mock show_error_toast to check if it's called
            with patch.object(self.page, 'show_error_toast') as mock_show_error:
                self.page.url_entry.set_text("")
                self.page.scan_button.clicked()
                process_gtk_events() # Allow signals and GLib.idle_add to run

                mock_show_error.assert_called_once_with("Target URL cannot be empty.")
                mock_popen.assert_not_called()

                # Check that results_textview contains the error (due to fallback in show_error_toast)
                final_text_buffer = self.page.results_textview.get_buffer()
                final_text = final_text_buffer.get_text(final_text_buffer.get_start_iter(), final_text_buffer.get_end_iter(), False)
                self.assertIn("ERROR: Target URL cannot be empty.", final_text)


    @patch('webscan_page.subprocess.Popen')
    def test_04_scan_button_triggers_nikto(self, mock_popen_class):
        """Test that the scan button callback triggers the nikto command and updates UI."""
        mock_process = MagicMock()
        mock_process.communicate.return_value = ("Nikto scan successful!", "") # stdout, stderr
        mock_popen_class.return_value = mock_process

        target_url = "http://example.com"
        self.page.url_entry.set_text(target_url)

        self.assertTrue(self.page.scan_button.get_sensitive(), "Button should be sensitive initially")

        self.page.scan_button.clicked()
        process_gtk_events() # Initial click to start thread

        # Button should be insensitive right after click (before thread finishes mock)
        self.assertFalse(self.page.scan_button.get_sensitive(), "Button should be insensitive during scan")

        # Allow Gio.Thread and GLib.idle_add calls to complete
        # In a real test environment, might need more robust waiting for thread completion
        # For mocked Popen, GLib.idle_add should execute fairly quickly
        for _ in range(5): # Process events multiple times
            process_gtk_events()

        mock_popen_class.assert_called_once_with(
            ['nikto', '-h', target_url, '-Tuning', 'xCGIVulnerable'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        mock_process.communicate.assert_called_once_with(timeout=300)

        results_buffer = self.page.results_textview.get_buffer()
        results_text = results_buffer.get_text(results_buffer.get_start_iter(), results_buffer.get_end_iter(), False)

        self.assertIn(f"Scanning {target_url}...", results_text)
        self.assertIn("Nikto scan successful!", results_text)
        self.assertTrue(self.page.scan_button.get_sensitive(), "Button should be sensitive after scan")

    @patch('webscan_page.subprocess.Popen')
    def test_05_scan_with_stderr_output(self, mock_popen_class):
        """Test scan that produces stderr output."""
        mock_process = MagicMock()
        mock_process.communicate.return_value = ("Nikto scan partial.", "Some errors occurred.")
        mock_popen_class.return_value = mock_process

        target_url = "http://testserver.invalid"
        self.page.url_entry.set_text(target_url)
        self.page.scan_button.clicked()
        process_gtk_events()
        for _ in range(5): process_gtk_events()


        results_buffer = self.page.results_textview.get_buffer()
        results_text = results_buffer.get_text(results_buffer.get_start_iter(), results_buffer.get_end_iter(), False)

        self.assertIn("Nikto scan partial.", results_text)
        self.assertIn("--- Errors ---\nSome errors occurred.", results_text)
        self.assertTrue(self.page.scan_button.get_sensitive())

    @patch('webscan_page.subprocess.Popen')
    def test_06_nikto_not_found(self, mock_popen_class):
        """Test handling when nikto command is not found."""
        mock_popen_class.side_effect = FileNotFoundError("Nikto not found mock")

        target_url = "http://anything.com"
        self.page.url_entry.set_text(target_url)

        with patch.object(self.page, 'show_error_toast') as mock_show_error:
            self.page.scan_button.clicked()
            process_gtk_events()
            for _ in range(5): process_gtk_events()

            mock_show_error.assert_any_call("Nikto command not found. Please ensure it is installed and in your PATH.")

        results_buffer = self.page.results_textview.get_buffer()
        results_text = results_buffer.get_text(results_buffer.get_start_iter(), results_buffer.get_end_iter(), False)
        self.assertIn("Error: Nikto not found.", results_text) # From _update_textview
        self.assertTrue(self.page.scan_button.get_sensitive())

    @patch('webscan_page.subprocess.Popen')
    def test_07_scan_timeout(self, mock_popen_class):
        """Test handling of subprocess.TimeoutExpired."""
        mock_process = MagicMock()
        mock_process.communicate.side_effect = subprocess.TimeoutExpired(cmd="nikto", timeout=300)
        mock_popen_class.return_value = mock_process

        target_url = "http://timeout.com"
        self.page.url_entry.set_text(target_url)

        with patch.object(self.page, 'show_error_toast') as mock_show_error:
            self.page.scan_button.clicked()
            process_gtk_events()
            for _ in range(5): process_gtk_events()

            mock_show_error.assert_any_call(f"Scan for {target_url} timed out.")

        results_buffer = self.page.results_textview.get_buffer()
        results_text = results_buffer.get_text(results_buffer.get_start_iter(), results_buffer.get_end_iter(), False)
        self.assertIn(f"Error: Scan for {target_url} timed out after 5 minutes.", results_text)
        self.assertTrue(self.page.scan_button.get_sensitive())

    def test_08_url_scheme_addition(self):
        """Test that http:// is added to URLs without a scheme."""
        with patch('webscan_page.subprocess.Popen') as mock_popen_class:
            mock_process = MagicMock()
            mock_process.communicate.return_value = ("", "")
            mock_popen_class.return_value = mock_process

            self.page.url_entry.set_text("example.com") # No scheme
            self.page.scan_button.clicked()
            process_gtk_events()
            for _ in range(5): process_gtk_events()

            mock_popen_class.assert_called_once_with(
                ['nikto', '-h', 'http://example.com', '-Tuning', 'xCGIVulnerable'], # Scheme added
                stdout=ANY, stderr=ANY, text=True
            )

            self.page.url_entry.set_text("https://secure.example.com") # Scheme present
            self.page.scan_button.clicked()
            process_gtk_events()
            for _ in range(5): process_gtk_events()

            mock_popen_class.assert_called_with(
                ['nikto', '-h', 'https://secure.example.com', '-Tuning', 'xCGIVulnerable'],
                stdout=ANY, stderr=ANY, text=True
            )
            self.assertEqual(mock_popen_class.call_count, 2)


if __name__ == '__main__':
    # This allows running the tests directly from this file
    # Set GSETTINGS_SCHEMA_DIR if your application uses GSettings
    # For example, if schemas are in project_root/data/schemas:
    project_root = os.path.abspath(os.path.join(src_path, '..'))
    schema_dir = os.path.join(project_root, 'data', 'schemas')
    if os.path.isdir(schema_dir):
         os.environ['GSETTINGS_SCHEMA_DIR'] = schema_dir

    unittest.main()
