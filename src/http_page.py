from .constants import RESOURCE_PREFIX, USER_AGENTS, APP_ID
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk
import logging
import re
from typing import Optional

import requests
import requests.utils  # For urlparse
from enum import Enum  # Added for HttpErrorType
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')

try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None # type: ignore
    logging.warning("dnspython library not found. Custom DNS functionality will be disabled.")


# Attempt to import urllib3 exceptions from requests, which vendors it.
try:
    from requests.packages.urllib3 import exceptions as urllib3_exceptions
except ImportError:
    # Fallback if requests changes its vendoring structure or for older versions
    try:
        import urllib3.exceptions as urllib3_exceptions
    except ImportError:
        # If urllib3 is not available at all (should not happen with requests installed)
        # Define dummy classes for isinstance checks to not fail, or handle differently.
        class _DummyUrllib3Exception(Exception):
            pass
        urllib3_exceptions = type('urllib3_exceptions', (), {
            'MaxRetryError': _DummyUrllib3Exception,
            'NewConnectionError': _DummyUrllib3Exception,
            })
        logging.warning("Could not import urllib3.exceptions. Connection refused detection might be limited.")


# Configure logger for the module
logger = logging.getLogger(__name__)

# Define error domain and codes for HTTP operations
WOES_HTTP_ERROR_DOMAIN = "woes-http-error-domain"


class HttpErrorType(int, Enum):
    TIMEOUT = 0
    HTTP_ERROR = 1
    CONNECTION_ERROR = 2
    REQUEST_EXCEPTION = 3
    GENERIC_UNEXPECTED = 4
    CANCELLED = 5


class HeaderItem(GObject.Object):
    key: str
    value: str
    is_special_row: bool

    def __init__(self, key: str, value: str, is_special_row: bool = False):
        super().__init__()
        self.key = key
        self.value = value
        self.is_special_row = is_special_row


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Adw.PreferencesPage):
    __gtype_name__ = "HttpPage"
    http_entry_row = Gtk.Template.Child("http_entry_row")
    http_apply_button = Gtk.Template.Child("http_apply_button")
    http_host_header_row = Gtk.Template.Child("http_host_header_row")
    http_user_agent_row = Gtk.Template.Child("http_user_agent_row")
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    error_banner = Gtk.Template.Child("error_banner")
    http_results_group = Gtk.Template.Child("http_results_group")
    clear_results_button = Gtk.Template.Child("clear_results_button")
    copy_results_button = Gtk.Template.Child() # Added

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task = None
        self._current_header_items = [] # For refreshing view on color change

        self.settings = Gio.Settings(schema_id=APP_ID)
        self._header_key_color = self.settings.get_string("http-output-header-key-color")
        self._header_value_color = self.settings.get_string("http-output-header-value-color")
        self._special_row_color = self.settings.get_string("http-output-special-row-color")

        self.settings.connect(f"changed::http-output-header-key-color", self._on_color_setting_changed)
        self.settings.connect(f"changed::http-output-header-value-color", self._on_color_setting_changed)
        self.settings.connect(f"changed::http-output-special-row-color", self._on_color_setting_changed)

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

        user_agent_options = ["None"] + USER_AGENTS
        self.http_user_agent_row.set_model(Gtk.StringList.new(user_agent_options))
        self.http_user_agent_row.set_selected(0)
        if self.http_apply_button:
            self.http_apply_button.set_use_underline(True)

    def _connect_signals(self) -> None:
        self.http_entry_row.connect(
            "entry-activated", self._on_entry_row_activated
            )
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect(
            "notify::active", self._on_pragma_toggled
            )
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button: # Added
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.error_banner:
            self.error_banner.connect("button-clicked", self._on_error_banner_dismiss)

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Copying all headers to clipboard.")
        lines = []
        if self.header_list_store:
            for i in range(self.header_list_store.get_n_items()):
                item = self.header_list_store.get_item(i)
                if isinstance(item, HeaderItem): # Make sure it's the correct type
                    if item.is_special_row:
                        # For special rows like URL/Status, just join key and value if value exists
                        if item.value and item.value.strip():
                            lines.append(f"{item.key} {item.value}")
                        else:
                            lines.append(item.key)
                    else:
                        lines.append(f"{item.key}: {item.value}")

        if lines:
            text_to_copy = "\n".join(lines)
            try:
                # Gtk.Clipboard.get_default() requires a Gdk.Display argument.
                # self.get_display() should provide it.
                clipboard = Gdk.Display.get_default().get_clipboard() # Corrected way to get clipboard
                if clipboard:
                    clipboard.set_text(text_to_copy) # Corrected: set_text does not take -1
                    logger.info("Headers copied to clipboard successfully.")
                    # Optional: Show a toast notification here
                    # Example: self.show_toast(Adw.Toast(title="Headers copied to clipboard"))
                else:
                    logger.warning("Failed to get default clipboard.")
            except Exception as e:
                logger.error(f"Error copying to clipboard: {e}", exc_info=True)
        else:
            logger.info("No headers to copy.")

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        original_url = self.http_entry_row.get_text().strip()
        url = self._ensure_scheme(original_url)
        logger.info("Fetching headers for URL: %s (original: %s)", url, original_url)

        if not self._is_valid_url(url):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url)
            self._display_error(
                "Invalid URL format: Please enter a valid URL."
                )
            self._update_column_view_model(None)
            return

        self._clear_error()
        self.http_entry_row.set_sensitive(False)
        if hasattr(self, 'http_apply_button'):
            self.http_apply_button.set_sensitive(False)

        host_header = self.http_host_header_row.get_text().strip()
        selected_ua_index = self.http_user_agent_row.get_selected()
        user_agent = None
        if selected_ua_index > 0:
            user_agent_model = self.http_user_agent_row.get_model()
            user_agent = user_agent_model.get_string(selected_ua_index)

        custom_dns_server = self.settings.get_string("custom-dns-server")

        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header,
            "user_agent": user_agent,
            "custom_dns_server": custom_dns_server,
            }
        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None)
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func)

    def _fetch_headers_task_thread_func(self,
                                        task: Gio.Task,
                                        source_object,
                                        task_data: dict,
                                        cancellable: Optional[Gio.Cancellable]):
        current_task_data = source_object._http_task_data_for_thread

        url_to_fetch = current_task_data["url"] # Initially, this is what we aim for
        original_url_with_scheme = current_task_data["url"] # Keep original for Host header if IP is resolved
        use_akamai_pragma = current_task_data["use_akamai_pragma"]
        host_header_from_input = current_task_data.get("host_header") # User-defined host header
        user_agent = current_task_data.get("user_agent")
        custom_dns_server = current_task_data.get("custom_dns_server")

        session = requests.Session()
        # Apply verify=False to the whole session for this task, as the entire operation
        # is under this "insecure" context (typically for specific self-signed certs or test IPs)
        # session.verify = False # SSL Verification is now ON by default

        initial_request_specific_headers = {} # For Host header mainly
        session_headers = {} # For User-Agent, Pragma
        original_hostname = None


        if custom_dns_server and dns: # Check if dnspython was imported
            try:
                parsed_url_obj = requests.utils.urlparse(original_url_with_scheme)
                original_hostname = parsed_url_obj.hostname
                if not original_hostname:
                    logger.warning(f"Could not parse hostname from URL: {original_url_with_scheme}")
                else:
                    logger.info(f"Attempting to resolve {original_hostname} using custom DNS server {custom_dns_server}")
                    resolver = dns.resolver.Resolver()
                    resolver.nameservers = [custom_dns_server]
                    resolver.timeout = 2.0
                    resolver.lifetime = 2.0

                    answers = resolver.resolve(original_hostname, 'A')
                    if answers:
                        resolved_ip = answers[0].address
                        url_to_fetch = parsed_url_obj._replace(netloc=resolved_ip).geturl()
                        initial_request_specific_headers["Host"] = original_hostname
                        logger.info(
                            f"Resolved {original_hostname} to {resolved_ip} via {custom_dns_server}. "
                            f"New URL for request: {url_to_fetch}. Host header set to: {original_hostname}"
                        )
                    else:
                        logger.warning(
                            f"Custom DNS {custom_dns_server} provided no A records for {original_hostname}. "
                            "Falling back to system DNS / original URL."
                        )
            except dns.exception.DNSException as e:
                logger.warning(
                    f"Custom DNS resolution for {original_hostname} via {custom_dns_server} failed: {e}. "
                    "Falling back to system DNS / original URL."
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error during custom DNS processing for {original_hostname}: {e}", exc_info=True
                )

        if host_header_from_input:
            if "Host" in initial_request_specific_headers:
                 logger.info(
                     f"Custom DNS set Host to {initial_request_specific_headers['Host']}. "
                     f"User input Host '{host_header_from_input}' will be overridden for this IP-based request."
                 )
            else:
                initial_request_specific_headers["Host"] = host_header_from_input
        elif not "Host" in initial_request_specific_headers and original_hostname:
             pass # Logic for this case remains the same as before, handled by requests if no override.

        # Prepare session-level headers
        if user_agent and user_agent != "None":
            session_headers["User-Agent"] = user_agent

        if use_akamai_pragma:
            akamai_pragma_directives = [
                "akamai-x-get-request-id", "akamai-x-get-cache-key", "akamai-x-cache-on",
                "akamai-x-cache-remote-on", "akamai-x-get-true-cache-key", "akamai-x-check-cacheable",
                "akamai-x-get-extracted-values", "akamai-x-feo-trace", "x-akamai-logging-mode: verbose",
            ]
            session_headers["Pragma"] = ", ".join(akamai_pragma_directives)

        if session_headers:
            session.headers.update(session_headers)

        logger.debug(
            "Task thread: Making GET request via session to %s. Akamai Pragma (on session): %s, User-Agent (on session): %s, Initial Specific Headers (Host): %s",
            url_to_fetch,
            "Yes" if use_akamai_pragma else "No", # More concise logging for boolean
            session.headers.get("User-Agent", "Default"), # Get from session directly
            initial_request_specific_headers.get("Host", "Default")
        )

        try:
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.CANCELLED.value,
                    "Task was cancelled."
                )
                return

            # The verify=False is now set on the session (session.verify = False)
            response = session.get(
                url_to_fetch,
                headers=initial_request_specific_headers if initial_request_specific_headers else None,
                allow_redirects=True, # Default for session, but explicit
                timeout=5
            )

            all_responses_data = []

            # Process history (redirects)
            for hist_resp in response.history:
                hist_data = {
                    'type': 'redirect',
                    'url': str(hist_resp.url),
                    'status_code': hist_resp.status_code,
                    'headers': {str(k): str(v) for k, v in dict(hist_resp.headers).items()}
                    }
                all_responses_data.append(hist_data)

            try:
                response.raise_for_status()
                final_data_type = 'final'
            except requests.exceptions.HTTPError as http_err:
                logger.warning("Task thread: HTTPError for %s: %s", url_to_fetch, http_err)
                error_message = self._format_http_error(http_err)
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.HTTP_ERROR.value,
                    error_message
                    )
                return

            final_data = {
                'type': final_data_type,
                'url': str(response.url),
                'status_code': response.status_code,
                'headers': {str(k): str(v) for k, v in dict(response.headers).items()}
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
                error_message
                )
            return
        except requests.exceptions.ConnectionError as e:
            logger.warning("Task thread: ConnectionError for %s: %s", url_to_fetch, e)
            custom_msg = self._get_detailed_connection_error_message(e, url_to_fetch) # Pass the URL that was actually used
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
                error_message
                )
            return
        except requests.exceptions.RequestException as e:
            logger.warning("Task thread: RequestException for %s: %s", url_to_fetch, e)
            error_message = "The request could not be completed. Please verify the URL and your network connection."
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.REQUEST_EXCEPTION.value,
                error_message
                )
            return
        except Exception as e:
            logger.error("Task thread: Truly unexpected error for %s: %s", url_to_fetch, e, exc_info=True)
            error_message = (
                "An unexpected internal error occurred while processing your request. "
                "Please try again later."
            )
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.GENERIC_UNEXPECTED.value,
                error_message
                )
            return

    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:
        """
        Traverses an exception chain to find a "Connection refused" error
        and returns a specific message if it's for an HTTPS URL.
        Otherwise, returns None.
        """
        current_exc = exc
        found_connection_refused = False
        max_depth = 5

        logger.debug("Starting connection error analysis for URL: %s", url)

        for depth in range(max_depth):
            if current_exc is None:
                logger.debug("Reached end of exception chain (current_exc is None) at depth %d.", depth)
                break

            exc_str = str(current_exc)

            logger.debug("Inspecting exception at depth %d: Type=%s, Args=%s, Str=%s",
                         depth, type(current_exc).__name__, str(current_exc.args) if hasattr(current_exc, 'args') else "N/A", exc_str)

            if isinstance(current_exc, ConnectionRefusedError):
                logger.debug("Direct ConnectionRefusedError found: %s", current_exc)
                found_connection_refused = True
                break

            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                logger.debug("urllib3.exceptions.NewConnectionError found: %s", current_exc)
                if "connection refused" in exc_str.lower() or "errno 111" in exc_str.lower():
                    found_connection_refused = True
                    break
                if hasattr(current_exc, 'original_error') and \
                   isinstance(current_exc.original_error, ConnectionRefusedError):
                    logger.debug("Nested ConnectionRefusedError found in NewConnectionError.original_error")
                    found_connection_refused = True
                    break
                if hasattr(current_exc, 'original_error') and \
                   hasattr(current_exc.original_error, 'errno') and \
                   current_exc.original_error.errno == 111:
                    logger.debug("Nested ConnectionRefusedError (errno 111) found in NewConnectionError.original_error")
                    found_connection_refused = True
                    break

            if isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                logger.debug("urllib3.exceptions.MaxRetryError found. Will inspect its reason.")
                if hasattr(current_exc, 'reason') and current_exc.reason is not None:
                    reason_exc = current_exc.reason
                    reason_exc_str = str(reason_exc)
                    logger.debug(
                        "Inspecting MaxRetryError.reason: Type=%s, Str=%s", type(reason_exc).__name__, reason_exc_str
                        )
                    if isinstance(reason_exc, urllib3_exceptions.NewConnectionError):
                        if "connection refused" in reason_exc_str.lower() or \
                           "errno 111" in reason_exc_str.lower():
                            found_connection_refused = True
                            break
                        if hasattr(reason_exc, 'original_error') and \
                           isinstance(reason_exc.original_error, ConnectionRefusedError):
                            found_connection_refused = True
                            break
                        if hasattr(reason_exc, 'original_error') and \
                           hasattr(reason_exc.original_error, 'errno') and \
                           reason_exc.original_error.errno == 111:
                            found_connection_refused = True
                            break

            if any("connection refused" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "connection refused" in exc_str.lower():
                logger.debug("Found 'connection refused' in string representation of current exception or its args.")
                found_connection_refused = True

            if any("errno 111" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "errno 111" in exc_str.lower():
                logger.debug("Found 'errno 111' in string representation of current exception or its args.")
                found_connection_refused = True

            next_exc = None
            if hasattr(current_exc, '__cause__') and current_exc.__cause__ is not None:
                logger.debug("Moving to __cause__: %s", type(current_exc.__cause__).__name__)
                next_exc = current_exc.__cause__
            elif hasattr(current_exc, '__context__') and \
                    current_exc.__context__ is not None and \
                    not current_exc.__suppress_context__:
                logger.debug("Moving to __context__: %s", type(current_exc.__context__).__name__)
                next_exc = current_exc.__context__

            if current_exc is next_exc:
                logger.debug("Next exception is same as current, stopping traversal.")
                break
            current_exc = next_exc

        if found_connection_refused:
            logger.info("Connection refused condition identified for URL: %s", url)
            if requests.utils.urlparse(url).scheme == 'https':
                logger.info("URL is HTTPS and connection was refused. Suggesting HTTP.")
                return (
                    "The URL targetted via HTTPS is refusing the connection. "
                    "It might be an HTTP-only service. Please try with 'http://'."
                    )
            elif requests.utils.urlparse(url).scheme == 'http':
                logger.info("URL is HTTP and connection was refused. Suggesting HTTPS.")
                return (
                    "The HTTP request failed. The server might only support HTTPS for this resource. "
                    "Please try with 'https://'."
                    )
            else:
                logger.info("URL is non-HTTP/HTTPS (or scheme missing) and connection was refused.")
                return "Connection Error: The server at the specified URL actively refused the connection."

        logger.debug("No specific 'Connection refused' condition found that warrants a custom message.")
        return None

    def _fetch_headers_task_done_cb(self, source_object, result: Gio.AsyncResult, user_data):
        local_task_ref = self.current_http_task
        # url_for_error_reporting = "" # F841
        # if hasattr(self, '_http_task_data_for_thread') and self._http_task_data_for_thread:
        #     url_for_error_reporting = self._http_task_data_for_thread.get('url', '') # F841

        try:
            actual_list_of_responses = local_task_ref.propagate_value()

            if not isinstance(actual_list_of_responses, list) and isinstance(actual_list_of_responses, tuple):
                logger.warning((
                    f"Received a tuple {type(actual_list_of_responses)} instead of a list for "
                    f"success value. Trying to extract from it."
                ))
                if len(actual_list_of_responses) > 0 and isinstance(actual_list_of_responses[0], list):
                    actual_list_of_responses = actual_list_of_responses[0]
                elif hasattr(actual_list_of_responses, 'value') and isinstance(actual_list_of_responses.value, list):
                    actual_list_of_responses = actual_list_of_responses.value

            if isinstance(actual_list_of_responses, list):
                logger.info("Successfully processed task result as list.")
                processed_headers_for_store = []
                if not actual_list_of_responses:
                    logger.warning("Received empty list for actual_list_of_responses.")
                    self._update_column_view_model(None)
                else:
                    for i, response_data in enumerate(actual_list_of_responses):
                        url_display = f"URL: {response_data.get('url', 'N/A')}"
                        status_display = f"Status: {response_data.get('status_code', 'N/A')}"

                        if response_data.get('type') == 'redirect':
                            status_display += " (Redirect)"
                        elif response_data.get('type') == 'final':
                            status_display += " (Final)"

                        processed_headers_for_store.append(
                            HeaderItem(key=url_display, value=status_display, is_special_row=True)
                            )

                        headers_for_this_response = response_data.get('headers', {})
                        for header_key, header_value in headers_for_this_response.items():
                            processed_headers_for_store.append(
                                HeaderItem(key=str(header_key), value=str(header_value), is_special_row=False)
                                )

                        if i < len(actual_list_of_responses) - 1:
                            processed_headers_for_store.append(HeaderItem(key="", value="", is_special_row=True))
                    self._current_header_items = processed_headers_for_store # Store for refresh
                    self._update_column_view_model(processed_headers_for_store)
                self.http_entry_row.remove_css_class("error")
            else:
                logger.error(
                    f"Task result data of unexpected type {type(actual_list_of_responses)}. Expected list."
                    )
                self._display_error((
                    f"Failed to process task result data (unexpected data structure: "
                    f"{type(actual_list_of_responses).__name__})."
                ))
                self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)

        except GLib.Error as e:
            logger.error("Task failed with GLib.Error: Domain=%s, Code=%s, Message=%s", e.domain, e.code, e.message)
            self._display_error(e.message.replace("<b>", "").replace("</b>", ""))
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        except Exception as e:
            logger.error("Unexpected Python error in _fetch_headers_task_done_cb: %s", e, exc_info=True)
            self._display_error((
                "An unexpected application error occurred while trying to display the results. "
                "Please try the operation again."
            ))
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        finally:
            self.http_entry_row.set_sensitive(True)
            if hasattr(self, 'http_apply_button'):
                self.http_apply_button.set_sensitive(True)
            if self.current_http_task is local_task_ref:
                self.current_http_task = None

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        parsed_url = requests.utils.urlparse(url)
        if not parsed_url.scheme:
            url = "https://" + url
        return url

    @staticmethod
    def _is_valid_url(url: str) -> bool:
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
        status_code = e.response.status_code
        if status_code == 404:
            return "404 Not Found: The requested URL was not found on this server."
        if status_code == 403:
            return "403 Forbidden: You don't have permission to access this URL."
        if status_code == 500:
            return "500 Internal Server Error: The server encountered an internal error."
        return f"HTTP Error {status_code}: {e.response.reason}."

    def _on_pragma_toggled(
            self, widget: Gtk.Switch, _gparam: GObject.ParamSpec
            ) -> None:
        logger.debug("Akamai Pragma toggled to: %s", widget.get_active())
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[list[HeaderItem]]) -> None:
        self.header_list_store.remove_all()

        if header_items:
            for item in header_items:
                self.header_list_store.append(item)
            self._show_results()
        else:
            self._hide_results()

    def _show_results(self):
        """Show the results group."""
        self.http_results_group.set_visible(True)

    def _hide_results(self):
        """Hide the results group."""
        self.http_results_group.set_visible(False)

    def _display_error(self, message: str) -> None:
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)
        self.http_entry_row.add_css_class("error")
        self._hide_results()

    def _clear_error(self) -> None:
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")
        self.http_entry_row.remove_css_class("error")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        self._clear_error()

    def _on_clear_results_clicked(self, _button: Gtk.Button, *_args):
        logger.info("Results cleared by user.")
        self._current_header_items = [] # Clear stored items
        self._update_column_view_model(None)
        self._clear_error()
        self.http_entry_row.set_text("")

    def _on_color_setting_changed(self, settings, key):
        logger.debug(f"Color setting changed for key: {key}")
        if key == "http-output-header-key-color":
            self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color":
            self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color":
            self._special_row_color = settings.get_string(key)

        if self._current_header_items:
            logger.debug("Re-populating view to apply color changes.")
            self._update_column_view_model(self._current_header_items)

    # Note: Removed @staticmethod decorator
    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()

        def setup_func(_, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0)
            label.set_hexpand(True)
            if wrap_text:
                label.set_wrap(True)
                label.set_max_width_chars(80)
            list_item.set_child(label)

        # This nested function can capture 'self' from the outer _create_factory method
        def bind_func_internal(_, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            item = list_item.get_item()

            if not (label and isinstance(label, Gtk.Label) and item and isinstance(item, HeaderItem)):
                if label and isinstance(label, Gtk.Label):
                    label.set_text("Error: Invalid item or label.") # Basic error display
                return

            text_to_display = getattr(item, attr_name, "")

            if item.is_special_row:
                if attr_name == 'key':
                    key_text = GLib.markup_escape_text(item.key if item.key else "")
                    value_text = GLib.markup_escape_text(item.value if item.value else "")
                    full_text = key_text
                    if value_text.strip():
                        full_text += f" {value_text}"
                    label.set_markup(f"<b><span foreground='{self._special_row_color}'>{full_text}</span></b>")
                else:
                    label.set_markup("")
            else:
                color_to_use = self._header_key_color if attr_name == 'key' else self._header_value_color
                escaped_text = GLib.markup_escape_text(text_to_display)
                label.set_markup(f"<span foreground='{color_to_use}'>{escaped_text}</span>")

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)

        return factory
