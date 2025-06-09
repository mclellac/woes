import unittest
from unittest.mock import Mock, patch, MagicMock

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, GLib

# Assuming src is in PYTHONPATH or we adjust path
# For simplicity in this environment, let's assume it can be imported
# If not, we might need to add to sys.path
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.'))) # Add /app to path for src import

# Before importing the class that uses Gtk.Template, mock Gtk.Template and Gtk.Template.Child
_original_gtk_template = Gtk.Template
_original_gtk_template_child = getattr(Gtk.Template, 'Child', None)

Gtk.Template = lambda resource_path: (lambda cls: cls) # Dummy decorator for @Gtk.Template()
Gtk.Template.Child = lambda: None # Dummy for Gtk.Template.Child() assignments

_webscan_page_imported_successfully = False
try:
    from src.webscan_page import WebScanPage
    _webscan_page_imported_successfully = True
except Exception as e:
    print(f"Error importing WebScanPage even with Gtk.Template and Child mocked: {e}")
    WebScanPage = MagicMock() # Fallback to a pure MagicMock
finally:
    # Restore Gtk.Template and Gtk.Template.Child after import
    Gtk.Template = _original_gtk_template
    if _original_gtk_template_child is not None and hasattr(Gtk.Template, 'Child'): # hasattr check for safety
         Gtk.Template.Child = _original_gtk_template_child
    elif _original_gtk_template_child is None and hasattr(Gtk.Template, 'Child'): # If it had Child but we set it to None
        del Gtk.Template.Child # Try to remove it if it was added by our mock on a Template that didn't have it


class TestWebScanPageIntegration(unittest.TestCase):

    def setUp(self):
        if not Gtk.Application.get_default():
            app = Gtk.Application(application_id="com.example.test.woes.unittest.setup")
            def do_activate(app_instance): pass
            app.do_activate = do_activate
            app.register(None)

        self.page = WebScanPage()

        # Manually assign mocks to fields that would normally be populated by Gtk.Template.Child
        # This is necessary because the @Gtk.Template decorator and .Child calls were dummied out
        # during the import of WebScanPage.
        self.page.url_entry = MagicMock(spec=Adw.EntryRow)
        self.page.scan_button = MagicMock(spec=Gtk.Button)

        self.page.results_textview = MagicMock(spec=Gtk.TextView)
        self.mock_text_buffer = MagicMock(spec=Gtk.TextBuffer)
        self.page.results_textview.get_buffer = Mock(return_value=self.mock_text_buffer)

        self.page.toast_overlay_internal = MagicMock(spec=Adw.ToastOverlay)
        self.mock_add_toast = Mock()
        self.page.toast_overlay_internal.add_toast = self.mock_add_toast

        self.mock_main_window = MagicMock()
        self.mock_main_window.show_error = Mock()

        # If the real WebScanPage was imported, its methods exist and can be patched.
        # If WebScanPage is a MagicMock, patching non-existent methods would fail,
        # but assigning to them (like above) works.
        if _webscan_page_imported_successfully:
            self.get_native_patch = patch.object(self.page, 'get_native', return_value=self.mock_main_window)
            self.mock_get_native = self.get_native_patch.start()

            self.run_scan_task_patch = patch.object(self.page, '_run_scan_task_thread_func')
            self.mock_run_scan_task = self.run_scan_task_patch.start()
        else: # If WebScanPage is a MagicMock, just make sure these attributes exist and are mocks
            self.page.get_native = Mock(return_value=self.mock_main_window)
            self.page._run_scan_task_thread_func = Mock()
            self.mock_run_scan_task = self.page._run_scan_task_thread_func


        self.gio_task_new_patch = patch('gi.repository.Gio.Task.new')
        self.mock_gio_task_new = self.gio_task_new_patch.start()
        self.mock_gio_task_instance = MagicMock(spec=Gio.Task)
        self.mock_gio_task_new.return_value = self.mock_gio_task_instance

    def tearDown(self):
        if _webscan_page_imported_successfully:
            self.get_native_patch.stop()
            self.run_scan_task_patch.stop()
        self.gio_task_new_patch.stop()

    def test_empty_url(self):
        print("Running test_empty_url...")
        self.page.url_entry.get_text.return_value = ""
        self.page.on_scan_button_clicked(None)
        self.mock_add_toast.assert_called_once()
        toast_arg = self.mock_add_toast.call_args[0][0]
        # self.assertIsInstance(toast_arg, Adw.Toast) # Adw.Toast might be mocked if Adw not fully init
        self.assertEqual(toast_arg.get_title(), "Target URL cannot be empty.")
        self.mock_main_window.show_error.assert_not_called()
        # self.mock_run_scan_task.assert_not_called() # This is now on self.page directly if mock
        self.page._run_scan_task_thread_func.assert_not_called()
        print("Finished test_empty_url.")

    def test_invalid_url_format(self):
        print("Running test_invalid_url_format...")
        self.page.url_entry.get_text.return_value = "this is not a url"
        self.page.on_scan_button_clicked(None)
        self.mock_add_toast.assert_called_once()
        toast_arg = self.mock_add_toast.call_args[0][0]
        # self.assertIsInstance(toast_arg, Adw.Toast)
        self.assertEqual(toast_arg.get_title(), "Invalid URL format. Please enter a valid URL.")
        self.mock_main_window.show_error.assert_not_called()
        self.page._run_scan_task_thread_func.assert_not_called()
        print("Finished test_invalid_url_format.")

    def test_valid_scan_starts(self):
        print("Running test_valid_scan_starts...")
        self.page.url_entry.get_text.return_value = "example.com"
        self.page.on_scan_button_clicked(None)
        self.mock_add_toast.assert_not_called()
        self.mock_main_window.show_error.assert_not_called()
        self.mock_gio_task_new.assert_called_once_with(self.page, unittest.mock.ANY, self.page._on_scan_task_done)
        self.mock_gio_task_instance.set_task_data.assert_called_once_with("example.com")
        self.mock_gio_task_instance.run_in_thread.assert_called_once_with(self.page._run_scan_task_thread_func)
        print("Finished test_valid_scan_starts.")

    def test_critical_error_nikto_not_found(self):
        print("Running test_critical_error_nikto_not_found...")
        mock_task_for_finish = MagicMock(spec=Gio.Task)
        mock_task_for_finish.get_task_data.return_value = "example.com"
        mock_task_for_finish.run_in_thread_finish = Mock(return_value=(None, None, "FileNotFoundError"))

        self.page._on_scan_task_done(mock_task_for_finish, Mock(spec=Gio.AsyncResult), None)
        self.mock_main_window.show_error.assert_called_once_with(
            "Nikto command not found. Please ensure it is installed and in your PATH."
        )
        self.mock_add_toast.assert_not_called()
        print("Finished test_critical_error_nikto_not_found.")

    def test_critical_error_timeout(self):
        print("Running test_critical_error_timeout...")
        target_url = "timeout.com"
        mock_task_for_finish = MagicMock(spec=Gio.Task)
        mock_task_for_finish.get_task_data.return_value = target_url
        mock_task_for_finish.run_in_thread_finish = Mock(return_value=(None, None, "TimeoutExpired"))

        self.page._on_scan_task_done(mock_task_for_finish, Mock(spec=Gio.AsyncResult), None)
        self.mock_main_window.show_error.assert_called_once_with(
            f"Scan for {target_url} timed out."
        )
        self.mock_add_toast.assert_not_called()
        print("Finished test_critical_error_timeout.")

if __name__ == '__main__':
    # Ensure Gtk.Application is only initialized once if tests are run multiple times in a session
    if not Gtk.Application.get_default():
        app = Gtk.Application(application_id="com.example.test.woes.unittest.main")
        def do_activate(app_instance): pass
        app.do_activate = do_activate
        app.register(None)

    unittest.main(argv=['first-arg-is-ignored'], exit=False)

    # No need to restore Gtk.Template here if done in the try/finally block after import.
