import logging
import re
from typing import Optional

import requests
import requests.utils # For urlparse
from enum import Enum # Added for HttpErrorType
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GObject, Gtk, GLib

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


from .constants import RESOURCE_PREFIX, USER_AGENTS

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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task = None

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

    def _connect_signals(self) -> None:
        self.http_entry_row.connect(
            "entry-activated", self._on_entry_row_activated
        )
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect(
            "notify::active", self._on_pragma_toggled
        )

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None: # Parameter renamed
        original_url = self.http_entry_row.get_text().strip() # Changed to self.http_entry_row
        url = self._ensure_scheme(original_url)
        logger.info("Fetching headers for URL: %s (original: %s)", url, original_url)

        if not self._is_valid_url(url):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url)
            self._display_error(
                "Invalid URL format: Please enter a valid URL."
            )
            self._update_column_view_model(None)  # Clear previous results
            return

        self._clear_error()
        self.http_entry_row.set_sensitive(False)
        # Disable the button too
        if hasattr(self, 'http_apply_button'):
            self.http_apply_button.set_sensitive(False)

        host_header = self.http_host_header_row.get_text().strip()
        selected_ua_index = self.http_user_agent_row.get_selected()
        user_agent = None
        if selected_ua_index > 0:
            user_agent_model = self.http_user_agent_row.get_model()
            user_agent = user_agent_model.get_string(selected_ua_index)

        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header,
            "user_agent": user_agent,
        }
        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None)
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func)

    def _fetch_headers_task_thread_func(self, task: Gio.Task, source_object, task_data: dict, cancellable: Optional[Gio.Cancellable]):
        current_task_data = source_object._http_task_data_for_thread

        url = current_task_data["url"]
        use_akamai_pragma = current_task_data["use_akamai_pragma"]
        host_header = current_task_data.get("host_header")
        user_agent = current_task_data.get("user_agent")

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
            request_headers["Pragma"] = ", ".join(akamai_pragma_directives)

        try:
            if cancellable and cancellable.is_cancelled():
                task.return_error(GLib.Error.new_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Task was cancelled."))
                return

            response = requests.get(url, headers=request_headers, allow_redirects=True, timeout=10)
            # Note: response.raise_for_status() will be called *after* initial response is received.
            # If an HTTPError occurs (4xx, 5xx), it will be caught by the HTTPError block.

            all_responses_data = []

            # Process history (redirects)
            for hist_resp in response.history:
                hist_data = { # Ensure this block is correctly defined
                    'type': 'redirect',
                    'url': str(hist_resp.url),
                    'status_code': hist_resp.status_code,
                    'headers': {str(k): str(v) for k, v in dict(hist_resp.headers).items()}
                }
                all_responses_data.append(hist_data)

            # Try to process the final response and its status
            try:
                response.raise_for_status()  # Check for 4xx/5xx errors
                final_data_type = 'final'    # Set if no HTTPError
            except requests.exceptions.HTTPError as http_err:
                # This block is entered if response.status_code is an HTTP error (4xx or 5xx)
                logger.warning("Task thread: HTTPError for %s: %s", url, http_err)
                error_message = self._format_http_error(http_err)
                task.return_error(GLib.Error.new_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.HTTP_ERROR.value, error_message))
                return # Exit the function after reporting the error

            # This part is reached ONLY if response.raise_for_status() did NOT raise an exception
            final_data = {
                'type': final_data_type,
                'url': str(response.url),
                'status_code': response.status_code,
                'headers': {str(k): str(v) for k, v in dict(response.headers).items()}
            }
            all_responses_data.append(final_data)

            task.return_value(all_responses_data) # Success: return all collected data
            # No explicit return needed here if this is the end of the main try's success path

        except requests.exceptions.Timeout as e:
            logger.warning("Task thread: Timeout for %s: %s", url, e)
            task.return_error(GLib.Error.new_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.TIMEOUT.value, "Request timed out. This could be due to a slow network, server issues, or a Web Application Firewall (WAF) interfering. Please check the URL or try again later."))
        # HTTPError is handled above for the final response. If requests.get itself raises one for a redirect, it's caught by RequestException.
        except requests.exceptions.ConnectionError as e:
            logger.warning("Task thread: ConnectionError for %s: %s", url, e)
            custom_msg = self._get_detailed_connection_error_message(e, url)
            error_message = custom_msg if custom_msg else f"Connection Error: {str(e)}"
            if not str(e) and not custom_msg: # Ensure some message is shown
                 error_message = "Connection Error: Failed to establish a connection."
            task.return_error(GLib.Error.new_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CONNECTION_ERROR.value, error_message))
        except requests.exceptions.RequestException as e: # Catches other requests errors like TooManyRedirects, etc.
            logger.warning("Task thread: RequestException for %s: %s", url, e)
            task.return_error(GLib.Error.new_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.REQUEST_EXCEPTION.value, f"Request Error: {str(e)}"))
        except Exception as e:
            logger.error("Task thread: Truly unexpected error for %s: %s", url, e, exc_info=True)
            task.return_error(GLib.Error.new_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, f"An unexpected error occurred: {str(e)}"))


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

            exc_type_name = type(current_exc).__name__
            exc_args_str = str(current_exc.args) if hasattr(current_exc, 'args') else "N/A"
            exc_str = str(current_exc)

            logger.debug("Inspecting exception at depth %d: Type=%s, Args=%s, Str=%s",
                         depth, exc_type_name, exc_args_str, exc_str)

            # Check for ConnectionRefusedError explicitly
            if isinstance(current_exc, ConnectionRefusedError):
                logger.debug("Direct ConnectionRefusedError found: %s", current_exc)
                found_connection_refused = True
                break  # Found the most specific type

            # Check for urllib3.exceptions common in requests
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
                    reason_exc_type_name = type(reason_exc).__name__
                    reason_exc_str = str(reason_exc)
                    logger.debug(
                        "Inspecting MaxRetryError.reason: Type=%s, Str=%s", reason_exc_type_name, reason_exc_str
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
        # Ensure _http_task_data_for_thread is accessed from source_object if needed for URL in ConnectionError
        # However, it's better if the URL is part of the exception or GLib.Error data.
        # For now, we'll try to get it from the instance if an error occurs that needs it.
        url_for_error_reporting = ""
        if hasattr(self, '_http_task_data_for_thread') and self._http_task_data_for_thread:
            url_for_error_reporting = self._http_task_data_for_thread.get('url', '')


        try:
            actual_list_of_responses = local_task_ref.propagate_value()

            # Attempt to handle _ResultTuple if it appears for successful calls
            # This is speculative and might need adjustment based on actual _ResultTuple structure
            if not isinstance(actual_list_of_responses, list) and isinstance(actual_list_of_responses, tuple):
                logger.warning(f"Received a tuple {type(actual_list_of_responses)} instead of a list for success value. Trying to extract from it.")
                if len(actual_list_of_responses) > 0 and isinstance(actual_list_of_responses[0], list):
                    actual_list_of_responses = actual_list_of_responses[0]
                # Add more checks if _ResultTuple has a known structure, e.g. by name if it's a namedtuple
                elif hasattr(actual_list_of_responses, 'value') and isinstance(actual_list_of_responses.value, list): # Example if it's an object
                     actual_list_of_responses = actual_list_of_responses.value


            if isinstance(actual_list_of_responses, list):
                logger.info("Successfully processed task result as list.")
                processed_headers_for_store = []
                if not actual_list_of_responses:
                    logger.warning("Received empty list for actual_list_of_responses.")
                    self._update_column_view_model(None) # Clear view if empty results
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

                        if i < len(actual_list_of_responses) - 1: # Add spacer between responses
                            processed_headers_for_store.append(HeaderItem(key="", value="", is_special_row=True))
                    self._update_column_view_model(processed_headers_for_store)
                self.http_entry_row.remove_css_class("error")
            else:
                # This case should ideally not be hit if success means a list and errors are exceptions.
                logger.error(
                    f"Task result data of unexpected type {type(actual_list_of_responses)}. Expected list."
                )
                self._display_error(
                    f"Failed to process task result data (unexpected data structure: {type(actual_list_of_responses).__name__})."
                )
                self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)

        except GLib.Error as e:
            logger.error("Task failed with GLib.Error: Domain=%s, Code=%s, Message=%s", e.domain, e.code, e.message)
            # You can use e.code and e.domain to show more specific user messages if desired
            # For now, e.message should contain the message from the thread.
            self._display_error(e.message.replace("<b>", "").replace("</b>", "")) # Sanitize markup
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        except Exception as e: # Fallback for any other unexpected error in the callback itself
            logger.error("Unexpected Python error in _fetch_headers_task_done_cb: %s", e, exc_info=True)
            self._display_error(f"An unexpected application error occurred: {str(e)}")
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        finally:
            self.http_entry_row.set_sensitive(True)
            if hasattr(self, 'http_apply_button'): # Check if button exists
                self.http_apply_button.set_sensitive(True)
            if self.current_http_task is local_task_ref: # Check if this task is still the current one
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
        self.header_list_store.remove_all()  # Clear existing items

        if header_items:  # Check if list is not None and not empty implicitly
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
        self._hide_results()  # Hide results on error

    def _clear_error(self) -> None:
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")
        self.http_entry_row.remove_css_class("error")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        self._clear_error()

    def _on_clear_results_clicked(self, _button: Gtk.Button, *_args):
        logger.info("Results cleared by user.")
        self._update_column_view_model(None)
        self._clear_error()
        self.http_entry_row.set_text("")


    @staticmethod
    def _create_factory(
        attr_name: str, wrap_text: bool = False
    ) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()

        def setup_func(_, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0)
            label.set_hexpand(True)
            if wrap_text:
                label.set_wrap(True)
                label.set_max_width_chars(80)
            list_item.set_child(label)

        def bind_func(_, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            item = list_item.get_item()
            text_to_display = getattr(item, attr_name, "")
            if label:
                if item and item.is_special_row:
                    escaped_text = GLib.markup_escape_text(text_to_display)
                    label.set_markup(f"<b>{escaped_text}</b>")
                else:
                    label.set_text(text_to_display)

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)

        return factory