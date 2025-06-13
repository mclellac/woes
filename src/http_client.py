"""Module for fetching HTTP headers and processing responses."""
import logging
from typing import Optional, Tuple, Dict, List, Any

import requests
import requests.utils # For urlparse, urlunparse
try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None # HttpFetcher will check this
    logging.warning("dnspython library not found. Custom DNS functionality will be disabled for HttpFetcher.")

try:
    import urllib3.exceptions as urllib3_exceptions
except ImportError:
    class _DummyUrllib3Exception(Exception):
        pass
    urllib3_exceptions = type(
        "urllib3_exceptions",
        (),
        {
            "MaxRetryError": _DummyUrllib3Exception,
            "NewConnectionError": _DummyUrllib3Exception,
        },
    )
    logging.warning("Could not import urllib3.exceptions. Connection refused detection might be limited.")

from gi.repository import Gio # For Cancellable

from .custom_dns_adapter import CustomDNSAdapter

logger = logging.getLogger(__name__)

# Custom Exceptions
class HttpClientError(Exception):
    """Base exception for HttpFetcher errors."""
    pass

class HttpRequestTimeoutError(HttpClientError):
    """Exception for request timeouts."""
    pass

class HttpConnectionError(HttpClientError):
    """Exception for connection errors."""
    pass

class HttpProcessingError(HttpClientError):
    """Exception for errors during HTTP response processing (e.g., bad status).

    :ivar status_code: The HTTP status code that caused the error, if available.
    :vartype status_code: Optional[int]
    :ivar url: The URL associated with the error, if available.
    :vartype url: Optional[str]
    """

    def __init__(self, message, status_code: Optional[int] = None, url: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url

class HttpGenericRequestError(HttpClientError):
    """Exception for other requests-related errors."""
    pass

class HttpFetcher:
    """Encapsulates logic for making HTTP requests and processing responses."""

    def __init__(self,
                 url: str,
                 use_akamai_pragma: bool = False,
                 host_header: Optional[str] = None,
                 user_agent: Optional[str] = None,
                 custom_dns_server: Optional[str] = None,
                 cancellable: Optional[Gio.Cancellable] = None):
        """Initialize HttpFetcher.

        :param url: The URL to fetch.
        :type url: str
        :param use_akamai_pragma: Whether to include Akamai Pragma headers.
        :type use_akamai_pragma: bool
        :param host_header: Optional custom Host header.
        :type host_header: Optional[str]
        :param user_agent: Optional custom User-Agent string.
        :type user_agent: Optional[str]
        :param custom_dns_server: Optional custom DNS server IP.
        :type custom_dns_server: Optional[str]
        :param cancellable: Optional Gio.Cancellable object for cancellation.
        :type cancellable: Optional[Gio.Cancellable]
        """
        self.url = url
        self.use_akamai_pragma = use_akamai_pragma
        self.host_header = host_header
        self.user_agent = user_agent
        self.custom_dns_server = custom_dns_server
        self.cancellable = cancellable
        self.session = requests.Session()

    def _prepare_request_headers(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Prepare initial request-specific headers and session-wide headers.

        Moved from HttpPage.

        :return: A tuple containing two dictionaries:
                 - initial_request_specific_headers: Headers for the first request only.
                 - session_headers: Headers to apply to the requests.Session.
        :rtype: Tuple[Dict[str, str], Dict[str, str]]
        """
        initial_request_specific_headers: Dict[str, str] = {}
        session_headers: Dict[str, str] = {}

        if self.host_header:
            initial_request_specific_headers["Host"] = self.host_header
            logger.info("HttpFetcher: Using user-provided Host header: '%s'", self.host_header)

        if self.user_agent:
            session_headers["User-Agent"] = self.user_agent
            logger.info("HttpFetcher: Using custom User-Agent: '%s'", self.user_agent)
        else:
            logger.info("HttpFetcher: No custom User-Agent; `requests` default will be used.")

        if self.use_akamai_pragma:
            directives = [
                "akamai-x-get-request-id", "akamai-x-get-cache-key", "akamai-x-cache-on",
                "akamai-x-cache-remote-on", "akamai-x-get-true-cache-key",
                "akamai-x-check-cacheable", "akamai-x-get-extracted-values",
                "akamai-x-feo-trace", "x-akamai-logging-mode: verbose",
            ]
            session_headers["Pragma"] = ", ".join(directives)
            logger.info("HttpFetcher: Akamai Pragma headers included.")
        else:
            logger.info("HttpFetcher: Akamai Pragma headers not included.")
        return initial_request_specific_headers, session_headers

    def _execute_http_request(self, initial_request_headers: Dict[str, str]) -> requests.Response:
        """Execute the HTTP GET request using the configured session.

        Moved from HttpPage.

        :param initial_request_headers: Headers to send with the initial request.
        :type initial_request_headers: Dict[str, str]
        :raises HttpRequestTimeoutError: If the request times out.
        :raises HttpConnectionError: If a connection error occurs.
        :raises HttpGenericRequestError: For other request-related errors.
        :raises HttpClientError: If cancelled.
        :return: The :class:`requests.Response` object.
        :rtype: requests.Response
        """
        logger.info("HttpFetcher: Executing HTTP GET to %s.", self.url)
        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError("Request cancelled before sending.")

        try:
            response = self.session.get(
                self.url,
                headers=(initial_request_headers if initial_request_headers else None),
                allow_redirects=True,
                timeout=5, # Standard timeout
            )
            return response
        except requests.exceptions.Timeout as e:
            logger.warning("HttpFetcher: Timeout for '%s': %s", self.url, e)
            raise HttpRequestTimeoutError(f"Request timed out for {self.url}.") from e
        except requests.exceptions.ConnectionError as e:
            logger.warning("HttpFetcher: ConnectionError for '%s': %s", self.url, e)
            custom_msg = self._get_detailed_connection_error_message(e, self.url)
            msg = custom_msg or f"Network connection error for {self.url}."
            raise HttpConnectionError(msg) from e
        except requests.exceptions.RequestException as e:
            logger.warning("HttpFetcher: RequestException for '%s': %s", self.url, e)
            raise HttpGenericRequestError(f"Request failed for {self.url}: {e}") from e


    def _process_http_response(self, response: requests.Response) -> List[Dict[str, Any]]:
        """Process the HTTP response, including redirects.

        Moved from HttpPage.

        :param response: The final :class:`requests.Response` object.
        :type response: requests.Response
        :raises HttpProcessingError: If an HTTPError (4xx/5xx) occurs.
        :return: A list of dictionaries, where each dictionary represents a
                 response (redirect or final).
        :rtype: List[Dict[str, Any]]
        """
        all_responses_data: List[Dict[str, Any]] = []
        for hist_resp in response.history:
            hist_data: Dict[str, Any] = {
                "type": "redirect",
                "url": str(hist_resp.url),
                "status_code": hist_resp.status_code,
                "headers": {str(k): str(v) for k, v in dict(hist_resp.headers).items()},
            }
            all_responses_data.append(hist_data)
        try:
            response.raise_for_status()  # Raises HTTPError for 4xx/5xx
            final_data_type = "final"
        except requests.exceptions.HTTPError as http_err:
            error_url = str(http_err.request.url) if http_err.request else self.url
            logger.warning("HttpFetcher: HTTPError for URL '%s' (final URL: '%s'): %s", self.url, error_url, http_err)
            error_message = self._format_http_error(http_err)
            raise HttpProcessingError(error_message, status_code=http_err.response.status_code, url=error_url) from http_err

        final_data: Dict[str, Any] = {
            "type": final_data_type,
            "url": str(response.url),
            "status_code": response.status_code,
            "headers": {str(k): str(v) for k, v in dict(response.headers).items()},
        }
        all_responses_data.append(final_data)
        return all_responses_data

    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:
        """Attempt to find a 'Connection Refused' error within a chain of exceptions.

        Moved from HttpPage.

        :param exc: The initial exception object.
        :type exc: Exception
        :param url: The URL for which the connection was attempted.
        :type url: str
        :return: A detailed error message if 'Connection Refused' is identified,
                 otherwise ``None``.
        :rtype: Optional[str]
        """
        current_exc: Optional[BaseException] = exc
        found_connection_refused = False
        max_depth = 5
        for _depth in range(max_depth):
            if current_exc is None: break
            exc_str = str(current_exc).lower()
            if isinstance(current_exc, ConnectionRefusedError): found_connection_refused = True; break
            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                if "connection refused" in exc_str or "errno 111" in exc_str: found_connection_refused = True; break
                if hasattr(current_exc, "original_error"):
                    original_error = getattr(current_exc, "original_error")
                    if isinstance(original_error, ConnectionRefusedError) or \
                       (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111):
                        found_connection_refused = True; break
            if isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                if hasattr(current_exc, "reason") and isinstance(current_exc.reason, urllib3_exceptions.NewConnectionError):
                    reason_exc_str = str(current_exc.reason).lower()
                    if "connection refused" in reason_exc_str or "errno 111" in reason_exc_str: found_connection_refused = True; break
                    if hasattr(current_exc.reason, "original_error"):
                        original_error = getattr(current_exc.reason, "original_error")
                        if isinstance(original_error, ConnectionRefusedError) or \
                           (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111):
                            found_connection_refused = True; break
            if any("connection refused" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "connection refused" in exc_str: found_connection_refused = True
            if any("errno 111" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "errno 111" in exc_str: found_connection_refused = True
            if found_connection_refused: break
            next_exc: Optional[BaseException] = None
            if hasattr(current_exc, "__cause__") and current_exc.__cause__ is not None: next_exc = current_exc.__cause__
            elif hasattr(current_exc, "__context__") and current_exc.__context__ is not None and \
                 not getattr(current_exc, "__suppress_context__", False): next_exc = current_exc.__context__
            if current_exc is next_exc: break
            current_exc = next_exc
        if found_connection_refused:
            logger.info("HttpFetcher: Connection refused condition identified for URL: %s", url)
            parsed_url_scheme = requests.utils.urlparse(url).scheme
            if parsed_url_scheme == "https": return "Connection Refused: Server at HTTPS URL actively refused. Try 'http://'?"
            if parsed_url_scheme == "http": return "Connection Refused: Server at HTTP URL actively refused. Try 'https://' or check if server is down."
            return "Connection Refused: The server at the specified URL actively refused the connection."
        return None

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        """Format an HTTPError into a user-friendly string.

        Moved from HttpPage.

        :param e: The :class:`requests.exceptions.HTTPError` object.
        :type e: requests.exceptions.HTTPError
        :return: A user-friendly error message string.
        :rtype: str
        """
        status_code = e.response.status_code
        reason = e.response.reason if e.response.reason else "Unknown Error"
        url = e.request.url if e.request else "N/A"
        if status_code == 403: return f"403 Forbidden: Access to {url} denied."
        if status_code == 404: return f"404 Not Found: Resource at {url} not found."
        if status_code == 500: return f"500 Internal Server Error for {url}."
        return f"HTTP Error {status_code} ({reason}) for URL: {url}."

    def fetch_headers(self) -> List[Dict[str, Any]]:
        """Main method to fetch and process HTTP headers.

        :raises HttpRequestTimeoutError: If the request times out.
        :raises HttpConnectionError: If a connection error occurs.
        :raises HttpProcessingError: If an HTTPError (4xx/5xx) occurs.
        :raises HttpGenericRequestError: For other request-related errors.
        :raises HttpClientError: If cancelled or other client-side issue.
        :return: A list of dictionaries, where each dictionary represents a
                 response (redirect or final).
        :rtype: List[Dict[str, Any]]
        """
        initial_request_specific_headers, session_headers = self._prepare_request_headers()

        adapter_sni_hint: Optional[str] = None
        if self.host_header: # If a host header is provided, it might be for an IP-based URL
            parsed_url = requests.utils.urlparse(self.url)
            # The try...except block for IP address check and setting adapter_sni_hint has been removed.
            # adapter_sni_hint will retain its initial None value if self.host_header is set,
            # or remain None if self.host_header was not set.


        effective_custom_dns_server = self.custom_dns_server if dns else None
        if self.custom_dns_server and not dns:
            logger.warning(
                "HttpFetcher: Custom DNS ('%s') configured, but dnspython missing for adapter.", self.custom_dns_server
            )

        # Instantiate CustomDNSAdapter
        # default_sni is used if the URL is an IP address, to set SNI for HTTPS.
        adapter = CustomDNSAdapter(custom_dns_server=effective_custom_dns_server, default_sni=adapter_sni_hint)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        logger.info(
            "HttpFetcher: CustomDNSAdapter mounted (DNS: '%s', SNI hint for IP URL: '%s').",
            effective_custom_dns_server,
            adapter_sni_hint,
        )

        if session_headers:
            self.session.headers.update(session_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError("Request cancelled before execution.")

        response = self._execute_http_request(initial_request_specific_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            # Check cancellation again after request returns, before processing
            raise HttpClientError("Request cancelled during/after execution, before processing.")

        return self._process_http_response(response)
