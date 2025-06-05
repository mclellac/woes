import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import requests
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GObject, Gtk, GLib

from .constants import RESOURCE_PREFIX, USER_AGENTS
# Removed Helper import as it's unused
# from .style_utils import set_widget_visibility  # This is no longer needed

# Configure logger for the module
logger = logging.getLogger(__name__)


class HeaderItem(GObject.Object):
    key: str
    value: str

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


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
                task.return_error(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED, "Task was cancelled")
                return

            response = requests.get(url, headers=request_headers, allow_redirects=False, timeout=10)
            response.raise_for_status()
            headers_dict = dict(response.headers)
            cleaned_headers_dict = {str(k): str(v) for k, v in headers_dict.items()}
            task.return_value(cleaned_headers_dict) # Return the Python dictionary directly
        except requests.exceptions.HTTPError as e:
            logger.error("Task thread: HTTPError for %s: %s", url, e, exc_info=True)
            # Create a GError for HTTP errors
            # For simplicity, using a generic error domain and code
            # A more robust solution might define a custom error domain
            error_message = self._format_http_error(e)
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED)
            task.return_error(g_error)
            return
        except requests.exceptions.ConnectionError as e:
            logger.warning("Task thread: ConnectionError for %s: %s", url, e, exc_info=True)
            error_message = "Connection Error: Failed to establish a connection."
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED)
            task.return_error(g_error)
            return
        except requests.exceptions.Timeout as e:
            logger.warning("Task thread: Timeout for %s: %s", url, e, exc_info=True)
            error_message = "Timeout Error: The request timed out."
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED)
            task.return_error(g_error)
            return
        except requests.exceptions.RequestException as e:
            logger.error("Task thread: RequestException for %s: %s", url, e, exc_info=True)
            error_message = f"Request Error: {str(e)}"
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED)
            task.return_error(g_error) # Corrected: single call
            return
        except Exception as e: # Catch any other unexpected errors
            logger.error("Task thread: Unexpected error for %s: %s", url, e, exc_info=True)
            error_message = f"An unexpected error occurred: {str(e)}"
            safe_error_message = str(error_message)
            g_error = GLib.Error(message=safe_error_message, domain=Gio.io_error_quark(), code=Gio.IOErrorEnum.FAILED)
            task.return_error(g_error) # Corrected: use return_gerror
            return


    def _fetch_headers_task_done_cb(self, source_object, result: Gio.AsyncResult, user_data):
        local_task_ref = self.current_http_task
        headers = None  # Initialize headers

        try:
            returned_obj = local_task_ref.propagate_value() # Renamed for clarity
            headers = None  # Initialize headers

            if returned_obj is None:
                logger.error("propagate_value returned None unexpectedly.")
                self._display_error("Failed to retrieve task result (returned None).")
                self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
            elif isinstance(returned_obj, dict): # Ideal case, direct Python dict
                headers = returned_obj
                logger.info("Successfully fetched headers (async, direct dict).")
                self._update_column_view_model(headers)
                self.http_entry_row.remove_css_class("error")
            else: # Handle _ResultTuple or other unexpected types
                logger.info(f"propagate_value returned type {type(returned_obj)}. Attempting to access its '.value' attribute.")
                if hasattr(returned_obj, 'value') and isinstance(getattr(returned_obj, 'value'), dict):
                    headers = getattr(returned_obj, 'value')
                    logger.info("Successfully fetched headers (async, from .value attribute of returned object).")
                    self._update_column_view_model(headers)
                    self.http_entry_row.remove_css_class("error")
                else:
                    logger.error(f"Returned object of type {type(returned_obj)} does not have a 'value' attribute containing a dict.")
                    self._display_error(f"Failed to process task result (unexpected data structure: {type(returned_obj).__name__}).")
                    self.http_entry_row.add_css_class("error")
                    self._update_column_view_model(None)
        except GObject.GError as e: # Catch errors propagated by propagate_value()
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

    def _update_column_view_model(self, headers: Optional[Dict[str, str]]) -> None:
        self.header_list_store.remove_all()  # Clear existing items

        if headers and "error" not in headers:
            for key, value in headers.items():
                self.header_list_store.append(HeaderItem(key, value))
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
            item = list_item.get_item()
            text = getattr(item, attr_name, "")
            if label:  # Check if label exists
                label.set_text(text)

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)

        return factory