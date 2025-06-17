"""Module for fetching HTTP headers and processing responses."""

import logging
from typing import Optional, Any, Dict, List, Tuple # Retaining List, Dict for clarity with older type hint styles if any linger elsewhere
                                         # but will use list and dict in annotations for Py3.9+ style.
import requests
import requests.utils  # For urlparse, urlunparse

try:
    import dns.resolver
    import dns.exception
    DNSPYTHON_AVAILABLE = True
except ImportError:
    dns = None # Explicitly None if not importable
    DNSPYTHON_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "dnspython library not found. Custom DNS functionality will be disabled for HttpFetcher."
    )

try:
    # For more specific connection error checking
    import urllib3.exceptions as urllib3_exceptions
except ImportError:
    # Create a dummy class to stand in for urllib3.exceptions if not available.
    # This allows the isinstance checks to proceed without runtime errors.
    class _DummyUrllib3Exception(Exception):
        pass
    urllib3_exceptions = type( # type: ignore [misc, assignment]
        "urllib3_exceptions",
        (),
        {
            "MaxRetryError": _DummyUrllib3Exception,
            "NewConnectionError": _DummyUrllib3Exception,
            # Add other specific exceptions if they are checked explicitly below
        },
    )
    logging.warning(
        "Could not import urllib3.exceptions. Detailed connection refused detection might be limited."
    )

from gi.repository import Gio, GLib # Added GLib for GSettings error handling
from .constants import APP_ID

from .custom_dns_adapter import CustomDNSAdapter

logger = logging.getLogger(__name__)


class HttpClientError(Exception):
    """Base exception for HttpFetcher errors."""

    pass


class HttpRequestTimeoutError(HttpClientError):
    """Exception raised when an HTTP request times out."""

    pass


class HttpConnectionError(HttpClientError):
    """Exception raised for errors during connection establishment."""

    pass


class HttpProcessingError(HttpClientError):
    """
    Exception for errors occurring during HTTP response processing.

    This typically indicates an issue with the response status code (e.g., 4xx or 5xx errors),
    meaning the server responded but indicated an error.

    :ivar status_code: The HTTP status code that caused the error, if available.
    :vartype status_code: Optional[int]
    :ivar url: The URL associated with the error, if available.
    :vartype url: Optional[str]
    """

    def __init__(self, message: str, status_code: Optional[int] = None, url: Optional[str] = None):
        """
        Initialize the HttpProcessingError.

        :param message: The error message.
        :param status_code: The HTTP status code, if applicable.
        :param url: The URL associated with the error, if applicable.
        """
        super().__init__(message)
        self.status_code: Optional[int] = status_code
        self.url: Optional[str] = url


class HttpGenericRequestError(HttpClientError):
    """Exception for other `requests` library-related errors not covered by more specific exceptions."""

    pass


class HttpFetcher:
    """
    Encapsulates logic for making HTTP GET requests and processing responses.

    This class handles setting custom headers (User-Agent, Host, Akamai Pragma),
    integrates with :class:`.CustomDNSAdapter` for custom DNS resolution and SNI,
    and provides structured error handling by wrapping `requests` exceptions
    into more specific custom exceptions. It also allows for request cancellation
    via a :class:`Gio.Cancellable` object.
    """

    def __init__(
        self,
        url: str,
        use_akamai_pragma: bool = False,
        host_header: Optional[str] = None,
        user_agent: Optional[str] = None,
        custom_dns_server: Optional[str] = None,
        cancellable: Optional[Gio.Cancellable] = None,
    ):
        """
        Initialize the HttpFetcher.

        :param url: The URL to fetch.
        :param use_akamai_pragma: If ``True``, Akamai Pragma headers for debugging are included.
                                  Defaults to ``False``.
        :param host_header: Optional custom 'Host' header value. If provided, this is used
                            for the HTTP 'Host' header and as a hint for SNI if the URL
                            is an IP address. Defaults to ``None``.
        :param user_agent: Optional custom 'User-Agent' string. If ``None``, the default
                           `requests` library User-Agent is used. Defaults to ``None``.
        :param custom_dns_server: Optional IP address of a custom DNS server.
                                  If ``None`` or `dnspython` is not available, system DNS is used.
                                  Defaults to ``None``.
        :param cancellable: An optional :class:`Gio.Cancellable` object to allow
                            for cancellation of the HTTP request. Defaults to ``None``.
        """
        self.url: str = url
        self.use_akamai_pragma: bool = use_akamai_pragma
        self.host_header: Optional[str] = host_header
        self.user_agent: Optional[str] = user_agent
        self.custom_dns_server: Optional[str] = custom_dns_server
        self.cancellable: Optional[Gio.Cancellable] = cancellable
        self.session: requests.Session = requests.Session()
        self.settings: Gio.Settings = Gio.Settings.new(APP_ID)

        try:
            self.request_timeout_seconds: int = self.settings.get_int("http-request-timeout")
            if self.request_timeout_seconds <= 0:
                logger.warning(
                    "HTTP request timeout from GSettings is invalid (<=0). Using default: 10s."
                )
                self.request_timeout_seconds = 10 # Default timeout
        except GLib.Error as e:
            logger.warning(
                "Could not read 'http-request-timeout' from GSettings (error: %s). Using default: 10s.", e
            )
            self.request_timeout_seconds = 10 # Default timeout

        logger.info("HttpFetcher initialized with request timeout: %d seconds.", self.request_timeout_seconds)

    def _prepare_request_headers(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        Prepare initial request-specific headers and session-wide headers.

        Session headers include User-Agent and potentially Akamai Pragma headers.
        The 'Host' header, if specified via `self.host_header`, is returned as an
        initial request-specific header. This is because `requests` handles the
        'Host' header specially, and it's often best to let it manage it or provide it
        per-request if overriding the URL's hostname.

        :return: A tuple:
                 - ``initial_request_specific_headers``: Headers for the first request (e.g., 'Host').
                 - ``session_headers``: Headers applied to the session for all subsequent requests.
        """
        initial_request_specific_headers: Dict[str, str] = {}
        session_headers: Dict[str, str] = {}

        if self.user_agent:
            session_headers["User-Agent"] = self.user_agent
            logger.info("HttpFetcher: Using custom User-Agent: '%s'", self.user_agent)
        else:
            # Let requests library use its default User-Agent
            logger.info("HttpFetcher: No custom User-Agent specified; `requests` default will be used.")

        if self.host_header:
            # The 'Host' header is typically managed by requests based on the URL.
            # If explicitly overridden, it's often for the initial request.
            # CustomDNSAdapter will use this for SNI if the URL is an IP.
            initial_request_specific_headers["Host"] = self.host_header
            logger.info("HttpFetcher: Custom 'Host' header for initial request: '%s'", self.host_header)

        if self.use_akamai_pragma:
            akamai_directives = [
                "akamai-x-get-request-id", "akamai-x-get-cache-key", "akamai-x-cache-on",
                "akamai-x-cache-remote-on", "akamai-x-get-true-cache-key",
                "akamai-x-check-cacheable", "akamai-x-get-extracted-values",
                "akamai-x-feo-trace", "x-akamai-logging-mode: verbose",
            ]
            session_headers["Pragma"] = ", ".join(akamai_directives)
            logger.info("HttpFetcher: Akamai Pragma headers included in session.")

        return initial_request_specific_headers, session_headers

    def _execute_http_request(self, initial_request_headers: Dict[str, str]) -> requests.Response:
        """
        Execute the HTTP GET request using the configured session and headers.

        :param initial_request_headers: Headers to be sent with this specific request.
                                       These override session-level headers for the same keys.
        :raises HttpRequestTimeoutError: If the request times out.
        :raises HttpConnectionError: If a connection error occurs (e.g., DNS failure, refused connection).
        :raises HttpGenericRequestError: For other `requests` library exceptions.
        :raises HttpClientError: If the operation is cancelled before sending.
        :return: The :class:`requests.Response` object from the GET request.
        """
        logger.info("HttpFetcher: Executing HTTP GET to %s.", self.url)
        if self.cancellable and self.cancellable.is_cancelled():
            logger.info("HttpFetcher: Request to %s cancelled before sending.", self.url)
            raise HttpClientError(f"Request cancelled for {self.url} before sending.")

        try:
            # Merge session headers with initial request headers, initial_request_headers take precedence
            final_headers = self.session.headers.copy()
            final_headers.update(initial_request_headers) # Use the correct parameter name

            response = self.session.get(
                self.url,
                headers=final_headers, # Send combined headers
                allow_redirects=True, # Allow requests to handle redirects
                timeout=self.request_timeout_seconds,
            )
            return response
        except requests.exceptions.Timeout as e:
            logger.warning("HttpFetcher: Timeout for '%s': %s", self.url, e)
            raise HttpRequestTimeoutError(f"Request timed out for {self.url}.") from e
        except requests.exceptions.ConnectionError as e:
            logger.warning("HttpFetcher: ConnectionError for '%s': %s", self.url, e)
            custom_msg = self._get_detailed_connection_error_message(e, self.url)
            msg = custom_msg or f"Network connection error to {self.url}."
            raise HttpConnectionError(msg) from e
        except requests.exceptions.RequestException as e: # Catch other requests-related errors
            logger.warning("HttpFetcher: RequestException for '%s': %s", self.url, e, exc_info=True)
            raise HttpGenericRequestError(f"Request failed for {self.url}: {e}") from e

    def _process_http_response(self, response: requests.Response) -> List[Dict[str, Any]]:
        """
        Process the HTTP response, including its history (redirects).

        Formats each response (initial and any redirects) into a dictionary
        containing URL, status code, and headers.
        Checks for HTTP errors on the final response and raises :exc:`.HttpProcessingError`
        if a 4xx or 5xx status code is encountered.

        :param response: The final :class:`requests.Response` object after all redirects.
        :raises HttpProcessingError: If the final response has a 4xx or 5xx status code.
        :return: A list of dictionaries, where each dictionary represents a
                 response in the redirect chain (chronological order), with the
                 final response as the last item.
        """
        all_responses_data: List[Dict[str, Any]] = []
        # Process redirect history first
        for hist_resp in response.history:  # hist_resp is a Response object
            hist_data: Dict[str, Any] = {
                "type": "redirect",
                "url": str(hist_resp.url),
                "status_code": hist_resp.status_code,
                "headers": {str(k): str(v) for k, v in hist_resp.headers.items()},
            }
            all_responses_data.append(hist_data)

        # Process the final response
        try:
            response.raise_for_status()  # Check for 4xx/5xx errors on the final response
            final_data_type = "final"
        except requests.exceptions.HTTPError as http_err:
            # Error occurred on the final response URL
            error_url = str(response.url)
            logger.warning("HttpFetcher: HTTPError for URL '%s' (final URL: '%s'): %s", self.url, error_url, http_err)
            error_message = self._format_http_error(http_err) # Uses http_err.response
            raise HttpProcessingError(
                error_message, status_code=response.status_code, url=error_url
            ) from http_err

        final_data: Dict[str, Any] = {
            "type": final_data_type,
            "url": str(response.url),
            "status_code": response.status_code,
            "headers": {str(k): str(v) for k, v in response.headers.items()},
        }
        all_responses_data.append(final_data)
        return all_responses_data

    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:
        """
        Attempt to find a 'Connection Refused' error within a chain of exceptions.

        This helper inspects the exception chain to provide a more specific
        error message if a "Connection Refused" (errno 111) is detected,
        which can be common if a server is not listening or a firewall blocks the connection.

        :param exc: The initial :class:`requests.exceptions.ConnectionError` object.
        :param url: The URL for which the connection was attempted.
        :return: A detailed error message string if 'Connection Refused' is identified,
                 otherwise ``None``.
        """
        current_exc: Optional[BaseException] = exc
        found_connection_refused: bool = False
        max_depth = 5 # Limit how deep we search in the exception chain

        for _depth in range(max_depth):
            if current_exc is None:
                break

            exc_str = str(current_exc).lower()
            # Direct check for ConnectionRefusedError
            if isinstance(current_exc, ConnectionRefusedError):
                found_connection_refused = True
                break

            # Check for urllib3 NewConnectionError indicating connection refused
            if isinstance(current_exc, urllib3_exceptions.NewConnectionError): # type: ignore[attr-defined]
                if "connection refused" in exc_str or "[errno 111]" in exc_str:
                    found_connection_refused = True
                    break

            # Check for urllib3 MaxRetryError wrapping a NewConnectionError
            if isinstance(current_exc, urllib3_exceptions.MaxRetryError): # type: ignore[attr-defined]
                if hasattr(current_exc, "reason") and isinstance(current_exc.reason, urllib3_exceptions.NewConnectionError): # type: ignore[attr-defined]
                    reason_exc_str = str(current_exc.reason).lower()
                    if "connection refused" in reason_exc_str or "[errno 111]" in reason_exc_str:
                        found_connection_refused = True
                        break

            # Generic string checks as a fallback
            if "connection refused" in exc_str or "errno 111" in exc_str:
                found_connection_refused = True
                break

            # Traverse the exception chain
            next_exc: Optional[BaseException] = None
            if hasattr(current_exc, "__cause__") and current_exc.__cause__ is not None:
                next_exc = current_exc.__cause__
            elif (
                hasattr(current_exc, "__context__")
                and current_exc.__context__ is not None
                and not getattr(current_exc, "__suppress_context__", False)
            ):
                next_exc = current_exc.__context__

            if current_exc is next_exc: # Avoid infinite loops on self-referential context/cause
                break
            current_exc = next_exc

        if found_connection_refused:
            logger.info("HttpFetcher: Connection refused condition identified for URL: %s", url)
            parsed_url_scheme = requests.utils.urlparse(url).scheme # type: ignore[attr-defined]
            if parsed_url_scheme == "https":
                return f"Connection Refused: The server at {url} actively refused the connection. (Is an HTTPS service running?)"
            if parsed_url_scheme == "http":
                return f"Connection Refused: The server at {url} actively refused the connection. (Is an HTTP service running?)"
            return f"Connection Refused: The server at {url} actively refused the connection."
        return None

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        """
        Format an :exc:`requests.exceptions.HTTPError` into a user-friendly string.

        Provides specific messages for common HTTP error codes (403, 404, 500)
        and a generic message for others.

        :param e: The :exc:`requests.exceptions.HTTPError` object.
        :return: A user-friendly error message string.
        """
        response = e.response
        status_code = response.status_code
        reason = response.reason if response.reason else "Unknown Status"
        url = e.request.url if e.request else "N/A"

        if status_code == 403:
            return f"Error {status_code} (Forbidden): Access to {url} is denied."
        if status_code == 404:
            return f"Error {status_code} (Not Found): The resource at {url} was not found."
        if status_code == 500:
            return f"Error {status_code} (Internal Server Error): The server encountered an error processing your request to {url}."
        return f"HTTP Error {status_code} ({reason}) for URL: {url}."

    def fetch_headers(self) -> List[Dict[str, Any]]:
        """
        Fetch and process HTTP headers for the configured URL.

        This is the main public method of the class. It prepares headers,
        configures the session with the :class:`.CustomDNSAdapter` (passing
        the `host_header` as `default_sni` for the adapter to use if the URL is an IP),
        executes the request, and processes the response.

        :raises HttpRequestTimeoutError: If the request times out.
        :raises HttpConnectionError: If a connection error occurs.
        :raises HttpProcessingError: If an HTTP error status (4xx/5xx) is returned.
        :raises HttpGenericRequestError: For other `requests` library errors.
        :raises HttpClientError: If the request is cancelled or for other client-side issues.
        :return: A list of dictionaries, where each dictionary represents a
                 response in the redirect chain (including the final response).
                 Each dictionary contains 'type' ('redirect' or 'final'), 'url',
                 'status_code', and 'headers'.
        """
        initial_request_specific_headers, session_headers = self._prepare_request_headers()

        effective_custom_dns_server: Optional[str] = self.custom_dns_server if DNSPYTHON_AVAILABLE else None
        if self.custom_dns_server and not DNSPYTHON_AVAILABLE:
            logger.warning(
                "HttpFetcher: Custom DNS ('%s') configured, but dnspython is missing. System DNS will be used by adapter.",
                self.custom_dns_server
            )

        # The host_header of HttpFetcher is used as the default_sni for CustomDNSAdapter.
        # This is relevant if the URL being fetched is an IP address.
        adapter = CustomDNSAdapter(custom_dns_server=effective_custom_dns_server, default_sni=self.host_header)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        logger.info(
            "HttpFetcher: CustomDNSAdapter mounted (Effective DNS: '%s', Default SNI for IP URLs: '%s').",
            effective_custom_dns_server or "System Default",
            self.host_header or "None",
        )

        if session_headers:
            self.session.headers.update(session_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError(f"Request cancelled for {self.url} before execution.")

        response = self._execute_http_request(initial_request_specific_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError(f"Request cancelled for {self.url} after execution, before processing.")

        return self._process_http_response(response)
