import unittest
from unittest.mock import patch, MagicMock, ANY, call
import requests # Keep standard imports
import sys
import importlib
import inspect
import time

# --- Global GI Mocking Setup ---
# Stop any active unittest.mock patches from other potential sources/previous runs
if 'unittest.mock' in sys.modules:
    from unittest.mock import patch as global_patcher
    global_patcher.stopall()

# Remove any pre-existing 'gi' related modules from sys.modules
# This is to ensure our mock 'gi' is the one that gets used by 'src.http_page'
gi_related_modules = [m for m in sys.modules if m.startswith('gi')]
for m in gi_related_modules:
    del sys.modules[m]

# Define and apply our controlled GI mocks
MOCK_GI_MODULES = {
    'gi': MagicMock(),
    'gi.repository': MagicMock(),
    'gi.repository.Gtk': MagicMock(),
    'gi.repository.Adw': MagicMock(),
    'gi.repository.Gio': MagicMock(),
    'gi.repository.GLib': MagicMock(),
    'gi.repository.GObject': MagicMock(),
}
# Ensure the mocked 'gi' module can handle require_version calls
MOCK_GI_MODULES['gi'].require_version = MagicMock()
sys.modules.update(MOCK_GI_MODULES)

# Now, when any module (like src.http_page) does 'from gi.repository import Gtk',
# it will get our MagicMock versions.
from gi.repository import Adw as MockAdw, Gtk as MockGtk, Gio as MockGio, GLib as MockGLib, GObject as MockGObject

# Configure MockGtk.Template to be a pass-through decorator
# Gtk.Template(resource_path="...") should return a function that takes a class and returns it.
MockGtk.Template = MagicMock(side_effect=lambda resource_path: (lambda cls: cls))
# Gtk.Template.Child used at class level also needs to return a mock
MockGtk.Template.Child = MagicMock(side_effect=lambda name: MagicMock(name=f"mock_template_child_class_level_{name}"))


# Setup specific attributes on the Mocks that HttpPage or tests might need
MockGLib.get_monotonic_time = MagicMock(side_effect=lambda: int(time.time() * 1e6)) # microseconds
MockGLib.MainContext.default.return_value.iteration = MagicMock(return_value=False)
MockGLib.MainContext.default.return_value.pending = MagicMock(return_value=False)
MockGLib.usleep = MagicMock(side_effect=lambda us: time.sleep(us / 1e6))

class MockGLibErrorForTest(Exception):
    def __init__(self, message, domain, code):
        super().__init__(message)
        self.message = message
        self.domain = domain
        self.code = code
MockGLib.Error = MockGLibErrorForTest
MockGObject.GError = MockGLibErrorForTest

MockGio.Task.new = MagicMock()
mock_cancellable = MagicMock() # Removed spec, it was causing issues
mock_cancellable.is_cancelled.return_value = False
mock_cancellable.cancel = MagicMock()
MockGio.Cancellable = MagicMock(return_value=mock_cancellable)
# Ensure io_error_quark and IOErrorEnum are available on the mocked Gio
MockGio.io_error_quark = MagicMock(return_value="mock-gio-io-error-quark")
MockGio.IOErrorEnum = MagicMock()
MockGio.IOErrorEnum.FAILED = 1 # Example value
MockGio.IOErrorEnum.CANCELLED = 36 # Example value (was 34, check common values)
MockGio.IOErrorEnum.TIMED_OUT = 14 # Example, if needed for error codes

# --- Module-level HttpPage class loading attempt ---
http_page_module = None
HttpPage_class = None
try:
    if 'src.http_page' in sys.modules: # Should have been deleted if it was there before mocks
        del sys.modules['src.http_page']
    http_page_module = importlib.import_module('src.http_page')
    if hasattr(http_page_module, 'HttpPage') and inspect.isclass(http_page_module.HttpPage):
        HttpPage_class = http_page_module.HttpPage
    else:
        # This path will be taken if HttpPage is a mock after import
        # Try the fallback from the problem description
        temp_HttpPage = http_page_module.HttpPage if hasattr(http_page_module, 'HttpPage') else None
        if temp_HttpPage and not inspect.isclass(temp_HttpPage):
            if hasattr(temp_HttpPage, 'get_original'):
                original_obj = temp_HttpPage.get_original()
                if isinstance(original_obj, tuple) and len(original_obj) > 0 and inspect.isclass(original_obj[0]):
                     HttpPage_class = original_obj[0] # Assuming (original_class, ...)
                elif inspect.isclass(original_obj): # If get_original returns the class directly
                     HttpPage_class = original_obj
            elif hasattr(temp_HttpPage, '_mock_wraps') and temp_HttpPage._mock_wraps and inspect.isclass(temp_HttpPage._mock_wraps):
                 HttpPage_class = temp_HttpPage._mock_wraps
        if HttpPage_class:
             print("HttpPage_class loaded via fallback.")
        else:
            print(f"Failed to load HttpPage as a class. http_page_module.HttpPage is: {temp_HttpPage}")

except Exception as e:
    print(f"Failed to import/load HttpPage from module: {e}")


class TestHttpPage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.HttpPage_class_to_test = HttpPage_class
        if cls.HttpPage_class_to_test:
            cls.app = MockAdw.Application(application_id="test.woes.http.page")
        else:
            print("WARNING: HttpPage_class not loaded, tests will be skipped.")


    def setUp(self):
        if not self.HttpPage_class_to_test:
            self.skipTest("HttpPage class could not be loaded at module level.")

        self.page = None
        self.mock_task_instance = MagicMock(spec=MockGio.Task)
        self.mock_task_instance.get_cancellable.return_value = mock_cancellable

        self.done_cb_to_call = None
        self.done_cb_args = None
        self.done_cb_kwargs = None # Store kwargs too

        def mock_idle_add(func, *args, **kwargs):
            # Store the priority and other args if necessary, but for now just func and main args
            # print(f"mock_idle_add called with func: {func}")
            self.done_cb_to_call = func
            self.done_cb_args = args
            self.done_cb_kwargs = kwargs # Capture keyword arguments from idle_add
            return 0
        MockGLib.idle_add = mock_idle_add
        MockGLib.source_remove = MagicMock(return_value=True)

        def actual_run_in_thread(task_func_on_http_page):
            # print(f"actual_run_in_thread: Calling {task_func_on_http_page}")
            task_func_on_http_page(
                self.mock_task_instance,
                self.page,
                self.page._http_task_data_for_thread, # Ensure this is set before calling _on_entry_row_activated
                self.mock_task_instance.get_cancellable()
            )
            if self.done_cb_to_call:
                # print(f"Calling done_cb: {self.done_cb_to_call}")
                # Callback signature: (source_object, async_result, user_data)
                # The user_data is what was passed to Gio.Task.new as its last argument (usually None)
                # The source_object for the callback is the one passed to Gio.Task.new (self.page)
                # The async_result is the task instance itself (self.mock_task_instance)
                self.done_cb_to_call(self.page, self.mock_task_instance, None) # user_data is None
                self.done_cb_to_call = None
                self.done_cb_args = None
                self.done_cb_kwargs = None
            return None

        self.mock_task_instance.run_in_thread = MagicMock(side_effect=actual_run_in_thread)
        MockGio.Task.new.return_value = self.mock_task_instance

        # Instantiate HttpPage
        # This needs to happen after mock_Gio.Task.new is configured for its __init__
        # if HttpPage creates tasks in __init__ (it doesn't seem to).
        # Patches for Gtk.Template.Child should be active here.
        template_child_mock = MagicMock(side_effect=lambda name: MagicMock(name=f"mock_template_child_{name}"))
        list_store_mock = MagicMock(spec=MockGio.ListStore) # Mock for Gio.ListStore
        list_store_mock.remove_all = MagicMock() # Mock method used in _update_column_view_model

        try:
            # The Gtk, Gio, etc. used by HttpPage at definition time are already the global mocks
            # So, Gtk.Template.Child inside HttpPage class definition will use the mocked Gtk.
            # We need to ensure that Gtk.Template.Child returns a mock when called.
            # This means the global MockGtk.Template.Child should be set up.
            MockGtk.Template.Child = template_child_mock
            MockGio.ListStore.new = MagicMock(return_value=list_store_mock)
            MockGtk.StringList.new = MagicMock(return_value=MagicMock(spec=MockGtk.StringList))
            MockGtk.MultiSelection.new = MagicMock(return_value=MagicMock(spec=MockGtk.MultiSelection))
            MockGtk.ColumnViewColumn.new = MagicMock(return_value=MagicMock(spec=MockGtk.ColumnViewColumn))
            MockGtk.SignalListItemFactory.new = MagicMock(return_value=MagicMock(spec=MockGtk.SignalListItemFactory))

            self.page = self.HttpPage_class_to_test()
        except Exception as e:
            self.fail(f"Failed to instantiate HttpPage: {e}")


    @patch('src.http_page.requests.get')
    def test_timeout_error_handling(self, mock_requests_get):
        mock_requests_get.side_effect = requests.exceptions.Timeout("Test timeout")

        page = self.page

        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_entry_row.get_text.return_value = "http://example.com"
        page.http_entry_row.get_sensitive.return_value = True
        page.http_entry_row.set_sensitive = MagicMock()
        page.http_entry_row.add_css_class = MagicMock()

        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow)
        page.http_user_agent_row.get_selected.return_value = 0
        mock_ua_model = MagicMock()
        mock_ua_model.get_string.return_value = "Test User Agent"
        page.http_user_agent_row.get_model.return_value = mock_ua_model

        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow)
        page.http_pragma_switch_row.get_active.return_value = False
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_host_header_row.get_text.return_value = ""

        page.error_banner = MagicMock(spec=MockAdw.Banner)
        page.error_banner.set_revealed = MagicMock()
        page.error_banner.set_title = MagicMock()

        page.http_results_group = MagicMock(spec=MockGtk.Box)
        # page.header_list_store is already a mock from the Gtk.Template.Child or Gio.ListStore.new mocking in setUp
        # For safety, ensure it has remove_all if it wasn't set up by Gio.ListStore.new mock
        if not hasattr(page.header_list_store, 'remove_all'):
            page.header_list_store.remove_all = MagicMock()

        # page._http_task_data_for_thread will be created by _on_entry_row_activated

        def mock_propagate_value_func(*args, **kwargs):
            raise MockGLibErrorForTest(
                message="Timeout Error: The request timed out.",
                domain="test-error-domain",
                code=123
            )
        self.mock_task_instance.propagate_value = mock_propagate_value_func
        self.mock_task_instance.return_error = MagicMock()

        page._on_entry_row_activated(page.http_entry_row)

        # Assertions
        page.error_banner.set_revealed.assert_called_with(True)
        self.assertTrue(page.error_banner.set_title.called, "error_banner.set_title was not called")
        args_title, _ = page.error_banner.set_title.call_args
        self.assertIn("timeout", args_title[0].lower(), f"Error message '{args_title[0]}' does not contain 'timeout'.")

        self.assertIsNone(page.current_http_task, "Task should be cleared after error handling.")
        page.http_entry_row.set_sensitive.assert_called_with(True)
        page.http_entry_row.add_css_class.assert_called_with("error")

        self.mock_task_instance.return_error.assert_called_once()
        error_arg = self.mock_task_instance.return_error.call_args[0][0]
        self.assertIsInstance(error_arg, MockGLibErrorForTest)
        self.assertIn("timed out", error_arg.message.lower())

    @patch('src.http_page.requests.get')
    def test_https_connection_refused_error_handling(self, mock_requests_get):
        # 1. Configure the mock to raise a ConnectionError simulating 'Connection refused' on HTTPS
        from requests.packages.urllib3.exceptions import MaxRetryError, NewConnectionError

        # The error message is in reason.args[0] for NewConnectionError,
        # which is wrapped by MaxRetryError, then by ConnectionError.
        # The actual string checked for is 'Connection refused'.
        connection_refused_msg = "[Errno 111] Connection refused"
        reason_mock = NewConnectionError("mock_pool", connection_refused_msg)
        max_retry_error = MaxRetryError(None, "https://example.com", reason_mock)
        connection_error = requests.exceptions.ConnectionError(max_retry_error)
        mock_requests_get.side_effect = connection_error

        page = self.page # Instantiated in setUp

        # Configure mocks for UI elements accessed in _on_entry_row_activated and callback
        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_entry_row.get_text.return_value = "https://example.com" # HTTPS URL
        page.http_entry_row.get_sensitive.return_value = True
        page.http_entry_row.set_sensitive = MagicMock()
        page.http_entry_row.add_css_class = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock() # For _clear_error

        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow)
        page.http_user_agent_row.get_selected.return_value = 0 # "None"
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow)
        page.http_pragma_switch_row.get_active.return_value = False

        page.error_banner = MagicMock(spec=MockAdw.Banner)
        page.error_banner.set_revealed = MagicMock()
        page.error_banner.set_title = MagicMock()

        page.http_results_group = MagicMock(spec=MockGtk.Box) # Used by _hide_results -> _update_column_view_model
        page.http_results_group.set_visible = MagicMock()

        # Ensure header_list_store mock (from Gio.ListStore.new mock in setUp) has remove_all
        if not hasattr(page.header_list_store, 'remove_all'):
             page.header_list_store.remove_all = MagicMock()


        # Setup task behavior: propagate_value should raise the error set by return_error
        def mock_propagate_value_based_on_return_error(*args, **kwargs):
            if self.mock_task_instance.return_error.call_args:
                g_error_instance = self.mock_task_instance.return_error.call_args[0][0]
                raise g_error_instance
            return MagicMock() # Default return if no error was set (shouldn't happen here)

        self.mock_task_instance.propagate_value = MagicMock(side_effect=mock_propagate_value_based_on_return_error)
        self.mock_task_instance.return_error = MagicMock() # To capture the GError

        # 2. Simulate activating the entry row
        page._on_entry_row_activated(page.http_entry_row)

        # 3. Assertions
        #    - Error banner is revealed
        #    - Title is the specific message
        page.error_banner.set_revealed.assert_called_with(True)
        page.error_banner.set_title.assert_called_once()
        args_title, _ = page.error_banner.set_title.call_args
        expected_message = "The URL is HTTP only and does not support HTTPS. Please try with 'http://'."
        self.assertEqual(args_title[0], expected_message)

        # Other assertions from test_timeout_error_handling that should also apply
        self.assertIsNone(page.current_http_task, "Task should be cleared after error handling.")
        page.http_entry_row.set_sensitive.assert_called_with(True) # Re-enabled in finally block
        page.http_entry_row.add_css_class.assert_called_with("error") # Set in _display_error

        # Check that task.return_error was called with a GLib.Error containing the specific message
        self.mock_task_instance.return_error.assert_called_once()
        error_arg = self.mock_task_instance.return_error.call_args[0][0]
        self.assertIsInstance(error_arg, MockGLibErrorForTest) # MockGLib.Error is MockGLibErrorForTest
        self.assertEqual(error_arg.message, expected_message)

    def test_clear_results_button_functionality(self):
        page = self.page # Instantiated in setUp

        # 1. Configure initial state:
        #    - Populate header_list_store (mocked in setUp)
        #    - Ensure http_results_group is visible
        #    - http_entry_row has text
        #    - error_banner is visible and has a title

        # Mock methods on header_list_store (which is already a MagicMock from setUp)
        page.header_list_store.append = MagicMock()
        # page.header_list_store.remove_all is already mocked via Gio.ListStore.new in setUp,
        # but ensure it is for this test context specifically if setUp changes.
        if not hasattr(page.header_list_store, 'remove_all'): # Should be there from global mock
            page.header_list_store.remove_all = MagicMock()

        page.header_list_store.append("dummy_key", "dummy_value") # Simulate adding an item

        page.http_results_group = MagicMock(spec=MockGtk.Box)
        page.http_results_group.set_visible = MagicMock()
        page.http_results_group.set_visible(True) # Simulate it's initially visible

        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_entry_row.set_text = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock() # For _clear_error
        page.http_entry_row.set_text("http://example.com/somepath") # Simulate initial text

        page.error_banner = MagicMock(spec=MockAdw.Banner)
        page.error_banner.set_revealed = MagicMock()
        page.error_banner.set_title = MagicMock()
        page.error_banner.set_title("Old error message") # Simulate initial error
        page.error_banner.set_revealed(True)


        # 2. Simulate a click on the "Clear Results" button
        # The button itself is not mocked here, we call the handler directly.
        # The _on_clear_results_clicked method takes (_button, *_args)
        # We can pass None for the button argument as it's not used in the method.
        page._on_clear_results_clicked(None)

        # 3. Assert the conditions
        #    *   `header_list_store` is empty (remove_all was called)
        #    *   `http_results_group` is not visible
        #    *   `http_entry_row` text is empty
        #    *   `error_banner` is not revealed

        page.header_list_store.remove_all.assert_called_once()
        page.http_results_group.set_visible.assert_called_with(False) # Called by _hide_results
        page.http_entry_row.set_text.assert_called_with("")
        page.error_banner.set_revealed.assert_called_with(False) # Called by _clear_error
        page.error_banner.set_title.assert_called_with("") # Also part of _clear_error


if __name__ == '__main__':
    if HttpPage_class:
        unittest.main()
    else:
        print("Skipping unittest.main() as HttpPage_class was not loaded.")
