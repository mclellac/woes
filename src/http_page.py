import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GObject, Gtk, GLib

from .constants import RESOURCE_PREFIX

logger = logging.getLogger(__name__)


class HeaderItem(GObject.Object):
    key: str
    value: str

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Gtk.Box):
    __gtype_name__ = "HttpPage"

    http_entry_row = Gtk.Template.Child("http_entry_row")
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    http_error_banner = Gtk.Template.Child("http_error_banner")
    http_response_group = Gtk.Template.Child("http_response_group")

    def __init__(self, **kwargs):
        logger.debug("HttpPage.__init__: Starting")
        super().__init__(**kwargs)
        self.current_http_task = None
        logger.debug(f"HttpPage.__init__: self.current_http_task initialized to {self.current_http_task}")
        # self._http_task_data_for_thread removed

        if self.http_column_view is None:
            logger.critical("HttpPage.__init__: Gtk.Template.Child 'http_column_view' not found. UI will be broken.")
        else:
            logger.debug("HttpPage.__init__: Initializing ColumnView model and columns.")
            self.header_list_store = Gio.ListStore.new(HeaderItem)
            logger.debug(f"HttpPage.__init__: self.header_list_store created: {self.header_list_store}")
            selection_model = Gtk.MultiSelection.new(self.header_list_store)
            logger.debug(f"HttpPage.__init__: selection_model created: {selection_model}")
            self.http_column_view.set_model(selection_model)
            logger.debug("HttpPage.__init__: ColumnView model set.")

            if not self.http_column_view.get_columns():
                logger.debug("HttpPage.__init__: Adding columns to ColumnView.")
                header_name_factory = self._create_factory("key")
                logger.debug(f"HttpPage.__init__: header_name_factory created: {header_name_factory}")
                header_value_factory = self._create_factory("value", wrap_text=True)
                logger.debug(f"HttpPage.__init__: header_value_factory created: {header_value_factory}")
                col_name = Gtk.ColumnViewColumn.new("Header", header_name_factory)
                logger.debug(f"HttpPage.__init__: col_name created: {col_name}")
                col_value = Gtk.ColumnViewColumn.new("Value", header_value_factory)
                logger.debug(f"HttpPage.__init__: col_value created: {col_value}")
                col_value.set_expand(True)
                self.http_column_view.append_column(col_name)
                logger.debug("HttpPage.__init__: col_name appended.")
                self.http_column_view.append_column(col_value)
                logger.debug("HttpPage.__init__: col_value appended.")
            else:
                logger.debug("HttpPage.__init__: Columns already exist in ColumnView.")

        self._connect_signals()
        self._clear_error()
        self._hide_results()
        logger.debug("HttpPage.__init__: Finished")

    def _connect_signals(self) -> None:
        logger.debug("HttpPage._connect_signals: Connecting signals")
        self.http_entry_row.connect(
            "entry-activated", self._on_entry_row_activated
        )
        logger.debug("HttpPage._connect_signals: Connected 'entry-activated' for http_entry_row")
        self.http_entry_row.connect("apply", self._on_entry_row_activated)
        logger.debug("HttpPage._connect_signals: Connected 'apply' for http_entry_row")
        self.http_pragma_switch_row.connect(
            "notify::active", self._on_pragma_toggled
        )
        logger.debug("HttpPage._connect_signals: Connected 'notify::active' for http_pragma_switch_row")
        self.http_error_banner.connect("button-clicked", self._on_error_banner_dismiss)
        logger.debug("HttpPage._connect_signals: Connected 'button-clicked' for http_error_banner")
        logger.debug("HttpPage._connect_signals: Finished connecting signals")

    def _on_entry_row_activated(self, entry_row: Gtk.Entry) -> None:
        logger.debug("HttpPage._on_entry_row_activated: Triggered")
        original_url = entry_row.get_text().strip()
        logger.debug(f"HttpPage._on_entry_row_activated: original_url: '{original_url}'")
        url_to_fetch = self._ensure_scheme(original_url)
        logger.debug(f"HttpPage._on_entry_row_activated: url_to_fetch: '{url_to_fetch}'")
        logger.info("Fetching headers for URL: %s (original: %s)", url_to_fetch, original_url)

        is_valid = self._is_valid_url(url_to_fetch)
        logger.debug(f"HttpPage._on_entry_row_activated: URL validation status for '{url_to_fetch}': {is_valid}")
        if not is_valid:
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url_to_fetch)
            self._display_error("Invalid URL format: Please enter a valid URL.")
            self._update_column_view_model(None)
            return

        self._clear_error()
        logger.debug("HttpPage._on_entry_row_activated: Setting http_entry_row and http_pragma_switch_row sensitive to False.")
        self.http_entry_row.set_sensitive(False)
        self.http_pragma_switch_row.set_sensitive(False)
        self._show_results()

        if self.current_http_task:
            logger.debug("HttpPage._on_entry_row_activated: Cancelling previous HTTP task.")
            try:
                self.current_http_task.return_error_if_cancelled()
                cancellable = self.current_http_task.get_cancellable()
                if cancellable and not cancellable.is_cancelled():
                    logger.debug(f"HttpPage._on_entry_row_activated: Calling cancellable.cancel() for task {self.current_http_task}")
                    cancellable.cancel()
                    logger.debug(f"HttpPage._on_entry_row_activated: cancellable.cancel() called for task {self.current_http_task}")
            except GLib.Error as e:
                logger.debug("HttpPage._on_entry_row_activated: Error cancelling previous task (likely already completed/cancelled): %s", e)
            self.current_http_task = None
            logger.debug("HttpPage._on_entry_row_activated: self.current_http_task set to None after cancellation.")

        use_akamai_pragma_val = self.http_pragma_switch_row.get_active()
        logger.debug(f"HttpPage._on_entry_row_activated: use_akamai_pragma: {use_akamai_pragma_val}")
        task_specific_data = {
            "url": url_to_fetch,
            "use_akamai_pragma": use_akamai_pragma_val
        }
        logger.debug(f"HttpPage._on_entry_row_activated: task_specific_data: {task_specific_data}")
        cancellable = Gio.Cancellable.new()
        logger.debug(f"HttpPage._on_entry_row_activated: Created Gio.Cancellable: {cancellable}")
        new_task = Gio.Task.new(self, cancellable, self._fetch_headers_task_done_cb, None)
        logger.debug(f"HttpPage._on_entry_row_activated: Created Gio.Task: {new_task}")
        # self._http_task_data_for_thread assignment removed
        self.current_http_task = new_task
        logger.debug(f"HttpPage._on_entry_row_activated: self.current_http_task set to: {self.current_http_task}")
        logger.debug(f"HttpPage._on_entry_row_activated: Starting async task fetch-headers by calling new_task.run_in_thread()")
        new_task.run_in_thread(self._fetch_headers_task_thread_func, task_data=task_specific_data)
        logger.debug(f"HttpPage._on_entry_row_activated: After new_task.run_in_thread()")

    def _fetch_headers_task_thread_func(
        self,
        task: Gio.Task,
        _source_object_do_not_use, # Renamed from source_object
        task_data: dict, # Changed from _task_data_ignored
        cancellable: Optional[Gio.Cancellable]
    ):
        logger.debug(
            f"HttpPage._fetch_headers_task_thread_func: Starting for task {task}, "
            f"_source_object_do_not_use: {_source_object_do_not_use}, task_data: {task_data}, cancellable: {cancellable}"
        )
        current_task_data = task_data # Use passed task_data
        url = current_task_data["url"]
        use_akamai_pragma = current_task_data["use_akamai_pragma"]
        logger.debug(f"HttpPage._fetch_headers_task_thread_func: Parameters - url: '{url}', use_akamai_pragma: {use_akamai_pragma}")

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
        logger.debug(f"HttpPage._fetch_headers_task_thread_func: Request headers prepared: {request_headers}")

        try:
            if cancellable and cancellable.is_cancelled():
                logger.debug("HttpPage._fetch_headers_task_thread_func: Task cancelled before request.")
                g_error = GLib.Error("User cancelled.", Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED.value)
                task.return_error(g_error)
                logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned error due to pre-request cancellation: {g_error}")
                return

            logger.debug(f"HttpPage._fetch_headers_task_thread_func: Before requests.get({url}, ...)")
            response = requests.get(url, headers=request_headers, allow_redirects=False, timeout=10)
            logger.debug(
                f"HttpPage._fetch_headers_task_thread_func: After requests.get(), response status: {response.status_code}, "
                f"headers: {response.headers}"
            )

            if cancellable and cancellable.is_cancelled():
                logger.debug("HttpPage._fetch_headers_task_thread_func: Task cancelled after request but before processing.")
                g_error = GLib.Error("Cancelled during req.", Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED.value)
                task.return_error(g_error)
                logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned error due to post-request cancellation: {g_error}")
                return

            response.raise_for_status()
            logger.debug("HttpPage._fetch_headers_task_thread_func: response.raise_for_status() passed.")
            response_headers_dict = dict(response.headers)
            task.return_value(GLib.Variant('a{ss}', response_headers_dict))
            logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned value with task.return_value(): {response_headers_dict}")

        except requests.exceptions.HTTPError as e:
            logger.error(f"HttpPage._fetch_headers_task_thread_func: HTTPError for {url}: {e}", exc_info=True)
            error_message = self._format_http_error(e) # Changed source_object to self
            if len(error_message) > 100:
                error_message = "HTTP Error (see logs for details)."
            g_error = GLib.Error(error_message, Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED.value)
            task.return_error(g_error)
            logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned error with task.return_error() for HTTPError: {g_error.message}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"HttpPage._fetch_headers_task_thread_func: ConnectionError for {url}: {e}", exc_info=True)
            g_error = GLib.Error("Connection Error: Could not connect to the server.", Gio.io_error_quark(), Gio.IOErrorEnum.CONNECTION_REFUSED.value)
            task.return_error(g_error)
            logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned error with task.return_error() for ConnectionError: {g_error.message}")
        except requests.exceptions.Timeout as e:
            logger.error(f"HttpPage._fetch_headers_task_thread_func: Timeout for {url}: {e}", exc_info=True)
            g_error = GLib.Error("Request Timed Out: The server did not respond in time.", Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT.value)
            task.return_error(g_error)
            logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned error with task.return_error() for Timeout: {g_error.message}")
        except requests.exceptions.RequestException as e:
            logger.error(f"HttpPage._fetch_headers_task_thread_func: RequestException for {url}: {e}", exc_info=True)
            err_name = type(e).__name__
            g_error = GLib.Error(f"Request Error: {err_name}. Check URL and logs.", Gio.io_error_quark(), Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error)
            logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned error with task.return_error() for RequestException ({err_name}): {g_error.message}")
        except Exception as e:
            logger.exception(f"HttpPage._fetch_headers_task_thread_func: Unexpected error for {url}.")
            err_name = type(e).__name__
            g_error = GLib.Error(f"Unexpected Error: {err_name}. See logs for details.", Gio.io_error_quark(), Gio.IOErrorEnum.FAILED.value)
            task.return_error(g_error)
            logger.debug(f"HttpPage._fetch_headers_task_thread_func: Returned error with task.return_error() for Unexpected error ({err_name}): {g_error.message}")
        logger.debug(f"HttpPage._fetch_headers_task_thread_func: Finished for task {task}")

    def _fetch_headers_task_done_cb(self, source_object, task: Gio.Task, user_data):
        logger.debug(
            f"HttpPage._fetch_headers_task_done_cb: Starting for task {task}, "
            f"source_object: {source_object}, user_data: {user_data}"
        )
        if task is not self.current_http_task:
            logger.warning("HttpPage._fetch_headers_task_done_cb: Callback received for an outdated or superseded HTTP task. Ignoring.")
            return

        headers = None
        try:
            logger.debug(f"HttpPage._fetch_headers_task_done_cb: Calling task.propagate_value() for task {task}")
            returned_variant = task.propagate_value()
            logger.debug(f"HttpPage._fetch_headers_task_done_cb: task.propagate_value() returned: {returned_variant}")
            if returned_variant:
                headers = returned_variant.unpack()
                logger.info("HttpPage._fetch_headers_task_done_cb: Successfully fetched headers (async).")
                logger.debug(f"HttpPage._fetch_headers_task_done_cb: Unpacked headers: {headers}")
                self._update_column_view_model(headers)
                self.http_entry_row.remove_css_class("error")
                logger.debug("HttpPage._fetch_headers_task_done_cb: Removed 'error' css class from http_entry_row.")
            else:
                logger.error("HttpPage._fetch_headers_task_done_cb: Task propagate_value returned None unexpectedly (no error raised but no value).")
                self._display_error("Failed to retrieve task result (no data).")
                self._update_column_view_model(None)
                self.http_entry_row.add_css_class("error")
                logger.debug("HttpPage._fetch_headers_task_done_cb: Added 'error' css class to http_entry_row due to None result.")

        except GObject.GError as e:
            logger.error(
                f"HttpPage._fetch_headers_task_done_cb: Error fetching headers (async GObject.GError): {e.message} "
                f"(Code: {e.code}, Domain: {GLib.quark_to_string(e.domain)})"
            )

            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                logger.info("HttpPage._fetch_headers_task_done_cb: HTTP header fetch task was cancelled by the user.")
                self._display_error("Operation cancelled.")
            else:
                logger.info(f"HttpPage._fetch_headers_task_done_cb: Displaying error from GError: {e.message}")
                self._display_error(e.message)

            self.http_entry_row.add_css_class("error")
            logger.debug("HttpPage._fetch_headers_task_done_cb: Added 'error' css class to http_entry_row due to GError.")
            self._update_column_view_model(None)
        finally:
            logger.debug("HttpPage._fetch_headers_task_done_cb: In finally block.")
            self.http_entry_row.set_sensitive(True)
            logger.debug("HttpPage._fetch_headers_task_done_cb: http_entry_row sensitivity set to True.")
            self.http_pragma_switch_row.set_sensitive(True)
            logger.debug("HttpPage._fetch_headers_task_done_cb: http_pragma_switch_row sensitivity set to True.")

            self.current_http_task = None
            logger.debug("HttpPage._fetch_headers_task_done_cb: self.current_http_task set to None.")
        logger.debug(f"HttpPage._fetch_headers_task_done_cb: Finished for task {task}")

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

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        status_code = e.response.status_code
        reason = e.response.reason
        url_short = f"{e.request.url[:30]}..." if e.request and e.request.url else "N/A"

        if status_code == 404:
            return f"HTTP 404: Not Found ({url_short})"
        if status_code == 403:
            return f"HTTP 403: Forbidden ({url_short})"
        if status_code == 500:
            return f"HTTP 500: Server Error ({url_short})"
        return f"HTTP {status_code}: {reason} ({url_short})"

    def _on_pragma_toggled(
        self, widget: Gtk.Switch, _gparam: GObject.ParamSpec
    ) -> None:
        logger.debug("HttpPage._on_pragma_toggled: Triggered")
        is_active = widget.get_active()
        logger.debug(f"HttpPage._on_pragma_toggled: Akamai Pragma toggled to: {is_active}")

    def _update_column_view_model(self, headers: Optional[Dict[str, str]]) -> None:
        logger.debug(f"HttpPage._update_column_view_model: Called with headers: {headers}")
        logger.debug("HttpPage._update_column_view_model: Calling self.header_list_store.remove_all()")
        self.header_list_store.remove_all()
        logger.debug("HttpPage._update_column_view_model: self.header_list_store.remove_all() finished.")

        if headers and "error" not in headers:
            logger.debug(f"HttpPage._update_column_view_model: Populating model with {len(headers)} headers.")
            for key, value in headers.items():
                item = HeaderItem(key, value)
                self.header_list_store.append(item)
            logger.debug("HttpPage._update_column_view_model: Finished appending items.")
            self._show_results()
        else:
            if headers and "error" in headers:
                logger.debug("HttpPage._update_column_view_model: Headers dictionary contains 'error' key.")
            elif not headers:
                logger.debug("HttpPage._update_column_view_model: Headers are None, hiding results.")
            self._hide_results()
        logger.debug("HttpPage._update_column_view_model: Finished.")

    def _show_results(self):
        """Show the results group."""
        logger.debug("HttpPage._show_results: Called.")
        self.http_response_group.set_visible(True)
        logger.debug("HttpPage._show_results: http_response_group visibility set to True.")

    def _hide_results(self):
        """Hide the results group."""
        logger.debug("HttpPage._hide_results: Called.")
        self.http_response_group.set_visible(False)
        logger.debug("HttpPage._hide_results: http_response_group visibility set to False.")

    def _display_error(self, message: str) -> None:
        logger.debug(f"HttpPage._display_error: Called with message: '{message}'")
        self.http_error_banner.set_title(message)
        logger.debug("HttpPage._display_error: http_error_banner title set.")
        self.http_error_banner.set_revealed(True)
        logger.debug("HttpPage._display_error: http_error_banner revealed set to True.")
        self.http_entry_row.add_css_class("error")
        logger.debug("HttpPage._display_error: 'error' CSS class added to http_entry_row.")
        self._hide_results()
        logger.debug("HttpPage._display_error: Finished.")

    def _clear_error(self) -> None:
        logger.debug("HttpPage._clear_error: Called.")
        self.http_error_banner.set_revealed(False)
        logger.debug("HttpPage._clear_error: http_error_banner revealed set to False.")
        self.http_error_banner.set_title("")
        logger.debug("HttpPage._clear_error: http_error_banner title set to empty string.")
        self.http_entry_row.remove_css_class("error")
        logger.debug("HttpPage._clear_error: 'error' CSS class removed from http_entry_row.")
        logger.debug("HttpPage._clear_error: Finished.")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        logger.debug(f"HttpPage._on_error_banner_dismiss: Triggered for banner: {_banner}")
        self._clear_error()
        logger.debug("HttpPage._on_error_banner_dismiss: Finished.")

    def _on_clear_results_clicked(self, _button: Gtk.Button, *_args):
        logger.debug("HttpPage._on_clear_results_clicked: Triggered by user.")
        logger.info("HttpPage._on_clear_results_clicked: Results cleared by user.")
        self._update_column_view_model(None)
        self._clear_error()
        logger.debug("HttpPage._on_clear_results_clicked: Setting http_entry_row text to ''")
        self.http_entry_row.set_text("")
        logger.debug("HttpPage._on_clear_results_clicked: Finished.")

    @staticmethod
    def _create_factory(
        attr_name: str, wrap_text: bool = False
    ) -> Gtk.SignalListItemFactory:
        logger.debug(f"HttpPage._create_factory: Creating factory for attribute '{attr_name}', wrap: {wrap_text}")
        factory = Gtk.SignalListItemFactory()
        logger.debug(f"HttpPage._create_factory: Gtk.SignalListItemFactory created: {factory} for attr: '{attr_name}'")

        def setup_func(_, list_item: Gtk.ListItem) -> None:
            logger.debug(f"HttpPage._create_factory.setup_func: Setting up ListItem {list_item} for factory of '{attr_name}'")
            label = Gtk.Label(xalign=0)
            logger.debug(
                f"HttpPage._create_factory.setup_func: Created Gtk.Label: {label} with xalign=0 "
                f"for ListItem {list_item}"
            )
            label.set_hexpand(True)
            logger.debug(f"HttpPage._create_factory.setup_func: Label hexpand set to True for ListItem {list_item}")
            if wrap_text:
                label.set_wrap(True)
                logger.debug(f"HttpPage._create_factory.setup_func: Label wrap set to True for ListItem {list_item}")
                label.set_max_width_chars(80)
                logger.debug(f"HttpPage._create_factory.setup_func: Label max_width_chars set to 80 for ListItem {list_item}")
            list_item.set_child(label)
            logger.debug(f"HttpPage._create_factory.setup_func: Child of ListItem {list_item} set to label {label}")

        def bind_func(_, list_item: Gtk.ListItem) -> None:
            item = list_item.get_item()
            logger.debug(
                f"HttpPage._create_factory.bind_func: Binding ListItem {list_item} to item {item} "
                f"for factory of '{attr_name}'"
            )
            label = list_item.get_child()
            if not label:
                logger.warning(f"HttpPage._create_factory.bind_func: Label is None for ListItem {list_item}. Cannot bind.")
                return

            text = getattr(item, attr_name, "")
            logger.debug(
                f"HttpPage._create_factory.bind_func: Text to set for ListItem {list_item} "
                f"(attr: '{attr_name}'): '{text}'"
            )
            label.set_text(text)
            logger.debug(f"HttpPage._create_factory.bind_func: Label text set for ListItem {list_item}")

        logger.debug(f"HttpPage._create_factory: Connecting 'setup' signal for factory of '{attr_name}'")
        factory.connect("setup", setup_func)
        logger.debug(f"HttpPage._create_factory: Connecting 'bind' signal for factory of '{attr_name}'")
        factory.connect("bind", bind_func)

        logger.debug(f"HttpPage._create_factory: Returning factory for attribute '{attr_name}': {factory}")
        return factory