import unittest
from unittest.mock import Mock, patch, MagicMock

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio

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
        if hasattr(Gtk.Template, 'Child'): # Ensure it actually has Child before trying to delete
             del Gtk.Template.Child


class TestWebScanPageIntegration(unittest.TestCase):

    def setUp(self):
        if not Gtk.Application.get_default():
            app = Gtk.Application(application_id="com.example.test.woes.unittest.setup")
            def do_activate(app_instance): pass
            app.do_activate = do_activate
            app.register(None)

        self.page = WebScanPage()

        # Manually assign mocks to fields that would normally be populated by Gtk.Template.Child
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

        if _webscan_page_imported_successfully:
            self.get_native_patch = patch.object(self.page, 'get_native', return_value=self.mock_main_window)
            self.mock_get_native = self.get_native_patch.start()

            self.run_scan_task_patch = patch.object(self.page, '_run_scan_task_thread_func')
            self.mock_run_scan_task = self.run_scan_task_patch.start()
        else:
            self.page.get_native = Mock(return_value=self.mock_main_window)
            self.page._run_scan_task_thread_func = Mock() # This will be self.mock_run_scan_task
            self.mock_run_scan_task = self.page._run_scan_task_thread_func


        self.gio_task_new_patch = patch('gi.repository.Gio.Task.new')
        self.mock_gio_task_new = self.gio_task_new_patch.start()
        self.mock_gio_task_instance = MagicMock(spec=Gio.Task)
        # Ensure run_in_thread_finish can be configured on this mock task instance
        self.mock_gio_task_instance.run_in_thread_finish = Mock()
        self.mock_gio_task_new.return_value = self.mock_gio_task_instance


    def tearDown(self):
        if _webscan_page_imported_successfully:
            if hasattr(self, 'get_native_patch') and self.get_native_patch.is_started:
                self.get_native_patch.stop()
            if hasattr(self, 'run_scan_task_patch') and self.run_scan_task_patch.is_started:
                self.run_scan_task_patch.stop()
        if hasattr(self, 'gio_task_new_patch') and self.gio_task_new_patch.is_started: # Check if started
            self.gio_task_new_patch.stop()


    def test_empty_url(self):
        print("Running test_empty_url...")
        self.page.url_entry.get_text.return_value = ""
        self.page.on_scan_button_clicked(None)
        self.mock_add_toast.assert_called_once()
        toast_arg = self.mock_add_toast.call_args[0][0]
        self.assertEqual(toast_arg.get_title(), "Target URL cannot be empty.")
        self.mock_main_window.show_error.assert_not_called()
        self.page._run_scan_task_thread_func.assert_not_called()
        print("Finished test_empty_url.")

    def test_invalid_url_format(self):
        print("Running test_invalid_url_format...")
        self.page.url_entry.get_text.return_value = "this is not a url"
        self.page.on_scan_button_clicked(None)
        self.mock_add_toast.assert_called_once()
        toast_arg = self.mock_add_toast.call_args[0][0]
        self.assertEqual(toast_arg.get_title(), "Invalid URL format. Please enter a valid URL.")
        self.mock_main_window.show_error.assert_not_called()
        self.page._run_scan_task_thread_func.assert_not_called()
        print("Finished test_invalid_url_format.")

    def test_valid_scan_starts(self): # Renamed from test_scan_initiation_logic_after_validation
        print("Running test_valid_scan_starts...")
        self.page.url_entry.get_text.return_value = "example.com"
        self.page.on_scan_button_clicked(None)
        self.mock_add_toast.assert_not_called()
        self.mock_main_window.show_error.assert_not_called()
        self.mock_gio_task_new.assert_called_once_with(self.page, unittest.mock.ANY, self.page._on_scan_task_done)
        self.mock_gio_task_instance.set_task_data.assert_called_once_with("example.com")
        self.mock_gio_task_instance.run_in_thread.assert_called_once_with(self.page._run_scan_task_thread_func)
        print("Finished test_valid_scan_starts.")

    def test_scan_flow_to_nikto_not_found_error(self):
        print("Running test_scan_flow_to_nikto_not_found_error...")
        self.page.url_entry.get_text.return_value = "example.com"

        # This mock task instance will be passed to simulate_nikto_not_found via run_in_thread
        # And then it will be used by _on_scan_task_done
        self.mock_gio_task_instance.get_task_data.return_value = "example.com"
        self.mock_gio_task_instance.run_in_thread_finish.return_value = (None, None, "FileNotFoundError")

        # Configure the mocked _run_scan_task_thread_func (which is self.mock_run_scan_task)
        # to indicate the error by setting the return value on the task instance it receives.
        def simulate_nikto_not_found_in_thread_func(gio_task, _source_object, _task_data, _cancellable):
            # This is what the real _run_scan_task_thread_func does for FileNotFoundError
            # It calls return_value on the Gio.Task instance it was given.
            gio_task.return_value = (None, None, "FileNotFoundError")
            # Note: In the real Gio.Task, this would trigger the callback.
            # Here, we will make our mock run_in_thread do that.

        # self.mock_run_scan_task is already self.page._run_scan_task_thread_func (or its mock)
        self.mock_run_scan_task.side_effect = simulate_nikto_not_found_in_thread_func

        # Simulate that task.run_in_thread calls _run_scan_task_thread_func,
        # which then sets up the result on the task,
        # and then _on_scan_task_done is called (as if by the main loop).
        def mock_run_in_thread_and_then_callback(target_thread_func_on_page_obj):
            # Call the thread func (our simulate_nikto_not_found_in_thread_func via self.mock_run_scan_task)
            # It needs the task, source_obj (page), task_data, cancellable
            target_thread_func_on_page_obj(self.mock_gio_task_instance, self.page, "example.com", None)
            # Now, manually call the task completion callback with the same task instance
            self.page._on_scan_task_done(self.mock_gio_task_instance, Mock(spec=Gio.AsyncResult), None)

        self.mock_gio_task_instance.run_in_thread.side_effect = mock_run_in_thread_and_then_callback

        self.page.on_scan_button_clicked(None) # This will trigger the chain

        self.mock_main_window.show_error.assert_called_once_with(
            "Nikto command not found. Please ensure it is installed and in your PATH."
        )
        self.mock_add_toast.assert_not_called()
        print("Finished test_scan_flow_to_nikto_not_found_error.")


    def test_critical_error_nikto_not_found_direct(self): # Renamed
        print("Running test_critical_error_nikto_not_found_direct...")
        mock_task_for_finish = MagicMock(spec=Gio.Task)
        mock_task_for_finish.get_task_data.return_value = "example.com"
        mock_task_for_finish.run_in_thread_finish = Mock(return_value=(None, None, "FileNotFoundError"))

        self.page._on_scan_task_done(mock_task_for_finish, Mock(spec=Gio.AsyncResult), None)
        self.mock_main_window.show_error.assert_called_once_with(
            "Nikto command not found. Please ensure it is installed and in your PATH."
        )
        self.mock_add_toast.assert_not_called()
        print("Finished test_critical_error_nikto_not_found_direct.")

    def test_critical_error_timeout_direct(self): # Renamed
        print("Running test_critical_error_timeout_direct...")
        target_url = "timeout.com"
        mock_task_for_finish = MagicMock(spec=Gio.Task)
        mock_task_for_finish.get_task_data.return_value = target_url
        mock_task_for_finish.run_in_thread_finish = Mock(return_value=(None, None, "TimeoutExpired"))

        self.page._on_scan_task_done(mock_task_for_finish, Mock(spec=Gio.AsyncResult), None)
        self.mock_main_window.show_error.assert_called_once_with(
            f"Scan for {target_url} timed out."
        )
        self.mock_add_toast.assert_not_called()
        print("Finished test_critical_error_timeout_direct.")

if __name__ == '__main__':
    if not Gtk.Application.get_default():
        app = Gtk.Application(application_id="com.example.test.woes.unittest.main")
        def do_activate(app_instance): pass
        app.do_activate = do_activate
        app.register(None)

    unittest.main(argv=['first-arg-is-ignored'], exit=False)

    # No need to restore Gtk.Template here if done in the try/finally block after import.
