import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import requests
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
        class _DummyUrllib3Exception(Exception): pass
        urllib3_exceptions = type('urllib3_exceptions', (), {
            'MaxRetryError': _DummyUrllib3Exception,
            'NewConnectionError': _DummyUrllib3Exception,
        })
        logger.warning("Could not import urllib3.exceptions. Connection refused detection might be limited.")


from .constants import RESOURCE_PREFIX, USER_AGENTS
# Removed Helper import as it's unused
# from .style_utils import set_widget_visibility  # This is no longer needed

# Configure logger for the module
logger = logging.getLogger(__name__)


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
    http_host_header_row = Gtk.Template.Child("http_host_header_row")
    http_user_agent_row = Gtk.Template.Child("http_user_agent_row")
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    error_banner = Gtk.Template.Child("error_banner")
    http_results_group = Gtk.Template.Child("http_results_group")
    # clear_results_button is connected via UI handler

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task = None

        # Initialize ColumnView model and columns here
        self.header_list_store = Gio.ListStore.new(HeaderItem)
        selection_model = Gtk.MultiSelection.new(self.header_list_store)
        self.http_column_view.set_model(selection_model)

        if not self.http_column_view.get_columns():  # Ensure columns are added only once
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

        # Populate User-Agent dropdown
        user_agent_options = ["None"] + USER_AGENTS
        self.http_user_agent_row.set_model(Gtk.StringList.new(user_agent_options))
        self.http_user_agent_row.set_selected(0) # Select "None" by default

    def _connect_signals(self) -> None:
        self.http_entry_row.connect(
            "entry-activated", self._on_entry_row_activated
        )
        self.http_entry_row.connect("apply", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect(
            "notify::active", self._on_pragma_toggled
        )
        # error_banner dismiss is connected in UI
        # clear_results_button click is connected in UI

    def _on_entry_row_activated(self, entry_row: Gtk.Entry) -> None:
        original_url = entry_row.get_text().strip()
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
        # Disable UI elements and show loading state
        self.http_entry_row.set_sensitive(False)
        # You might want to add a spinner here, e.g., self.spinner.start()

        host_header = self.http_host_header_row.get_text().strip()
        selected_ua_index = self.http_user_agent_row.get_selected()
        user_agent = None
        if selected_ua_index > 0: # 0 is "None"
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
        # The problematic task.set_task_data line is now fully removed.
        task.run_in_thread(self._fetch_headers_task_thread_func)

    def _fetch_headers_task_thread_func(self, task: Gio.Task, source_object, task_data: dict, cancellable: Optional[Gio.Cancellable]):
        # 'source_object' is the HttpPage instance (self).
        # The 'task_data' argument in the function signature is likely None or unreliable now.
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
            # Check for cancellation before making the request
            if cancellable and cancellable.is_cancelled():
                g_error = GLib.Error(
                    message="Task was cancelled",
                    domain=Gio.io_error_quark(),
                    code=Gio.IOErrorEnum.CANCELLED.value
                )
                task.return_error(g_error)
                return

            response = requests.get(url, headers=request_headers, allow_redirects=True, timeout=10)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

            all_responses_data = []

            # Process history (redirect responses)
            # response.history is a list of Response objects, from oldest to most recent.
            for hist_resp in response.history:
                hist_data = {
                    'type': 'redirect',
                    'url': str(hist_resp.url), # Ensure URL is string
                    'status_code': hist_resp.status_code,
                    'headers': {str(k): str(v) for k, v in dict(hist_resp.headers).items()}
                }
                all_responses_data.append(hist_data)

            # Process final response (the one not in history)
            final_data = {
                'type': 'final',
                'url': str(response.url), # Ensure URL is string
                'status_code': response.status_code,
                'headers': {str(k): str(v) for k, v in dict(response.headers).items()}
            }
            all_responses_data.append(final_data)

            task.return_value(all_responses_data)
        except requests.exceptions.HTTPError as e:
            # If raise_for_status() was called on the final response and it was an error,
            # we might still want to capture its details if response object 'e.response' exists.
            if e.response is not None:
                logger.error("Task thread: HTTPError for %s: %s. Response was: %s", url, e, e.response.url)
                # Capture the error response like other responses if needed by UI
                # For now, just format the error message as before.
                # Consider if response.history should be processed even on HTTPError for the final response.
                # The current logic will not include it if an HTTPError occurs.
            else:
                logger.error("Task thread: HTTPError for %s: %s. No response object in exception.", url, e)

            # Create a GError for HTTP errors
            # For simplicity, using a generic error domain and code
            # A more robust solution might define a custom error domain
            error_message = self._format_http_error(e)
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error)
            return
        except requests.exceptions.ConnectionError as e:
            logger.warning("Task thread: ConnectionError for %s: %s", url, e, exc_info=True)
            custom_msg = self._get_detailed_connection_error_message(e, url)
            if custom_msg:
                error_message = custom_msg
            else:
                # Generic fallback, but try to get a bit more from the top-level error if possible
                error_message = f"Connection Error: {str(e)}"
                if not str(e): # Handle cases where str(e) might be empty
                     error_message = "Connection Error: Failed to establish a connection."

            safe_error_message = str(error_message) # Ensure it's a string
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error)
            return
        except requests.exceptions.Timeout as e:
            logger.warning("Task thread: Timeout for %s: %s", url, e, exc_info=True)
            error_message = "Timeout Error: The request timed out."
            safe_error_message = str(error_message) # Ensure message is string
            # Use specific GIO error code for timeouts
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.TIMED_OUT.value)
            task.return_error(g_error)
            return
        except requests.exceptions.RequestException as e:
            logger.error("Task thread: RequestException for %s: %s", url, e, exc_info=True)
            error_message = f"Request Error: {str(e)}"
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error) # Corrected: single call
            return
        except Exception as e: # Catch any other unexpected errors
            logger.error("Task thread: Unexpected error for %s: %s", url, e, exc_info=True)
            error_message = f"An unexpected error occurred: {str(e)}"
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error) # Corrected: use return_gerror
            return


    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:
        """
        Traverses an exception chain to find a "Connection refused" error
        and returns a specific message if it's for an HTTPS URL.
        Otherwise, returns None.
        """
        current_exc = exc
        found_connection_refused = False
        max_depth = 5  # Limit traversal depth

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
                break # Found the most specific type

            # Check for urllib3.exceptions common in requests
            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                logger.debug("urllib3.exceptions.NewConnectionError found: %s", current_exc)
                # This exception's string representation often includes the OS error like "[Errno 111] Connection refused"
                if "connection refused" in exc_str.lower() or "errno 111" in exc_str.lower():
                    found_connection_refused = True
                    break
                # Also check original_error if present (newer urllib3)
                if hasattr(current_exc, 'original_error') and isinstance(current_exc.original_error, ConnectionRefusedError):
                    logger.debug("Nested ConnectionRefusedError found in NewConnectionError.original_error")
                    found_connection_refused = True
                    break
                if hasattr(current_exc, 'original_error') and hasattr(current_exc.original_error, 'errno') and current_exc.original_error.errno == 111:
                    logger.debug("Nested ConnectionRefusedError (errno 111) found in NewConnectionError.original_error")
                    found_connection_refused = True
                    break


            if isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                logger.debug("urllib3.exceptions.MaxRetryError found. Will inspect its reason.")
                # MaxRetryError's reason is often NewConnectionError
                if hasattr(current_exc, 'reason') and current_exc.reason is not None:
                    # Temporarily recurse on the reason without advancing general cause/context chain
                    # This is a bit of a special case for MaxRetryError
                    reason_exc = current_exc.reason
                    reason_exc_type_name = type(reason_exc).__name__
                    reason_exc_str = str(reason_exc)
                    logger.debug("Inspecting MaxRetryError.reason: Type=%s, Str=%s", reason_exc_type_name, reason_exc_str)
                    if isinstance(reason_exc, urllib3_exceptions.NewConnectionError):
                         if "connection refused" in reason_exc_str.lower() or "errno 111" in reason_exc_str.lower():
                            found_connection_refused = True
                            break
                         if hasattr(reason_exc, 'original_error') and isinstance(reason_exc.original_error, ConnectionRefusedError):
                             found_connection_refused = True
                             break
                         if hasattr(reason_exc, 'original_error') and hasattr(reason_exc.original_error, 'errno') and reason_exc.original_error.errno == 111:
                             found_connection_refused = True
                             break


            # General check for string messages in args or str(exc)
            # This is a broader, less precise check
            if any("connection refused" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "connection refused" in exc_str.lower():
                logger.debug("Found 'connection refused' in string representation of current exception or its args.")
                found_connection_refused = True
                # Don't break here if we want to find more specific types like ConnectionRefusedError itself

            if any("errno 111" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or \
               "errno 111" in exc_str.lower():
                logger.debug("Found 'errno 111' in string representation of current exception or its args.")
                found_connection_refused = True
                # Don't break here

            # Move to the next exception in the chain
            next_exc = None
            if hasattr(current_exc, '__cause__') and current_exc.__cause__ is not None:
                logger.debug("Moving to __cause__: %s", type(current_exc.__cause__).__name__)
                next_exc = current_exc.__cause__
            elif hasattr(current_exc, '__context__') and current_exc.__context__ is not None and not current_exc.__suppress_context__:
                logger.debug("Moving to __context__: %s", type(current_exc.__context__).__name__)
                next_exc = current_exc.__context__

            if current_exc is next_exc: # Avoid infinite loop on self-referential cause/context
                logger.debug("Next exception is same as current, stopping traversal.")
                break
            current_exc = next_exc

        if found_connection_refused:
            logger.info("Connection refused condition identified for URL: %s", url)
            if urlparse(url).scheme == 'https':
                logger.info("URL is HTTPS and connection was refused. Suggesting HTTP.")
                return "The URL targetted via HTTPS is refusing the connection. It might be an HTTP-only service. Please try with 'http://'."
            elif urlparse(url).scheme == 'http': # Specifically check for http
                logger.info("URL is HTTP and connection was refused. Suggesting HTTPS.")
                return "The HTTP request failed. The server might only support HTTPS for this resource. Please try with 'https://'."
            else: # Other schemes, or if somehow scheme is not http/https but connection refused
                logger.info("URL is non-HTTP/HTTPS (or scheme missing) and connection was refused.")
                return "Connection Error: The server at the specified URL actively refused the connection." # Generic for other cases

        logger.debug("No specific 'Connection refused' condition found that warrants a custom message.")
        return None

    def _fetch_headers_task_done_cb(self, source_object, result: Gio.AsyncResult, user_data):
        local_task_ref = self.current_http_task
        headers = None  # Initialize headers

        try:
            raw_task_result = local_task_ref.propagate_value()
            # --- START DEBUG LOGGING ---
            logger.error(f"DEBUG: Raw raw_task_result: {raw_task_result!r}")
            logger.error(f"DEBUG: Type of raw_task_result: {type(raw_task_result)}")
            logger.error(f"DEBUG: Class of raw_task_result: {getattr(raw_task_result, '__class__', 'N/A')}")
            if hasattr(raw_task_result, '__class__') and hasattr(raw_task_result.__class__, '__mro__'):
                logger.error(f"DEBUG: MRO of raw_task_result: {raw_task_result.__class__.__mro__}")
            else:
                logger.error("DEBUG: MRO of raw_task_result: N/A")
            logger.error(f"DEBUG: Is raw_task_result a GObject.Object? {isinstance(raw_task_result, GObject.Object)}")
            try:
                logger.error(f"DEBUG: vars(raw_task_result): {vars(raw_task_result)}")
            except TypeError:
                logger.error("DEBUG: vars(raw_task_result): Not applicable for this type.")
            # --- END DEBUG LOGGING ---

            actual_list_of_responses = None
            if isinstance(raw_task_result, tuple) and hasattr(raw_task_result, 'value'):
                logger.info("Accessing .value attribute from raw_task_result.")
                actual_list_of_responses = raw_task_result.value
            elif isinstance(raw_task_result, tuple) and len(raw_task_result) == 2:
                logger.info("Accessing second element (index 1) from raw_task_result tuple.")
                actual_list_of_responses = raw_task_result[1]
            else:
                logger.info("raw_task_result is not a recognized tuple wrapper. Assuming it might be the direct list or an error.")
                actual_list_of_responses = raw_task_result

            if actual_list_of_responses is None: # Check after potential extraction
                logger.error("Task result (actual_list_of_responses) is None unexpectedly.")
                self._display_error("Failed to retrieve task result (processed as None).")
                self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None) # Pass None to clear
            elif isinstance(actual_list_of_responses, list):
                logger.info("Successfully processed task result as list.")

                processed_headers_for_store = []
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

                        processed_headers_for_store.append(HeaderItem(key=url_display, value=status_display, is_special_row=True))

                        headers_for_this_response = response_data.get('headers', {})
                        for header_key, header_value in headers_for_this_response.items():
                            processed_headers_for_store.append(HeaderItem(key=str(header_key), value=str(header_value), is_special_row=False))

                        if i < len(actual_list_of_responses) - 1:
                            processed_headers_for_store.append(HeaderItem(key="", value="", is_special_row=True))

                    self._update_column_view_model(processed_headers_for_store)
                self.http_entry_row.remove_css_class("error")
            else: # If actual_list_of_responses is not None and not a list
                logger.error(f"Task result (actual_list_of_responses) of unexpected type {type(actual_list_of_responses)}. Expected list or None.")
                self._display_error(f"Failed to process task result (unexpected data structure: {type(actual_list_of_responses).__name__}).")
                self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
        except GObject.GError as e:
            error_message = e.message
            logger.error("Error fetching headers (async GObject.GError): %s", error_message)
            # Sanitize message if it contains markup, AdwBanner might not render it well
            error_message = error_message.replace("<b>", "").replace("</b>", "")
            self._display_error(error_message)
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None) # Clear previous results if error
        finally:
            # Re-enable UI elements that might have been disabled
            self.http_entry_row.set_sensitive(True)
            # e.g., self.spinner.stop() (if a spinner was used)

            # Clear the task reference if it matches the one this callback was for.
            if self.current_http_task is local_task_ref:
                self.current_http_task = None


    @staticmethod
    def _ensure_scheme(url: str) -> str:
        parsed_url = urlparse(url)
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

        return re.match(url_regex, url) is not None and bool(urlparse(url).netloc)

    # The _fetch_headers method is now part of _fetch_headers_task_thread_func
    # and is no longer called directly by _on_entry_row_activated.
    # It's kept here for the _format_http_error utility or if needed elsewhere.

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        status_code = e.response.status_code
        if status_code == 404:
            return "404 Not Found: The requested URL was not found on this server."
        if status_code == 403:  # Changed from elif to if for R1705
            return "403 Forbidden: You don't have permission to access this URL."
        if status_code == 500:  # Changed from elif to if for R1705
            return "500 Internal Server Error: The server encountered an internal error."
        return f"HTTP Error {status_code}: {e.response.reason}." # f-string is fine here as it's not logging

    def _on_pragma_toggled(
        self, widget: Gtk.Switch, _gparam: GObject.ParamSpec
    ) -> None:
        logger.debug("Akamai Pragma toggled to: %s", widget.get_active())
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[list[HeaderItem]]) -> None:
        self.header_list_store.remove_all()  # Clear existing items

        if header_items: # Check if list is not None and not empty implicitly
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

    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        self._clear_error()

    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def _on_clear_results_clicked(self, _button: Gtk.Button, *_args):
        logger.info("Results cleared by user.")
        self._update_column_view_model(None)  # Clears the view
        self._clear_error()  # Clear any errors
        self.http_entry_row.set_text("")  # Clear entry row


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
                label.set_max_width_chars(80)  # Or adjust as needed
            list_item.set_child(label)

        def bind_func(_, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            item = list_item.get_item() # This is a HeaderItem instance
            text_to_display = getattr(item, attr_name, "")
            if label:  # Check if label exists
                if item and item.is_special_row:
                    # For special rows, make the text bold.
                    # Escape markup in the text to prevent Pango errors if text contains '&', '<', etc.
                    escaped_text = GLib.markup_escape_text(text_to_display)
                    label.set_markup(f"<b>{escaped_text}</b>")
                else:
                    label.set_text(text_to_display)

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)

        return factory