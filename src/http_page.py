"""Defines the HTTP Headers page for the Woes application.

This page allows users to fetch and inspect HTTP headers for a given URL,
with options for custom Host headers, User-Agent strings, and Akamai Pragma headers.
It also supports using a custom DNS server for domain resolution via a custom HTTPAdapter.
"""

# pylint: disable=too-many-lines
import logging
import re
import ssl
import socket # Used by CustomDNSAdapter for IP family checks, not for patching.
from enum import Enum
from typing import Optional, List

import requests
import requests.utils # For urlparse, urlunparse
from requests.adapters import HTTPAdapter

try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None # Will be checked by features requiring dnspython
    logging.warning("dnspython library not found. Custom DNS functionality will be disabled.")

import gi
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk, Pango

from .constants import RESOURCE_PREFIX, USER_AGENTS, APP_ID


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

try:
    import urllib3.exceptions as urllib3_exceptions
except ImportError:
    # If urllib3 is not available at all (should not happen with requests installed)
    # Define dummy classes for isinstance checks to not fail.
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


class CustomDNSAdapter(HTTPAdapter):
    """A custom HTTPAdapter for `requests` that enables custom DNS resolution and SNI handling.

    This adapter intercepts requests to:
    1.  Resolve the hostname using a specified custom DNS server if `dnspython` is available.
        If resolution occurs, the request URL is internally modified to use the resolved IP address
        for the connection, while the original hostname is saved for SNI and Host header purposes.
    2.  Configure Server Name Indication (SNI) for HTTPS connections:
        - If a hostname was resolved to an IP, the original hostname is used for SNI.
        - If the request URL is already an IP address, a `default_sni` (typically from the
          user's 'Host' header input) can be used for SNI.
    3.  Set `assert_hostname` for `urllib3`'s connection pool to ensure the SSL certificate
        is validated against the correct hostname (either the original or the specified SNI).

    If custom DNS is not used or resolution fails, it behaves like a standard HTTPAdapter.
    """

    def __init__(self, *args, custom_dns_server: Optional[str] = None, default_sni: Optional[str] = None, **kwargs):
        """Initialize the CustomDNSAdapter.

        Args:
        ----
            *args: Positional arguments to pass to the parent HTTPAdapter.
            custom_dns_server: IP address of the custom DNS server. If None, or if `dns` (dnspython)
                               module is unavailable, system DNS will be used.
            default_sni: The hostname to use for SNI and certificate validation if the request URL
                         is already an IP address. This is typically derived from the user's
                         'Host' header input.
            **kwargs: Keyword arguments to pass to the parent HTTPAdapter.

        """
        self.custom_dns_server = custom_dns_server
        self.default_sni_for_ip_url = default_sni
        self._resolved_sni: Optional[str] = None # SNI derived from hostname resolution by this adapter.
        super().__init__(*args, **kwargs)

    def _resolve_hostname_to_ip(self, hostname: str) -> Optional[str]:
        """Resolves a hostname using the custom DNS server.

        Attempts to resolve AAAA records first, then A records.

        Args:
        ----
            hostname: The hostname to resolve.

        Returns:
        -------
            The first resolved IP address (preferring IPv6) as a string, or None if
            resolution fails, custom DNS is not configured, or `dnspython` is unavailable.

        """
        if not self.custom_dns_server or not dns:
            logger.debug("CustomDNSAdapter: Skipping custom DNS (server: %s, dnspython available: %s) for %s.",
                         self.custom_dns_server, bool(dns), hostname)
            return None

        logger.info("CustomDNSAdapter: Attempting to resolve '%s' using DNS server %s", hostname, self.custom_dns_server)
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [self.custom_dns_server]
        resolver.timeout = 2.0  # Short timeout for DNS resolution
        resolver.lifetime = 2.0 # Total time for resolution attempt including retries

        ips: list[str] = []
        try:
            for rdtype in ("AAAA", "A"):  # Prefer AAAA (IPv6) if available
                try:
                    answers = resolver.resolve(hostname, rdtype)
                    for rdata in answers:
                        ips.append(rdata.address)
                    if ips:  # Found records of the current type
                        break
                except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                    logger.debug("CustomDNSAdapter: No %s records found for %s via %s.",
                                 rdtype, hostname, self.custom_dns_server)
                except dns.exception.DNSException as e:
                    logger.warning("CustomDNSAdapter: DNS %s query for %s via %s failed: %s",
                                 rdtype, hostname, self.custom_dns_server, e)

            if ips:
                selected_ip = ips[0]
                logger.info("CustomDNSAdapter: Resolved '%s' to %s (using %s).",
                            hostname, selected_ip, self.custom_dns_server)
                return selected_ip
            logger.warning("CustomDNSAdapter: No AAAA or A records found for %s via %s.",
                           hostname, self.custom_dns_server)
        except Exception as e:  # pylint: disable=broad-except
            logger.error("CustomDNSAdapter: Unexpected error during custom DNS resolution for %s: %s",
                         hostname, e, exc_info=True)
        return None

    def send(self, request: requests.models.PreparedRequest, stream: bool = False, timeout: Optional[float] = None,
             verify: bool = True, cert: Optional[tuple[str, str] | str] = None, proxies=None):
        """Overrides HTTPAdapter.send method.

        This method is called by `requests.Session` to send a `PreparedRequest`.
        It performs custom DNS resolution if applicable before the request is actually sent.
        If a hostname is resolved to an IP:
        - The `request.url` is updated to use the IP for the connection.
        - `self._resolved_sni` is set to the original hostname for SNI and cert validation.
        The Host header in `request.headers` should remain the original hostname.

        Args:
        ----
            request: The `PreparedRequest` to send.
            stream: Whether to stream the response content.
            timeout: Request timeout.
            verify: Whether to verify SSL certificates.
            cert: Client-side certificate.
            proxies: Proxies to use for the request.

        Returns:
        -------
            The response from `super().send()`.

        """
        parsed_url = requests.utils.urlparse(request.url)
        original_hostname = parsed_url.hostname
        self._resolved_sni = None  # Reset for each request

        # Determine if the original hostname in the URL is already an IP address.
        is_original_hostname_ip = False
        if original_hostname:
            try:
                # Check if it's a valid IP address (IPv4 or IPv6)
                socket.inet_pton(socket.AF_INET, original_hostname)
                is_original_hostname_ip = True
            except socket.error:
                try:
                    socket.inet_pton(socket.AF_INET6, original_hostname)
                    is_original_hostname_ip = True
                except socket.error:
                    is_original_hostname_ip = False

        if original_hostname and not is_original_hostname_ip and self.custom_dns_server and dns:
            resolved_ip = self._resolve_hostname_to_ip(original_hostname)
            if resolved_ip:
                # Store original hostname for SNI and certificate validation
                self._resolved_sni = original_hostname

                # Modify the request URL to point to the resolved IP address.
                # The Host header (in request.headers) should still be the original hostname.
                # `requests` uses the hostname from request.url to generate the Host header
                # if not already present. We must ensure it's the original, or set it manually.
                # However, initial_request_specific_headers["Host"] in _fetch_headers_task_thread_func
                # usually sets this correctly based on user input or original URL.

                new_netloc = resolved_ip
                if parsed_url.port:
                    new_netloc += f":{parsed_url.port}"

                # Create new URL parts list for urlunparse
                # (scheme, netloc, path, params, query, fragment)
                new_url_parts = list(parsed_url[:]) # Make a mutable copy
                new_url_parts[1] = new_netloc  # Index 1 is 'netloc'
                request.url = requests.utils.urlunparse(new_url_parts)
                logger.info("CustomDNSAdapter.send: Modified request URL for IP connection: %s (Original Host: %s, SNI will be: %s)",
                            request.url, original_hostname, self._resolved_sni)

        # The actual SNI value to be used by init_poolmanager is determined there based on
        # self._resolved_sni or self.default_sni_for_ip_url.
        return super().send(request, stream, timeout, verify, cert, proxies)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs):
        """Initializes the `urllib3.PoolManager`.

        This method is overridden to inject SNI (Server Name Indication) and
        `assert_hostname` parameters into the pool's keyword arguments if:
        1.  A hostname was resolved to an IP by this adapter (`self._resolved_sni` is set).
            In this case, the original hostname is used for SNI and certificate validation.
        2.  The original request URL was an IP address and `self.default_sni_for_ip_url` was provided
            (e.g., from a user-supplied 'Host' header). This value is then used.

        Args:
        ----
            connections: The number of urllib3 connection pools to cache.
            maxsize: The maximum number of connections to save in the pool.
            block: Whether to block when no free connections are available.
            **pool_kwargs: Additional keyword arguments to pass to the PoolManager.

        """
        sni_hostname_for_pool = None
        if self._resolved_sni:
            # Case 1: Hostname was resolved to an IP. Use original hostname for SNI/validation.
            sni_hostname_for_pool = self._resolved_sni
            logger.info("CustomDNSAdapter.init_poolmanager: Using SNI/assert_hostname from resolved hostname: %s",
                        sni_hostname_for_pool)
        elif self.default_sni_for_ip_url:
            # Case 2: Original URL was an IP, and a default SNI was provided.
            # This is used only if _resolved_sni is not set (i.e., no DNS resolution occurred for a hostname).
            # This implies the connection is likely being made to an IP specified in the original URL.
            sni_hostname_for_pool = self.default_sni_for_ip_url
            logger.info("CustomDNSAdapter.init_poolmanager: Using default SNI for IP URL: %s",
                        sni_hostname_for_pool)

        if sni_hostname_for_pool:
            pool_kwargs["assert_hostname"] = sni_hostname_for_pool
            pool_kwargs["server_hostname"] = sni_hostname_for_pool  # For SNI in modern urllib3/ssl contexts
            pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED # Ensure certificate validation is enforced
            logger.debug("CustomDNSAdapter.init_poolmanager: Pool configured with SNI/assert_hostname: %s",
                         sni_hostname_for_pool)
        else:
            logger.debug("CustomDNSAdapter.init_poolmanager: No specific SNI/assert_hostname configuration for this pool.")

        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

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
    Uses CustomDNSAdapter for custom DNS resolution and SNI.
    """

    __gtype_name__ = "HttpPage"
    http_entry_row = Gtk.Template.Child("http_entry_row")
    http_apply_button = Gtk.Template.Child("http_apply_button")
    http_host_header_row = Gtk.Template.Child("http_host_header_row")
    http_user_agent_row = Gtk.Template.Child("http_user_agent_row")
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    http_results_group = Gtk.Template.Child("http_results_group")
    clear_results_button = Gtk.Template.Child("clear_results_button")
    copy_results_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        """Initialize the HttpPage.

        Sets up UI elements from the template, initializes GSettings,
        configures the Gtk.ColumnView for displaying headers,
        connects signal handlers, and sets initial UI state including visual cues
        for active overrides.
        """
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task = None
        self._current_header_items = []
        self._http_task_data_for_thread: dict = {} # Explicitly typed for clarity
        self._ua_title_to_value_map: dict[str, Optional[str]] = {} # Explicitly typed

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
        self._clear_error() # Clear any persistent error state from previous view
        self._hide_results() # Initially hide results section

        self._update_user_agent_model() # Populate User-Agent dropdown
        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._update_user_agent_model())

        if self.http_apply_button:
            self.http_apply_button.set_use_underline(True)

        # Call handlers to set initial visual state for overrides based on current values
        if self.http_host_header_row:
            self._on_host_header_changed(self.http_host_header_row)
        if self.http_user_agent_row:
            self._on_user_agent_changed(self.http_user_agent_row, None)


    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)

        # Signals for visual cues on active overrides
        if self.http_host_header_row:
            self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row:
            self.http_user_agent_row.connect("notify::selected-item", self._on_user_agent_changed)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        """Adds or removes 'active-override' CSS class based on Host header text.

        Args:
        ----
            entry_row: The Adw.EntryRow for the Host header.

        """
        if not entry_row: return
        text = entry_row.get_text().strip()
        if text:
            entry_row.add_css_class("active-override")
        else:
            entry_row.remove_css_class("active-override")

    def _on_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        """Adds or removes 'active-override' CSS class based on User-Agent selection.

        The class is added if the selected User-Agent is not the default "None" option.

        Args:
        ----
            combo_row: The Adw.ComboRow for User-Agent selection.
            _gparam: The GObject.ParamSpec associated with the signal (unused here).

        """
        if not combo_row: return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            # Check if the effective UA value is non-empty (i.e., not the "None" option)
            effective_ua_value = self._ua_title_to_value_map.get(selected_title)
            if effective_ua_value:  # An actual UA string is selected (not the one mapping to None)
                combo_row.add_css_class("active-override")
            else: # "None" is selected, or title not in map
                combo_row.remove_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map:
            # Handles case where model might be initially empty before _update_user_agent_model.
            combo_row.remove_css_class("active-override")


    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle click event for the 'Copy Results' button.

        Collects all displayed header items (URLs, statuses, headers), formats them as text,
        and copies them to the clipboard.

        Args:
        ----
            _button: The Gtk.Button that was clicked (unused).

        """
        logger.info("Copying all headers to clipboard.")
        lines = []
        if self.header_list_store:
            for i in range(self.header_list_store.get_n_items()):
                item = self.header_list_store.get_item(i)
                if isinstance(item, HeaderItem):
                    if item.is_special_row: # For URL/Status rows
                        if item.value and item.value.strip(): # If there's a value part (status)
                            lines.append(f"{item.key} {item.value}")
                        else: # Just the key (URL line or separator)
                            lines.append(item.key)
                    else: # For actual header rows
                        lines.append(f"{item.key}: {item.value}")

        if lines:
            text_to_copy = "\n".join(lines)
            try:
                clipboard = Gdk.Display.get_default().get_clipboard()
                if clipboard:
                    clipboard.set(text_to_copy)
                    logger.info("Headers copied to clipboard successfully.")
                else:
                    logger.warning("Failed to get default clipboard for copying.")
            except Exception as e:  # pylint: disable=broad-except
                logger.error("Error copying headers to clipboard: %s", e, exc_info=True)
        else:
            logger.info("No headers to copy from the results view.")

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        """Handle activation of the URL entry row or click of the 'Fetch' button.

        Initiates the process of fetching HTTP headers. Validates the URL,
        gathers configuration from UI elements (Host header, User-Agent, etc.),
        and starts an asynchronous task (`Gio.Task`) to perform the HTTP request.
        UI elements are made insensitive during the fetch.

        Args:
        ----
            _widget: The widget that triggered the activation (Adw.EntryRow or Gtk.Button, unused).

        """
        original_url = self.http_entry_row.get_text().strip()
        url = self._ensure_scheme(original_url) # Ensure URL has a scheme (https:// by default)
        logger.info("Fetching headers for URL: %s (original input: %s)", url, original_url)

        if not self._is_valid_url(url):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url)
            main_window = self.get_native()
            if main_window and hasattr(main_window, 'show_toast'):
                main_window.show_toast("Invalid URL format. Please enter a valid URL (e.g., https://example.com).")
            else:
                # Fallback or log if main_window or show_toast is not available
                logger.warning("Could not find main window or show_toast method to display invalid URL toast.")
                self._display_error("Invalid URL format: Please enter a valid URL (e.g., https://example.com).") # Fallback to banner
            self._update_column_view_model(None)
            return

        self._clear_error()
        self.http_entry_row.set_sensitive(False)
        if hasattr(self, "http_apply_button") and self.http_apply_button:
            self.http_apply_button.set_sensitive(False)
            self.http_apply_button.set_icon_name("process-working-symbolic")

        host_header = self.http_host_header_row.get_text().strip()

        user_agent_to_send: Optional[str] = None # Explicitly typed
        selected_title_obj = self.http_user_agent_row.get_selected_item()
        if isinstance(selected_title_obj, Gtk.StringObject):
            selected_title = selected_title_obj.get_string()
            user_agent_to_send = self._ua_title_to_value_map.get(selected_title)

        custom_dns_server = self.settings.get_string("custom-dns-server")
        if custom_dns_server and dns is None:
            logger.warning(
                "Custom DNS server ('%s') is configured, but 'dnspython' library is not available. "
                "Custom DNS resolution will be skipped; system DNS will be used.", custom_dns_server
            )
            # This warning is for logging; CustomDNSAdapter handles dns=None by falling back.

        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header if host_header else None, # Ensure None if empty
            "user_agent": user_agent_to_send, # Already Optional[str]
            "custom_dns_server": custom_dns_server if custom_dns_server else None, # Ensure None if empty
        }
        logger.debug(f"Starting header fetch task with data: {self._http_task_data_for_thread}")

        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None)
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func)


    def _prepare_request_headers(
        self, use_akamai_pragma: bool, host_header_from_input: Optional[str], user_agent: Optional[str]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Prepares initial request-specific headers and session-wide headers.

        Args:
        ----
            use_akamai_pragma: Whether to include Akamai Pragma headers.
            host_header_from_input: Custom Host header value, if any.
            user_agent: Custom User-Agent string, if any.

        Returns:
        -------
            A tuple containing two dictionaries:
            - `initial_request_specific_headers`: Headers for the first request only (e.g., Host).
            - `session_headers`: Headers to be applied to the `requests.Session` object for all requests.

        """
        initial_request_specific_headers: dict[str,str] = {}
        session_headers: dict[str, str] = {}

        if host_header_from_input:
            initial_request_specific_headers["Host"] = host_header_from_input
            logger.info("Using user-provided Host header for the initial request: '%s'", host_header_from_input)

        if user_agent: # If a specific User-Agent is chosen (not "None" which maps to None value)
            session_headers["User-Agent"] = user_agent
            logger.info("Using custom User-Agent for the session: '%s'", user_agent)
        else: # "None" selected or no UA preference
            logger.info("No custom User-Agent specified; `requests` default will be used.")


        if use_akamai_pragma:
            akamai_pragma_directives = [
                "akamai-x-get-request-id", "akamai-x-get-cache-key", "akamai-x-cache-on",
                "akamai-x-cache-remote-on", "akamai-x-get-true-cache-key", "akamai-x-check-cacheable",
                "akamai-x-get-extracted-values", "akamai-x-feo-trace", "x-akamai-logging-mode: verbose",
            ]
            session_headers["Pragma"] = ", ".join(akamai_pragma_directives)
            logger.info("Akamai Pragma headers will be included in session headers.")
        else:
            logger.info("Akamai Pragma headers will not be included.")

        return initial_request_specific_headers, session_headers

    # _configure_sni_adapter method was removed as its logic is now integrated into CustomDNSAdapter.

    def _fetch_headers_task_thread_func(
        self,
        task: Gio.Task,
        source_object,  # pylint: disable=unused-argument
        task_data_arg: dict,  # pylint: disable=unused-argument
        cancellable: Optional[Gio.Cancellable],
    ):
        """Performs the HTTP GET request in a separate thread via `Gio.Task`.

        This method orchestrates the HTTP request by:
        1.  Preparing headers using `_prepare_request_headers`.
        2.  Configuring a `requests.Session` with a `CustomDNSAdapter` for custom DNS
            resolution and SNI handling.
        3.  Executing the request using `_execute_http_request`.
        4.  Processing the response (including redirects and HTTP errors) using `_process_http_response`.

        Exceptions during the request (Timeout, ConnectionError, etc.) are caught and
        reported to the main thread via `task.return_new_error_literal` with an appropriate
        `HttpErrorType` and message.

        Args:
        ----
            task: The `Gio.Task` associated with this asynchronous operation.
            source_object: The source object that initiated the task (unused).
            task_data_arg: Task-specific data (unused, uses `self._http_task_data_for_thread`).
            cancellable: A `Gio.Cancellable` object to monitor for cancellation requests.

        """
        # Retrieve task data set in _on_entry_row_activated
        current_task_data = self._http_task_data_for_thread
        url_to_fetch: str = current_task_data["url"]
        use_akamai_pragma: bool = current_task_data["use_akamai_pragma"]
        host_header_from_input: Optional[str] = current_task_data.get("host_header")
        user_agent: Optional[str] = current_task_data.get("user_agent")
        custom_dns_server: Optional[str] = current_task_data.get("custom_dns_server")

        # 1. Prepare headers
        initial_request_specific_headers, session_headers = self._prepare_request_headers(
            use_akamai_pragma, host_header_from_input, user_agent
        )

        # 2. Configure session with CustomDNSAdapter
        session = requests.Session()

        parsed_url = requests.utils.urlparse(url_to_fetch)
        url_hostname = parsed_url.hostname or "" # Ensure str, not None

        # Determine if the URL's hostname part is already an IP address
        is_url_direct_ip = False
        if url_hostname:
            try:
                socket.inet_pton(socket.AF_INET, url_hostname)
                is_url_direct_ip = True
            except socket.error:
                try:
                    socket.inet_pton(socket.AF_INET6, url_hostname)
                    is_url_direct_ip = True
                except socket.error:
                    is_url_direct_ip = False

        # SNI hint for the adapter if the URL is an IP and a Host header is provided
        adapter_sni_hint: Optional[str] = None
        if is_url_direct_ip and host_header_from_input:
            adapter_sni_hint = host_header_from_input
            logger.info(
                "HttpPage: URL '%s' is IP-based and Host header '%s' provided. Passing as SNI hint to CustomDNSAdapter.",
                 url_to_fetch, adapter_sni_hint
            )

        # Use custom_dns_server only if dnspython (dns module) is available
        effective_custom_dns_server = custom_dns_server if dns else None
        if custom_dns_server and not dns: # Log if configured but unavailable
            logger.warning(
                "HttpPage._fetch_headers_task_thread_func: Custom DNS server ('%s') configured, "
                "but dnspython library is missing. Adapter will use system DNS.", custom_dns_server
            )

        # Instantiate and mount the CustomDNSAdapter
        adapter = CustomDNSAdapter(
            custom_dns_server=effective_custom_dns_server,
            default_sni=adapter_sni_hint # Used by adapter if URL is IP
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        logger.info(
            "HttpPage: CustomDNSAdapter mounted (DNS server: '%s', SNI hint for IP URL: '%s').",
            effective_custom_dns_server, adapter_sni_hint
        )

        if session_headers: # Apply session-wide headers (User-Agent, Pragma)
            session.headers.update(session_headers)

        # 3. Execute request and process response
        try:
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.CANCELLED.value,
                    "Task was cancelled before request execution.",
                )
                return

            # Core request execution
            response = self._execute_http_request(
                session, url_to_fetch, initial_request_specific_headers, cancellable
            )

            # Process response (handles redirects and HTTP errors)
            processed_data = self._process_http_response(response, task, url_to_fetch)
            if processed_data: # If no HTTPError occurred (error not set on task by _process_http_response)
                task.return_value(processed_data)
            # If processed_data is None, an error was already set on the task by _process_http_response

        except requests.exceptions.Timeout as e:
            logger.warning("Task thread: Timeout for URL '%s': %s", url_to_fetch, e)
            error_message = (
                "Request timed out. This could be due to a slow network, server issues, "
                "or a Web Application Firewall (WAF) interfering. "
                "Please check the URL or try again later."
            )
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.TIMEOUT.value, error_message
            )
        except requests.exceptions.ConnectionError as e:
            logger.warning("Task thread: ConnectionError for URL '%s': %s", url_to_fetch, e)
            custom_msg = self._get_detailed_connection_error_message(e, url_to_fetch)
            error_message = custom_msg or ( # Use detailed message if available
                "A network connection error occurred. Please check your internet connection "
                "and the entered URL, then try again."
            )
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CONNECTION_ERROR.value, error_message
            )
        except requests.exceptions.RequestException as e: # Catch other requests-related errors
            logger.warning("Task thread: RequestException for URL '%s': %s", url_to_fetch, e)
            error_message = (f"The request could not be completed due to an issue: {e}. "
                             "Please verify the URL and your network connection.")
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.REQUEST_EXCEPTION.value, error_message
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("Task thread: Truly unexpected error during HTTP fetch for URL '%s':", url_to_fetch)
            error_message = ("An unexpected internal error occurred while processing your request. "
                             "Please try again later or report this issue if it persists.")
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, error_message
            )
        # No 'finally' block for unpatching socket needed anymore due to adapter pattern.

    def _execute_http_request(
        self,
        session: requests.Session,
        url_to_fetch: str,
        initial_request_headers: dict[str, str],
        cancellable: Optional[Gio.Cancellable],
    ) -> requests.Response:
        """Executes the HTTP GET request using the provided session and headers.

        Handles pre-request cancellation check. Network-related exceptions
        (Timeout, ConnectionError, other RequestException) are expected to be
        caught by the caller (`_fetch_headers_task_thread_func`).

        Args:
        ----
            session: The configured `requests.Session` to use for the request.
            url_to_fetch: The URL to fetch.
            initial_request_headers: Headers specific to this initial request (e.g., Host).
                                     These are passed directly to `session.get()`.
            cancellable: `Gio.Cancellable` object to check for cancellation.

        Returns:
        -------
            `requests.Response` object from `session.get()`.

        Raises:
        ------
            requests.exceptions.RequestException: If cancelled before sending, or for other
                                                 `requests` library issues (Timeout, ConnectionError, etc.).
                                                 HTTPError is not raised here but handled by `_process_http_response`.

        """
        logger.info("Executing HTTP GET request to %s via configured session.", url_to_fetch)
        if cancellable and cancellable.is_cancelled():
            # This exception will be caught by the RequestException handler in the calling function.
            raise requests.exceptions.RequestException("Request cancelled by user before sending.")

        response = session.get(
            url_to_fetch,
            headers=(initial_request_headers if initial_request_headers else None),
            allow_redirects=True, # Allow requests to handle redirects automatically
            timeout=5, # Standard timeout for the request in seconds
        )
        # HTTPError (4xx/5xx) is not raised here; _process_http_response will handle it.
        return response

    def _process_http_response(
        self, response: requests.Response, task: Gio.Task, url_being_fetched: str
    ) -> Optional[list[dict[str, object]]]:
        """Processes the HTTP response, including redirects, and checks for HTTP errors.

        If an `requests.exceptions.HTTPError` (4xx or 5xx status code) occurs on the
        final response, this method sets an error on the `Gio.Task` and returns `None`.
        Otherwise, it compiles a list of dictionaries containing data for each response
        in the redirect chain and the final response.

        Args:
        ----
            response: The final `requests.Response` object from `session.get()`.
            task: The `Gio.Task` to set errors on if an HTTPError occurs.
            url_being_fetched: The original URL that was fetched, for logging context.

        Returns:
        -------
            A list of response data dictionaries if successful. Each dictionary represents
            a response (redirect or final) and includes 'type', 'url', 'status_code',
            and 'headers'. Returns `None` if an HTTPError occurred (error is set on the task).

        """
        all_responses_data: list[dict[str, object]] = []

        # Process redirect history
        for hist_resp in response.history: # response.history contains prior Response objects
            hist_data: dict[str, object] = {
                "type": "redirect",
                "url": str(hist_resp.url),
                "status_code": hist_resp.status_code,
                "headers": {str(k): str(v) for k, v in dict(hist_resp.headers).items()},
            }
            all_responses_data.append(hist_data)

        # Process final response - check for HTTP errors first
        try:
            response.raise_for_status()  # Raises HTTPError for 4xx/5xx status codes
            final_data_type = "final"
        except requests.exceptions.HTTPError as http_err:
            logger.warning("HTTPError for URL '%s': %s", url_being_fetched, http_err)
            error_message = self._format_http_error(http_err)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.HTTP_ERROR.value,
                error_message,
            )
            return None # Indicate error by returning None; task error is set.

        # If no HTTPError, proceed to format final response data
        final_data: dict[str, object] = {
            "type": final_data_type,
            "url": str(response.url),
            "status_code": response.status_code,
            "headers": {str(k): str(v) for k, v in dict(response.headers).items()},
        }
        all_responses_data.append(final_data)
        return all_responses_data


    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:  # pylint: disable=too-many-branches,too-many-statements # noqa: C901
        """Attempt to provide a more specific error message for connection errors.

        Inspects the given exception (and its causes) to identify common patterns
        like 'Connection refused'. If found, it suggests potential causes based on the URL scheme
        (e.g., trying HTTP for an HTTPS site or vice-versa).

        Args:
        ----
            exc: The connection-related exception caught (e.g., `requests.exceptions.ConnectionError`).
            url: The URL that was being accessed.

        Returns:
        -------
            A more specific error message string if a known pattern is matched, otherwise `None`.

        """
        current_exc: Optional[BaseException] = exc # Type hint for clarity
        found_connection_refused = False
        max_depth = 5 # Limit how deep we traverse exception causes

        for _depth in range(max_depth):
            if current_exc is None:
                break

            exc_str = str(current_exc).lower() # For case-insensitive search

            # Check for direct ConnectionRefusedError or specific strings/errno in common exceptions
            if isinstance(current_exc, ConnectionRefusedError):
                found_connection_refused = True
                break
            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                if "connection refused" in exc_str or "errno 111" in exc_str:
                    found_connection_refused = True
                    break
                # Check original_error if present (urllib3 specific)
                if hasattr(current_exc, "original_error"):
                    original_error = getattr(current_exc, "original_error")
                    if isinstance(original_error, ConnectionRefusedError) or \
                       (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111):
                        found_connection_refused = True
                        break
            if isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                # MaxRetryError often wraps NewConnectionError in its 'reason' attribute
                if hasattr(current_exc, "reason") and isinstance(current_exc.reason, urllib3_exceptions.NewConnectionError):
                    reason_exc_str = str(current_exc.reason).lower()
                    if "connection refused" in reason_exc_str or "errno 111" in reason_exc_str:
                        found_connection_refused = True
                        break
                    if hasattr(current_exc.reason, "original_error"):
                        original_error = getattr(current_exc.reason, "original_error")
                        if isinstance(original_error, ConnectionRefusedError) or \
                           (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111):
                            found_connection_refused = True
                            break

            # Generic check in args or string representation (less reliable)
            if any("connection refused" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "connection refused" in exc_str:
                found_connection_refused = True
            if any("errno 111" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "errno 111" in exc_str: # Errno 111 is Connection Refused on Linux
                found_connection_refused = True

            if found_connection_refused: break


            # Traverse to the next exception in the chain (__cause__ or __context__)
            next_exc: Optional[BaseException] = None
            if hasattr(current_exc, "__cause__") and current_exc.__cause__ is not None:
                next_exc = current_exc.__cause__
            elif (
                hasattr(current_exc, "__context__")
                and current_exc.__context__ is not None
                and not getattr(current_exc, "__suppress_context__", False) # Check __suppress_context__
            ):
                next_exc = current_exc.__context__

            if current_exc is next_exc:  # Avoid infinite loops if somehow context is self
                break
            current_exc = next_exc

        if found_connection_refused:
            logger.info("Connection refused condition identified for URL: %s", url)
            parsed_url_scheme = requests.utils.urlparse(url).scheme
            if parsed_url_scheme == "https":
                return (
                    "Connection Refused: The server at the HTTPS URL actively refused the connection. "
                    "This might be an HTTP-only service. Consider trying with 'http://'."
                )
            if parsed_url_scheme == "http":
                return (
                    "Connection Refused: The server at the HTTP URL actively refused the connection. "
                    "The server might only support HTTPS or be down. Consider trying with 'https://'."
                )
            return "Connection Error: The server at the specified URL actively refused the connection."

        return None # No specific detailed message generated

    def _fetch_headers_task_done_cb(self, _source_object, result: Gio.AsyncResult, _user_data):  # pylint: disable=too-many-branches,too-many-statements,unused-argument # noqa: C901
        """Callback executed in the main GTK thread when `_fetch_headers_task_thread_func` completes.

        Processes the results (a list of response data dictionaries) or errors returned
        by the background task. Updates the UI (ColumnView for headers, error banner) accordingly.
        Re-enables UI elements that were disabled during the fetch operation.

        Args:
        ----
            _source_object: The object that initiated the task (HttpPage instance, unused).
            result: A `Gio.AsyncResult` containing the task's outcome (data or error).
            _user_data: User data passed to the callback (unused).

        """
        task_being_processed = self.current_http_task # Get the task we stored

        if task_being_processed is None: # Should ideally not happen if task management is correct
            logger.warning(
                "_fetch_headers_task_done_cb: current_http_task is None. UI state might be inconsistent."
            )
            # Attempt to re-enable UI elements as a fallback
            if hasattr(self, "http_entry_row") and self.http_entry_row and not self.http_entry_row.get_sensitive():
                self.http_entry_row.set_sensitive(True)
            if (hasattr(self, "http_apply_button") and self.http_apply_button and
                    not self.http_apply_button.get_sensitive()):
                self.http_apply_button.set_sensitive(True)
                self.http_apply_button.set_icon_name("") # Icon cleared with empty string
            return

        self.current_http_task = None
        logger.info("Processing task completion in _fetch_headers_task_done_cb.")

        try:
            # Propagate the result. This will raise GLib.Error if the task returned an error.
            actual_list_of_responses = task_being_processed.propagate_value()

            # Ensure the result is a list, as expected.
            # Gio.Task can sometimes wrap results in a tuple if multiple values are returned,
            # or a GObject.Value. We expect a direct list from our thread function.
            if not isinstance(actual_list_of_responses, list) and isinstance(actual_list_of_responses, tuple):
                if len(actual_list_of_responses) > 0 and isinstance(actual_list_of_responses[0], list):
                    actual_list_of_responses = actual_list_of_responses[0] # Extract if wrapped
                elif hasattr(actual_list_of_responses, "value") and isinstance(actual_list_of_responses.value, list):
                    actual_list_of_responses = actual_list_of_responses.value # Extract from GObject.Value

            if isinstance(actual_list_of_responses, list):
                logger.info("Successfully processed task result: %d response stages.", len(actual_list_of_responses))
                processed_headers_for_store: list[HeaderItem] = []
                if not actual_list_of_responses:
                    logger.info("Received empty list of responses (e.g. no redirects and no final data).")
                    self._update_column_view_model(None)
                else:
                    for i, response_data_dict in enumerate(actual_list_of_responses):
                        if not isinstance(response_data_dict, dict):
                            logger.error(
                                "Expected dict item in response list, got %s. Data: %s",
                                type(response_data_dict), response_data_dict,
                            )
                            continue # Skip malformed item

                        url_display = f"URL: {response_data_dict.get('url', 'N/A')}"
                        status_code = response_data_dict.get('status_code', 'N/A')
                        response_type = response_data_dict.get("type", "unknown")
                        status_display = f"Status: {status_code} ({response_type.capitalize()})"

                        processed_headers_for_store.append(
                            HeaderItem(key=url_display, value=status_display, is_special_row=True)
                        )

                        headers_for_this_response = response_data_dict.get("headers", {})
                        if isinstance(headers_for_this_response, dict):
                            for key, value in headers_for_this_response.items():
                                processed_headers_for_store.append(
                                    HeaderItem(key=str(key), value=str(value), is_special_row=False)
                                )
                        else:
                            logger.warning("Headers data for response stage %d is not a dict: %s",
                                           i, headers_for_this_response)


                        if i < len(actual_list_of_responses) - 1:
                            processed_headers_for_store.append(
                                HeaderItem(key="--- Redirected To ---", value="", is_special_row=True)
                            )

                    self._current_header_items = processed_headers_for_store
                    self._update_column_view_model(processed_headers_for_store)
                if hasattr(self, "http_entry_row") and self.http_entry_row:
                    self.http_entry_row.remove_css_class("error") # Clear error style from entry if successful
            else:
                # This case indicates an unexpected result type from the task.
                logger.error(
                    "Result data from task is of unexpected type %s. Expected list. Data: %s",
                    type(actual_list_of_responses), actual_list_of_responses
                )
                self._display_error(
                    f"Failed to process task result (unexpected data structure: "
                    f"{type(actual_list_of_responses).__name__}). Please check logs."
                )
                if hasattr(self, "http_entry_row") and self.http_entry_row:
                    self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)

        except GLib.Error as e: # Errors set by task.return_new_error_literal land here
            logger.warning(
                "Task failed with GLib.Error (Domain: %s, Code: %s, Message: %s)",
                e.domain, e.code, e.message
            )
            # Sanitize error message for display (remove potential Pango markup if simple text is desired)
            display_message = e.message.replace("<b>", "").replace("</b>", "") if e.message else "An unknown error occurred."
            self._display_error(display_message)
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.add_css_class("error") # Style entry as erroneous
            self._update_column_view_model(None)
        except Exception as e:  # Catch any other Python exceptions during result processing
            logger.exception("Unexpected Python error in _fetch_headers_task_done_cb:")
            error_message = "An unexpected application error occurred while displaying results."
            # Try to get a more specific message if available
            if hasattr(e, 'message') and e.message: # Check for a message attribute
                error_message = str(e.message)
            elif str(e): # Fallback to string representation of the exception
                error_message = str(e)

            self._display_error(error_message)
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        finally:
            # Ensure UI elements are re-enabled regardless of success or failure
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.set_sensitive(True)
            if hasattr(self, "http_apply_button") and self.http_apply_button:
                self.http_apply_button.set_sensitive(True)
                self.http_apply_button.set_icon_name("") # Remove spinner icon

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        """Ensure the URL has a scheme (defaults to https if missing).

        Args:
        ----
            url: The input URL string.

        Returns:
        -------
            The URL string with "https://" prepended if no scheme was present.

        """
        parsed_url = requests.utils.urlparse(url)
        if not parsed_url.scheme:
            logger.debug("URL '%s' has no scheme, prepending 'https://'.", url)
            return "https://" + url
        return url

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """Validate if a string is a well-formed HTTP or HTTPS URL.

        Checks for a scheme, a netloc (domain/IP), and uses a regex for overall structure.

        Args:
        ----
            url: The URL string to validate.

        Returns:
        -------
            True if the URL is considered valid, False otherwise.

        """
        # Regex for basic URL structure (scheme, authority, path/query/fragment)
        # Allows for IP addresses (IPv4 and IPv6), hostnames, optional ports.
        url_regex = re.compile(
            r"^(?:http|https)://"  # Scheme: http or https
            r"(?:\S+(?::\S*)?@)?"  # Optional user:pass@
            r"(?:(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,6}|"  # Hostname
            r"localhost|"  # localhost
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|"  # IPv4 address
            r"\[?[A-Fa-f0-9:.]+\]?)"  # IPv6 address (basic check, allows surrounding brackets)
            r"(?::\d+)?"  # Optional port
            r"(?:/?|[/?]\S+)$",  # Optional path, query, fragment
            re.IGNORECASE,
        )
        # Additionally, ensure netloc is present after parsing, as regex might be too lenient alone.
        return bool(re.match(url_regex, url)) and bool(requests.utils.urlparse(url).netloc)

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        """Format an `requests.exceptions.HTTPError` into a user-friendly message.

        Provides specific messages for common HTTP error codes (403, 404, 500)
        and a generic message for other HTTP errors.

        Args:
        ----
            e: The `requests.exceptions.HTTPError` instance.

        Returns:
        -------
            A user-friendly error message string.

        """
        status_code = e.response.status_code
        reason = e.response.reason if e.response.reason else "Unknown Error"
        if status_code == 403:
            return f"403 Forbidden: Access to the requested resource at {e.request.url} is denied."
        if status_code == 404:
            return f"404 Not Found: The requested resource at {e.request.url} was not found on the server."
        if status_code == 500:
            return f"500 Internal Server Error: The server encountered an internal error for {e.request.url}."
        return f"HTTP Error {status_code} ({reason}) for URL: {e.request.url}."

    def _on_pragma_toggled(
        self,
        _widget: Gtk.Switch, # The Gtk.Switch that was toggled (unused)
        _gparam: GObject.ParamSpec, # The GObject.ParamSpec of the 'active' property (unused)
    ) -> None:
        """Handle the toggle event for the Akamai Pragma switch.

        If the URL entry row is not empty, this method re-triggers the header fetch
        to reflect the new Pragma header state.

        Args:
        ----
            _widget: The Gtk.Switch that was toggled.
            _gparam: The GObject.ParamSpec of the 'active' property.

        """
        logger.debug("Akamai Pragma toggled to: %s. Re-fetching if URL is present.",
                     self.http_pragma_switch_row.get_active())
        if self.http_entry_row.get_text().strip(): # Only re-fetch if there's a URL
            self._on_entry_row_activated(self.http_entry_row) # Pass any widget, it's unused by handler

    def _update_column_view_model(self, header_items: Optional[List['HeaderItem']]) -> None: # Forward reference for HeaderItem
        """Update the Gtk.ColumnView's model (`Gio.ListStore`) with new header items.

        Clears existing items and populates the ListStore with the provided list.
        Shows or hides the results group (`http_results_group`) based on whether
        `header_items` are present.

        Args:
        ----
            header_items: A list of `HeaderItem` objects to display, or `None` to clear the view.

        """
        self.header_list_store.remove_all() # Clear previous items

        if header_items:
            for item in header_items:
                self.header_list_store.append(item)
            self._show_results() # Make results group visible
        else:
            self._hide_results() # Hide results group if no items

    def _show_results(self):
        """Make the HTTP results group visible."""
        if self.http_results_group: # Check if it exists
            self.http_results_group.set_visible(True)

    def _hide_results(self):
        """Make the HTTP results group invisible."""
        if self.http_results_group: # Check if it exists
            self.http_results_group.set_visible(False)

    def _display_error(self, message: str) -> None:
        """Display an error message in the main window's error banner.

        Also adds an 'error' CSS class to the URL entry row and hides any existing results.

        Args:
        ----
            message: The error message string to display.

        """
        main_window = self.get_native() # Get the top-level window (WoesWindow)
        if main_window and hasattr(main_window, 'show_error'):
            main_window.show_error(message) # Call show_error method on WoesWindow
        else:
            # Fallback or log if WoesWindow or show_error is not found
            logger.warning("Could not find main window or its 'show_error' method to display: %s", message)

        if self.http_entry_row: # Check if it exists
            self.http_entry_row.add_css_class("error")
        self._hide_results() # Hide results section when an error occurs

    def _clear_error(self) -> None:
        """Clear any displayed error messages from the main window's banner.

        Also removes the 'error' CSS class from the URL entry row.
        """
        main_window = self.get_native() # Get the top-level window
        if main_window and hasattr(main_window, 'hide_error'):
            main_window.hide_error() # Call hide_error method on WoesWindow
        else:
            logger.warning("Could not find main window or its 'hide_error' method to clear error.")

        if self.http_entry_row: # Check if it exists
            self.http_entry_row.remove_css_class("error")


    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle click of the 'Clear Results' button.

        Clears current header items from the internal list and the view model,
        clears any displayed errors, and resets the URL entry row text.

        Args:
        ----
            _button: The Gtk.Button that was clicked (unused).

        """
        logger.info("Results cleared by user action.")
        self._current_header_items = [] # Clear internal cache of items
        self._update_column_view_model(None) # Clear the ListStore and hide results view
        self._clear_error() # Clear any error banners/styles
        if self.http_entry_row: # Check if it exists
            self.http_entry_row.set_text("") # Clear URL entry

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        """Handle changes to GSettings related to HTTP output colors.

        Updates internal color attributes (`_header_key_color`, etc.) with the new
        values from settings. If results are currently displayed, it re-populates
        the `Gtk.ColumnView` to apply the new colors immediately.

        Args:
        ----
            settings: The `Gio.Settings` object that changed.
            key: The name of the GSetting key that changed.

        """
        logger.debug("Color setting changed for GSettings key: %s", key)
        if key == "http-output-header-key-color":
            self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color":
            self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color":
            self._special_row_color = settings.get_string(key)

        # If results are currently displayed, re-bind them to update colors
        if self._current_header_items:
            logger.debug("Re-populating ColumnView to apply new color changes.")
            self._update_column_view_model(self._current_header_items) # This re-triggers bind

    def _update_user_agent_model(self) -> None:
        """Update the User-Agent dropdown (Adw.ComboRow) model.

        Clears and repopulates the User-Agent title-to-value map (`_ua_title_to_value_map`)
        and the dropdown's `Gtk.StringList` model. The order is:
        1. Custom User-Agents from GSettings.
        2. A "None" option (representing no override / default `requests` UA).
        3. Default User-Agents from `constants.USER_AGENTS`.

        Preserves the currently selected User-Agent if possible after rebuilding the list.
        """
        if not self.http_user_agent_row: # Should not happen if UI is built correctly
            logger.error("HttpPage._update_user_agent_model: http_user_agent_row is None.")
            return

        self._ua_title_to_value_map.clear()  # Clear internal mapping

        current_selection_text: Optional[str] = None
        # Preserve current selection text if an item is selected
        if (
            self.http_user_agent_row.get_model() and # Check model exists
            self.http_user_agent_row.get_selected() != Gtk.INVALID_LIST_POSITION
        ):
            selected_item = self.http_user_agent_row.get_selected_item()
            if isinstance(selected_item, Gtk.StringObject):
                current_selection_text = selected_item.get_string()

        display_titles: list[str] = []

        # 1. Add Custom User-Agents from GSettings
        variant = self.settings.get_value("custom-user-agents")
        # Ensure variant is not None and is of the correct type 'a(ss)' (array of string pairs)
        custom_ua_pairs: list[tuple[str,str]] = list(variant.unpack() if variant and variant.get_type_string() == "a(ss)" else [])

        for title, value in custom_ua_pairs:
            if title not in self._ua_title_to_value_map: # Avoid duplicate titles if somehow in GSettings
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        # 2. Add "None" option (to use default requests UA)
        none_title = "None"
        if none_title not in self._ua_title_to_value_map: # Ensure "None" title is unique
            display_titles.append(none_title)
        # Always map the "None" title to a None value, signifying no override.
        self._ua_title_to_value_map[none_title] = None

        # 3. Add Default User-Agents from constants.USER_AGENTS
        for ua_dict in USER_AGENTS: # USER_AGENTS is a list of dicts
            title = ua_dict.get("title")
            value = ua_dict.get("value")
            if title and value: # Ensure both title and value exist
                if title not in self._ua_title_to_value_map:  # Add only if title not used by custom or "None"
                    display_titles.append(title)
                    self._ua_title_to_value_map[title] = value

        # Set the new model for the ComboRow
        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles))

        # Restore selection if possible
        if current_selection_text and current_selection_text in display_titles:
            try:
                idx = display_titles.index(current_selection_text)
                self.http_user_agent_row.set_selected(idx)
            except ValueError: # Should not happen if current_selection_text is in display_titles
                logger.warning("Error restoring User-Agent selection: '%s' not in new list.", current_selection_text)
                if display_titles: self.http_user_agent_row.set_selected(0) # Default to first
        elif display_titles: # If no prior selection or prior selection not found, select first item
            self.http_user_agent_row.set_selected(0)

        # After updating model, also update visual cue for active override
        self._on_user_agent_changed(self.http_user_agent_row, None)


        logging.info(
            "User-Agent dropdown model updated with %d titles (Custom -> None -> Default order).",
            len(display_titles)
        )

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """Create a Gtk.SignalListItemFactory for a column in the Gtk.ColumnView.

        This factory is responsible for setting up Gtk.Label widgets and binding them
        to display `HeaderItem` data (either 'key' or 'value' attribute).
        It applies custom colors based on GSettings and whether the row is a special
        informational row (like URL/Status) or a standard HTTP header.
        Text wrapping is enabled for the 'value' column.

        Args:
        ----
            attr_name: The attribute name of `HeaderItem` to display (e.g., "key", "value").
            wrap_text: Whether the text in the label should be wrapped (True for "value" column).

        Returns:
        -------
            A configured `Gtk.SignalListItemFactory`.

        """
        factory = Gtk.SignalListItemFactory()

        # Setup function: Called once per list item when it's created.
        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Setup function for the list item factory. Creates and sets a Gtk.Label as child."""
            label = Gtk.Label(xalign=0.0) # Align text to the left
            label.set_hexpand(True) # Allow label to expand horizontally
            if wrap_text: # For "value" column
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR) # Wrap at word or char boundaries
                label.set_max_width_chars(80) # Hint for max width before wrapping (approx)
            list_item.set_child(label)

        # Bind function: Called when an item needs to be displayed or re-displayed.
        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Bind function for the list item factory. Sets label text and Pango markup."""
            label = list_item.get_child()
            item = list_item.get_item() # This is a HeaderItem instance

            if not (isinstance(label, Gtk.Label) and isinstance(item, HeaderItem)):
                if isinstance(label, Gtk.Label): # If label exists but item is wrong type
                    label.set_text("Error: Invalid item type in ColumnView.")
                return

            text_to_display = getattr(item, attr_name, "") # Get "key" or "value" from HeaderItem

            # Apply Pango markup for styling
            if item.is_special_row: # For URL/Status rows or separators
                if attr_name == "key": # Primary text for special rows is in 'key'
                    key_text = GLib.markup_escape_text(str(item.key))
                    value_text = GLib.markup_escape_text(str(item.value)) if item.value else ""
                    # Combine key and value for display in the 'key' column's label for special rows
                    full_text = key_text
                    if value_text.strip() and value_text != "N/A": # Append value if meaningful
                        full_text += f" {value_text}"

                    # Use bold and special row color
                    label.set_markup(f"<b><span foreground='{self._special_row_color}'>{full_text}</span></b>")
                else: # 'value' column for special rows is usually empty or handled by 'key'
                    label.set_markup("") # Clear any previous markup
            else: # For standard HTTP header rows
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(str(text_to_display))
                # Use bold and specific color for key/value
                label.set_markup(f"<b><span foreground='{color_to_use}'>{escaped_text}</span></b>")

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)

        return factory

    def trigger_fetch(self):
        """Programmatically triggers the 'Fetch' action.

        This method is typically called by a global action/shortcut.
        It simulates a click on the fetch button.
        """
        logging.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.clicked()
        else:
            logging.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")

[end of src/http_page.py]
