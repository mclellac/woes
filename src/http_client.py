# -*- coding: utf-8 -*-
"""Provides the HttpClient class for making HTTP/HTTPS requests.

This module defines :class:`HttpClient`, which simplifies making HTTP requests
by managing sessions, custom headers (including Akamai Pragma headers and
User-Agent), custom DNS resolution (via
:class:`~.custom_dns_adapter.CustomDNSAdapter`), request timeouts, and
processing of redirects and responses. It also defines a set of custom
exceptions for various HTTP and network related errors.
"""
import logging
from typing import Optional, Tuple, Dict, List, Any, Union

import requests
import requests.utils  # For urlparse, urlunparse

try:
    # dnspython is used by CustomDNSAdapter for custom DNS resolution.
    import dns.resolver
    import dns.exception
    dnspython_available = True
except ImportError:
    dnspython_available = False # Gracefully degrade if dnspython is not available.
    logging.getLogger(__name__).warning(
        "dnspython library not found. Custom DNS functionality will be disabled.")

try:
    # urllib3 exceptions are used for more specific connection error handling.
    import urllib3.exceptions as urllib3_exceptions
except ImportError:
    # Create dummy exception classes if urllib3 is not available or its exceptions changed.
    # This allows the code to run but with potentially less specific error messages.
    class _DummyUrllib3Exception(Exception): # pylint: disable=C0115 (Missing class docstring)
        pass
    urllib3_exceptions = type( # pylint: disable=C0103 (Invalid class name "urllib3_exceptions")
        "urllib3_exceptions",  # Name for the dynamic type
        (),  # Base classes
        {  # Attributes dictionary
            "MaxRetryError": _DummyUrllib3Exception,
            "NewConnectionError": _DummyUrllib3Exception,
        }
    )
    logging.warning(
        "Could not import urllib3.exceptions. Connection error details might be limited.")

from gi.repository import Gio  # For Cancellable object

from .custom_dns_adapter import CustomDNSAdapter

logger = logging.getLogger(__name__)

# --- Custom Exceptions ---
class HttpClientError(Exception):
    """Base exception for HttpClient errors."""
    pass


class HttpRequestTimeoutError(HttpClientError):
    """Exception raised when an HTTP request times out."""
    pass


class HttpConnectionError(HttpClientError):
    """Exception raised for errors during the HTTP connection process.

    This can include issues like DNS failures (if not using custom DNS),
    refused connections, or other network-level problems preventing
    a successful connection to the server.
    """
    pass


class HttpProcessingError(HttpClientError):
    """Exception for errors during HTTP response processing (e.g., bad status code).

    :ivar status_code: The HTTP status code that caused the error, if available.
    :vartype status_code: int, optional
    :ivar url: The URL associated with the error, if available.
    :vartype url: str, optional
    """
    def __init__(self, message: str, status_code: Optional[int] = None, url: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class HttpGenericRequestError(HttpClientError):
    """Exception for other `requests`-library related errors not covered by more specific exceptions."""
    pass


class HttpClient: # Renamed from HttpFetcher
    """Manages HTTP/HTTPS requests with support for custom configurations.

    This client handles the complexities of making HTTP requests, including:
    - Managing a :class:`requests.Session` for connection pooling.
    - Applying custom headers, such as 'User-Agent', 'Host', and Akamai Pragma.
    - Optional custom DNS resolution using :class:`~.custom_dns_adapter.CustomDNSAdapter`.
    - Handling redirects and processing response history.
    - Providing specific exceptions for different error conditions.
    - Support for request cancellation via a :class:`Gio.Cancellable` object.
    """

    def __init__(self,
                 url: str,
                 use_akamai_pragma: bool = False,
                 host_header: Optional[str] = None,
                 user_agent: Optional[str] = None,
                 custom_dns_server: Optional[str] = None,
                 cancellable: Optional[Gio.Cancellable] = None,
                 timeout: Union[float, Tuple[float, float]] = (5.0, 10.0)): # Default (connect, read) timeout
        """Initializes the HttpClient.

        :param url: The URL to target for requests made by this client instance.
        :type url: str
        :param use_akamai_pragma: If ``True``, include Akamai Pragma headers for debugging.
        :type use_akamai_pragma: bool
        :param host_header: An optional custom 'Host' header. If provided, this may also
                            be used as the SNI for IP-based URLs if `custom_dns_server`
                            is not used or does not resolve the original hostname.
        :type host_header: str, optional
        :param user_agent: An optional custom 'User-Agent' string.
        :type user_agent: str, optional
        :param custom_dns_server: Optional IP address of a custom DNS server to use for
                                  hostname resolution. Requires `dnspython` to be installed.
        :type custom_dns_server: str, optional
        :param cancellable: An optional :class:`Gio.Cancellable` object to allow for
                            request cancellation.
        :type cancellable: Gio.Cancellable, optional
        :param timeout: Timeout for requests. Can be a float (total timeout) or
                        a tuple (connect_timeout, read_timeout).
                        Defaults to (5.0, 10.0) seconds.
        :type timeout: Union[float, Tuple[float, float]]
        """
        self.url = url
        self.use_akamai_pragma = use_akamai_pragma
        self.host_header = host_header
        self.user_agent = user_agent
        self.custom_dns_server = custom_dns_server if dnspython_available else None
        self.cancellable = cancellable
        self.session = requests.Session()
        self.timeout = timeout

        if self.custom_dns_server and not dnspython_available:
            logger.warning(
                "HttpClient: Custom DNS server ('%s') was specified, but 'dnspython' "
                "library is not available. Custom DNS will be disabled.",
                self.custom_dns_server
            )

    def _prepare_request_headers(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Prepares initial request-specific headers and session-wide headers.

        Separates headers that should only be sent on the first request (like a
        custom 'Host' header, which `requests` handles specially for the initial
        request based on the URL's hostname) from headers that should be applied
        to the entire session (like 'User-Agent' or 'Pragma').

        :return: A tuple containing two dictionaries:
                 - ``initial_request_specific_headers``: Headers for the first request
                   (currently, only 'Host' if explicitly set by user for scenarios
                   like overriding URL's hostname, though `requests` usually handles Host).
                 - ``session_headers``: Headers to apply to the :class:`requests.Session`.
        :rtype: Tuple[Dict[str, str], Dict[str, str]]
        """
        initial_request_specific_headers: Dict[str, str] = {}
        session_headers: Dict[str, str] = {}

        if self.host_header:
            # While `requests` sets the Host header from the URL, providing it here
            # allows for explicit override, which might be needed if the URL is an IP.
            # The CustomDNSAdapter uses this for SNI if the URL is an IP.
            initial_request_specific_headers["Host"] = self.host_header
            logger.info(
                "HttpClient: Using user-provided Host header: '%s'", self.host_header)

        if self.user_agent:
            session_headers["User-Agent"] = self.user_agent
            logger.info(
                "HttpClient: Using custom User-Agent: '%s'", self.user_agent)
        else:
            # Let `requests` use its default User-Agent.
            logger.info(
                "HttpClient: No custom User-Agent; `requests` default will be used.")

        if self.use_akamai_pragma:
            directives = [
                "akamai-x-get-request-id", "akamai-x-get-cache-key",
                "akamai-x-cache-on", "akamai-x-cache-remote-on",
                "akamai-x-get-true-cache-key", "akamai-x-check-cacheable",
                "akamai-x-get-extracted-values", "akamai-x-feo-trace",
                "x-akamai-logging-mode: verbose", # Example of a directive with a value
            ]
            session_headers["Pragma"] = ", ".join(directives)
            logger.info("HttpClient: Akamai Pragma headers included.")
        else:
            logger.info("HttpClient: Akamai Pragma headers not included.")
        return initial_request_specific_headers, session_headers

    def _execute_http_request(self, initial_request_headers: Dict[str, str]) -> requests.Response:
        """Executes the HTTP GET request using the configured session and timeout.

        :param initial_request_headers: Headers to send with the initial request.
                                        These are merged with session headers by `requests`.
        :type initial_request_headers: Dict[str, str]
        :raises HttpRequestTimeoutError: If the request times out.
        :raises HttpConnectionError: If a connection error occurs (e.g., DNS failure, refused).
        :raises HttpGenericRequestError: For other `requests` library exceptions.
        :raises HttpClientError: If the request was cancelled before sending.
        :return: The :class:`requests.Response` object from the GET request.
        :rtype: requests.Response
        """
        logger.info("HttpClient: Executing HTTP GET to %s with timeout %s.", self.url, self.timeout)
        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError("Request cancelled before sending.")

        try:
            # `requests` merges session headers with per-request headers.
            response = self.session.get(
                self.url,
                headers=initial_request_headers or None, # Pass None if empty for clarity
                allow_redirects=True, # Allow requests to handle redirects
                timeout=self.timeout,
                stream=False # Ensure content is downloaded for immediate processing
            )
            return response
        except requests.exceptions.Timeout as e:
            logger.warning("HttpClient: Timeout for '%s': %s", self.url, e)
            raise HttpRequestTimeoutError(
                f"Request timed out for {self.url} after {self.timeout}s.") from e
        except requests.exceptions.ConnectionError as e:
            logger.warning(
                "HttpClient: ConnectionError for '%s': %s", self.url, e)
            # Try to get a more specific message (e.g., for connection refused)
            custom_msg = self._get_detailed_connection_error_message(e, self.url)
            msg = custom_msg or f"Network connection error for {self.url}."
            raise HttpConnectionError(msg) from e
        except requests.exceptions.RequestException as e: # Catch other `requests` exceptions
            logger.warning(
                "HttpClient: RequestException for '%s': %s", self.url, e)
            raise HttpGenericRequestError(
                f"Request failed for {self.url}: {e}") from e

    def _process_http_response(self, response: requests.Response) -> List[Dict[str, Any]]:
        """Processes the HTTP response, including its history (redirects).

        Formats each response in the redirect chain and the final response into
        a list of dictionaries. Raises :class:`HttpProcessingError` if the final
        response has a 4xx or 5xx status code.

        :param response: The final :class:`requests.Response` object after all redirects.
        :type response: requests.Response
        :raises HttpProcessingError: If the final response has a 4xx/5xx status code.
        :return: A list of dictionaries, where each dictionary represents a
                 response (redirect or final) in the chain. Each dictionary includes
                 'type', 'url', 'status_code', and 'headers'.
        :rtype: List[Dict[str, Any]]
        """
        all_responses_data: List[Dict[str, Any]] = []
        # Process redirect history
        for hist_resp in response.history:
            hist_data: Dict[str, Any] = {
                "type": "redirect",
                "url": str(hist_resp.url),
                "status_code": hist_resp.status_code,
                "headers": {str(k): str(v) for k, v in dict(hist_resp.headers).items()},
            }
            all_responses_data.append(hist_data)

        # Process final response
        try:
            response.raise_for_status()  # Raises HTTPError for 4xx/5xx status codes
            final_data_type = "final_success" # Mark as successful final response
        except requests.exceptions.HTTPError as http_err:
            logger.warning("HttpClient: HTTPError for '%s': %s",
                           self.url, http_err)
            error_message = self._format_http_error(http_err)
            # Include response data even for HTTP errors before raising
            final_error_data: Dict[str, Any] = {
                "type": "final_error",
                "url": str(response.url),
                "status_code": response.status_code,
                "headers": {str(k): str(v) for k, v in dict(response.headers).items()},
            }
            all_responses_data.append(final_error_data)
            raise HttpProcessingError(
                error_message, status_code=http_err.response.status_code,
                url=str(http_err.request.url)) from http_err # type: ignore

        final_data: Dict[str, Any] = {
            "type": final_data_type,
            "url": str(response.url),
            "status_code": response.status_code,
            "headers": {
                str(k): str(v) for k, v in dict(response.headers).items()
            },
        }
        all_responses_data.append(final_data)
        return all_responses_data

    def _get_detailed_connection_error_message(
            self, exc: Exception, url: str) -> Optional[str]:
        """Attempts to find a 'Connection Refused' or similar specific error
        within a chain of exceptions provided by the `requests` library.

        This helps provide more user-friendly messages for common network issues.

        :param exc: The initial exception object (typically `requests.exceptions.ConnectionError`).
        :type exc: Exception
        :param url: The URL for which the connection was attempted.
        :type url: str
        :return: A detailed error message if a specific condition like 'Connection Refused'
                 is identified, otherwise ``None``.
        :rtype: Optional[str]
        """
        current_exc: Optional[BaseException] = exc
        found_connection_refused = False
        max_depth = 5 # Limit how deep we traverse the exception chain

        for _depth in range(max_depth):
            if current_exc is None:
                break

            exc_str = str(current_exc).lower()

            # Check for direct ConnectionRefusedError
            if isinstance(current_exc, ConnectionRefusedError):
                found_connection_refused = True
                break

            # Check for urllib3 specific errors indicating connection refused
            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                if "connection refused" in exc_str or "[errno 111]" in exc_str:
                    found_connection_refused = True
                    break
                # Check original_error if present (urllib3 often wraps system errors)
                if hasattr(current_exc, "original_error"): # type: ignore
                    original_error = getattr(current_exc, "original_error") # type: ignore
                    if isinstance(original_error, ConnectionRefusedError) or \
                       (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111):
                        found_connection_refused = True
                        break
            elif isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                if hasattr(current_exc, "reason") and \
                   isinstance(current_exc.reason, urllib3_exceptions.NewConnectionError):
                    reason_exc_str = str(current_exc.reason).lower()
                    if "connection refused" in reason_exc_str or "[errno 111]" in reason_exc_str:
                        found_connection_refused = True
                        break
                    # Check original_error within the reason
                    if hasattr(current_exc.reason, "original_error"):
                        original_error = getattr(current_exc.reason, "original_error")
                        if isinstance(original_error, ConnectionRefusedError) or \
                           (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111):
                            found_connection_refused = True
                            break
            # Generic string check in current exception's arguments or message
            if any("connection refused" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "connection refused" in exc_str:
                found_connection_refused = True
            if any("[errno 111]" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "[errno 111]" in exc_str: # POSIX standard for Connection Refused
                found_connection_refused = True

            if found_connection_refused:
                break

            # Traverse to the next exception in the chain
            next_exc: Optional[BaseException] = None
            if hasattr(current_exc, "__cause__") and current_exc.__cause__ is not None:
                next_exc = current_exc.__cause__
            elif hasattr(current_exc, "__context__") and \
                    current_exc.__context__ is not None and \
                    not getattr(current_exc, "__suppress_context__", False): # Respect suppression
                next_exc = current_exc.__context__

            if current_exc is next_exc:  # Avoid infinite loop on self-referential cause/context
                break
            current_exc = next_exc

        if found_connection_refused:
            logger.info(
                "HttpClient: Connection refused condition identified for URL: %s", url)
            parsed_url_scheme = requests.utils.urlparse(url).scheme
            if parsed_url_scheme == "https":
                return ("Connection Refused: Server at HTTPS URL actively refused connection. "
                        "Is the service running? Try 'http://' if SSL is not expected.")
            if parsed_url_scheme == "http":
                return ("Connection Refused: Server at HTTP URL actively refused connection. "
                        "Is the service running? Try 'https://' if SSL is expected.")
            return ("Connection Refused: The server at the specified URL actively refused the connection. "
                    "Please check the URL and ensure the service is running.")
        return None # No specific "Connection Refused" pattern found

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        """Formats a :class:`requests.exceptions.HTTPError` into a user-friendly string.

        :param e: The :class:`requests.exceptions.HTTPError` object.
        :type e: requests.exceptions.HTTPError
        :return: A user-friendly error message string.
        :rtype: str
        """
        status_code = e.response.status_code
        reason = e.response.reason if e.response.reason else "Unknown Error"
        url = e.request.url if e.request else "N/A" # Should always have request.url

        if status_code == 400:
            return f"400 Bad Request: The server could not understand the request for {url}."
        if status_code == 401:
            return f"401 Unauthorized: Authentication is required and has failed or not been provided for {url}."
        if status_code == 403:
            return f"403 Forbidden: Access to {url} is denied."
        if status_code == 404:
            return f"404 Not Found: Resource at {url} could not be found."
        if status_code == 500:
            return f"500 Internal Server Error: The server encountered an unexpected condition at {url}."
        if status_code == 502:
            return f"502 Bad Gateway: The server, while acting as a gateway, received an invalid response from an upstream server for {url}."
        if status_code == 503:
            return f"503 Service Unavailable: The server is currently unavailable for {url} (overloaded or down for maintenance)."
        if status_code == 504:
            return f"504 Gateway Timeout: The server, while acting as a gateway, did not receive a timely response from an upstream server for {url}."

        # Generic message for other 4xx/5xx errors
        return f"HTTP Error {status_code} ({reason}) for URL: {url}."

    def execute(self) -> List[Dict[str, Any]]: # Renamed from fetch_headers
        """Executes the configured HTTP GET request and processes its response.

        This is the main public method for this class. It sets up the session with
        any custom DNS adapter, applies headers, executes the request, and processes
        the response (including redirects and error handling).

        :raises HttpRequestTimeoutError: If the request times out.
        :raises HttpConnectionError: If a connection error occurs.
        :raises HttpProcessingError: If an HTTPError (4xx/5xx status code) occurs on the final response.
        :raises HttpGenericRequestError: For other `requests`-related errors.
        :raises HttpClientError: If the request is cancelled or another client-side issue arises.
        :return: A list of dictionaries, where each dictionary represents a
                 response in the redirect chain (if any) and the final response.
                 Each dictionary includes 'type', 'url', 'status_code', and 'headers'.
        :rtype: List[Dict[str, Any]]
        """
        initial_request_specific_headers, session_headers = self._prepare_request_headers()

        # Use self.host_header as the default SNI if the URL is an IP address
        # and custom DNS isn't resolving a hostname. The CustomDNSAdapter handles this logic.
        default_sni_for_ip_url = self.host_header

        adapter = CustomDNSAdapter(
            custom_dns_server=self.custom_dns_server, # Will be None if dnspython is unavailable
            default_sni=default_sni_for_ip_url
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        logger.info(
            "HttpClient: CustomDNSAdapter mounted (DNS: '%s', Default SNI for IP URL: '%s').",
            self.custom_dns_server if dnspython_available else "System Default",
            default_sni_for_ip_url,
        )

        if session_headers:
            self.session.headers.update(session_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            raise HttpClientError("Request cancelled before execution.")

        response = self._execute_http_request(initial_request_specific_headers)

        if self.cancellable and self.cancellable.is_cancelled():
            # Check cancellation again after request returns, before processing.
            raise HttpClientError(
                "Request cancelled during/after execution, before processing.")

        return self._process_http_response(response)

    def close_session(self) -> None:
        """Closes the underlying :class:`requests.Session`.

        Should be called when the HttpClient is no longer needed to free resources.

        :return: None
        :rtype: None
        """
        logger.info("HttpClient: Closing session.")
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_session()
