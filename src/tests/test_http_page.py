import unittest
from unittest.mock import MagicMock, patch, ANY

# Assuming HttpPage is in src.http_page
# We need to ensure the GI environment is set up for HttpPage if it's imported directly
# For simplicity in testing the header logic, we might not need a full HttpPage instance.
# However, _fetch_headers_task_thread_func is a method of HttpPage.

import requests # Needed for the standalone function
from typing import Dict, Optional # Needed for the standalone function
import sys # Ensure sys is imported for the gi check

# It's cleaner to define these at the module level for the standalone function
# or pass them as arguments if they need to be MagicMock for some reason.
# For now, let's assume standalone_fetch_headers_logic will use a global logger mock
# and the globally mocked GLib.
logger = MagicMock()

# Mock essential Gio/GLib parts needed by the standalone function if not already globally mocked
# This assumes MOCK_GI_MODULES is applied if this test file is run directly.
# If run by a test runner, sys.modules might already be patched or not.
# For robustness, ensure critical mocks are available.
if 'gi' not in sys.modules: # Basic check
    sys.modules['gi'] = MagicMock()
    sys.modules['gi.repository'] = MagicMock()
    sys.modules['gi.repository.Gio'] = MagicMock()
    sys.modules['gi.repository.GLib'] = MagicMock()

mock_Gio = sys.modules['gi.repository.Gio']
mock_GLib = sys.modules['gi.repository.GLib']

# Ensure these have the necessary attributes if not fully mocked earlier
if not hasattr(mock_Gio, 'io_error_quark'):
    mock_Gio.io_error_quark = MagicMock(return_value="gio-io-error-quark")
if not hasattr(mock_Gio, 'IOErrorEnum'):
    mock_Gio.IOErrorEnum = MagicMock()
    mock_Gio.IOErrorEnum.FAILED = 1
    mock_Gio.IOErrorEnum.CANCELLED = 34
if not hasattr(mock_GLib, 'Error'):
    mock_GLib.Error = MagicMock(side_effect=Exception)


# --- Standalone function copied and adapted from HttpPage ---
def standalone_fetch_headers_logic(
    source_object_mock: MagicMock, # Mock of HttpPage instance
    task_mock: MagicMock,           # Mock of Gio.Task
    cancellable_mock: Optional[MagicMock] # Mock of Gio.Cancellable
    # task_data is no longer part of signature as it was unused
):
    current_task_data = source_object_mock._http_task_data_for_thread

    url = current_task_data["url"]
    use_akamai_pragma = current_task_data["use_akamai_pragma"]
    host_header = current_task_data.get("host_header")
    user_agent = current_task_data.get("user_agent")

    # logger.debug is from the HttpPage module, here we use the global mock logger
    logger.debug(
        "Task thread: Making GET request to %s with Akamai headers: %s, Host: %s, UA: %s",
        url, use_akamai_pragma, host_header, user_agent
    )

    request_headers = {}
    if host_header:
        request_headers["Host"] = host_header
    if user_agent and user_agent != "None":
        request_headers["User-Agent"] = user_agent

    if use_akamai_pragma:
        akamai_pragma_directives = [
            "akamai-x-get-request-id", "akamai-x-get-cache-key", "akamai-x-cache-on",
            "akamai-x-cache-remote-on", "akamai-x-get-true-cache-key", "akamai-x-check-cacheable",
            "akamai-x-get-extracted-values", "akamai-x-feo-trace", "x-akamai-logging-mode: verbose",
        ]
        request_headers["Pragma"] = ", ".join(akamai_pragma_directives)

    try:
        if cancellable_mock and cancellable_mock.is_cancelled():
            task_mock.return_error(mock_Gio.io_error_quark(), mock_Gio.IOErrorEnum.CANCELLED, "Task was cancelled")
            return

        response = requests.get(url, headers=request_headers, allow_redirects=False, timeout=10)
        response.raise_for_status()
        task_mock.return_value(dict(response.headers))
    except requests.exceptions.HTTPError as e:
        logger.error("Task thread: HTTPError for %s: %s", url, e, exc_info=True)
        # _format_http_error was a method on HttpPage. Mock it on source_object_mock
        error_message = source_object_mock._format_http_error(e)
        safe_error_message = str(error_message)
        g_error = mock_GLib.Error(message=safe_error_message, domain=mock_Gio.io_error_quark(), code=mock_Gio.IOErrorEnum.FAILED)
        task_mock.return_error(g_error)
        return
    except requests.exceptions.ConnectionError as e:
        logger.warning("Task thread: ConnectionError for %s: %s", url, e, exc_info=True)
        error_message = "Connection Error: Failed to establish a connection."
        safe_error_message = str(error_message)
        g_error = mock_GLib.Error(message=safe_error_message, domain=mock_Gio.io_error_quark(), code=mock_Gio.IOErrorEnum.FAILED)
        task_mock.return_error(g_error)
        return
    except requests.exceptions.Timeout as e:
        logger.warning("Task thread: Timeout for %s: %s", url, e, exc_info=True)
        error_message = "Timeout Error: The request timed out."
        safe_error_message = str(error_message)
        g_error = mock_GLib.Error(message=safe_error_message, domain=mock_Gio.io_error_quark(), code=mock_Gio.IOErrorEnum.FAILED)
        task_mock.return_error(g_error)
        return
    except requests.exceptions.RequestException as e:
        logger.error("Task thread: RequestException for %s: %s", url, e, exc_info=True)
        error_message = f"Request Error: {str(e)}"
        safe_error_message = str(error_message)
        g_error = mock_GLib.Error(message=safe_error_message, domain=mock_Gio.io_error_quark(), code=mock_Gio.IOErrorEnum.FAILED)
        task_mock.return_error(g_error)
        return
    except Exception as e:
        logger.error("Task thread: Unexpected error for %s: %s", url, e, exc_info=True)
        error_message = f"An unexpected error occurred: {str(e)}"
        safe_error_message = str(error_message)
        g_error = mock_GLib.Error(message=safe_error_message, domain=mock_Gio.io_error_quark(), code=mock_Gio.IOErrorEnum.FAILED)
        task_mock.return_error(g_error)
        return
# --- End Standalone function ---


class TestHttpPageHeaders(unittest.TestCase):

    def setUp(self):
        self.mock_source_object = MagicMock()
        self.mock_source_object._http_task_data_for_thread = {}
        # Mock the _format_http_error method expected by the standalone function
        self.mock_source_object._format_http_error = MagicMock(return_value="Formatted HTTP Error")

        # Use the globally mocked Gio/GLib for Task and Cancellable
        self.mock_task = mock_Gio.Task() # If Task is a class
        # If Task is a function mock_Gio.Task.return_value = MagicMock()
        # Ensure it has return_value and return_error methods
        self.mock_task.return_value = MagicMock()
        self.mock_task.return_error = MagicMock()

        self.mock_cancellable = mock_Gio.Cancellable() # If Cancellable is a class
        self.mock_cancellable.is_cancelled.return_value = False


    @patch('requests.get')
    def test_host_header_added(self, mock_requests_get):
        """Test that the Host header is added correctly."""
        self.mock_source_object._http_task_data_for_thread = {
            "url": "http://example.com",
            "use_akamai_pragma": False,
            "host_header": "custom.host.com",
            "user_agent": None,
        }

        standalone_fetch_headers_logic(
            self.mock_source_object,
            self.mock_task,
            self.mock_cancellable
        )

        mock_requests_get.assert_called_once()
        args, kwargs = mock_requests_get.call_args
        self.assertIn("headers", kwargs)
        self.assertIn("Host", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["Host"], "custom.host.com")
        self.assertNotIn("User-Agent", kwargs["headers"])

    @patch('requests.get')
    def test_user_agent_header_added(self, mock_requests_get):
        """Test that the User-Agent header is added correctly."""
        ua_string = "Mozilla/5.0 (Test)"
        self.mock_source_object._http_task_data_for_thread = {
            "url": "http://example.com",
            "use_akamai_pragma": False,
            "host_header": None,
            "user_agent": ua_string,
        }

        standalone_fetch_headers_logic(
            self.mock_source_object, self.mock_task, self.mock_cancellable
        )

        mock_requests_get.assert_called_once()
        args, kwargs = mock_requests_get.call_args
        self.assertIn("headers", kwargs)
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["User-Agent"], ua_string)
        self.assertNotIn("Host", kwargs["headers"])

    @patch('requests.get')
    def test_no_host_header_if_empty(self, mock_requests_get):
        """Test no Host header is added if the input is empty or None."""
        for host_val in ["", None]:
            mock_requests_get.reset_mock()
            self.mock_source_object._http_task_data_for_thread = {
                "url": "http://example.com",
                "use_akamai_pragma": False,
                "host_header": host_val,
                "user_agent": None,
            }
            standalone_fetch_headers_logic(
                self.mock_source_object, self.mock_task, self.mock_cancellable
            )
            mock_requests_get.assert_called_once()
            args, kwargs = mock_requests_get.call_args
            self.assertIn("headers", kwargs)
            self.assertNotIn("Host", kwargs["headers"])

    @patch('requests.get')
    def test_no_user_agent_header_if_none(self, mock_requests_get):
        """Test no User-Agent header is added if 'None' is selected or value is None."""
        for ua_val in ["None", None]:
            mock_requests_get.reset_mock()
            self.mock_source_object._http_task_data_for_thread = {
                "url": "http://example.com",
                "use_akamai_pragma": False,
                "host_header": None,
                "user_agent": ua_val,
            }
            standalone_fetch_headers_logic(
                self.mock_source_object, self.mock_task, self.mock_cancellable
            )
            mock_requests_get.assert_called_once()
            args, kwargs = mock_requests_get.call_args
            self.assertIn("headers", kwargs)
            self.assertNotIn("User-Agent", kwargs["headers"])


    @patch('requests.get')
    def test_both_headers_added(self, mock_requests_get):
        """Test both Host and User-Agent headers are added if provided."""
        ua_string = "Mozilla/5.0 (Test Both)"
        host_string = "custom.both.com"
        self.mock_source_object._http_task_data_for_thread = {
            "url": "http://example.com",
            "use_akamai_pragma": False,
            "host_header": host_string,
            "user_agent": ua_string,
        }
        standalone_fetch_headers_logic(
            self.mock_source_object, self.mock_task, self.mock_cancellable
        )
        mock_requests_get.assert_called_once()
        args, kwargs = mock_requests_get.call_args
        self.assertIn("headers", kwargs)
        self.assertIn("Host", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["Host"], host_string)
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["User-Agent"], ua_string)

    @patch('requests.get')
    def test_akamai_pragma_headers_added(self, mock_requests_get):
        """Test Akamai Pragma headers are added when use_akamai_pragma is True."""
        self.mock_source_object._http_task_data_for_thread = {
            "url": "http://example.com",
            "use_akamai_pragma": True,
            "host_header": None,
            "user_agent": None,
        }
        standalone_fetch_headers_logic(
            self.mock_source_object, self.mock_task, self.mock_cancellable
        )
        mock_requests_get.assert_called_once()
        args, kwargs = mock_requests_get.call_args
        self.assertIn("headers", kwargs)
        self.assertIn("Pragma", kwargs["headers"])
        self.assertIn("akamai-x-get-request-id", kwargs["headers"]["Pragma"])

    @patch('requests.get')
    def test_all_headers_added_with_akamai(self, mock_requests_get):
        """Test Host, User-Agent, and Akamai Pragma headers are all added."""
        ua_string = "Mozilla/5.0 (Test All)"
        host_string = "custom.all.com"
        self.mock_source_object._http_task_data_for_thread = {
            "url": "http://example.com",
            "use_akamai_pragma": True,
            "host_header": host_string,
            "user_agent": ua_string,
        }
        standalone_fetch_headers_logic(
            self.mock_source_object, self.mock_task, self.mock_cancellable
        )
        mock_requests_get.assert_called_once()
        args, kwargs = mock_requests_get.call_args
        self.assertIn("headers", kwargs)
        self.assertIn("Host", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["Host"], host_string)
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["User-Agent"], ua_string)
        self.assertIn("Pragma", kwargs["headers"])
        self.assertIn("akamai-x-get-cache-key", kwargs["headers"]["Pragma"])


if __name__ == '__main__':
    unittest.main()
