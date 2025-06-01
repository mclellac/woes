import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import requests
import gi

# GTK version requirements must be called before importing from gi.repository
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')

# Now import GTK libraries and other dependencies
# pylint: disable=wrong-import-position
from gi.repository import Adw, Gio, GObject, Gtk, GLib
# pylint: disable=wrong-import-position
from .constants import RESOURCE_PREFIX

# Configure logger for the module - AFTER all imports
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
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    error_banner = Gtk.Template.Child("error_banner")
    http_results_group = Gtk.Template.Child("http_results_group")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task = None
        self._http_task_data_for_thread = {}  # Initialize the task data dict

        # Initialize ColumnView model and columns here
        if self.http_column_view is None:
            logger.critical("HttpPage: Gtk.Template.Child 'http_column_view' not found. UI will be broken.")
        else:
            self.header_list_store = Gio.ListStore.new(HeaderItem)
            selection_model = Gtk.MultiSelection.new(self.header_list_store)
            self.http_column_view.set_model(selection_model)

            if not self.http_column_view.get_columns():  # Ensure columns are added only once
                header_name_factory = self._create_factory("key")
                header_value_factory = self._create_factory("value", wrap_text=True)
                # Define col_name and col_value here before use
                col_name = Gtk.ColumnViewColumn.new("Header", header_name_factory)
                col_value = Gtk.ColumnViewColumn.new("Value", header_value_factory)
                col_value.set_expand(True)
                self.http_column_view.append_column(col_name)
                self.http_column_view.append_column(col_value)
            # The lines below were outside the 'if not self.http_column_view.get_columns()'
            # and also outside the 'else' for 'if self.http_column_view is None'.
            # They should be inside the 'else' and potentially inside the 'if not get_columns'
            # if col_name/col_value are only defined there.
            # For safety, they are moved inside the 'if not get_columns block' as they define columns.
            # If columns could be appended multiple times or if col_name/value were defined elsewhere,
            # this could be different. But given the "Ensure columns are added only once" comment,
            # this seems like the correct scope.

        self._connect_signals()
        self._clear_error()
        self._hide_results()

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
        url_to_fetch = self._ensure_scheme(original_url)  # Renamed to avoid confusion with 'url' in task_data
        logger.info("Fetching headers for URL: %s (original: %s)", url_to_fetch, original_url)

        if not self._is_valid_url(url_to_fetch):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url_to_fetch)
            self._display_error("Invalid URL format: Please enter a valid URL.")
            self._update_column_view_model(None)
            return

        self._clear_error()
        self.http_entry_row.set_sensitive(False)
        self.http_pragma_switch_row.set_sensitive(False)
        # self.http_spinner removed
        self._show_results()  # Show group, but it will be empty or show old results briefly

        # Cancel any existing task first
        if self.current_http_task:
            logger.debug("Cancelling previous HTTP task.")
            try:
                self.current_http_task.return_error_if_cancelled()  # Mark as cancelled if not already
                # Attempt to actually cancel the thread if possible, though GLib tasks are tricky
                cancellable = self.current_http_task.get_cancellable()
                if cancellable and not cancellable.is_cancelled():
                    cancellable.cancel()
            except GLib.Error as e:
                # This can happen if the task is already completed or cancelled.
                logger.debug("Error cancelling previous task (likely already completed/cancelled): %s", e)
            self.current_http_task = None

        # Store data for the thread in a way that's tied to this specific task instance
        # This is safer if tasks could be created rapidly, though self.current_http_task helps.
        task_specific_data = {
            "url": url_to_fetch,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active()
        }
        new_task = Gio.Task.new(self, Gio.Cancellable.new(), self._fetch_headers_task_done_cb, None)
        # Pass data directly to the task if GObject or GLib.Variant
        # For dict, we'll retrieve it via source_object in thread as done below.
        self._http_task_data_for_thread = task_specific_data  # Still using this for simplicity with dict
        self.current_http_task = new_task
        new_task.run_in_thread(self._fetch_headers_task_thread_func)

    def _fetch_headers_task_thread_func(
        self,
        task: Gio.Task,
        source_object,
        _task_data_ignored,  # Renamed to indicate it's unused
        cancellable: Optional[Gio.Cancellable]
    ):
        # Retrieve data using source_object._http_task_data_for_thread as set before run_in_thread
        current_task_data = source_object._http_task_data_for_thread
        url = current_task_data["url"]
        use_akamai_pragma = current_task_data["use_akamai_pragma"]
        logger.debug("Task thread: Making GET request to %s with Akamai headers: %s", url, use_akamai_pragma)

        request_headers = {}
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
                g_error = GLib.Error("User cancelled.", Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED.value)
                task.return_error(g_error)
                return

            response = requests.get(url, headers=request_headers, allow_redirects=False, timeout=10)

            if cancellable and cancellable.is_cancelled():
                g_error = GLib.Error("Cancelled during req.", Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED.value)
                task.return_error(g_error)
                return

            response.raise_for_status()  # Raises HTTPError for 4xx/5xx
            task.return_value(GLib.Variant('a{ss}', dict(response.headers)))  # Pass as GLib.Variant

        except requests.exceptions.HTTPError as e:
            logger.error("Task thread: HTTPError for %s: %s", url, e, exc_info=True)
            error_message = source_object._format_http_error(e)  # Use source_object to call instance method
            # Shorten error_message if it's too long for GLib.Error
            if len(error_message) > 100:  # Adjusted limit based on typical GLib.Error call structure
                error_message = "HTTP Error (see logs)."
            g_error = GLib.Error(error_message, Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED.value)
            task.return_error(g_error)
        except requests.exceptions.ConnectionError as e:
            logger.error("Task thread: ConnectionError for %s: %s", url, e, exc_info=True)
            g_error = GLib.Error("Connection Error.", Gio.io_error_quark(), Gio.IOErrorEnum.CONNECTION_REFUSED.value)
            task.return_error(g_error)
        except requests.exceptions.Timeout as e:
            logger.error("Task thread: Timeout for %s: %s", url, e, exc_info=True)
            g_error = GLib.Error("Request Timed Out.", Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT.value)
            task.return_error(g_error)
        except requests.exceptions.RequestException as e:  # Other requests-related errors
            logger.error("Task thread: RequestException for %s: %s", url, e, exc_info=True)
            err_name = type(e).__name__
            g_error = GLib.Error(f"Request Err: {err_name}", Gio.io_error_quark(), Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error)
        except Exception as e:
            logger.exception("Task thread: Unexpected error for %s.", url)  # Use logger.exception for general errors
            err_name = type(e).__name__
            g_error = GLib.Error(f"Unexpected Err: {err_name}", Gio.io_error_quark(), Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error)

    def _fetch_headers_task_done_cb(self, source_object, task: Gio.Task, user_data):
        # Ensure this callback is for the current task, ignore if it's an old one.
        if task is not self.current_http_task:
            logger.warning("Callback received for an outdated or superseded HTTP task. Ignoring.")
            return

        headers = None
        try:
            # This call will raise a GLib.Error (caught as GObject.GError)
            # if task.return_error() was called in the thread.
            returned_variant = task.propagate_value()  # For GLib.Variant
            if returned_variant:
                headers = returned_variant.unpack()  # Unpack GLib.Variant to Python dict
                logger.info("Successfully fetched headers (async).")
                self._update_column_view_model(headers)
                self.http_entry_row.remove_css_class("error")
            else:
                # This case implies task.return_value(None) was called, which we are not doing.
                # If it happens, it's an unexpected state.
                logger.error("Task propagate_value returned None unexpectedly (no error raised but no value).")
                self._display_error("Failed to retrieve task result (no data).")
                self._update_column_view_model(None)
                self.http_entry_row.add_css_class("error")

        except GObject.GError as e:
            logger.error("Error fetching headers (async GObject.GError): %s (Code: %s, Domain: %s)",
                         e.message, e.code, GLib.quark_to_string(e.domain))

            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self._display_error("Operation cancelled.")
                logger.info("HTTP header fetch task was cancelled by the user.")
            else:
                # Display the error message from GLib.Error, already formatted
                self._display_error(e.message)

            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        finally:
            self.http_entry_row.set_sensitive(True)
            self.http_pragma_switch_row.set_sensitive(True)
            # self.http_spinner removed

            # Clear the current task reference as it's now completed or failed.
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
        # This method is now called from the thread, ensure it's static or passed `self` correctly.
        # It was already an instance method, so source_object._format_http_error() is correct.
        status_code = e.response.status_code
        reason = e.response.reason
        # Ensure plain text for GLib.Error
        # Shorten these messages to avoid E501 when used in GLib.Error
        if status_code == 404:
            return f"HTTP 404: Not Found ({e.request.url[:30]}...)"
        if status_code == 403:
            return f"HTTP 403: Forbidden ({e.request.url[:30]}...)"
        if status_code == 500:
            return f"HTTP 500: Server Error ({e.request.url[:30]}...)"
        return f"HTTP {status_code}: {reason} ({e.request.url[:30]}...)"

    def _on_pragma_toggled(
        self, widget: Gtk.Switch, _gparam: GObject.ParamSpec
    ) -> None:
        logger.debug("Akamai Pragma toggled to: %s", widget.get_active())
        # Optionally, re-fetch if a URL is already present and results are shown
        # For now, it will apply on the next manual fetch.
        # if self.http_entry_row.get_text().strip() and self.http_results_group.get_visible():
        #     self._on_entry_row_activated(self.http_entry_row)

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