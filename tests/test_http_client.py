import unittest
from unittest.mock import patch, MagicMock

from src.http_client import (
    HttpFetcher,
    HttpRequestTimeoutError,
    HttpConnectionError,
    HttpGenericRequestError,
    HttpProcessingError,
    HttpClientError,
)
import requests.exceptions

class TestHttpFetcher(unittest.TestCase):

    def setUp(self):
        # Default URL for tests, can be overridden
        self.url = "http://example.com"
        self.fetcher = HttpFetcher(url=self.url)

    @patch('requests.Session.get')
    def test_fetch_headers_timeout(self, mock_session_get: MagicMock):
        """Test that HttpRequestTimeoutError is raised for requests.exceptions.Timeout."""
        mock_session_get.side_effect = requests.exceptions.Timeout("Test Timeout")
        with self.assertRaises(HttpRequestTimeoutError):
            self.fetcher.fetch_headers()

    @patch('requests.Session.get')
    def test_fetch_headers_connection_error(self, mock_session_get: MagicMock):
        """Test that HttpConnectionError is raised for requests.exceptions.ConnectionError."""
        # Mock the _get_detailed_connection_error_message to return None, so we test the default path
        with patch.object(self.fetcher, '_get_detailed_connection_error_message', return_value=None):
            mock_session_get.side_effect = requests.exceptions.ConnectionError("Test Connection Error")
            with self.assertRaises(HttpConnectionError):
                self.fetcher.fetch_headers()

    @patch('requests.Session.get')
    def test_fetch_headers_generic_request_exception(self, mock_session_get: MagicMock):
        """Test that HttpGenericRequestError is raised for requests.exceptions.RequestException."""
        mock_session_get.side_effect = requests.exceptions.RequestException("Test Generic Request Exception")
        with self.assertRaises(HttpGenericRequestError):
            self.fetcher.fetch_headers()

    @patch('requests.Session.get')
    def test_fetch_headers_http_error_processing(self, mock_session_get: MagicMock):
        """Test that HttpProcessingError is raised for requests.exceptions.HTTPError."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 404
        mock_response.reason = "Not Found"
        mock_response.url = self.url
        mock_response.history = []
        # Create a mock request object for the response's request attribute
        mock_request = MagicMock()
        mock_request.url = self.url
        mock_response.request = mock_request

        # Configure raise_for_status to raise HTTPError
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_response)

        mock_session_get.return_value = mock_response

        with self.assertRaises(HttpProcessingError) as cm:
            self.fetcher.fetch_headers()

        # Optionally, check details of the raised HttpProcessingError
        self.assertEqual(cm.exception.status_code, 404)
        self.assertTrue("404 Not Found" in str(cm.exception))

    @patch('requests.Session.get')
    def test_fetch_headers_successful_request(self, mock_session_get: MagicMock):
        """Test a successful header fetch (no client-side exceptions)."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.url = self.url
        mock_response.headers = {'Content-Type': 'text/html'}
        mock_response.history = [] # No redirects
        mock_response.raise_for_status.return_value = None # Does not raise HTTPError

        mock_session_get.return_value = mock_response

        try:
            results = self.fetcher.fetch_headers()
            self.assertIsInstance(results, list)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['status_code'], 200)
            self.assertEqual(results[0]['url'], self.url)
            self.assertEqual(results[0]['headers']['Content-Type'], 'text/html')
        except HttpClientError as e:
            self.fail(f"fetch_headers() raised HttpClientError unexpectedly: {e}")

    @patch('requests.Session.get')
    def test_fetch_headers_connection_error_with_detail(self, mock_session_get: MagicMock):
        """Test HttpConnectionError with a detailed message from _get_detailed_connection_error_message."""
        detailed_msg = "Connection Refused: Special detail."
        with patch.object(self.fetcher, '_get_detailed_connection_error_message', return_value=detailed_msg):
            mock_session_get.side_effect = requests.exceptions.ConnectionError("Underlying Connection Error")
            with self.assertRaises(HttpConnectionError) as cm:
                self.fetcher.fetch_headers()
            self.assertEqual(str(cm.exception), detailed_msg)
            self.assertIsInstance(cm.exception.__cause__, requests.exceptions.ConnectionError)

    def test_default_user_agent_used_when_none(self):
        """Test that a default browser User-Agent is set when no user_agent is provided."""
        from src.constants import get_default_user_agent
        fetcher = HttpFetcher(url="http://example.com", user_agent=None)
        _, session_headers = fetcher._prepare_request_headers()
        self.assertIn("User-Agent", session_headers)
        self.assertEqual(session_headers["User-Agent"], get_default_user_agent())

    def test_custom_user_agent_used_when_provided(self):
        """Test that custom user_agent is set in session headers when provided."""
        custom_ua = "CustomUA/1.0"
        fetcher = HttpFetcher(url="http://example.com", user_agent=custom_ua)
        _, session_headers = fetcher._prepare_request_headers()
        self.assertEqual(session_headers.get("User-Agent"), custom_ua)

    @patch('src.http_client.CustomDNSAdapter')
    @patch('requests.Session.get')
    def test_host_header_sets_adapter_sni_hint(self, mock_session_get: MagicMock, mock_adapter_cls: MagicMock):
        """Test that providing a host header sets the SNI hint on CustomDNSAdapter."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.url = "http://1.2.3.4"
        mock_response.headers = {}
        mock_response.history = []
        mock_session_get.return_value = mock_response

        fetcher = HttpFetcher(url="http://1.2.3.4", host_header="example.com")
        fetcher.fetch_headers()
        mock_adapter_cls.assert_called_with(custom_dns_server=None, default_sni="example.com")


if __name__ == '__main__':
    unittest.main()

