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

# Import the real HeaderItem for type checking and instance creation in tests
HeaderItem_class_for_test = None
if 'src.http_page' in sys.modules: # http_page_module might be defined below
    temp_http_page_module = sys.modules['src.http_page']
    if hasattr(temp_http_page_module, 'HeaderItem') and inspect.isclass(temp_http_page_module.HeaderItem):
        HeaderItem_class_for_test = temp_http_page_module.HeaderItem

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

# Helper function to create mock response objects for testing redirect history
def _create_mock_response(url, status_code, headers, history_list=None):
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.url = str(url) # Ensure URL is string
    mock_resp.status_code = status_code
    mock_resp.headers = headers # Should be a dict-like object
    mock_resp.history = history_list if history_list is not None else []
    return mock_resp

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
        # Ensure the code used matches the mocked Gio.IOErrorEnum.TIMED_OUT
        self.assertEqual(error_arg.code, int(MockGio.IOErrorEnum.TIMED_OUT))

    @patch('src.http_page.requests.get')
    def test_timeout_error_post_fix_verification(self, mock_requests_get):
        """
        Specifically verifies timeout handling after the AttributeError fix,
        ensuring the correct Gio.IOErrorEnum is used and error is propagated.
        This test is similar to test_timeout_error_handling but focuses on the
        integrity of the GError creation and propagation for timeouts.
        """
        mock_requests_get.side_effect = requests.exceptions.Timeout("Test timeout specifically for post-fix verification")

        page = self.page

        # Minimal UI mock setup, similar to other tests
        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_entry_row.get_text.return_value = "http://example-for-timeout-verification.com"
        page.http_entry_row.get_sensitive.return_value = True # Initial state
        page.http_entry_row.set_sensitive = MagicMock()
        page.http_entry_row.add_css_class = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock()


        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow)
        page.http_user_agent_row.get_selected.return_value = 0 # "None"
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_host_header_row.get_text.return_value = "" # No specific host header
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow)
        page.http_pragma_switch_row.get_active.return_value = False # Pragma off

        page.error_banner = MagicMock(spec=MockAdw.Banner)
        page.error_banner.set_revealed = MagicMock()
        page.error_banner.set_title = MagicMock()

        page.http_results_group = MagicMock(spec=MockGtk.Box)
        page.http_results_group.set_visible = MagicMock() # Used by _hide_results
        if not hasattr(page.header_list_store, 'remove_all'): # From setUp's Gio.ListStore.new mock
            page.header_list_store.remove_all = MagicMock()


        # Setup task behavior: propagate_value should raise the GError that was set by return_error
        def mock_propagate_value_based_on_return_error(*args, **kwargs):
            # Check if return_error was called and retrieve the GError instance
            if self.mock_task_instance.return_error.call_count > 0:
                # Get the first argument of the first call to return_error, which is the GError
                g_error_instance = self.mock_task_instance.return_error.call_args[0][0]
                # Ensure it's an instance of our test GLib.Error
                if isinstance(g_error_instance, MockGLibErrorForTest):
                    raise g_error_instance # Raise it to simulate GIO behavior
                else:
                    # This case should ideally not happen if http_page correctly uses GLib.Error
                    raise TypeError(f"Error passed to return_error was not a MockGLibErrorForTest: {type(g_error_instance)}")
            # Fallback if return_error was not called, or if it was called with something unexpected
            # This would indicate a problem in the task's error handling logic itself.
            # For a timeout, return_error should always be called.
            return MagicMock() # Or raise an assertion error if this path is unexpected

        self.mock_task_instance.propagate_value = MagicMock(side_effect=mock_propagate_value_based_on_return_error)
        self.mock_task_instance.return_error = MagicMock() # Reset/ensure it's a fresh mock for this test

        # --- Action ---
        page._on_entry_row_activated(page.http_entry_row)

        # --- Assertions ---
        # 1. Error banner shows the correct timeout message and is revealed
        page.error_banner.set_revealed.assert_called_with(True)
        page.error_banner.set_title.assert_called_once()
        args_title, _ = page.error_banner.set_title.call_args
        # The message in http_page.py is "Timeout Error: The request timed out."
        self.assertEqual(args_title[0], "Timeout Error: The request timed out.")

        # 2. UI elements are in the correct state post-error
        self.assertIsNone(page.current_http_task, "Task should be cleared after error handling.")
        page.http_entry_row.set_sensitive.assert_called_with(True) # Re-enabled in finally
        page.http_entry_row.add_css_class.assert_called_with("error") # Set by _display_error

        # 3. Gio.Task.return_error was called correctly
        self.mock_task_instance.return_error.assert_called_once()
        error_arg = self.mock_task_instance.return_error.call_args[0][0]

        # 3a. The error is of the correct type (our mocked GLib.Error)
        self.assertIsInstance(error_arg, MockGLibErrorForTest)

        # 3b. The error message is correct
        self.assertEqual(error_arg.message, "Timeout Error: The request timed out.")

        # 3c. The error domain is correct
        self.assertEqual(error_arg.domain, MockGio.io_error_quark.return_value)

        # 3d. Crucially, the error code matches Gio.IOErrorEnum.TIMED_OUT (mocked value)
        # This verifies that `code=int(Gio.IOErrorEnum.TIMED_OUT)` was used correctly.
        self.assertEqual(error_arg.code, int(MockGio.IOErrorEnum.TIMED_OUT))
        self.assertIsInstance(error_arg.code, int) # Ensure it's an int, as expected by GLib.Error

    @patch('src.http_page.requests.get')
    def test_https_connection_refused_error_handling(self, mock_requests_get):
        # 1. Configure the mock to raise a ConnectionError simulating 'Connection refused' on HTTPS,
        #    matching a typical traceback structure.
        from requests.packages.urllib3.exceptions import MaxRetryError, NewConnectionError

        # Define parts of the URL for consistency in the mock error messages
        mock_url_host = "example.com"
        mock_url_path = "/some/path/for/test"
        full_mock_url = f"https://{mock_url_host}{mock_url_path}"

        # Inner-most error message part we are searching for
        inner_refused_msg_fragment = "[Errno 111] Connection refused"

        # Construct NewConnectionError:
        # The message typically includes a representation of the connection object and the OS error.
        new_conn_error_message = (
            f"<requests.packages.urllib3.connection.HTTPSConnection object at 0xmockaddress>: "
            f"Failed to establish a new connection: {inner_refused_msg_fragment}"
        )
        # The NewConnectionError usually takes the pool and the message string as positional args.
        # For testing, the pool can be None.
        new_conn_error = NewConnectionError(None, new_conn_error_message)
        # Optionally, to simulate newer urllib3 that might carry the original ConnectionRefusedError:
        # new_conn_error.original_error = ConnectionRefusedError(inner_refused_msg_fragment)


        # Construct MaxRetryError:
        # This error wraps the NewConnectionError as its 'reason'.
        # Its message includes the pool details (host, port) and the requested URL/path.
        max_retry_error_message = (
            f"HTTPSConnectionPool(host='{mock_url_host}', port=443): "
            f"Max retries exceeded with url: {mock_url_path} (Caused by {new_conn_error!r})"
        )
        max_retry_error = MaxRetryError(
            pool=None, # Mock pool or None
            url=mock_url_path, # The path part of the URL
            reason=new_conn_error # The NewConnectionError instance
        )
        # Manually set args to match how requests might construct it if its __str__ is just the message.
        # Or rely on its default __str__ if it includes the reason.
        # For this test, ensuring the NewConnectionError is the 'reason' is key.

        # Construct ConnectionError (from requests.exceptions):
        # This is the top-level exception, and it wraps the MaxRetryError.
        # Its args[0] will be the max_retry_error.
        connection_error = requests.exceptions.ConnectionError(max_retry_error)
        mock_requests_get.side_effect = connection_error

        page = self.page # Instantiated in setUp

        # Configure mocks for UI elements accessed in _on_entry_row_activated and callback
        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_entry_row.get_text.return_value = full_mock_url # Use the same URL for consistency
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
        expected_message = "The URL targetted via HTTPS is refusing the connection. It might be an HTTP-only service. Please try with 'http://'."
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
        # Verify domain and code of the GLib.Error
        self.assertEqual(error_arg.domain, MockGio.io_error_quark.return_value)
        self.assertEqual(error_arg.code, MockGio.IOErrorEnum.FAILED) # Mocked to 1
        self.assertIsInstance(error_arg.code, int) # Explicitly check type of code

    # Test methods for redirect handling will be added after this one.

    @patch('src.http_page.requests.get')
    def test_http_request_to_https_only_service(self, mock_requests_get):
        # 1. Simulate an http:// URL and a ConnectionRefusedError
        from requests.packages.urllib3.exceptions import MaxRetryError, NewConnectionError

        connection_refused_msg = "Failed to establish a new connection: [Errno 111] Connection refused"
        new_conn_error = NewConnectionError(None, reason=connection_refused_msg)
        max_retry_error = MaxRetryError(None, "http://example-https-only.com", reason=new_conn_error) # HTTP URL
        connection_error = requests.exceptions.ConnectionError(max_retry_error)
        mock_requests_get.side_effect = connection_error

        page = self.page

        # Configure mocks for UI elements
        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_entry_row.get_text.return_value = "http://example-https-only.com" # HTTP URL
        page.http_entry_row.get_sensitive.return_value = True
        page.http_entry_row.set_sensitive = MagicMock()
        page.http_entry_row.add_css_class = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock()

        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow)
        page.http_user_agent_row.get_selected.return_value = 0
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow)
        page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow)
        page.http_pragma_switch_row.get_active.return_value = False

        page.error_banner = MagicMock(spec=MockAdw.Banner)
        page.error_banner.set_revealed = MagicMock()
        page.error_banner.set_title = MagicMock()

        page.http_results_group = MagicMock(spec=MockGtk.Box)
        page.http_results_group.set_visible = MagicMock()
        if not hasattr(page.header_list_store, 'remove_all'):
             page.header_list_store.remove_all = MagicMock()

        # Setup task behavior for error propagation
        def mock_propagate_value_based_on_return_error(*args, **kwargs):
            if self.mock_task_instance.return_error.call_args:
                g_error_instance = self.mock_task_instance.return_error.call_args[0][0]
                raise g_error_instance
            return MagicMock()

        self.mock_task_instance.propagate_value = MagicMock(side_effect=mock_propagate_value_based_on_return_error)
        self.mock_task_instance.return_error = MagicMock()

        # 2. Simulate activating the entry row
        # Note: _ensure_scheme will NOT change http:// to https:// if http is already present
        page._on_entry_row_activated(page.http_entry_row)

        # 3. Assertions
        expected_message = "The HTTP request failed. The server might only support HTTPS for this resource. Please try with 'https://'."

        page.error_banner.set_revealed.assert_called_with(True)
        page.error_banner.set_title.assert_called_once_with(expected_message)

        self.assertIsNone(page.current_http_task)
        page.http_entry_row.set_sensitive.assert_called_with(True)
        page.http_entry_row.add_css_class.assert_called_with("error")

        self.mock_task_instance.return_error.assert_called_once()
        error_arg = self.mock_task_instance.return_error.call_args[0][0]
        self.assertIsInstance(error_arg, MockGLibErrorForTest)
        self.assertEqual(error_arg.message, expected_message)
        self.assertEqual(error_arg.domain, MockGio.io_error_quark.return_value)
        self.assertEqual(error_arg.code, MockGio.IOErrorEnum.FAILED)

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

# Helper function to create mock response objects for testing redirect history
def _create_mock_response(url, status_code, headers, history_list=None):
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.url = str(url) # Ensure URL is string
    mock_resp.status_code = status_code
    mock_resp.headers = headers # Should be a dict-like object
    mock_resp.history = history_list if history_list is not None else []
    return mock_resp

# TestHttpPage class continues...
# We will append new test methods inside the TestHttpPage class structure.
# The following search block targets the end of the class to append new tests.
# This is a common pattern: find a known line, then add after it.
# In this case, finding the `if __name__ == '__main__':` line and inserting before it.

# Find the last method of TestHttpPage and add new methods after it.
# The last method currently is test_clear_results_button_functionality.
# So, the new content will be added after that method's definition.

# This is a placeholder for the diff tool. The actual content will be appended.
# SEARCH/REPLACE for adding new methods requires careful structure.
# It's often easier to replace the whole class or a large chunk if adding multiple methods.
# However, attempting to append:

    @patch('src.http_page.requests.get')
    def test_fetch_headers_no_redirects(self, mock_requests_get):
        page = self.page
        final_url = "http://final.com"
        final_headers = {"Content-Type": "text/html", "X-Final-Header": "FinalValue"}

        mock_final_response = _create_mock_response(final_url, 200, final_headers, history_list=[])
        mock_requests_get.return_value = mock_final_response

        page._update_column_view_model = MagicMock()

        # This simulates what _fetch_headers_task_thread_func returns via task.return_value()
        # which is then retrieved by task.propagate_value() in the callback.
        self.mock_task_instance.propagate_value = MagicMock(return_value=[
            {'type': 'final', 'url': final_url, 'status_code': 200, 'headers': final_headers}
        ])

        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow); page.http_entry_row.get_text.return_value = final_url
        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow); page.http_user_agent_row.get_selected.return_value = 0
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow); page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow); page.http_pragma_switch_row.get_active.return_value = False
        page.error_banner = MagicMock(spec=MockAdw.Banner); page.error_banner.set_revealed = MagicMock(); page.error_banner.set_title = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock()


        page._on_entry_row_activated(page.http_entry_row)
        page._update_column_view_model.assert_called_once()
        processed_items = page._update_column_view_model.call_args[0][0]

        # Expected: URL/Status row (special), header rows (regular). No spacer after the last response block.
        self.assertEqual(len(processed_items), 1 + len(final_headers))
        self.assertTrue(processed_items[0].is_special_row)
        self.assertEqual(processed_items[0].key, f"URL: {final_url}")
        self.assertEqual(processed_items[0].value, "Status: 200 (Final)")
        idx = 1
        for key, value in final_headers.items():
            self.assertFalse(processed_items[idx].is_special_row); self.assertEqual(processed_items[idx].key, key); self.assertEqual(processed_items[idx].value, value); idx+=1

    @patch('src.http_page.requests.get')
    def test_fetch_headers_single_redirect(self, mock_requests_get):
        page = self.page
        r1_url = "http://initial.com"; r1_hdrs = {"L1": "V1"}; r1_stat = 301
        final_url = "http://final.com"; final_hdrs = {"L_Final": "V_Final"}; final_stat = 200

        mock_r1 = _create_mock_response(r1_url, r1_stat, r1_hdrs)
        mock_final = _create_mock_response(final_url, final_stat, final_hdrs, history_list=[mock_r1])
        mock_requests_get.return_value = mock_final

        page._update_column_view_model = MagicMock()
        self.mock_task_instance.propagate_value = MagicMock(return_value=[
            {'type': 'redirect', 'url': r1_url, 'status_code': r1_stat, 'headers': r1_hdrs},
            {'type': 'final', 'url': final_url, 'status_code': final_stat, 'headers': final_hdrs}
        ])

        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow); page.http_entry_row.get_text.return_value = r1_url
        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow); page.http_user_agent_row.get_selected.return_value = 0
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow); page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow); page.http_pragma_switch_row.get_active.return_value = False
        page.error_banner = MagicMock(spec=MockAdw.Banner); page.error_banner.set_revealed = MagicMock(); page.error_banner.set_title = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock()

        page._on_entry_row_activated(page.http_entry_row)
        page._update_column_view_model.assert_called_once()
        processed_items = page._update_column_view_model.call_args[0][0]

        expected_len = (1 + len(r1_hdrs) + 1) + (1 + len(final_hdrs)) # r1_info, r1_hdrs, spacer, final_info, final_hdrs
        self.assertEqual(len(processed_items), expected_len)

        # R1
        self.assertTrue(processed_items[0].is_special_row)
        self.assertEqual(processed_items[0].key, f"URL: {r1_url}")
        self.assertEqual(processed_items[0].value, f"Status: {r1_stat} (Redirect)")
        idx = 1
        for k, v in r1_hdrs.items():
            self.assertFalse(processed_items[idx].is_special_row)
            self.assertEqual(processed_items[idx].key, k)
            self.assertEqual(processed_items[idx].value, v)
            idx += 1
        # Spacer
        self.assertTrue(processed_items[idx].is_special_row)
        self.assertEqual(processed_items[idx].key, "")
        idx += 1
        # Final
        self.assertTrue(processed_items[idx].is_special_row)
        self.assertEqual(processed_items[idx].key, f"URL: {final_url}")
        self.assertEqual(processed_items[idx].value, f"Status: {final_stat} (Final)")
        idx += 1
        for k, v in final_hdrs.items():
            self.assertFalse(processed_items[idx].is_special_row)
            self.assertEqual(processed_items[idx].key, k)
            self.assertEqual(processed_items[idx].value, v)
            idx += 1

    @patch('src.http_page.requests.get')
    def test_fetch_headers_multiple_redirects(self, mock_requests_get):
        page = self.page
        r1_url="http://r1.com"; r1_h={"R1H":"1"}; r1_s=301
        r2_url="http://r2.com"; r2_h={"R2H":"2"}; r2_s=302
        f_url="http://final.com"; f_h={"FH":"F"}; f_s=200

        mock_r1 = _create_mock_response(r1_url, r1_s, r1_h)
        mock_r2 = _create_mock_response(r2_url, r2_s, r2_h)
        mock_final = _create_mock_response(f_url, f_s, f_h, history_list=[mock_r1, mock_r2])
        mock_requests_get.return_value = mock_final

        page._update_column_view_model = MagicMock()
        self.mock_task_instance.propagate_value = MagicMock(return_value=[
            {'type': 'redirect', 'url': r1_url, 'status_code': r1_s, 'headers': r1_h},
            {'type': 'redirect', 'url': r2_url, 'status_code': r2_s, 'headers': r2_h},
            {'type': 'final', 'url': f_url, 'status_code': f_s, 'headers': f_h}
        ])

        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow); page.http_entry_row.get_text.return_value = r1_url
        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow); page.http_user_agent_row.get_selected.return_value = 0
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow); page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow); page.http_pragma_switch_row.get_active.return_value = False
        page.error_banner = MagicMock(spec=MockAdw.Banner); page.error_banner.set_revealed = MagicMock(); page.error_banner.set_title = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock()

        page._on_entry_row_activated(page.http_entry_row)
        page._update_column_view_model.assert_called_once()
        processed_items = page._update_column_view_model.call_args[0][0]

        expected_len = (1+len(r1_h)+1) + (1+len(r2_h)+1) + (1+len(f_h))
        self.assertEqual(len(processed_items), expected_len)

        # Basic checks for order and type
        self.assertTrue(processed_items[0].is_special_row); self.assertIn(r1_url, processed_items[0].key) # R1 info
        self.assertTrue(processed_items[1+len(r1_h)].is_special_row) # Spacer after R1
        self.assertTrue(processed_items[2+len(r1_h)].is_special_row); self.assertIn(r2_url, processed_items[2+len(r1_h)].key) # R2 info
        self.assertTrue(processed_items[2+len(r1_h)+1+len(r2_h)].is_special_row) # Spacer after R2
        self.assertTrue(processed_items[2+len(r1_h)+2+len(r2_h)].is_special_row); self.assertIn(f_url, processed_items[2+len(r1_h)+2+len(r2_h)].key) # Final info

    def test_styling_of_special_rows(self):
        page = self.page
        # Ensure HeaderItem_class_for_test is available
        self.assertIsNotNone(HeaderItem_class_for_test, "HeaderItem class not loaded for test")

        mock_list_item = MagicMock(spec=MockGtk.ListItem)
        mock_label = MagicMock(spec=MockGtk.Label)
        mock_list_item.get_child.return_value = mock_label

        # We need a factory instance that has its 'setup' and 'bind' callbacks captured.
        # The HttpPage.__init__ normally creates these. We'll create one and manually set up capture.
        factory_capturer = MagicMock(spec=MockGtk.SignalListItemFactory)
        captured_callbacks = {}
        def capture_connect(signal_name, func_to_capture):
            captured_callbacks[signal_name] = func_to_capture
        factory_capturer.connect = MagicMock(side_effect=capture_connect)

        # Temporarily override the mock for SignalListItemFactory.new
        original_factory_new = MockGtk.SignalListItemFactory.new
        MockGtk.SignalListItemFactory.new = MagicMock(return_value=factory_capturer)

        # Call _create_factory which internally calls new() and connect()
        # This factory will use the factory_capturer and store its connected functions
        # in captured_callbacks
        tested_factory = page._create_factory(attr_name="key")

        self.assertIn("setup", captured_callbacks)
        self.assertIn("bind", captured_callbacks)
        setup_func = captured_callbacks["setup"]
        bind_func = captured_callbacks["bind"]

        # Restore the original mock for SignalListItemFactory.new for other tests
        MockGtk.SignalListItemFactory.new = original_factory_new

        # Call setup_func to simulate Gtk setting up the list item
        setup_func(None, mock_list_item) # First arg (factory) is not used in setup_func

        # Test Case 1: Special row
        special_item_key = "URL: http://example.com"
        special_item = HeaderItem_class_for_test(key=special_item_key, value="Status: 200", is_special_row=True)
        mock_list_item.get_item.return_value = special_item

        original_markup_escape = MockGLib.markup_escape_text
        MockGLib.markup_escape_text = MagicMock(side_effect=lambda text: text) # Simple pass-through for this test

        bind_func(None, mock_list_item) # First arg (factory) is not used in bind_func

        expected_markup = f"<b>{special_item_key}</b>"
        mock_label.set_markup.assert_called_once_with(expected_markup)
        mock_label.set_text.assert_not_called()
        mock_label.reset_mock()

        # Test Case 2: Regular row
        regular_item_key = "Host"
        regular_item = HeaderItem_class_for_test(key=regular_item_key, value="example.com", is_special_row=False)
        mock_list_item.get_item.return_value = regular_item

        bind_func(None, mock_list_item)

        mock_label.set_text.assert_called_once_with(regular_item_key)
        mock_label.set_markup.assert_not_called()

        MockGLib.markup_escape_text = original_markup_escape # Restore


    @patch('src.http_page.requests.get')
    def test_fetch_headers_no_redirects(self, mock_requests_get):
        page = self.page
        final_url = "http://final.com"
        final_headers = {"Content-Type": "text/html", "X-Final-Header": "FinalValue"}

        mock_final_response = _create_mock_response(final_url, 200, final_headers, history_list=[])
        mock_requests_get.return_value = mock_final_response

        # Mock _update_column_view_model to capture its arguments
        # This is where the processed HeaderItem list will be sent.
        page._update_column_view_model = MagicMock()

        # Simulate that the task returns the structured list of response data
        self.mock_task_instance.propagate_value = MagicMock(return_value=[
            {'type': 'final', 'url': final_url, 'status_code': 200, 'headers': final_headers}
        ])

        # Setup other necessary mocks for _on_entry_row_activated
        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow); page.http_entry_row.get_text.return_value = final_url
        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow); page.http_user_agent_row.get_selected.return_value = 0
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow); page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow); page.http_pragma_switch_row.get_active.return_value = False
        page.error_banner = MagicMock(spec=MockAdw.Banner); page.error_banner.set_revealed = MagicMock(); page.error_banner.set_title = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock() # Called in _fetch_headers_task_done_cb

        page._on_entry_row_activated(page.http_entry_row)

        page._update_column_view_model.assert_called_once()
        processed_items = page._update_column_view_model.call_args[0][0]

        # Expected: 1 special row (URL/Status) + number of headers. No spacer after the last item.
        self.assertEqual(len(processed_items), 1 + len(final_headers))
        # Ensure HeaderItem is available for isinstance check
        self.assertIsNotNone(http_page_module.HeaderItem, "HeaderItem class could not be loaded from http_page_module")

        self.assertIsInstance(processed_items[0], http_page_module.HeaderItem)
        self.assertTrue(processed_items[0].is_special_row)
        self.assertEqual(processed_items[0].key, f"URL: {final_url}")
        self.assertEqual(processed_items[0].value, "Status: 200 (Final)")
        idx = 1
        for key, value in final_headers.items():
            self.assertIsInstance(processed_items[idx], http_page_module.HeaderItem)
            self.assertFalse(processed_items[idx].is_special_row)
            self.assertEqual(processed_items[idx].key, str(key))
            self.assertEqual(processed_items[idx].value, str(value))
            idx += 1

    @patch('src.http_page.requests.get')
    def test_fetch_headers_single_redirect(self, mock_requests_get):
        page = self.page
        r1_url = "http://initial.com"; r1_hdrs = {"L1": "V1"}; r1_stat = 301
        final_url = "http://final.com"; final_hdrs = {"L_Final": "V_Final"}; final_stat = 200

        mock_r1 = _create_mock_response(r1_url, r1_stat, r1_hdrs)
        mock_final = _create_mock_response(final_url, final_stat, final_hdrs, history_list=[mock_r1])
        mock_requests_get.return_value = mock_final

        page._update_column_view_model = MagicMock()
        self.mock_task_instance.propagate_value = MagicMock(return_value=[
            {'type': 'redirect', 'url': r1_url, 'status_code': r1_stat, 'headers': r1_hdrs},
            {'type': 'final', 'url': final_url, 'status_code': final_stat, 'headers': final_hdrs}
        ])

        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow); page.http_entry_row.get_text.return_value = r1_url
        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow); page.http_user_agent_row.get_selected.return_value = 0
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow); page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow); page.http_pragma_switch_row.get_active.return_value = False
        page.error_banner = MagicMock(spec=MockAdw.Banner); page.error_banner.set_revealed = MagicMock(); page.error_banner.set_title = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock()

        page._on_entry_row_activated(page.http_entry_row)
        page._update_column_view_model.assert_called_once()
        processed_items = page._update_column_view_model.call_args[0][0]

        expected_len = (1 + len(r1_hdrs) + 1) + (1 + len(final_hdrs))
        self.assertEqual(len(processed_items), expected_len)

        # R1
        self.assertTrue(processed_items[0].is_special_row); self.assertEqual(processed_items[0].key, f"URL: {r1_url}"); self.assertEqual(processed_items[0].value, f"Status: {r1_stat} (Redirect)");
        idx = 1; for k,v in r1_hdrs.items(): self.assertFalse(processed_items[idx].is_special_row); self.assertEqual(processed_items[idx].key,k); self.assertEqual(processed_items[idx].value,v); idx+=1
        # Spacer
        self.assertTrue(processed_items[idx].is_special_row); self.assertEqual(processed_items[idx].key, ""); idx+=1
        # Final
        self.assertTrue(processed_items[idx].is_special_row); self.assertEqual(processed_items[idx].key, f"URL: {final_url}"); self.assertEqual(processed_items[idx].value, f"Status: {final_stat} (Final)"); idx+=1
        for k,v in final_hdrs.items(): self.assertFalse(processed_items[idx].is_special_row); self.assertEqual(processed_items[idx].key,k); self.assertEqual(processed_items[idx].value,v); idx+=1

    @patch('src.http_page.requests.get')
    def test_fetch_headers_multiple_redirects(self, mock_requests_get):
        page = self.page
        r1_url="http://r1.com"; r1_h={"R1H":"1"}; r1_s=301
        r2_url="http://r2.com"; r2_h={"R2H":"2"}; r2_s=302
        f_url="http://final.com"; f_h={"FH":"F"}; f_s=200

        mock_r1 = _create_mock_response(r1_url, r1_s, r1_h)
        mock_r2 = _create_mock_response(r2_url, r2_s, r2_h)
        mock_final = _create_mock_response(f_url, f_s, f_h, history_list=[mock_r1, mock_r2])
        mock_requests_get.return_value = mock_final

        page._update_column_view_model = MagicMock()
        self.mock_task_instance.propagate_value = MagicMock(return_value=[
            {'type': 'redirect', 'url': r1_url, 'status_code': r1_s, 'headers': r1_h},
            {'type': 'redirect', 'url': r2_url, 'status_code': r2_s, 'headers': r2_h},
            {'type': 'final', 'url': f_url, 'status_code': f_s, 'headers': f_h}
        ])

        page.http_entry_row = MagicMock(spec=MockAdw.EntryRow); page.http_entry_row.get_text.return_value = r1_url
        page.http_user_agent_row = MagicMock(spec=MockAdw.ComboRow); page.http_user_agent_row.get_selected.return_value = 0
        page.http_host_header_row = MagicMock(spec=MockAdw.EntryRow); page.http_host_header_row.get_text.return_value = ""
        page.http_pragma_switch_row = MagicMock(spec=MockAdw.SwitchRow); page.http_pragma_switch_row.get_active.return_value = False
        page.error_banner = MagicMock(spec=MockAdw.Banner); page.error_banner.set_revealed = MagicMock(); page.error_banner.set_title = MagicMock()
        page.http_entry_row.remove_css_class = MagicMock()

        page._on_entry_row_activated(page.http_entry_row)
        page._update_column_view_model.assert_called_once()
        processed_items = page._update_column_view_model.call_args[0][0]

        expected_len = (1+len(r1_h)+1) + (1+len(r2_h)+1) + (1+len(f_h))
        self.assertEqual(len(processed_items), expected_len)

        self.assertTrue(processed_items[0].is_special_row); self.assertIn(r1_url, processed_items[0].key)
        self.assertTrue(processed_items[1+len(r1_h)].is_special_row)
        self.assertTrue(processed_items[2+len(r1_h)].is_special_row); self.assertIn(r2_url, processed_items[2+len(r1_h)].key)
        self.assertTrue(processed_items[2+len(r1_h)+1+len(r2_h)].is_special_row)
        self.assertTrue(processed_items[2+len(r1_h)+2+len(r2_h)].is_special_row); self.assertIn(f_url, processed_items[2+len(r1_h)+2+len(r2_h)].key)

    def test_styling_of_special_rows(self):
        page = self.page
        # HeaderItem is part of http_page_module, which should be loaded globally in this test file
        self.assertIsNotNone(http_page_module.HeaderItem, "HeaderItem class not loaded for test via http_page_module")

        mock_list_item = MagicMock(spec=MockGtk.ListItem)
        mock_label = MagicMock(spec=MockGtk.Label)
        mock_list_item.get_child.return_value = mock_label

        factory_capturer = MagicMock(spec=MockGtk.SignalListItemFactory)
        captured_callbacks = {}
        # Simplified connect capture
        def capture_connect(signal_name, func_to_capture, *_): # Added *_ to accept potential extra args
            captured_callbacks[signal_name] = func_to_capture
        factory_capturer.connect = MagicMock(side_effect=capture_connect)

        original_factory_new = MockGtk.SignalListItemFactory.new
        MockGtk.SignalListItemFactory.new = MagicMock(return_value=factory_capturer)

        tested_factory = page._create_factory(attr_name="key")

        self.assertIn("setup", captured_callbacks)
        self.assertIn("bind", captured_callbacks)
        setup_func = captured_callbacks["setup"]
        bind_func = captured_callbacks["bind"]

        MockGtk.SignalListItemFactory.new = original_factory_new # Restore

        # Simulate Gtk's behavior
        setup_func(tested_factory, mock_list_item) # Pass factory as first arg for consistency with Gtk callbacks

        # Test Case 1: Special row
        special_item_key = "URL: http://example.com"
        # Use http_page_module.HeaderItem directly
        special_item = http_page_module.HeaderItem(key=special_item_key, value="Status: 200", is_special_row=True)
        mock_list_item.get_item.return_value = special_item

        original_markup_escape = MockGLib.markup_escape_text
        MockGLib.markup_escape_text = MagicMock(side_effect=lambda text: text)

        bind_func(tested_factory, mock_list_item) # Pass factory as first arg

        expected_markup = f"<b>{special_item_key}</b>"
        mock_label.set_markup.assert_called_once_with(expected_markup)
        mock_label.set_text.assert_not_called()
        mock_label.reset_mock()

        # Test Case 2: Regular row
        regular_item_key = "Host"
        regular_item = http_page_module.HeaderItem(key=regular_item_key, value="example.com", is_special_row=False)
        mock_list_item.get_item.return_value = regular_item

        bind_func(tested_factory, mock_list_item) # Pass factory as first arg

        mock_label.set_text.assert_called_once_with(regular_item_key)
        mock_label.set_markup.assert_not_called()

        MockGLib.markup_escape_text = original_markup_escape


if __name__ == '__main__':
    if HttpPage_class:
        unittest.main()
    else:
        print("Skipping unittest.main() as HttpPage_class was not loaded.")
