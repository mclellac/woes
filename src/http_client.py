"""Module for fetching HTTP headers and processing responses."""

import logging
from typing import Optional, Dict, List, Any  # Use dict, list

import requests
# import requests.utils  # For urlparse, urlunparse -> Replaced by direct import
from urllib.parse import urlparse

try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None
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

from gi.repository import Gio

from .custom_dns_adapter import CustomDNSAdapter

logger = logging.getLogger(__name__)


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
    """
    Exception for errors during HTTP response processing (e.g., bad status).

    :ivar status_code: The HTTP status code that caused the error, if available.
    :vartype status_code: Optional[int]
    :ivar url: The URL associated with the error, if available.
    :vartype url: Optional[str]
    """

    def __init__(self, message: str, status_code: Optional[int] = None, url: Optional[str] = None):
        """
        Initialize the HTTP client error.

        :param message: The error message.
        :type message: str
        :param status_code: The HTTP status code, if applicable. Defaults to ``None``.
        :type status_code: Optional[int]
        :param url: The URL associated with the error, if applicable. Defaults to ``None``.
        :type url: Optional[str]
        """
        super().__init__(message)
        self.status_code: Optional[int] = status_code
        self.url = url


class HttpGenericRequestError(HttpClientError):
    """Exception for other requests-related errors."""

    pass


class HttpFetcher:
    """Encapsulates logic for making HTTP requests and processing responses."""

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
        Initialize HttpFetcher.

        :param url: The URL to fetch.
        :type url: str
        :param use_akamai_pragma: Whether to include Akamai Pragma headers. Defaults to ``False``.
        :type use_akamai_pragma: bool
        :param host_header: Optional custom Host header. Defaults to ``None``.
        :type host_header: Optional[str]
        :param user_agent: Optional custom User-Agent string. Defaults to ``None``.
        :type user_agent: Optional[str]
        :param custom_dns_server: Optional custom DNS server IP. Defaults to ``None``.
        :type custom_dns_server: Optional[str]
        :param cancellable: Optional :class:`Gio.Cancellable` object for cancellation. Defaults to ``None``.
        :type cancellable: Optional[Gio.Cancellable]
        """
        self.url: str = url
        self.use_akamai_pragma: bool = use_akamai_pragma
        self.host_header: Optional[str] = host_header
        self.user_agent: Optional[str] = user_agent
        self.custom_dns_server: Optional[str] = custom_dns_server
        self.cancellable: Optional[Gio.Cancellable] = cancellable
        self.session: requests.Session = requests.Session()

    def _prepare_request_headers(self) -> tuple[dict[str, str], dict[str, str]]:
        """
        Prepare initial request-specific headers and session-wide headers.

        The ``session_headers`` are built with the following precedence (later items override earlier):
        1. `self.user_agent` (if provided).
        2. Akamai Pragma headers (if `self.use_akamai_pragma` is true, overrides 'Pragma').

        The `self.host_header` is handled separately in ``initial_request_specific_headers``.

        :return: A tuple containing two dictionaries:
                 - ``initial_request_specific_headers``: Headers for the first request only (e.g., Host).
                 - ``session_headers``: Headers to apply to the :class:`requests.Session` for all requests.
        :rtype: tuple[dict[str, str], dict[str, str]]
        """
        initial_request_specific_headers: dict[str, str] = {}
        session_headers: dict[str, str] = {}

        # User-Agent override
        if self.user_agent:
            session_headers["User-Agent"] = self.user_agent
            logger.info("HttpFetcher: Using custom User-Agent: '%s'", self.user_agent)
        else:
            logger.info("HttpFetcher: No custom User-Agent; `requests` default will be used.")

        # Host header is special and often set on the initial request directly
        if self.host_header:
            initial_request_specific_headers["Host"] = self.host_header
            logger.info("HttpFetcher: Using user-provided Host header for initial request: '%s'", self.host_header)


        # Akamai Pragma headers
        if self.use_akamai_pragma:
            directives = [
                "akamai-x-get-request-id",
                "akamai-x-get-cache-key",
                "akamai-x-cache-on",
                "akamai-x-cache-remote-on",
                "akamai-x-get-true-cache-key",
                "akamai-x-check-cacheable",
                "akamai-x-get-extracted-values",
                "akamai-x-feo-trace",
                "x-akamai-logging-mode: verbose",
            ]
            session_headers["Pragma"] = ", ".join(directives)
            logger.info("HttpFetcher: Akamai Pragma headers included.")
        else:
            logger.info("HttpFetcher: Akamai Pragma headers not included.")
        return initial_request_specific_headers, session_headers

    def _execute_http_request(self, initial_request_headers: Dict[str, str]) -> requests.Response:
        """
        Execute the HTTP GET request using the configured session.

        Moved from ``HttpPage``.

        :param initial_request_headers: Headers to send with the initial request.
        :type initial_request_headers: dict[str, str]
        :raises .HttpRequestTimeoutError: If the request times out.
        :raises .HttpConnectionError: If a connection error occurs.
        :raises .HttpGenericRequestError: For other request-related errors.
        :raises .HttpClientError: If cancelled.
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
                timeout=5,
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
        """
        Process the HTTP response, including redirects.

        Moved from ``HttpPage``.

        :param response: The final :class:`requests.Response` object.
        :type response: requests.Response
        :raises .HttpProcessingError: If an :exc:`requests.exceptions.HTTPError` (4xx/5xx) occurs.
        :return: A list of dictionaries, where each dictionary represents a
                 response (redirect or final).
        :rtype: list[dict[str, Any]]
        """
        all_responses_data: list[dict[str, Any]] = []
        for hist_resp in response.history:  # type: ignore[attr-defined]
            hist_data: dict[str, Any] = {
                "type": "redirect",
                "url": str(hist_resp.url),
                "status_code": hist_resp.status_code,
                "headers": {str(k): str(v) for k, v in dict(hist_resp.headers).items()},
            }
            all_responses_data.append(hist_data)
        try:
            response.raise_for_status()  # type: ignore[attr-defined]
            final_data_type = "final"
        except requests.exceptions.HTTPError as http_err:
            error_url = str(http_err.request.url) if http_err.request else self.url
            logger.warning("HttpFetcher: HTTPError for URL '%s' (final URL: '%s'): %s", self.url, error_url, http_err)
            error_message = self._format_http_error(http_err)
            raise HttpProcessingError(
                error_message, status_code=http_err.response.status_code, url=error_url
            ) from http_err  # type: ignore[union-attr]

        final_data: dict[str, Any] = {
            "type": final_data_type,
            "url": str(response.url),  # type: ignore[attr-defined]
            "status_code": response.status_code,  # type: ignore[attr-defined]
            "headers": {str(k): str(v) for k, v in dict(response.headers).items()},  # type: ignore[attr-defined]
        }
        all_responses_data.append(final_data)
        return all_responses_data

    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:
        """
        Attempt to find a 'Connection Refused' error within a chain of exceptions.

        Moved from ``HttpPage``.

        :param exc: The initial exception object.
        :type exc: Exception
        :param url: The URL for which the connection was attempted.
        :type url: str
        :return: A detailed error message if 'Connection Refused' is identified,
                 otherwise ``None``.
        :rtype: Optional[str]
        """
        current_exc: Optional[BaseException] = exc
        found_connection_refused: bool = False
        max_depth = 5
import socket
import errno # For errno constants

# ... (other imports) ...

# Helper to get errno from an exception if possible
def _get_errno(e: BaseException) -> Optional[int]:
    if hasattr(e, 'errno') and isinstance(e.errno, int):
        return e.errno
    if hasattr(e, 'args') and isinstance(e.args, tuple) and len(e.args) > 0 and isinstance(e.args[0], int):
        # Sometimes errno is the first argument, e.g. in some OSErrors
        # Check if it's a known errno value to be more certain, though this is heuristic
        # For now, we'll just return it if it's an int.
        # A more robust check might involve checking against a list of valid errnos.
        return e.args[0]
    return None

# ... (class HttpFetcher and other methods) ...

    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:
        """
        Attempt to find a 'Connection Refused' error within a chain of exceptions.
        Prioritizes specific exception types and errno checks over string matching.

        Moved from ``HttpPage``.

        :param exc: The initial exception object.
        :type exc: Exception
        :param url: The URL for which the connection was attempted.
        :type url: str
        :return: A detailed error message if 'Connection Refused' is identified,
                 otherwise ``None``.
        :rtype: Optional[str]
        """
        current_exc: Optional[BaseException] = exc
        found_connection_refused: bool = False
        max_depth = 10 # Increased depth slightly for deeply nested exceptions
        processed_exceptions = set() # To avoid infinite loops in rare cases

        for _depth in range(max_depth):
            if current_exc is None or id(current_exc) in processed_exceptions:
                break
            processed_exceptions.add(id(current_exc))

            # 1. Direct check for ConnectionRefusedError
            if isinstance(current_exc, ConnectionRefusedError):
                found_connection_refused = True
                break

            # 2. Check for OSError with specific errno ECONNREFUSED
            # ConnectionRefusedError is a subclass of OSError, so this might be redundant
            # if the direct check above is comprehensive, but kept for thoroughness.
            if isinstance(current_exc, OSError) and _get_errno(current_exc) == errno.ECONNREFUSED:
                found_connection_refused = True
                break

            # 3. Check urllib3 specific exceptions that often wrap ConnectionRefusedError
            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                # Check if the NewConnectionError itself was caused by a ConnectionRefusedError or ECONNREFUSED
                # This can be in its __cause__ or sometimes an 'original_error' attribute if urllib3 adds one.
                # The loop will check __cause__ automatically.
                # We also check its string representation as a fallback.
                exc_str_lower = str(current_exc).lower()
                if "connection refused" in exc_str_lower or "errno 111" in exc_str_lower:
                    found_connection_refused = True
                    break
                # Check original_error if present (as in existing code)
                if hasattr(current_exc, "original_error"):
                    original_error = current_exc.original_error # type: ignore
                    if isinstance(original_error, ConnectionRefusedError) or \
                       (isinstance(original_error, OSError) and _get_errno(original_error) == errno.ECONNREFUSED):
                        found_connection_refused = True
                        break
                    if "connection refused" in str(original_error).lower() or "errno 111" in str(original_error).lower():
                         found_connection_refused = True
                         break


            if isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                # The actual connection error is in the 'reason' attribute
                if hasattr(current_exc, "reason") and current_exc.reason is not None: # type: ignore
                    # We don't break here; instead, we set current_exc to its reason
                    # and let the loop continue to analyze the reason exception.
                    # This avoids duplicating all the checks for the reason.
                    # However, to ensure progress, we'll do a quick check on reason string here
                    # and if it matches, we can break early.
                    reason_exc_str_lower = str(current_exc.reason).lower() # type: ignore
                    if "connection refused" in reason_exc_str_lower or "errno 111" in reason_exc_str_lower:
                        found_connection_refused = True
                        break
                    # If not immediately found in string, the loop will continue with reason as current_exc
                else: # No reason, MaxRetryError itself might contain clues (less likely for refused)
                    exc_str_lower = str(current_exc).lower()
                    if "connection refused" in exc_str_lower or "errno 111" in exc_str_lower:
                        found_connection_refused = True
                        break


            # 4. Fallback: Generic string matching on the current exception's string or args
            # This is kept from the original logic as a final catch-all.
            # We check this after specific types because string matching can be less precise.
            if not found_connection_refused:
                exc_str_lower = str(current_exc).lower()
                if "connection refused" in exc_str_lower or "errno 111" in exc_str_lower:
                    found_connection_refused = True
                    break
                if hasattr(current_exc, 'args') and isinstance(current_exc.args, tuple):
                    for arg in current_exc.args:
                        if isinstance(arg, str):
                            arg_lower = arg.lower()
                            if "connection refused" in arg_lower or "errno 111" in arg_lower:
                                found_connection_refused = True
                                break
                    if found_connection_refused:
                        break

            if found_connection_refused: # Should be caught by inner breaks, but for safety.
                break

            # Navigate to the next exception in the chain
            next_exc: Optional[BaseException] = None
            if hasattr(current_exc, "__cause__") and current_exc.__cause__ is not None:
                next_exc = current_exc.__cause__
            elif (
                hasattr(current_exc, "__context__")
                and current_exc.__context__ is not None
                and not getattr(current_exc, "__suppress_context__", False) # type: ignore
            ):
                next_exc = current_exc.__context__

            # Avoid getting stuck if current_exc is its own cause/context (shouldn't happen)
            if current_exc is next_exc or next_exc is None: # Break if no progress or end of chain
                break
            current_exc = next_exc

        if found_connection_refused:
            logger.info("HttpFetcher: Connection refused condition identified for URL: %s", url)
            # self.url is available if needed, but the passed 'url' param is more direct for this method's scope
            parsed_url = urlparse(url)
            scheme = parsed_url.scheme.lower() if parsed_url.scheme else ""

            if scheme == "https":
                return "Connection Refused: Server at HTTPS URL actively refused. Try 'http://'?"
            elif scheme == "http":
                return "Connection Refused: Server at HTTP URL actively refused. Try 'https://' or check if server is down."
            else: # Fallback for ftp, ws, or other schemes, or if scheme parsing failed
                return "Connection Refused: The server at the specified URL actively refused the connection."
        return None

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        """
        Format an :exc:`requests.exceptions.HTTPError` into a user-friendly string.

        Moved from ``HttpPage``.

        :param e: The :exc:`requests.exceptions.HTTPError` object.
        :type e: requests.exceptions.HTTPError
        :return: A user-friendly error message string.
        :rtype: str
        """
        status_code = e.response.status_code  # type: ignore[union-attr]
        reason = e.response.reason if e.response.reason else "Unknown Error"  # type: ignore[union-attr]
        url = e.request.url if e.request else "N/A"  # type: ignore[union-attr]
        if status_code == 403:
            return f"403 Forbidden: Access to {url} denied."
        if status_code == 404:
            return f"404 Not Found: Resource at {url} not found."
        if status_code == 500:
            return f"500 Internal Server Error for {url}."
        return f"HTTP Error {status_code} ({reason}) for URL: {url}."

    def fetch_headers(self) -> list[dict[str, Any]]:
        """
        Fetch and process HTTP headers.

        :raises .HttpRequestTimeoutError: If the request times out.
        :raises .HttpConnectionError: If a connection error occurs.
        :raises .HttpProcessingError: If an :exc:`requests.exceptions.HTTPError` (4xx/5xx) occurs.
        :raises .HttpGenericRequestError: For other request-related errors.
        :raises .HttpClientError: If cancelled or other client-side issue.
        :return: A list of dictionaries, where each dictionary represents a
                 response (redirect or final).
        :rtype: list[dict[str, Any]]
        """
        initial_request_specific_headers, session_headers = self._prepare_request_headers()

        # Determine the SNI hint for the CustomDNSAdapter.
        # Priority:
        # 1. User-defined Host header (self.host_header)
        # 2. Hostname from the URL
        adapter_sni_hint: Optional[str] = None
        parsed_url = urlparse(self.url)
        url_hostname = parsed_url.hostname

        if self.host_header:
            adapter_sni_hint = self.host_header
            logger.info("HttpFetcher: Using Host header ('%s') as SNI hint.", self.host_header)
        elif url_hostname:
            adapter_sni_hint = url_hostname
            logger.info("HttpFetcher: Using URL hostname ('%s') as SNI hint.", url_hostname)
        else:
            logger.warning("HttpFetcher: Could not determine hostname for SNI hint from URL: %s", self.url)

        effective_custom_dns_server: Optional[str] = self.custom_dns_server if dns else None
        if self.custom_dns_server and not dns:
            logger.warning(
                "HttpFetcher: Custom DNS ('%s') configured, but dnspython missing for adapter.", self.custom_dns_server
            )

        # Pass the determined SNI hint to the adapter.
        # The adapter is expected to use this for TLS connections, especially when connecting to an IP.
        adapter = CustomDNSAdapter(custom_dns_server=effective_custom_dns_server, default_sni=adapter_sni_hint)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        logger.info(
            "HttpFetcher: CustomDNSAdapter mounted (DNS: '%s', SNI hint: '%s').",
            effective_custom_dns_server,
            adapter_sni_hint, # Log the actual hint being used
        )

        if session_headers:
            self.session.headers.update(session_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError("Request cancelled before execution.")

        response = self._execute_http_request(initial_request_specific_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError("Request cancelled during/after execution, before processing.")

        return self._process_http_response(response)
