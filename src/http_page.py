"""
Defines the HTTP Headers page for the Woes application.

This module provides the :class:`.HttpPage` class, which allows users to fetch and
inspect HTTP headers for a given URL. It includes options for custom Host
headers, User-Agent string selection, and Akamai Pragma header toggling.
The page also integrates with GSettings for persisting user preferences
and uses a background thread for network operations to keep the UI responsive.
"""

import logging
from enum import Enum
from typing import Any, Optional

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Pango

from .constants import RESOURCE_PREFIX, APP_ID, USER_AGENTS
from .utils import show_global_error, show_global_toast, is_valid_url
from .helper import Helper
from .http_client import (
    HttpFetcher,
    HttpClientError,
    HttpRequestTimeoutError,
    HttpConnectionError,
    HttpProcessingError,
    HttpGenericRequestError,
)


logger = logging.getLogger(__name__)

WOES_HTTP_ERROR_DOMAIN = "woes-http-error-domain"


class HttpErrorType(int, Enum):
    """Enumeration of HTTP error types for Gio.Task error reporting."""

    TIMEOUT = 0
    HTTP_ERROR = 1
    CONNECTION_ERROR = 2
    REQUEST_EXCEPTION = 3
    GENERIC_UNEXPECTED = 4
    CANCELLED = 5


class HeaderItem(GObject.Object):
    """
    :class:`GObject.Object` representing a single header key-value pair for the :class:`Gtk.ColumnView`.

    :ivar key: The header key or special row title.
    :vartype key: str
    :ivar value: The header value or special row description.
    :vartype value: str
    :ivar is_special_row: Whether this item represents a special row (e.g., URL/status line).
    :vartype is_special_row: bool
    """

    key: str
    value: str
    is_special_row: bool

    def __init__(self, key: str, value: str, is_special_row: bool = False):
        super().__init__()
        self.key = key
        self.value = value
        self.is_special_row = is_special_row


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Gtk.Box):
    __gtype_name__ = "HttpPage"
    http_entry_row: Adw.EntryRow = Gtk.Template.Child()
    http_apply_button: Gtk.Button = Gtk.Template.Child()
    http_host_header_row: Adw.EntryRow = Gtk.Template.Child()
    http_user_agent_row: Adw.ComboRow = Gtk.Template.Child()
    http_pragma_switch_row: Adw.SwitchRow = Gtk.Template.Child()
    http_column_view: Gtk.ColumnView = Gtk.Template.Child()
    http_results_group: Adw.PreferencesGroup = Gtk.Template.Child()
    clear_results_button: Gtk.Button = Gtk.Template.Child()
    copy_results_button: Gtk.Button = Gtk.Template.Child()
    http_status_row: Adw.ActionRow = Gtk.Template.Child()
    http_status_spinner: Gtk.Spinner = Gtk.Template.Child()
    http_cancel_button: Optional[Gtk.Button] = Gtk.Template.Child() # Bound from UI

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task: Optional[Gio.Task] = None
        self.current_http_cancellable: Optional[Gio.Cancellable] = None # Added
        self._current_header_items: list[HeaderItem] = []
        self._http_task_data_for_thread: dict[str, Any] = {}
        self._ua_title_to_value_map: dict[str, Optional[str]] = {}
        self.settings: Gio.Settings = Gio.Settings(schema_id=APP_ID)
        self._http_page_ua_is_following_gsettings_default = True
        self._gsettings_ua_changed_handler_id = 0
        self._header_key_color: str = self.settings.get_string("http-output-header-key-color")
        self._header_value_color: str = self.settings.get_string("http-output-header-value-color")
        self._special_row_color: str = self.settings.get_string("http-output-special-row-color")

        self._output_font_gsettings_key: str = "output-font"
        output_font_str: str = self.settings.get_string(self._output_font_gsettings_key)
        self._output_font_desc: Pango.FontDescription = Pango.FontDescription.from_string(
            output_font_str if output_font_str else "Sans 10"
        )

        self.settings.connect("changed::http-output-header-key-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-header-value-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-special-row-color", self._on_color_setting_changed)
        self.settings.connect(f"changed::{self._output_font_gsettings_key}", self._on_global_output_font_changed)

        self.header_list_store: Gio.ListStore = Gio.ListStore.new(HeaderItem)
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
        self._update_user_agent_model()
        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._update_user_agent_model())
        self._gsettings_ua_changed_handler_id = self.settings.connect(
            "changed::default-user-agent-title", self._on_default_ua_gsetting_changed
        )

        if self.http_apply_button:
            self.http_apply_button.get_style_context().add_class("suggested-action")
        if self.clear_results_button:
            self.clear_results_button.get_style_context().add_class("destructive-action")
        if self.copy_results_button:
            self.copy_results_button.get_style_context().add_class("flat")

        if self.http_cancel_button:
            self.http_cancel_button.set_sensitive(False)
            self.http_cancel_button.set_visible(False)
            # Assuming destructive-action or flat style will be set in UI file.

        if self.http_host_header_row:
            self._on_host_header_changed(self.http_host_header_row)
        if self.http_user_agent_row:
            self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)

        self.column_view_helper = Helper(widget=self.http_column_view, parent_window=self.get_native())

    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        try:
            # display = widget.get_display() # No longer needed
            # clipboard = Gtk.Clipboard.get_default(display) # Old failing line
            clipboard = widget.get_clipboard() # New approach
            if clipboard: # Gtk.Clipboard might be None if not available
                clipboard.set_text(text) # set_text does not take a length argument in GTK4
                logger.info("Text copied to clipboard: %s", text[:100] + "..." if len(text) > 100 else text)
                # show_global_toast(widget, "Text copied to clipboard.") # Caller handles success toast
            else:
                logger.warning("Could not get clipboard from widget: %s", widget)
                show_global_toast(widget.get_native(), "Failed to access clipboard.") # type: ignore
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error copying to clipboard:")
            show_global_toast(widget.get_native(), "Error copying to clipboard.") # type: ignore

    def _connect_signals(self) -> None:
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_entry_row.connect("changed", self._on_http_entry_changed)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_host_header_row:
            self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row:
            self.http_user_agent_row.connect("notify::selected-item", self._on_http_page_ua_selection_changed)

        if self.http_cancel_button:
            self.http_cancel_button.connect("clicked", self._on_cancel_fetch_clicked)
        else: # This case should ideally not happen if UI file is correct and Gtk.Template.Child works
            logger.warning("HttpPage: http_cancel_button was not bound from UI file. Cancellation UI may not work.")


    def _on_cancel_fetch_clicked(self, _button: Gtk.Button) -> None:
        """Handle click of the Cancel Fetch button."""
        logger.info("HTTP fetch cancellation requested by user.")
        if self.current_http_cancellable and not self.current_http_cancellable.is_cancelled():
            self.current_http_cancellable.cancel()
            if self.http_cancel_button:
                self.http_cancel_button.set_sensitive(False) # Disable immediately
            if self.http_status_row:
                self.http_status_row.set_subtitle("Cancelling fetch...") # type: ignore
        else:
            logger.warning("No active HTTP fetch cancellable to cancel, or already cancelled.")


    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        if not entry_row:
            return
        text = entry_row.get_text().strip()
        if text:
            entry_row.add_css_class("active-override")
        else:
            entry_row.remove_css_class("active-override")

    def _on_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        if not combo_row:
            return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            effective_ua_value = self._ua_title_to_value_map.get(selected_title)
            if effective_ua_value:
                combo_row.add_css_class("active-override")
            else:
                combo_row.remove_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map:
            combo_row.remove_css_class("active-override")

    def _on_user_agent_changed_visual_feedback(
        self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]
    ) -> None:
        self._on_user_agent_changed(combo_row, _gparam)

    def _save_selected_user_agent_preference(
        self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]
    ) -> None:
        if not combo_row:
            return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            logger.info(
                f"HTTP Page User-Agent selection changed to: '{selected_title}'. This is a session-specific change."
            )
        elif selected_item_obj is None:
            logger.info("HTTP Page User-Agent selection cleared (None). This is a session-specific change.")

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Copying all headers to clipboard.")
        lines: list[str] = []
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
            HttpPage._copy_to_clipboard(text_to_copy, self) # Use static method
            show_global_toast(self, "All headers copied to clipboard.")
        else:
            logger.info("No headers to copy from the results view.")
            show_global_toast(self, "No results to copy.")

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        original_url = self.http_entry_row.get_text().strip()
        url = self._ensure_scheme(original_url)

        if not is_valid_url(url):
            logger.warning("HTTP Page: Invalid URL provided: %s (processed as: %s)", original_url, url)
            error_message = "Invalid URL. Please enter a valid URL (e.g., https://example.com)."
            self.http_entry_row.add_css_class("error")
            show_global_error(self, error_message)
            self._update_column_view_model(None)
            # self._clear_error() # Error is shown, don't clear immediately
            self._set_loading_state(False, "Idle - Invalid URL.")
            return

        self.http_entry_row.remove_css_class("error")
        self._clear_error()

        if self.current_http_task and not self.current_http_task.is_done():
            if self.current_http_cancellable and not self.current_http_cancellable.is_cancelled():
                logger.info("Requesting cancellation of previous HTTP fetch task.")
                self.current_http_cancellable.cancel()
                # Don't start a new task immediately; let the cancellation complete.
                # The UI will be updated by the _fetch_headers_task_done_cb of the cancelled task.
                # User can click "Fetch" again if needed after cancellation is processed.
                # Or, we could queue the new request, but that adds complexity.
                # For now, simply cancelling and requiring user to re-initiate is simpler.
                # self._set_loading_state(False, "Previous task cancelled. Ready for new input.") # Optional feedback
                # return # Optionally prevent starting a new task immediately

        self._set_loading_state(True, "Fetching headers...")
        self.current_http_cancellable = Gio.Cancellable()

        host_header = self.http_host_header_row.get_text().strip()
        user_agent_to_send: Optional[str] = None
        selected_ua_title_in_http_page_dropdown: Optional[str] = None
        selected_item_obj = self.http_user_agent_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_ua_title_in_http_page_dropdown = selected_item_obj.get_string()

        if selected_ua_title_in_http_page_dropdown and selected_ua_title_in_http_page_dropdown != "None":
            user_agent_to_send = self._ua_title_to_value_map.get(selected_ua_title_in_http_page_dropdown)
            logging.info(
                f"HTTP Page: Using User-Agent from page dropdown selection: '{selected_ua_title_in_http_page_dropdown}'"
            )
            if (
                user_agent_to_send is None and selected_ua_title_in_http_page_dropdown != "None"
            ):
                logging.warning(
                    f"HTTP Page: UA title '{selected_ua_title_in_http_page_dropdown}' in dropdown but not in map. This is unexpected. Sending no specific UA (requests default)."
                )
        else:
            logging.info("HTTP Page: Page dropdown selection is 'None'. Using GSettings default User-Agent.")
            gsettings_default_title = self.settings.get_string("default-user-agent-title")
            if gsettings_default_title:
                ua_found_in_constants = False
                for ua_dict in USER_AGENTS:
                    if ua_dict["title"] == gsettings_default_title:
                        user_agent_to_send = ua_dict["value"]
                        ua_found_in_constants = True
                        break
                if ua_found_in_constants:
                    logging.info(
                        f"HTTP Page: Resolved GSettings default '{gsettings_default_title}' from standard User-Agents."
                    )
                else:
                    custom_uas_variant = self.settings.get_value("custom-user-agents")
                    custom_ua_pairs: list[tuple[str, str]] = list(
                        custom_uas_variant.unpack()
                        if custom_uas_variant and custom_uas_variant.get_type_string() == "a(ss)"
                        else []
                    )
                    ua_found_in_custom = False
                    for cust_title, cust_value in custom_ua_pairs:
                        if cust_title == gsettings_default_title:
                            user_agent_to_send = cust_value
                            ua_found_in_custom = True
                            break
                    if ua_found_in_custom:
                        logging.info(
                            f"HTTP Page: Resolved GSettings default '{gsettings_default_title}' from custom User-Agents."
                        )
                    else:
                        logging.warning(
                            f"HTTP Page: GSettings default User-Agent title '{gsettings_default_title}' not found in constants or custom UAs. Sending no specific UA (requests default)."
                        )
                        user_agent_to_send = None
            else:
                logging.info(
                    "HTTP Page: GSettings default User-Agent is 'None' (empty string). Sending no specific UA (requests default)."
                )
                user_agent_to_send = None

        custom_dns_server = self.settings.get_string("custom-dns-server")
        logging.info(
            f"HttpPage: Final User-Agent for request task: {user_agent_to_send if user_agent_to_send else 'None (requests default will be used)'}"
        )

        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header if host_header else None,
            "user_agent": user_agent_to_send,
            "custom_dns_server": custom_dns_server if custom_dns_server else None,
        }
        logger.debug("HttpPage: Starting header fetch task with data: %s", self._http_task_data_for_thread)

        # Pass the new cancellable to the task
        task = Gio.Task.new(self, self.current_http_cancellable, self._fetch_headers_task_done_cb, None)
        self.current_http_task = task
        # Gio.Task.run_in_thread passes its own cancellable to the thread func,
        # which is linked to the task's cancellable.
        task.run_in_thread(self._fetch_headers_task_thread_func)


    def _fetch_headers_task_thread_func(
        self,
        task: Gio.Task,
        _source_object: GObject.Object,
        _task_data_arg: dict[str, Any],
        cancellable: Gio.Cancellable,
    ) -> None:
        current_task_data = self._http_task_data_for_thread
        url_to_fetch: str = current_task_data["url"]

        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.CANCELLED.value,
                "Task cancelled before fetching.",
            )
            return

        fetcher = HttpFetcher(
            url=url_to_fetch,
            use_akamai_pragma=current_task_data["use_akamai_pragma"],
            host_header=current_task_data.get("host_header"),
            user_agent=current_task_data.get("user_agent"),
            custom_dns_server=current_task_data.get("custom_dns_server"),
            cancellable=cancellable,
        )

        try:
            processed_data = fetcher.fetch_headers()
            if cancellable and cancellable.is_cancelled(): # Check again after the blocking call
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.CANCELLED.value,
                    "Task cancelled during/after fetching.",
                )
            else:
                task.return_value(GLib.Variant.new_python(processed_data))
        except HttpRequestTimeoutError as e:
            if not (cancellable and cancellable.is_cancelled()):
                logger.warning("HttpPage Task: Timeout for '%s': %s", url_to_fetch, e)
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.TIMEOUT.value, str(e))
            else:
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Fetch cancelled during timeout.")
        except HttpConnectionError as e:
            if not (cancellable and cancellable.is_cancelled()):
                logger.warning("HttpPage Task: ConnectionError for '%s': %s", url_to_fetch, e)
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CONNECTION_ERROR.value, str(e))
            else:
                 task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Fetch cancelled during connection attempt.")
        except HttpProcessingError as e: # For HTTP status codes >= 400
            if not (cancellable and cancellable.is_cancelled()):
                logger.warning("HttpPage Task: HttpProcessingError for '%s': %s", url_to_fetch, e)
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.HTTP_ERROR.value, str(e))
            # No specific cancel for this as it's usually a quick error response
        except HttpGenericRequestError as e: # Other requests library exceptions
            if not (cancellable and cancellable.is_cancelled()):
                logger.warning("HttpPage Task: HttpGenericRequestError for '%s': %s", url_to_fetch, e)
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.REQUEST_EXCEPTION.value, str(e))
        except HttpClientError as e: # Custom base error, check if it's a cancellation
            if "cancelled" in str(e).lower() or (cancellable and cancellable.is_cancelled()):
                 task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, str(e))
            else: # Other client errors
                 task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, str(e))
        except Exception as e_generic: # Catch-all for truly unexpected issues
            if not (cancellable and cancellable.is_cancelled()):
                logger.exception("HttpPage Task: Unexpected generic error for URL '%s':", url_to_fetch)
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.GENERIC_UNEXPECTED.value,
                    f"Unexpected internal error for {url_to_fetch}: {str(e_generic)}",
                )
            else: # If an unknown exception occurred during a cancellation sequence
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Fetch cancelled with an unknown error.")


    def _fetch_headers_task_done_cb(
        self,
        _source_object: GObject.Object,
        result: Gio.AsyncResult,
        _user_data: Optional[Any] = None,
    ) -> None:
        task_being_processed = _source_object

        if task_being_processed != self.current_http_task and self.current_http_task is not None :
             logger.warning("_fetch_headers_task_done_cb: Callback for an outdated task %s. Current task is %s. Ignoring.", task_being_processed, self.current_http_task)
             return

        if task_being_processed == self.current_http_task:
            self.current_http_task = None
            # self.current_http_cancellable = None # Reset when new task starts or in dispose

        logger.info("Processing task completion in _fetch_headers_task_done_cb for task: %s", task_being_processed)
        user_url_for_messages = self._http_task_data_for_thread.get("url", "this URL")


        try:
            if task_being_processed is None:
                 logger.warning("_fetch_headers_task_done_cb: task_being_processed was None before result propagation.")
                 if not self.current_http_task: self._set_loading_state(False, "Idle - internal error.")
                 return

            propagated_result = task_being_processed.propagate_value(result) # type: ignore

            actual_list_of_responses: Optional[list[dict[str, Any]]] = None
            if isinstance(propagated_result, GLib.Variant):
                unpacked_value = propagated_result.unpack()
                if isinstance(unpacked_value, list):
                    actual_list_of_responses = unpacked_value
                else:
                    logger.error("HttpPage: GVariant did not unpack to a list: %s", type(unpacked_value))
            elif isinstance(propagated_result, list):
                actual_list_of_responses = propagated_result

            if actual_list_of_responses is None:
                logger.error("HttpPage: Unexpected type from propagate_value after GVariant check: %s", type(propagated_result))
                show_global_error(self, "Unexpected result type from background task.")
                if self.http_entry_row: self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                if not self.current_http_task: self._set_loading_state(False, "Error: Unexpected data format.")
                return

            logger.info("HttpPage: Successfully processed task result: %d response stages.", len(actual_list_of_responses))
            processed_headers_for_store: list[HeaderItem] = []
            status_message = "" # Initialize status message
            if not actual_list_of_responses:
                logger.info("HttpPage: Received empty list of responses.")
                self._update_column_view_model(None)
                status_message = f"No headers found for {user_url_for_messages}."
            else:
                for i, response_data_dict_item in enumerate(actual_list_of_responses):
                    if not isinstance(response_data_dict_item, dict):
                        logger.error("HttpPage: Expected dict item in response list, got %s.", type(response_data_dict_item))
                        continue
                    response_data_dict: dict[str, Any] = response_data_dict_item
                    url_display = f"URL: {response_data_dict.get('url', 'N/A')}"
                    status_code = response_data_dict.get("status_code", "N/A")
                    response_type = response_data_dict.get("type", "unknown")
                    status_display = f"Status: {status_code} ({str(response_type).capitalize()})"
                    processed_headers_for_store.append(HeaderItem(key=url_display, value=status_display, is_special_row=True))
                    headers_for_this_response = response_data_dict.get("headers", {})
                    if isinstance(headers_for_this_response, dict):
                        for key, value in headers_for_this_response.items():
                            original_value_str = str(value)
                            display_parts = []
                            current_value_segment = original_value_str
                            if original_value_str.strip() == "":
                                display_parts.append("")
                            else:
                                while True:
                                    semicolon_index = current_value_segment.find(";")
                                    if semicolon_index != -1:
                                        part_to_add = current_value_segment[: semicolon_index + 1].strip()
                                        display_parts.append(part_to_add)
                                        current_value_segment = current_value_segment[semicolon_index + 1 :]
                                    else:
                                        display_parts.append(current_value_segment.strip())
                                        break
                            first_part_processed = False
                            for part_content in display_parts:
                                if not first_part_processed:
                                    processed_headers_for_store.append(HeaderItem(key=str(key), value=part_content, is_special_row=False))
                                    first_part_processed = True
                                else:
                                    processed_headers_for_store.append(HeaderItem(key="", value=part_content, is_special_row=False))
                    if i < len(actual_list_of_responses) - 1:
                        processed_headers_for_store.append(HeaderItem(key="--- Redirected To ---", value="", is_special_row=True))
                self._current_header_items = processed_headers_for_store
                self._update_column_view_model(processed_headers_for_store)
                status_message = "Headers loaded successfully."

            if self.http_entry_row: self.http_entry_row.remove_css_class("error")
            if not self.current_http_task: self._set_loading_state(False, status_message)


        except GLib.Error as e:
            user_url_for_messages = self._http_task_data_for_thread.get("url", "this URL")
            logger.error(
                "HttpPage: Task for '%s' failed with GLib.Error (Domain: %s, Code: %d, Message: %s)",
                user_url_for_messages, e.domain, e.code, e.message, exc_info=True
            )

            if e.matches(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value):
                display_message = f"Fetch for {user_url_for_messages} was cancelled."
                logger.info(display_message)
                if not self.current_http_task:
                    self._set_loading_state(False, display_message)
                show_global_toast(self, display_message)
            else:
                display_message = e.message if e.message else "An unknown error occurred."
                if not ("<b>" in display_message or "<" in display_message and ">" in display_message):
                    display_message = GLib.markup_escape_text(display_message)
                show_global_error(self, display_message)
                if self.http_entry_row: self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                if not self.current_http_task:
                    self._set_loading_state(False, f"Error: {e.message.splitlines()[0]}")
        except Exception as e_generic:
            logger.exception("HttpPage: Unexpected Python error in _fetch_headers_task_done_cb:")
            error_message_str = f"An unexpected application error occurred: {e_generic}"
            show_global_error(self, error_message_str)
            if self.http_entry_row: self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
            if not self.current_http_task:
                self._set_loading_state(False, "Error: Unexpected application error.")
        finally:
            if not self.current_http_task:
                current_subtitle = self.http_status_row.get_subtitle() if self.http_status_row else "" # type: ignore
                _final_status_message = "Idle."
                if task_being_processed and task_being_processed.get_cancellable() and task_being_processed.get_cancellable().is_cancelled(): # type: ignore
                    _final_status_message = f"Fetch for {self._http_task_data_for_thread.get('url', 'operation')} cancelled."
                elif "Error:" not in current_subtitle and "loaded successfully" not in current_subtitle : # if not already set to a specific final state
                     pass # Keep the message from success/specific error
                else: # If it was an error or still fetching/cancelling, reset to Idle or Cancelled.
                    if "Error:" in current_subtitle: # If it was an error, just make it Idle
                         pass # Error message is already set by _set_loading_state(False, f"Error: ...")
                    # else, if it was "Fetching..." or "Cancelling...", it will become "Idle." or "Fetch ... cancelled."
                    # This logic path seems a bit convoluted, simplifying:

                # Simplified finally logic:
                # If no new task is running, ensure the UI is in a final state.
                if not self.current_http_task:
                    final_ui_message = self.http_status_row.get_subtitle() # type: ignore
                    if "Fetching headers..." in final_ui_message or "Cancelling fetch..." in final_ui_message:
                        # If task was cancelled, specific message is set by CANCELLED block
                        # If not cancelled, but still in progress somehow, set to Idle.
                        if not (task_being_processed and task_being_processed.get_cancellable() and task_being_processed.get_cancellable().is_cancelled()): # type: ignore
                           final_ui_message = "Idle."
                    self._set_loading_state(False, final_ui_message)


    def _set_loading_state(self, active: bool, message: str = "Idle") -> None:
        if self.http_status_spinner:
            self.http_status_spinner.set_visible(active)
            if active:
                self.http_status_spinner.start()
            else:
                self.http_status_spinner.stop()

        if self.http_status_row:
            self.http_status_row.set_subtitle(message) # type: ignore

        sensitive = not active
        if self.http_entry_row: self.http_entry_row.set_sensitive(sensitive)
        if self.http_apply_button: self.http_apply_button.set_sensitive(sensitive)
        if self.http_host_header_row: self.http_host_header_row.set_sensitive(sensitive)
        if self.http_user_agent_row: self.http_user_agent_row.set_sensitive(sensitive)
        if self.http_pragma_switch_row: self.http_pragma_switch_row.set_sensitive(sensitive)

        if self.http_cancel_button:
            self.http_cancel_button.set_visible(active)
            self.http_cancel_button.set_sensitive(active)

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        if "://" not in url:
            logger.debug("URL '%s' has no scheme, prepending 'https://'.", url)
            return "https://" + url
        return url

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        logger.debug("Akamai Pragma toggled: %s. Re-fetching if URL present.", self.http_pragma_switch_row.get_active())
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[list[HeaderItem]]) -> None:
        self.header_list_store.remove_all() # type: ignore
        if header_items:
            for item in header_items:
                self.header_list_store.append(item) # type: ignore
            self._show_results()
        else:
            self._hide_results()

    def _show_results(self) -> None:
        if self.http_results_group:
            self.http_results_group.set_visible(True) # type: ignore

    def _hide_results(self) -> None:
        if self.http_results_group:
            self.http_results_group.set_visible(False) # type: ignore

    def _clear_error(self) -> None:
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error() # type: ignore
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error")

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Results cleared by user action.")
        self._current_header_items = []
        self._update_column_view_model(None)
        self._clear_error()
        if self.http_entry_row:
            self.http_entry_row.set_text("")
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error")

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        logger.debug("Color setting changed for GSettings key: %s", key)
        if key == "http-output-header-key-color":
            self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color":
            self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color":
            self._special_row_color = settings.get_string(key)
        if self._current_header_items:
            logger.debug("Re-populating ColumnView to apply new color changes.")
            self._update_column_view_model(self._current_header_items)

    def _on_global_output_font_changed(self, settings: Gio.Settings, key: str) -> None:
        logger.debug("HttpPage: Global output font setting changed for key: %s", key)
        if key == self._output_font_gsettings_key:
            output_font_str = settings.get_string(key)
            self._output_font_desc = Pango.FontDescription.from_string(
                output_font_str if output_font_str else "Sans 10"
            )
            if self._current_header_items:
                logger.debug("HttpPage: Re-populating ColumnView to apply new global font.")
                self._update_column_view_model(self._current_header_items)

    def _update_user_agent_model(self) -> None:
        if not self.http_user_agent_row: # type: ignore
            logger.error("HttpPage: _update_user_agent_model: http_user_agent_row is None, cannot update model.")
            return
        self._ua_title_to_value_map.clear()
        display_titles: list[str] = ["None"]
        self._ua_title_to_value_map["None"] = None
        for ua_dict in USER_AGENTS:
            title = ua_dict["title"]
            value = ua_dict["value"]
            if title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value
            else:
                if title == "None":
                    logger.warning("A standard User-Agent is titled 'None'. This may cause confusion with the system default option.")
                else:
                    logger.warning(f"Standard User-Agent title '{title}' is a duplicate. Skipping.")

        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []
        )
        for title, value in custom_ua_pairs:
            if title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles)) # type: ignore

        default_ua_title_from_prefs = self.settings.get_string("default-user-agent-title")
        self._http_page_ua_is_following_gsettings_default = True
        effective_default_title = default_ua_title_from_prefs if default_ua_title_from_prefs else "None"
        self._select_ua_in_http_page_dropdown(effective_default_title)

    def _on_http_page_ua_selection_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec) -> None:
        selected_item_obj = combo_row.get_selected_item()
        if not isinstance(selected_item_obj, Gtk.StringObject):
            return

        selected_title_on_page = selected_item_obj.get_string()
        gsettings_default_ua_title = self.settings.get_string("default-user-agent-title")
        effective_gsettings_default = gsettings_default_ua_title if gsettings_default_ua_title else "None"

        if selected_title_on_page == "None":
            self._http_page_ua_is_following_gsettings_default = True
            logging.info(
                f"HttpPage: UA selection is 'None'. Now following GSettings. Current GSettings default: '{effective_gsettings_default}'."
            )
            self._select_ua_in_http_page_dropdown(effective_gsettings_default)
        elif selected_title_on_page == effective_gsettings_default:
            self._http_page_ua_is_following_gsettings_default = True
            logging.info(
                f"HttpPage: UA selection '{selected_title_on_page}' matches GSettings default. Now following GSettings."
            )
        else:
            self._http_page_ua_is_following_gsettings_default = False
            logging.info(
                f"HttpPage: UA selection is '{selected_title_on_page}'. This is a session override. Not following GSettings default ('{effective_gsettings_default}')."
            )

        self._on_user_agent_changed_visual_feedback(combo_row, _gparam)

    def _on_default_ua_gsetting_changed(self, settings: Gio.Settings, key: str) -> None:
        if key == "default-user-agent-title":
            new_gsettings_default_ua_title = settings.get_string(key)
            effective_new_gsettings_default = (
                new_gsettings_default_ua_title if new_gsettings_default_ua_title else "None"
            )

            if self._http_page_ua_is_following_gsettings_default:
                logging.info(
                    f"HttpPage: Currently following GSettings default. Updating dropdown to new GSettings default: '{effective_new_gsettings_default}'."
                )
                self._select_ua_in_http_page_dropdown(effective_new_gsettings_default)
            else:
                current_http_page_selection_obj = self.http_user_agent_row.get_selected_item()
                current_http_page_selected_title = "Unknown"
                if isinstance(current_http_page_selection_obj, Gtk.StringObject):
                    current_http_page_selected_title = current_http_page_selection_obj.get_string()
                logging.info(
                    f"HttpPage: Not following GSettings default (current page selection: '{current_http_page_selected_title}'). Ignoring GSettings change for dropdown update."
                )

    def _select_ua_in_http_page_dropdown(self, title_to_select: str) -> bool:
        model = self.http_user_agent_row.get_model() # type: ignore
        if not isinstance(model, Gtk.StringList):
            logging.error("HttpPage: http_user_agent_row model is not Gtk.StringList, cannot select UA.")
            return False

        all_titles_in_dropdown = [model.get_string(i) for i in range(model.get_n_items())] # type: ignore

        final_title_to_select = title_to_select
        if title_to_select not in all_titles_in_dropdown:
            logging.warning(f"HttpPage: UA Title '{title_to_select}' not found in dropdown. Falling back to 'None'.")
            final_title_to_select = "None"

        if final_title_to_select in all_titles_in_dropdown:
            try:
                idx = all_titles_in_dropdown.index(final_title_to_select)
                self.http_user_agent_row.set_selected(idx) # type: ignore
                self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None) # type: ignore
                return True
            except ValueError:
                logger.error(
                    f"HttpPage: Error selecting '{final_title_to_select}' (ValueError) despite it being in list. This is unexpected."
                )
                return False
        elif model.get_n_items() > 0: # type: ignore
            self.http_user_agent_row.set_selected(0) # type: ignore
            logging.error(
                "HttpPage: Critical - Could not find target UA nor fallback 'None' in dropdown. Selected first available item."
            )
            self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None) # type: ignore
            return False

        logging.warning(
            "HttpPage: Could not select any UA in dropdown (model might be empty or 'None' option missing)."
        )
        return False

    def do_dispose(self):
        if self._gsettings_ua_changed_handler_id and self.settings.is_connected(self._gsettings_ua_changed_handler_id):
            self.settings.disconnect(self._gsettings_ua_changed_handler_id)
        self._gsettings_ua_changed_handler_id = 0
        # Cancel any ongoing task
        if self.current_http_cancellable and not self.current_http_cancellable.is_cancelled():
            self.current_http_cancellable.cancel()
            logger.info("HttpPage disposed, ongoing task cancelled.")
        self.current_http_task = None
        self.current_http_cancellable = None
        super().do_dispose()

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()
        # ... (setup_func and bind_func_internal as before, omitted for brevity) ...
        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0.0, hexpand=True)
            if wrap_text:
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(80)
            list_item.set_child(label)
        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child() # type: ignore
            item = list_item.get_item() # type: ignore
            if not (isinstance(label, Gtk.Label) and isinstance(item, HeaderItem)):
                if isinstance(label, Gtk.Label): label.set_text("Error: Invalid item type.")
                return
            text_to_display = getattr(item, attr_name, "")
            family = self._output_font_desc.get_family()
            size_in_pango_units = self._output_font_desc.get_size()
            current_family = family if family else "Sans"
            if item.is_special_row:
                if attr_name == "key":
                    key_text = GLib.markup_escape_text(str(item.key))
                    value_text = GLib.markup_escape_text(str(item.value)) if item.value else ""
                    full_text = key_text
                    if value_text.strip() and value_text != "N/A": full_text += f" {value_text}"
                    label.set_markup(
                        f"<b><span font_family='{GLib.markup_escape_text(current_family)}' size='{size_in_pango_units}' foreground='{self._special_row_color}'>{full_text}</span></b>"
                    )
                else: label.set_markup("")
            else:
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(str(text_to_display))
                label.set_markup(
                    f"<span font_family='{GLib.markup_escape_text(current_family)}' size='{size_in_pango_units}' foreground='{color_to_use}'>{escaped_text}</span>"
                )
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory


    def trigger_fetch(self) -> None:
        logging.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.activate()
        else:
            logger.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")

    def _on_http_entry_changed(self, editable: Adw.EntryRow) -> None:
        if editable.get_text() != "":
            if editable.has_css_class("error"):
                editable.remove_css_class("error")
                main_window = self.get_native()
                if main_window and hasattr(main_window, "hide_error_if_message_matches"):
                     main_window.hide_error_if_message_matches("Invalid URL. Please enter a valid URL (e.g., https://example.com).") # type: ignore[attr-defined]
                # No explicit else to call main_window.hide_error() to avoid hiding unrelated errors.
