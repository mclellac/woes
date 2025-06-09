"""Defines the HTTP Headers page for the Woes application.

This page allows users to fetch and inspect HTTP headers for a given URL,
with options for custom Host headers, User-Agent strings, and Akamai Pragma headers.
It also supports using a custom DNS server for domain resolution.
"""

# pylint: disable=too-many-lines
import logging
import re
import ssl
import socket
from enum import Enum
from typing import Optional, Any

import requests
import requests.utils
from requests.adapters import HTTPAdapter

try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None
    logging.warning("dnspython library not found. Custom DNS functionality will be disabled.")

import gi
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk

from .constants import RESOURCE_PREFIX, USER_AGENTS, APP_ID


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

try:
    import urllib3.exceptions as urllib3_exceptions
except ImportError:
    # If urllib3 is not available at all (should not happen with requests installed)
    # Define dummy classes for isinstance checks to not fail, or handle differently.
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


class CustomSNIAdapter(HTTPAdapter):
    """A custom HTTPAdapter for `requests` that allows specifying a Server Name Indication (SNI)
    hostname different from the URL's hostname. This is useful for HTTPS requests to an IP address
    when the server expects a specific hostname for TLS handshake.
    """

    def __init__(self, *args, sni_hostname=None, **kwargs):
        """Initialize the CustomSNIAdapter.

        Args:
        ----
            *args: Positional arguments to pass to the parent HTTPAdapter.
            sni_hostname (Optional[str]): The hostname to use for SNI. If None, behaves like a normal adapter.
            **kwargs: Keyword arguments to pass to the parent HTTPAdapter.

        """
        self.sni_hostname = sni_hostname
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        """Initialize the connection pool manager.

        Overrides the parent method to inject SNI-specific SSL context options
        if an `sni_hostname` was provided.

        Args:
        ----
            connections: The number of urllib3 connection pools to cache.
            maxsize: The maximum number of connections to save in the pool.
            block: Whether to block when no free connections are available.
            **pool_kwargs: Additional keyword arguments to pass to the PoolManager.

        """
        if self.sni_hostname:
            # These kwargs are for urllib3.PoolManager and its ConnectionPools
            pool_kwargs["assert_hostname"] = self.sni_hostname
            pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED

            # Urllib3's PoolManager passes `server_hostname` to individual ConnectionPools.
            # We get the existing connection_pool_kw, add our server_hostname,
            # and then update pool_kwargs to include these for the PoolManager.
            conn_pool_specific_kwargs = pool_kwargs.pop("connection_pool_kw", {}).copy()
            conn_pool_specific_kwargs["server_hostname"] = self.sni_hostname
            pool_kwargs.update(conn_pool_specific_kwargs)

        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    # This implementation focuses on direct connections.


logger = logging.getLogger(__name__)


WOES_HTTP_ERROR_DOMAIN = "woes-http-error-domain"


class HttpErrorType(int, Enum):
    """Enumeration of HTTP error types for Gio.Task error reporting."""

    TIMEOUT = 0
    HTTP_ERROR = 1  # Covers HTTP status codes >= 400
    CONNECTION_ERROR = 2  # Covers network issues like DNS failure, connection refused
    REQUEST_EXCEPTION = 3  # Covers other requests.exceptions like InvalidURL
    GENERIC_UNEXPECTED = 4  # Fallback for truly unexpected Python exceptions
    CANCELLED = 5  # If the Gio.Task was cancelled


class HeaderItem(GObject.Object):
    """GObject representing a single header key-value pair for the Gtk.ColumnView.

    Attributes
    ----------
        key (str): The header name or special row key.
        value (str): The header value or special row value.
        is_special_row (bool): True if this item represents a special informational row
                               (e.g., URL, status) rather than a standard HTTP header.

    """

    key: str
    value: str
    is_special_row: bool

    def __init__(self, key: str, value: str, is_special_row: bool = False):
        """Initialize a HeaderItem.

        Args:
        ----
            key: The header name or special row key.
            value: The header value or special row value.
            is_special_row: True if this is a special informational row.

        """
        super().__init__()
        self.key = key
        self.value = value
        self.is_special_row = is_special_row


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Adw.PreferencesPage):
    """Activity page for fetching and inspecting HTTP headers.

    Provides UI elements for URL input, Host header, User-Agent selection,
    Akamai Pragma toggles, and displays results in a Gtk.ColumnView.
    Handles asynchronous fetching of headers and error display.
    """

    __gtype_name__ = "HttpPage"
    http_entry_row = Gtk.Template.Child("http_entry_row")
    http_apply_button = Gtk.Template.Child("http_apply_button")
    http_host_header_row = Gtk.Template.Child("http_host_header_row")
    http_user_agent_row = Gtk.Template.Child("http_user_agent_row")
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    # error_banner = Gtk.Template.Child("error_banner") # Removed
    http_results_group = Gtk.Template.Child("http_results_group")
    clear_results_button = Gtk.Template.Child("clear_results_button")
    copy_results_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        """Initialize the HttpPage.

        Sets up UI elements from the template, initializes GSettings,
        configures the Gtk.ColumnView for displaying headers,
        connects signal handlers, and sets initial UI state.
        """
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task = None
        self._current_header_items = []
        self._http_task_data_for_thread = {}
        self._ua_title_to_value_map = {}

        self.settings = Gio.Settings(schema_id=APP_ID)
        self._header_key_color = self.settings.get_string("http-output-header-key-color")
        self._header_value_color = self.settings.get_string("http-output-header-value-color")
        self._special_row_color = self.settings.get_string("http-output-special-row-color")

        self.settings.connect("changed::http-output-header-key-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-header-value-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-special-row-color", self._on_color_setting_changed)

        self.header_list_store = Gio.ListStore.new(HeaderItem)
        selection_model = Gtk.MultiSelection.new(self.header_list_store)
        self.http_column_view.set_model(selection_model)

        if not self.http_column_view.get_columns():
            header_name_factory = self._create_factory("key")
            header_value_factory = self._create_factory("value", wrap_text=True)

            col_name = Gtk.ColumnViewColumn.new("Header", header_name_factory)
            col_value = Gtk.ColumnViewColumn.new("Value", header_value_factory)
            col_value.set_expand(True)

            self.http_column_view.append_column(col_name)
            self.http_column_view.append_column(col_value)

        self._connect_signals()
        self._clear_error()
        self._hide_results()

        self._update_user_agent_model()
        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._update_user_agent_model())

        if self.http_apply_button:
            self.http_apply_button.set_use_underline(True)

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        # Removed signal connection for local error_banner
        # if self.error_banner:
        #     self.error_banner.connect("button-clicked", self._on_error_banner_dismiss)

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle click event for the 'Copy Results' button.

        Collects all displayed header items, formats them as text,
        and copies them to the clipboard.

        Args:
        ----
            _button: The Gtk.Button that was clicked.

        """
        logger.info("Copying all headers to clipboard.")
        lines = []
        if self.header_list_store:
            for i in range(self.header_list_store.get_n_items()):
                item = self.header_list_store.get_item(i)
                if isinstance(item, HeaderItem):
                    if item.is_special_row:
                        if item.value and item.value.strip():
                            lines.append(f"{item.key} {item.value}")
                        else:
                            lines.append(item.key)
                    else:
                        lines.append(f"{item.key}: {item.value}")

        if lines:
            text_to_copy = "\n".join(lines)
            try:
                clipboard = Gdk.Display.get_default().get_clipboard()
                if clipboard:
                    clipboard.set_text(text_to_copy)
                    logger.info("Headers copied to clipboard successfully.")
                else:
                    logger.warning("Failed to get default clipboard.")
            except Exception as e:  # pylint: disable=broad-except
                logger.error("Error copying to clipboard: %s", e, exc_info=True)
        else:
            logger.info("No headers to copy.")

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        """Handle activation of the URL entry row or click of the 'Apply' button.

        Initiates the process of fetching HTTP headers for the entered URL.
        Validates the URL, gathers configuration from UI elements (Host header, User-Agent, etc.),
        and starts an asynchronous task to perform the HTTP request.

        Args:
        ----
            _widget: The widget that triggered the activation (Adw.EntryRow or Gtk.Button).

        """
        original_url = self.http_entry_row.get_text().strip()
        url = self._ensure_scheme(original_url)
        logger.info("Fetching headers for URL: %s (original: %s)", url, original_url)

        if not self._is_valid_url(url):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url)
            self._display_error("Invalid URL format: Please enter a valid URL.")
            self._update_column_view_model(None)
            return

        self._clear_error()
        self.http_entry_row.set_sensitive(False)
        if hasattr(self, "http_apply_button"):
            self.http_apply_button.set_sensitive(False)

        host_header = self.http_host_header_row.get_text().strip()

        user_agent_to_send = None
        selected_title_obj = self.http_user_agent_row.get_selected_item()
        if isinstance(selected_title_obj, Gtk.StringObject):
            selected_title = selected_title_obj.get_string()
            # .get will return None if selected_title is not found, or if its mapped value is None
            user_agent_to_send = self._ua_title_to_value_map.get(selected_title)

        custom_dns_server = self.settings.get_string("custom-dns-server")

        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header,
            "user_agent": user_agent_to_send,
            "custom_dns_server": custom_dns_server,
        }
        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None)
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func)

    def _fetch_headers_task_thread_func(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-nested-blocks # noqa: C901
        self,
        task: Gio.Task,
        source_object,  # pylint: disable=unused-argument
        task_data_arg: dict,  # pylint: disable=unused-argument
        cancellable: Optional[Gio.Cancellable],
    ):
        """Perform the HTTP GET request in a separate thread.

        This method is executed by `Gio.Task.run_in_thread`. It constructs the request
        based on `_http_task_data_for_thread`, handles custom DNS resolution via
        `socket.getaddrinfo` patching if configured, mounts a `CustomSNIAdapter`
        if necessary (for HTTPS to IP with Host header), and makes the request
        using the `requests` library.

        It captures all responses (redirects and final) and returns them as a list
        of dictionaries. Errors during the process are returned via `task.return_new_error_literal`.

        Args:
        ----
            task: The `Gio.Task` associated with this asynchronous operation.
            source_object: The source object that initiated the task (unused).
            task_data_arg: Task-specific data (unused, uses `self._http_task_data_for_thread`).
            cancellable: A `Gio.Cancellable` object to monitor for cancellation requests.

        """
        current_task_data = self._http_task_data_for_thread

        url_to_fetch = current_task_data["url"]
        original_url_with_scheme = current_task_data["url"]
        use_akamai_pragma = current_task_data["use_akamai_pragma"]
        host_header_from_input = current_task_data.get("host_header")
        user_agent = current_task_data.get("user_agent")
        custom_dns_server = current_task_data.get("custom_dns_server")

        initial_request_specific_headers = {}
        session_headers = {}
        original_hostname = None
        captured_original_hostname = ""  # Initialize to ensure it's always bound

        original_getaddrinfo = None
        resolved_addresses_for_host = None

        if custom_dns_server and dns:
            parsed_url_obj = requests.utils.urlparse(original_url_with_scheme)
            original_hostname = parsed_url_obj.hostname

            if not original_hostname:
                logger.warning("Could not parse hostname from URL for custom DNS: %s", original_url_with_scheme)
            else:
                logger.info(
                    "Attempting to resolve '%s' using custom DNS server %s",
                    original_hostname,
                    custom_dns_server,
                )
                resolver = dns.resolver.Resolver()
                resolver.nameservers = [custom_dns_server]
                resolver.timeout = 2.0
                resolver.lifetime = 2.0

                all_ips = []
                try:
                    for rdtype in ("A", "AAAA"):
                        try:
                            answers = resolver.resolve(original_hostname, rdtype)
                            for rdata in answers:
                                all_ips.append(rdata.address)
                        except dns.resolver.NoAnswer:
                            logger.debug(
                                "No %s records found for %s using %s.",
                                rdtype,
                                original_hostname,
                                custom_dns_server,
                            )
                        except dns.exception.DNSException as e:
                            logger.warning(
                                "DNS resolution for %s records of %s failed: %s",
                                rdtype,
                                original_hostname,
                                e,
                            )

                    if all_ips:
                        resolved_addresses_for_host = all_ips
                        logger.info(
                            "Resolved '%s' to %s via %s.",
                            original_hostname,
                            resolved_addresses_for_host,
                            custom_dns_server,
                        )
                    else:
                        logger.warning(
                            "Custom DNS %s provided no A or AAAA records for %s. "
                            "Falling back to system DNS for this request.",
                            custom_dns_server,
                            original_hostname,
                        )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error(
                        "Unexpected error during custom DNS processing for %s: %s",
                        original_hostname,
                        e,
                        exc_info=True,
                    )

        if resolved_addresses_for_host and original_hostname:
            original_getaddrinfo = socket.getaddrinfo

            captured_original_hostname = original_hostname
            captured_resolved_ips = resolved_addresses_for_host

            def custom_getaddrinfo(host, port, family=0, addr_type=0, proto=0, flags=0):  # pylint: disable=too-many-arguments,redefined-builtin,too-many-positional-arguments
                if host == captured_original_hostname:
                    logger.debug(
                        "Custom getaddrinfo: Intercepting '%s', returning %s",
                        host,
                        captured_resolved_ips,
                    )
                    results = []
                    for ip_addr in captured_resolved_ips:
                        addr_family = socket.AF_INET6 if ":" in ip_addr else socket.AF_INET
                        if family in (0, addr_family):
                            results.append(
                                (
                                    addr_family,
                                    socket.SOCK_STREAM,
                                    socket.IPPROTO_TCP,
                                    "",
                                    (ip_addr, port),
                                )
                            )

                    if not results and family:
                        logger.warning(
                            "Custom getaddrinfo: No addresses for '%s' matched requested family %s. Falling back.",
                            host,
                            family,
                        )
                        return original_getaddrinfo(host, port, family, addr_type, proto, flags)
                    if not results:
                        logger.warning(
                            "Custom getaddrinfo: No addresses for '%s' after filtering. Falling back.",
                            host,
                        )
                        return original_getaddrinfo(host, port, family, addr_type, proto, flags)
                    return results
                return original_getaddrinfo(host, port, family, addr_type, proto, flags)

            socket.getaddrinfo = custom_getaddrinfo
            logger.info("socket.getaddrinfo patched to use custom DNS results for '%s'.", original_hostname)
        else:
            logger.debug("Not patching socket.getaddrinfo, custom DNS not used or resolution failed/yielded no IPs.")

        use_custom_sni_adapter = False
        parsed_url_for_sni_check = requests.utils.urlparse(url_to_fetch)
        is_url_ip_address = all(
            c.isdigit() or c == "." or c == ":" or c == "[" or c == "]"
            for c in parsed_url_for_sni_check.netloc.split(":", 1)[0]
        )

        if url_to_fetch.startswith("https://") and is_url_ip_address and host_header_from_input:
            use_custom_sni_adapter = True
            logger.info(
                "URL '%s' is IP-based. User-provided Host header '%s' will be used for SNI via CustomSNIAdapter.",
                url_to_fetch,
                host_header_from_input,
            )
        elif custom_dns_server and original_hostname and not is_url_ip_address:
            logger.info(
                "Custom DNS resolved %s, but URL '%s' is a hostname. "
                "Requests will handle SNI; CustomSNIAdapter not mounted for this reason.",
                original_hostname,
                url_to_fetch,
            )

        session = requests.Session()

        if use_custom_sni_adapter and host_header_from_input:
            adapter = CustomSNIAdapter(sni_hostname=host_header_from_input)
            session.mount("https://", adapter)
            logger.debug("Mounted CustomSNIAdapter for https:// with SNI: %s", host_header_from_input)
        elif use_custom_sni_adapter and not host_header_from_input:
            logger.warning("CustomSNIAdapter was considered but no host_header_from_input was available for SNI name.")

        if host_header_from_input:
            initial_request_specific_headers["Host"] = host_header_from_input
            logger.info(
                "User-provided Host header '%s' will be used for the request.",
                host_header_from_input,
            )

        if user_agent:
            session_headers["User-Agent"] = user_agent
        if use_akamai_pragma:
            akamai_pragma_directives = [
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
            session_headers["Pragma"] = ", ".join(akamai_pragma_directives)
        if session_headers:
            session.headers.update(session_headers)

        logger.info("Task thread: Making GET request to %s.", url_to_fetch)

        try:
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.CANCELLED.value,
                    "Task was cancelled.",
                )
                return

            response = session.get(
                url_to_fetch,
                headers=(initial_request_specific_headers if initial_request_specific_headers else None),
                allow_redirects=True,
                timeout=5,
            )

            all_responses_data = []

            for hist_resp in response.history:
                hist_data = {
                    "type": "redirect",
                    "url": str(hist_resp.url),
                    "status_code": hist_resp.status_code,
                    "headers": {str(k): str(v) for k, v in dict(hist_resp.headers).items()},
                }
                all_responses_data.append(hist_data)

            try:
                response.raise_for_status()
                final_data_type = "final"
            except requests.exceptions.HTTPError as http_err:
                logger.warning("Task thread: HTTPError for %s: %s", url_to_fetch, http_err)
                error_message = self._format_http_error(http_err)
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.HTTP_ERROR.value,
                    error_message,
                )
                return

            final_data = {
                "type": final_data_type,
                "url": str(response.url),
                "status_code": response.status_code,
                "headers": {str(k): str(v) for k, v in dict(response.headers).items()},
            }
            all_responses_data.append(final_data)

            task.return_value(all_responses_data)

        except requests.exceptions.Timeout as e:
            logger.warning("Task thread: Timeout for %s: %s", url_to_fetch, e)
            error_message = (
                "Request timed out. This could be due to a slow network, server issues, "
                "or a Web Application Firewall (WAF) interfering. "
                "Please check the URL or try again later."
            )
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.TIMEOUT.value,
                error_message,
            )
            return
        except requests.exceptions.ConnectionError as e:
            logger.warning("Task thread: ConnectionError for %s: %s", url_to_fetch, e)
            custom_msg = self._get_detailed_connection_error_message(e, url_to_fetch)
            if custom_msg:
                error_message = custom_msg
            else:
                error_message = (
                    "A network connection error occurred. Please check your internet connection "
                    "and the entered URL, then try again."
                )
            if not error_message:
                error_message = "Connection Error: Failed to establish a connection."
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.CONNECTION_ERROR.value,
                error_message,
            )
            return
        except requests.exceptions.RequestException as e:
            logger.warning("Task thread: RequestException for %s: %s", url_to_fetch, e)
            error_message = "The request could not be completed. Please verify the URL and your network connection."
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.REQUEST_EXCEPTION.value,
                error_message,
            )
            return
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Task thread: Truly unexpected error for %s: %s",
                url_to_fetch,
                e,
                exc_info=True,
            )
            error_message = (
                "An unexpected internal error occurred while processing your request. Please try again later."
            )
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.GENERIC_UNEXPECTED.value,
                error_message,
            )
            return
        finally:
            if original_getaddrinfo:
                socket.getaddrinfo = original_getaddrinfo
                # Use captured_original_hostname directly as it's now guaranteed to be bound
                logger.info(
                    "socket.getaddrinfo restored for '%s'.",
                    captured_original_hostname,
                )

    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:  # pylint: disable=too-many-branches,too-many-statements # noqa: C901
        """Attempt to provide a more specific error message for connection errors.

        Inspects the given exception (and its causes) to identify common patterns
        like 'Connection refused'. If found, it suggests potential causes based on the URL scheme
        (e.g., trying HTTP for an HTTPS site or vice-versa).

        Args:
        ----
            exc: The connection-related exception caught.
            url: The URL that was being accessed.

        Returns:
        -------
            A more specific error message string if a known pattern is matched, otherwise None.

        """
        current_exc = exc
        found_connection_refused = False
        max_depth = 5

        for _depth in range(max_depth):
            if current_exc is None:
                break

            exc_str = str(current_exc)

            if isinstance(current_exc, ConnectionRefusedError):
                found_connection_refused = True
                break

            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                if "connection refused" in exc_str.lower() or "errno 111" in exc_str.lower():
                    found_connection_refused = True
                    break
                if hasattr(current_exc, "original_error") and isinstance(
                    current_exc.original_error, ConnectionRefusedError
                ):
                    found_connection_refused = True
                    break
                if (
                    hasattr(current_exc, "original_error")
                    and hasattr(current_exc.original_error, "errno")
                    and current_exc.original_error.errno == 111
                ):
                    found_connection_refused = True
                    break

            if isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                if hasattr(current_exc, "reason") and current_exc.reason is not None:
                    reason_exc = current_exc.reason
                    reason_exc_str = str(reason_exc)
                    if isinstance(reason_exc, urllib3_exceptions.NewConnectionError):
                        if "connection refused" in reason_exc_str.lower() or "errno 111" in reason_exc_str.lower():
                            found_connection_refused = True
                            break
                        if hasattr(reason_exc, "original_error") and isinstance(
                            reason_exc.original_error, ConnectionRefusedError
                        ):
                            found_connection_refused = True
                            break
                        if (
                            hasattr(reason_exc, "original_error")
                            and hasattr(reason_exc.original_error, "errno")
                            and reason_exc.original_error.errno == 111
                        ):
                            found_connection_refused = True
                            break

            if (
                any("connection refused" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str))
                or "connection refused" in exc_str.lower()
            ):
                found_connection_refused = True

            if (
                any("errno 111" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str))
                or "errno 111" in exc_str.lower()
            ):
                found_connection_refused = True

            next_exc = None
            if hasattr(current_exc, "__cause__") and current_exc.__cause__ is not None:
                next_exc = current_exc.__cause__
            elif (
                hasattr(current_exc, "__context__")
                and current_exc.__context__ is not None
                and not current_exc.__suppress_context__
            ):
                next_exc = current_exc.__context__

            if current_exc is next_exc:  # Avoid infinite loops
                break
            current_exc = next_exc

        if found_connection_refused:
            logger.info("Connection refused condition identified for URL: %s", url)
            if requests.utils.urlparse(url).scheme == "https":
                return (
                    "The URL targetted via HTTPS is refusing the connection. "
                    "It might be an HTTP-only service. Please try with 'http://'."
                )
            if requests.utils.urlparse(url).scheme == "http":
                return (
                    "The HTTP request failed. The server might only support HTTPS for this resource. "
                    "Please try with 'https://'."
                )
            return "Connection Error: The server at the specified URL actively refused the connection."

        return None

    def _fetch_headers_task_done_cb(self, _source_object, result: Gio.AsyncResult, _user_data):  # pylint: disable=too-many-branches,too-many-statements,unused-argument # noqa: C901
        """Callback executed in the main thread when `_fetch_headers_task_thread_func` completes.

        Processes the results (list of response data dictionaries) or errors returned
        by the background task. Updates the UI (ColumnView, error banners) accordingly.
        Re-enables UI elements that were disabled during the fetch.

        Args:
        ----
            _source_object: The object that initiated the task (unused).
            result: A `Gio.AsyncResult` containing the task's outcome.
            _user_data: User data passed to the callback (unused).

        """
        task_being_processed = self.current_http_task

        if task_being_processed is None:
            logger.warning(
                "_fetch_headers_task_done_cb: current_http_task is None. UI might have been re-enabled prematurely."
            )
            if hasattr(self, "http_entry_row") and self.http_entry_row and not self.http_entry_row.get_sensitive():
                self.http_entry_row.set_sensitive(True)
            if (
                hasattr(self, "http_apply_button")
                and self.http_apply_button
                and not self.http_apply_button.get_sensitive()
            ):
                self.http_apply_button.set_sensitive(True)
            return

        self.current_http_task = None
        logger.info("Processing task completion in _fetch_headers_task_done_cb.")

        try:
            actual_list_of_responses = task_being_processed.propagate_value()

            if not isinstance(actual_list_of_responses, list) and isinstance(actual_list_of_responses, tuple):
                if len(actual_list_of_responses) > 0 and isinstance(actual_list_of_responses[0], list):
                    actual_list_of_responses = actual_list_of_responses[0]
                elif hasattr(actual_list_of_responses, "value") and isinstance(actual_list_of_responses.value, list):
                    actual_list_of_responses = actual_list_of_responses.value

            if isinstance(actual_list_of_responses, list):
                logger.info("Successfully processed task result as list.")
                processed_headers_for_store = []
                if not actual_list_of_responses:
                    logger.info("Received empty list for results.")
                    self._update_column_view_model(None)
                else:
                    for i, response_data in enumerate(actual_list_of_responses):
                        if not isinstance(response_data, dict):
                            logger.error(
                                "Expected dict item in response list, got %s. Data: %s",
                                type(response_data),
                                response_data,
                            )
                            continue

                        url_display = f"URL: {response_data.get('url', 'N/A')}"
                        status_display = f"Status: {response_data.get('status_code', 'N/A')}"

                        if response_data.get("type") == "redirect":
                            status_display += " (Redirect)"
                        elif response_data.get("type") == "final":
                            status_display += " (Final)"

                        processed_headers_for_store.append(
                            HeaderItem(
                                key=url_display,
                                value=status_display,
                                is_special_row=True,
                            )
                        )

                        headers_for_this_response = response_data.get("headers", {})
                        for (
                            header_key,
                            header_value,
                        ) in headers_for_this_response.items():
                            processed_headers_for_store.append(
                                HeaderItem(
                                    key=str(header_key),
                                    value=str(header_value),
                                    is_special_row=False,
                                )
                            )

                        if i < len(actual_list_of_responses) - 1:
                            processed_headers_for_store.append(HeaderItem(key="---", value="---", is_special_row=True))

                    self._current_header_items = processed_headers_for_store
                    self._update_column_view_model(processed_headers_for_store)
                if hasattr(self, "http_entry_row") and self.http_entry_row:
                    self.http_entry_row.remove_css_class("error")
            else:
                logger.error(
                    "Result data of unexpected type %s. Expected list.",
                    type(actual_list_of_responses),
                )
                self._display_error(
                    f"Failed to process task result (unexpected data structure: "
                    f"{type(actual_list_of_responses).__name__})."
                )
                if hasattr(self, "http_entry_row") and self.http_entry_row:
                    self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)

        except GLib.Error as e:
            logger.error(
                "Task failed with GLib.Error: Domain=%s, Code=%s, Message='%s'",
                e.domain,
                e.code,
                e.message,
            )
            self._display_error(e.message.replace("<b>", "").replace("</b>", ""))
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Unexpected Python error in _fetch_headers_task_done_cb: %s",
                e,
                exc_info=True,
            )
            self._display_error("An unexpected application error occurred while displaying the results.")
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        finally:
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.set_sensitive(True)
            if hasattr(self, "http_apply_button") and self.http_apply_button:
                self.http_apply_button.set_sensitive(True)

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        """Ensure the URL has a scheme (defaults to https if missing).

        Args:
        ----
            url: The input URL string.

        Returns:
        -------
            The URL string with a scheme.

        """
        parsed_url = requests.utils.urlparse(url)
        if not parsed_url.scheme:
            url = "https://" + url
        return url

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """Validate if a string is a well-formed HTTP/HTTPS URL.

        Args:
        ----
            url: The URL string to validate.

        Returns:
        -------
            True if the URL is valid, False otherwise.

        """
        url_regex = re.compile(
            r"^(?:http|https)://"
            r"(?:\S+(?::\S*)?@)?"
            r"(?:[A-Za-z0-9.-]+\.[A-Za-z]{2,}|localhost|"
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"\[?[A-Fa-f0-9]*:[A-Fa-f0-9:]+\]?)"
            r"(?::\d+)?"
            r"(?:/?|[/?]\S+)$",
            re.IGNORECASE,
        )

        return re.match(url_regex, url) is not None and bool(requests.utils.urlparse(url).netloc)

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        """Format an HTTPError from the `requests` library into a user-friendly message.

        Args:
        ----
            e: The `requests.exceptions.HTTPError` instance.

        Returns:
        -------
            A user-friendly error message string.

        """
        status_code = e.response.status_code
        if status_code == 404:
            return "404 Not Found: The requested URL was not found on this server."
        if status_code == 403:
            return "403 Forbidden: You don't have permission to access this URL."
        if status_code == 500:
            return "500 Internal Server Error: The server encountered an internal error."
        return f"HTTP Error {status_code}: {e.response.reason}."

    def _on_pragma_toggled(
        self,
        widget: Gtk.Switch,
        _gparam: GObject.ParamSpec,
    ) -> None:
        """Handle the toggle event for the Akamai Pragma switch.

        If the URL entry is not empty, it re-triggers the header fetch.

        Args:
        ----
            widget: The Gtk.Switch that was toggled.
            _gparam: The GObject.ParamSpec of the 'active' property (unused).

        """
        logger.debug("Akamai Pragma toggled to: %s", widget.get_active())
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[list[HeaderItem]]) -> None:
        """Update the Gtk.ColumnView's model with new header items.

        Clears existing items and populates the ListStore with the provided list.
        Shows or hides the results group based on whether items are present.

        Args:
        ----
            header_items: A list of `HeaderItem` objects, or None to clear.

        """
        self.header_list_store.remove_all()

        if header_items:
            for item in header_items:
                self.header_list_store.append(item)
            self._show_results()
        else:
            self._hide_results()

    def _show_results(self):
        """Make the HTTP results group visible."""
        self.http_results_group.set_visible(True)

    def _hide_results(self):
        """Make the HTTP results group invisible."""
        self.http_results_group.set_visible(False)

    def _display_error(self, message: str) -> None:
        """Display an error message in the UI.

        Sets the error banner title, reveals it, adds 'error' CSS class
        to the entry row, and hides the results section.

        Args:
        ----
            message: The error message to display.

        """
        # self.error_banner.set_title(message) # Removed
        # self.error_banner.set_revealed(True) # Removed
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'show_error'):
            main_window.show_error(message)
        else:
            logger.warning("Could not find main window or show_error method to display: %s", message)

        self.http_entry_row.add_css_class("error")
        self._hide_results()

    def _clear_error(self) -> None:
        """Clear any displayed error messages from the UI.

        Hides the error banner, clears its title, and removes 'error'
        CSS class from the entry row.
        """
        # self.error_banner.set_revealed(False) # Removed
        # self.error_banner.set_title("") # Removed
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'hide_error'):
            main_window.hide_error()
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

        self.http_entry_row.remove_css_class("error")

    # Removed _on_error_banner_dismiss method
    # def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
    #     """Handle dismissal of the error banner by clearing the error state."""
    #     self._clear_error()

    def _on_clear_results_clicked(self, _button: Gtk.Button, *_args):
        """Handle click of the 'Clear Results' button.

        Clears current header items, updates the view model, clears errors,
        and resets the URL entry row.
        """
        logger.info("Results cleared by user.")
        self._current_header_items = []
        self._update_column_view_model(None)
        self._clear_error()
        self.http_entry_row.set_text("")

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str):
        """Handle changes to color-related GSettings.

        Updates internal color attributes and re-populates the ColumnView
        if results are currently displayed to apply new colors.

        Args:
        ----
            settings: The Gio.Settings object that changed.
            key: The name of the setting key that changed.

        """
        logger.debug("Color setting changed for key: %s", key)
        if key == "http-output-header-key-color":
            self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color":
            self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color":
            self._special_row_color = settings.get_string(key)

        if self._current_header_items:
            logger.debug("Re-populating view to apply color changes.")
            self._update_column_view_model(self._current_header_items)

    def _update_user_agent_model(self):
        """Update the User-Agent dropdown model.

        Clears and repopulates the User-Agent title-to-value map and
        the dropdown model (Gtk.StringList) with custom User-Agents first,
        then "None", then default User-Agents.
        Preserves selection if possible.
        """
        if not self.http_user_agent_row:
            return

        self._ua_title_to_value_map.clear()  # Clear map at the beginning

        current_selection_text = None
        # Preserve current selection
        if (
            self.http_user_agent_row.get_model()
            and self.http_user_agent_row.get_selected() != Gtk.INVALID_LIST_POSITION
        ):
            selected_item = self.http_user_agent_row.get_selected_item()
            if isinstance(selected_item, Gtk.StringObject):
                current_selection_text = selected_item.get_string()

        display_titles = []

        # 1. Custom UAs from GSettings
        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs = list(variant.unpack() if variant and variant.get_type_string() == "a(ss)" else [])

        for title, value in custom_ua_pairs:
            # Custom UAs are added first, so their titles are definitely new to the map in this loop
            display_titles.append(title)
            self._ua_title_to_value_map[title] = value

        # 2. "None" option
        none_title = "None"
        # Ensure "None" title is unique if a custom UA is named "None"
        if none_title not in self._ua_title_to_value_map:
            display_titles.append(none_title)
        # Always ensure "None" maps to None for sending no header,
        # even if a custom UA is named "None" (its custom value would be in the map for selection purposes).
        self._ua_title_to_value_map[none_title] = None

        # 3. Default UAs from constants.py
        for ua_dict in USER_AGENTS:
            title = ua_dict.get("title")
            value = ua_dict.get("value")
            if title and value:
                if title not in self._ua_title_to_value_map:  # Add if title not used by custom or "None"
                    display_titles.append(title)
                    self._ua_title_to_value_map[title] = value
                # If title was used by custom, map already has custom value.
                # If title is "None", it's already handled to map to None for sending.

        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles))

        # Restore selection
        if current_selection_text and current_selection_text in self._ua_title_to_value_map:
            try:
                idx = display_titles.index(current_selection_text)
                self.http_user_agent_row.set_selected(idx)
            except ValueError:
                # If current_selection_text is in map but not display_titles (e.g. a default overridden by custom and not re-added)
                # or simply not found, default to the first available item.
                if display_titles:
                    self.http_user_agent_row.set_selected(0)
        elif display_titles:
            self.http_user_agent_row.set_selected(0)

        logging.info(
            f"User-Agent dropdown model updated with {len(display_titles)} titles in custom->None->default order."
        )

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """Create a Gtk.SignalListItemFactory for a column in the Gtk.ColumnView.

        This factory is responsible for setting up and binding Gtk.Label widgets
        to display `HeaderItem` data. It applies custom colors based on settings
        and whether the row is a special informational row.

        Args:
        ----
            attr_name: The attribute name of `HeaderItem` to display (e.g., "key", "value").
            wrap_text: Whether the text in the label should be wrapped.

        Returns:
        -------
            A configured Gtk.SignalListItemFactory.

        """
        factory = Gtk.SignalListItemFactory()

        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Setup function for the list item factory. Creates and sets a Gtk.Label as child."""
            label = Gtk.Label(xalign=0)
            label.set_hexpand(True)
            if wrap_text:
                label.set_wrap(True)
                label.set_max_width_chars(80)
            list_item.set_child(label)

        # This nested function can capture 'self' from the outer _create_factory method
        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Bind function for the list item factory. Sets label text and markup."""
            label = list_item.get_child()
            item = list_item.get_item()

            if not (label and isinstance(label, Gtk.Label) and item and isinstance(item, HeaderItem)):
                if label and isinstance(label, Gtk.Label):
                    label.set_text("Error: Invalid item or label.")
                return

            text_to_display = getattr(item, attr_name, "")

            if item.is_special_row:
                if attr_name == "key":
                    key_text = GLib.markup_escape_text(item.key if item.key else "")
                    value_text = GLib.markup_escape_text(item.value if item.value else "")
                    full_text = key_text
                    if value_text.strip():
                        full_text += f" {value_text}"
                    label.set_markup(f"<b><span foreground='{self._special_row_color}'>{full_text}</span></b>")
                else:
                    label.set_markup("")
            else:
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(text_to_display)
                label.set_markup(f"<b><span foreground='{color_to_use}'>{escaped_text}</span></b>")

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)

        return factory
