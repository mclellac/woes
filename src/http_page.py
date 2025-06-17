"""
Defines the HTTP Headers page for the Woes application.

This module provides the :class:`.HttpPage` class, which allows users to fetch
and inspect HTTP headers for a given URL. Key features include:

- Asynchronous fetching of HTTP headers to keep the UI responsive.
- Support for custom 'Host' headers.
- Selection of predefined or custom 'User-Agent' strings.
- Option to include Akamai debugging Pragma headers.
- Display of redirect history and final response headers.
- Integration with GSettings for persisting user preferences like custom User-Agents
  and UI theme colors for syntax highlighting of headers.
- Copy-to-clipboard functionality for results.
"""

import logging
from enum import Enum
from typing import Any, List, Optional, Dict, Final, Tuple

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk, Pango

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

# Domain for Gio.Task errors, useful for identifying source of GLib.Error
WOES_HTTP_ERROR_DOMAIN = "woes-http-error-domain"


class HttpErrorType(int, Enum):
    """
    Enumeration of HTTP error types for Gio.Task error reporting.

    These values are used as the error code when returning a :class:`GLib.Error`
    from an asynchronous task if a specific Python exception from :mod:`.http_client`
    is not being propagated directly.
    """

    TIMEOUT = 0
    HTTP_ERROR = 1  # Typically for 4xx/5xx status codes
    CONNECTION_ERROR = 2
    REQUEST_EXCEPTION = 3 # Other requests.RequestException
    GENERIC_UNEXPECTED = 4
    CANCELLED = 5


class HeaderItem(GObject.Object):
    """
    A GObject representation of an HTTP header or a special informational row.

    Used as the item type for the :class:`Gio.ListStore` that backs
    the :class:`Gtk.ColumnView` displaying headers.

    :ivar key: The header name or special row title (e.g., "URL: ...").
    :vartype key: str
    :ivar value: The header value or special row subtitle (e.g., "Status: ...").
    :vartype value: str
    :ivar is_special_row: ``True`` if this item represents a special informational
                          row (like URL/Status or redirect indicators), ``False`` if it's
                          a standard key-value header pair.
    :vartype is_special_row: bool
    """

    key: str
    value: str
    is_special_row: bool

    def __init__(self, key: str, value: str, is_special_row: bool = False):
        """
        Initialize a HeaderItem.

        :param key: The header name or special row key/title.
        :param value: The header value or special row value/subtitle.
        :param is_special_row: Whether this item is a special informational row.
                               Defaults to ``False``.
        """
        super().__init__()
        self.key = key
        self.value = value
        self.is_special_row = is_special_row


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Gtk.Box):
    """
    Manages the HTTP Headers tab, allowing users to fetch and view HTTP headers.

    This class handles UI interactions, asynchronous fetching of headers using
    :class:`.HttpFetcher`, display of results (including redirects and errors)
    in a :class:`Gtk.ColumnView`, and integration with application settings
    for features like custom User-Agents and theme-based syntax highlighting.

    :ivar __gtype_name__: GObject type name.
    :vartype __gtype_name__: str
    :ivar _current_header_items: Data for the ListStore.
    :vartype _current_header_items: List[HeaderItem]
    :ivar http_entry_row: :class:`Adw.EntryRow` for URL input.
    :vartype http_entry_row: Adw.EntryRow
    :ivar http_apply_button: :class:`Gtk.Button` to initiate header fetching.
    :vartype http_apply_button: Gtk.Button
    :ivar http_host_header_row: :class:`Adw.EntryRow` for custom 'Host' header input.
    :vartype http_host_header_row: Adw.EntryRow
    :ivar http_user_agent_row: :class:`Adw.ComboRow` for User-Agent selection.
    :vartype http_user_agent_row: Adw.ComboRow
    :ivar http_pragma_switch_row: :class:`Adw.SwitchRow` to toggle Akamai Pragma headers.
    :vartype http_pragma_switch_row: Adw.SwitchRow
    :ivar http_column_view: :class:`Gtk.ColumnView` to display header results.
    :vartype http_column_view: Gtk.ColumnView
    :ivar http_results_group: :class:`Adw.PreferencesGroup` containing the results view.
    :vartype http_results_group: Adw.PreferencesGroup
    :ivar clear_results_button: :class:`Gtk.Button` to clear results.
    :vartype clear_results_button: Gtk.Button
    :ivar copy_results_button: :class:`Gtk.Button` to copy results to clipboard.
    :vartype copy_results_button: Gtk.Button
    :ivar http_status_row: :class:`Adw.ActionRow` to display status messages.
    :vartype http_status_row: Adw.ActionRow
    :ivar http_status_spinner: :class:`Gtk.Spinner` to indicate loading.
    :vartype http_status_spinner: Gtk.Spinner
    """

    __gtype_name__: str = "HttpPage"

    SYSTEM_DEFAULT_UA_TITLE: Final[str] = "System Default (requests)"

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

    def __init__(self, **kwargs: Any):
        """
        Initialize the HttpPage.

        Sets up GSettings, UI styles, connects signals, initializes the User-Agent model,
        and configures the ColumnView for displaying headers.

        :param kwargs: Keyword arguments passed to the :class:`Gtk.Box` constructor.
        """
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task: Optional[Gio.Task] = None
        self._current_header_items: List[HeaderItem] = []
        self._http_task_data_for_thread: dict[str, Any] = {}
        self._ua_title_to_value_map: dict[str, Optional[str]] = {}
        self.settings: Gio.Settings = Gio.Settings(schema_id=APP_ID)

        # Color and font settings for results display
        self._gsettings_ua_changed_handler_id: int = 0
        self._header_key_color: str = self.settings.get_string("http-output-header-key-color")
        self._header_value_color: str = self.settings.get_string("http-output-header-value-color")
        self._special_row_color: str = self.settings.get_string("http-output-special-row-color")

        self._output_font_gsettings_key: str = "output-font"
        output_font_str: str = self.settings.get_string(self._output_font_gsettings_key)
        font_desc_str = output_font_str if output_font_str else "Sans 10"
        self._output_font_desc: Pango.FontDescription = (
            Pango.FontDescription.from_string(font_desc_str)
        )

        # Connect GSettings change signals
        self.settings.connect(
            f"changed::{'http-output-header-key-color'}",
            self._on_color_setting_changed
        )
        self.settings.connect(
            f"changed::{'http-output-header-value-color'}",
            self._on_color_setting_changed
        )
        self.settings.connect(
            f"changed::{'http-output-special-row-color'}",
            self._on_color_setting_changed
        )
        self.settings.connect(
            f"changed::{self._output_font_gsettings_key}",
            self._on_global_output_font_changed
        )

        # Setup ColumnView
        self.header_list_store = Gio.ListStore.new(item_type=HeaderItem)
        selection_model = Gtk.MultiSelection.new(self.header_list_store)
        self.http_column_view.set_model(selection_model)
        if not self.http_column_view.get_columns(): # Ensure columns are added only once
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

        # Initial UI styling and state
        if self.http_apply_button:
            self.http_apply_button.get_style_context().add_class("suggested-action")
        if self.clear_results_button:
            self.clear_results_button.get_style_context().add_class("destructive-action")
        if self.copy_results_button:
            self.copy_results_button.get_style_context().add_class("flat")
        if self.http_host_header_row: # Ensure it exists before connecting
            self._on_host_header_changed(self.http_host_header_row) # Initial visual state
        if self.http_user_agent_row: # Ensure it exists
            self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None) # Initial visual state

        # Initialize the ColumnView context menu helper
        if self.http_column_view and self.get_native(): # Ensure ColumnView and window exist
            self.column_view_helper = Helper(widget=self.http_column_view, parent_window=self.get_native())

        self._hide_results() # Initially no results to show

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        if self.http_entry_row:
            self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        if self.http_apply_button:
            self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        if self.http_pragma_switch_row:
            self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        if self.clear_results_button:
            self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_host_header_row:
            self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row:
            self.http_user_agent_row.connect("notify::selected-item", self._on_http_page_ua_selection_changed)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        """
        Update visual cue on the Host header EntryRow if it has text.

        :param entry_row: The :class:`Adw.EntryRow` for the Host header.
        """
        if not entry_row:
            return
        if entry_row.get_text().strip():
            entry_row.add_css_class("active-override")
        else:
            entry_row.remove_css_class("active-override")

    def _on_user_agent_changed_visual_feedback(
        self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]
    ) -> None:
        """
        Update visual cue on the User-Agent ComboRow based on selection.

        Adds 'active-override' class if not using system default.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        """
        if not combo_row:
            return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            if selected_title == self.SYSTEM_DEFAULT_UA_TITLE:
                combo_row.remove_css_class("active-override")
            else:
                combo_row.add_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map: # Handle empty model case
            combo_row.remove_css_class("active-override")

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle click of the 'Copy Results' button.

        Formats all currently displayed headers (including redirect information)
        into a single text block and copies it to the clipboard.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        """
        logger.info("Copying all headers to clipboard.")
        if not self.header_list_store or self.header_list_store.get_n_items() == 0:
            show_global_toast(self, "No results to copy.")
            return

        lines: List[str] = []
        for i in range(self.header_list_store.get_n_items()):
            item = self.header_list_store.get_item(i)
            if isinstance(item, HeaderItem):
                if item.is_special_row:
                    # For special rows, key is the main info, value might be parenthesized status
                    line = item.key
                    if item.value and item.value.strip() and item.value.lower() != "n/a":
                        line += f" {item.value}"
                    lines.append(line)
                else:
                    # For standard headers, format as "Key: Value"
                    # If key is empty, it's a continuation line, just append value.
                    if item.key:
                        lines.append(f"{item.key}: {item.value}")
                    else:
                        lines.append(f"  {item.value}") # Indent continuation lines for readability

        if not lines:
            show_global_toast(self, "No content formatted for copying.")
            return

        text_to_copy = "\n".join(lines)
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            if clipboard:
                clipboard.set_text(text_to_copy) # Using set_text for simplicity
                logger.info("Headers copied to clipboard successfully.")
                show_global_toast(self, "Headers copied to clipboard.")
            else:
                logger.warning("Failed to get default clipboard for copying.")
                show_global_error(self, "Failed to access clipboard.")
        except Exception as e:
            logger.error("Error copying headers to clipboard: %s", e, exc_info=True)
            show_global_error(self, f"Error copying to clipboard: {e}")


    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        """
        Handle URL input activation (Enter key or Apply button).

        Validates the URL, sets loading state, and initiates asynchronous header fetching.

        :param _widget: The widget that triggered the activation (unused).
        """
        original_url = self.http_entry_row.get_text().strip() if self.http_entry_row else ""

        if not original_url:
            show_global_toast(self, "URL cannot be empty.")
            return

        url_to_fetch = self._ensure_scheme(original_url)

        if not is_valid_url(url_to_fetch):
            logger.warning("HTTP Page: Invalid URL provided: %s (processed as: %s)", original_url, url_to_fetch)
            toast_message = "Invalid URL format. Please enter a valid URL (e.g., https://example.com)."
            show_global_error(self, toast_message) # More prominent for invalid URL
            if self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None) # Clear previous results
            self._set_loading_state(False, "Idle - Invalid URL.")
            return

        self._clear_error()
        self._set_loading_state(True, f"Fetching headers for {url_to_fetch}...")

        host_header = self.http_host_header_row.get_text().strip() if self.http_host_header_row else None

        user_agent_to_send: Optional[str] = None
        selected_ua_title_in_dropdown: Optional[str] = None
        if self.http_user_agent_row:
            selected_item_obj = self.http_user_agent_row.get_selected_item()
            if isinstance(selected_item_obj, Gtk.StringObject):
                selected_ua_title_in_dropdown = selected_item_obj.get_string()

        if selected_ua_title_in_dropdown and selected_ua_title_in_dropdown != self.SYSTEM_DEFAULT_UA_TITLE:
            user_agent_to_send = self._ua_title_to_value_map.get(selected_ua_title_in_dropdown)
            log_msg = f"HTTP Page: Using User-Agent from dropdown: '{selected_ua_title_in_dropdown}'"
            if user_agent_to_send is None: # Should not happen if map is correct
                 log_msg += " (Warning: value not found in map, sending None)."
            logger.info(log_msg)
        else:
            logger.info("HTTP Page: Using system default User-Agent (requests library default).")
            user_agent_to_send = None # Explicitly None for requests default

        custom_dns_server = self.settings.get_string("custom-dns-server")

        self._http_task_data_for_thread = {
            "url": url_to_fetch,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active() if self.http_pragma_switch_row else False,
            "host_header": host_header if host_header else None,
            "user_agent": user_agent_to_send,
            "custom_dns_server": custom_dns_server if custom_dns_server else None,
        }
        logger.debug("HttpPage: Starting header fetch task with data: %s", self._http_task_data_for_thread)

        if self.current_http_task and not self.current_http_task.is_done():
            try:
                if self.current_http_task.get_cancellable():
                    self.current_http_task.get_cancellable().cancel()
                logger.info("HttpPage: Previous HTTP fetch task cancelled.")
            except Exception as e_cancel:
                logger.warning("HttpPage: Error cancelling previous HTTP task: %s", e_cancel)

        cancellable = Gio.Cancellable()
        self.current_http_task = Gio.Task.new(self, cancellable, self._fetch_headers_task_done_cb, None)
        self.current_http_task.set_task_data(self._http_task_data_for_thread) # Store data with the task
        self.current_http_task.run_in_thread(self._fetch_headers_task_thread_func)

    def _fetch_headers_task_thread_func(
        self, task: Gio.Task, source_object: GObject.Object, task_data: Any, cancellable: Gio.Cancellable
    ) -> None:
        """
        Perform HTTP header fetching in a background thread.

        Retrieves parameters from task data, uses :class:`.HttpFetcher`.
        Returns results or errors via the task.

        :param task: The :class:`Gio.Task` for this operation.
        :type task: Gio.Task
        :param source_object: Source object (unused).
        :type source_object: GObject.Object
        :param task_data: Task data parameter (unused, data retrieved from `task.get_task_data()`).
        :type task_data: Any
        :param cancellable: The :class:`Gio.Cancellable` for this task.
        :type cancellable: Gio.Cancellable
        """
        # task_data is already set on the task object by the caller
        current_task_data: dict[str, Any] = task.get_task_data()
        url_to_fetch: str = current_task_data["url"]

        if cancellable.is_cancelled():
            task.return_error(GLib.Error.new_literal(WOES_HTTP_ERROR_DOMAIN, HttpErrorType.CANCELLED.value, "Task cancelled before fetching."))
            return

        fetcher = HttpFetcher(
            url=url_to_fetch,
            use_akamai_pragma=current_task_data["use_akamai_pragma"],
            host_header=current_task_data.get("host_header"),
            user_agent=current_task_data.get("user_agent"),
            custom_dns_server=current_task_data.get("custom_dns_server"),
            cancellable=cancellable, # Pass cancellable to HttpFetcher
        )
        try:
            processed_data: list[dict[str, Any]] = fetcher.fetch_headers()
            if cancellable.is_cancelled(): # Check again after fetch_headers returns
                task.return_error(GLib.Error.new_literal(WOES_HTTP_ERROR_DOMAIN, HttpErrorType.CANCELLED.value, "Task cancelled after fetching."))
            else:
                task.return_value(processed_data)
        except HttpClientError as e: # Catch custom exceptions from HttpFetcher
            task.get_task_data()["original_exception"] = e
            task.get_task_data()["original_exception_type_name"] = type(e).__name__

            error_type_map = {
                HttpRequestTimeoutError: HttpErrorType.TIMEOUT,
                HttpConnectionError: HttpErrorType.CONNECTION_ERROR,
                HttpProcessingError: HttpErrorType.HTTP_ERROR,
                HttpGenericRequestError: HttpErrorType.REQUEST_EXCEPTION,
            }
            glib_error_code = error_type_map.get(type(e), HttpErrorType.GENERIC_UNEXPECTED).value
            if "cancelled" in str(e).lower(): # Override if cancellation specific message in HttpClientError
                 glib_error_code = HttpErrorType.CANCELLED.value
            task.return_error(GLib.Error.new_literal(WOES_HTTP_ERROR_DOMAIN, glib_error_code, str(e)))
        except Exception as e: # Catch any other unexpected errors from fetcher
            logger.exception("HttpPage Task: Unexpected error from HttpFetcher for URL '%s':", url_to_fetch)
            task.get_task_data()["original_exception"] = e
            task.get_task_data()["original_exception_type_name"] = type(e).__name__
            task.return_error(GLib.Error.new_literal(WOES_HTTP_ERROR_DOMAIN, HttpErrorType.GENERIC_UNEXPECTED.value, f"Unexpected internal error: {e}"))

    def _fetch_headers_task_done_cb(
        self, source_object: GObject.Object, result: Gio.AsyncResult, user_data: Any = None
    ) -> None:
        """
        Handle completion of the asynchronous HTTP header fetch task.

        This method is called in the main GTK thread. It processes the
        result from the background task (retrieved via `propagate_value`)
        and updates the UI accordingly with headers or error messages.

        :param source_object: The source object (HttpPage instance).
        :type source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param user_data: User data passed with the callback (unused).
        :type user_data: Any
        """
        # Ensure this callback is for the current task.
        if not self.current_http_task or not self.current_http_task.matches_async_result(result):
            logger.warning("HttpPage: Callback received for an outdated or mismatched HTTP task.")
            if not self.current_http_task or self.current_http_task.is_done():
                self._set_loading_state(False, "Idle.")
            return

        task_data_from_task_obj: dict[str, Any] = self.current_http_task.get_task_data()
        original_url_for_log = task_data_from_task_obj.get("url", "unknown URL")

        try:
            returned_value = self.current_http_task.propagate_value(result) # This will raise GLib.Error if task failed

            # If propagate_value didn't raise, task was successful.
            # returned_value should be List[Dict] from thread_func
            if isinstance(returned_value, list):
                data: list[dict[str, Any]] = returned_value
                error_msg = None
            else: # Should not happen if thread_func returns correctly
                logger.error(
                    "HttpPage: Unexpected data type '%s' from successful task for URL '%s'.",
                    type(returned_value),
                    original_url_for_log
                )
                data = None
                error_msg = "Received unexpected data format from background task."

            if error_msg:
                # This block would typically be hit if process_task_result was used and it returned an error string
                show_global_error(self, error_msg)
                if self.http_entry_row:
                    self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                self._set_loading_state(False, f"Error: {error_msg.splitlines()[0]}")
            elif data is not None: # data is List[Dict[str, Any]], result from HttpFetcher
                # The following loop that populated self._current_header_items directly from `data` was removed
                # as it was redundant and based on a misunderstanding of HttpFetcher's output structure.
                # `processed_headers_for_store` is now the sole source for `self._current_header_items`.

                processed_headers_for_store: List[HeaderItem] = []
                for i, response_data_dict_item in enumerate(data): # `data` is the actual list of responses
                    if not isinstance(response_data_dict_item, dict):
                        continue

                    url_display = f"URL: {response_data_dict_item.get('url', 'N/A')}" # pyright: ignore[reportUnknownMemberType]
                    status_code = response_data_dict_item.get("status_code", "N/A") # pyright: ignore[reportUnknownMemberType]
                    response_type = response_data_dict_item.get("type", "unknown") # pyright: ignore[reportUnknownMemberType] # 'redirect' or 'final'
                    status_display = f"Status: {status_code} ({str(response_type).capitalize()})"
                    # For special rows, key is main info, value is parenthesized status
                    processed_headers_for_store.append(
                        HeaderItem(key=url_display, value=status_display, is_special_row=True)
                    )

                    headers_for_this_response = response_data_dict_item.get("headers", {}) # pyright: ignore[reportUnknownMemberType]
                    if isinstance(headers_for_this_response, dict):
                        for key, value in headers_for_this_response.items():
                            original_value_str = str(value)
                            display_parts: List[str] = []
                            current_value_segment = original_value_str
                            if not original_value_str.strip():
                                display_parts.append("") # Keep empty values if they are intentional
                            else:
                                # Split known multi-value headers like Set-Cookie, Cache-Control by comma then semicolon
                                # For simplicity, only splitting by semicolon here as a general approach
                                while True:
                                    idx = current_value_segment.find(";")
                                    if idx != -1:
                                        part = current_value_segment[:idx+1].strip()
                                        if part:
                                            display_parts.append(part)
                                        current_value_segment = current_value_segment[idx+1:]
                                    else:
                                        part = current_value_segment.strip()
                                        if part:
                                            display_parts.append(part)
                                        break
                            first_part_processed = False
                            if not display_parts: # If original_value_str was empty or only ";"
                                processed_headers_for_store.append(HeaderItem(key=str(key), value="", is_special_row=False))
                            else:
                                for part_content in display_parts:
                                    if not first_part_processed:
                                        processed_headers_for_store.append(HeaderItem(key=str(key), value=part_content, is_special_row=False))
                                        first_part_processed = True
                                    else: # Continuation line for a multi-part header
                                        processed_headers_for_store.append(HeaderItem(key="", value=part_content, is_special_row=False))

                    if response_type == "redirect" and i < len(data) - 1 : # If redirect and not last item
                        processed_headers_for_store.append(
                            HeaderItem(key="--- Redirected To ---", value="", is_special_row=True)
                        )

                self._current_header_items = processed_headers_for_store
                self._update_column_view_model(self._current_header_items)
                if self.http_entry_row:
                    self.http_entry_row.remove_css_class("error")
                self._set_loading_state(False, "Headers loaded successfully.")
            else:
                # This case (data is None, error_msg is None) should ideally be handled by process_task_result returning an error_msg
                show_global_error(self, "Failed to process data from background task (data is None).")
                if self.http_entry_row:
                    self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                self._set_loading_state(False, "Error: Failed to process data.")

        except GLib.Error as e: # Catch errors propagated by Gio.Task.propagate_value()
            original_py_exception = task_data_from_task_obj.get("original_exception")
            error_to_handle = original_py_exception if original_py_exception else e

            error_message = str(error_to_handle)
            user_facing_error_message = error_message # Default to full message
            status_subtitle = f"Error: {error_message.splitlines()[0]}"

            if isinstance(error_to_handle, HttpRequestTimeoutError):
                user_facing_error_message = f"Request timed out for {original_url_for_log}."
                status_subtitle = "Error: Request Timeout."
            elif isinstance(error_to_handle, HttpConnectionError):
                # _get_detailed_connection_error_message was part of HttpFetcher,
                # we use the message from the exception directly.
                user_facing_error_message = error_message
                status_subtitle = f"Error: Connection Failed for {original_url_for_log}."
            elif isinstance(error_to_handle, HttpProcessingError):
                user_facing_error_message = error_message # Already formatted by HttpFetcher
                url_in_error = error_to_handle.url or original_url_for_log # pyright: ignore[reportUnknownMemberType]
                status_subtitle = f"Error: HTTP {error_to_handle.status_code} for {url_in_error}" # pyright: ignore[reportUnknownMemberType]
            elif isinstance(error_to_handle, HttpGenericRequestError):
                user_facing_error_message = (
                    f"Request failed for {original_url_for_log}: {error_message}"
                )
                status_subtitle = f"Error: Request Failed for {original_url_for_log}"
            elif isinstance(error_to_handle, HttpClientError) and \
                 "cancelled" in error_message.lower(): # From HttpFetcher's own cancellation checks
                 user_facing_error_message = f"Request cancelled for {original_url_for_log}."
                 status_subtitle = "Request Cancelled."
                 show_global_toast(self, status_subtitle) # Toast for cancellation
            elif e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED): # Gio cancellation
                user_facing_error_message = f"Lookup for {original_url_for_log} was cancelled."
                status_subtitle = "Lookup Cancelled."
                show_global_toast(self, status_subtitle) # Toast for cancellation
            else: # Other GLib.Error or unexpected Python exceptions from thread
                logger.exception(
                    "HttpPage: Error processing task result for URL '%s': %s",
                    original_url_for_log, e
                )
                user_facing_error_message = (
                    f"An unexpected error occurred: {str(e).splitlines()[0]}"
                )
                status_subtitle = "Error: Unexpected."

            # Show error unless it's a cancellation type already handled by a toast
            is_cancellation_by_http_client = (
                isinstance(error_to_handle, HttpClientError) and
                "cancelled" in str(error_to_handle).lower()
            )
            is_cancellation_by_gio = (
                isinstance(e, GLib.Error) and
                e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
            )
            if not (is_cancellation_by_http_client or is_cancellation_by_gio):
                show_global_error(self, user_facing_error_message)

            if self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None) # Clear results on error
            self._set_loading_state(False, status_subtitle)

        except Exception as e_unexpected: # Catch-all for any other unexpected error in this callback
            logger.exception("HttpPage: Truly unexpected error in _fetch_headers_task_done_cb for URL '%s':", original_url_for_log)
            final_error_message = f"A critical unexpected error occurred: {str(e_unexpected).splitlines()[0]}"
            show_global_error(self, final_error_message)
            self._update_column_view_model(None)
            self._set_loading_state(False, "Critical Error.")
        finally:
            if self.current_http_task is None: # If task was cleared, ensure UI is idle
                if self.http_status_row and \
                   self.http_status_row.get_subtitle() not in ["Idle", "Idle - Invalid URL."]:
                    # Check if it was already set to a final state by error/success handling
                    current_status = self.http_status_row.get_subtitle()
                    # if status was "Fetching" or an error/timeout from this op
                    if "Fetching headers..." in current_status or \
                       "Error:" in current_status or \
                       "Timeout:" in current_status:
                        self._set_loading_state(False, "Idle - operation ended.")
            self.current_http_task = None


    def _set_loading_state(self, active: bool, message: str = "Idle") -> None:
        """
        Update UI elements to reflect loading or idle state.

        :param active: ``True`` if loading, ``False`` otherwise.
        :param message: Status message to display. Defaults to "Idle".
        """
        if self.http_status_spinner:
            self.http_status_spinner.set_visible(active)
            if active:
                self.http_status_spinner.start()
            else:
                self.http_status_spinner.stop()
        if self.http_status_row:
            self.http_status_row.set_subtitle(message)

        sensitive = not active
        if self.http_entry_row:
            self.http_entry_row.set_sensitive(sensitive)
        if self.http_apply_button:
            self.http_apply_button.set_sensitive(sensitive)
        if self.http_host_header_row:
            self.http_host_header_row.set_sensitive(sensitive)
        if self.http_user_agent_row:
            self.http_user_agent_row.set_sensitive(sensitive)
        if self.http_pragma_switch_row:
            self.http_pragma_switch_row.set_sensitive(sensitive)

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        """
        Ensure the URL has a scheme, defaulting to https if missing.

        :param url: The URL string.
        :return: The URL string with a scheme.
        """
        if "://" not in url:
            return "https://" + url
        return url

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        """
        Handle Akamai Pragma switch toggle.

        If there's a URL in the entry, re-fetches headers with the new Pragma setting.

        :param _widget: The :class:`Gtk.Switch` that was toggled (unused).
        :type _widget: Gtk.Switch
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        if self.http_entry_row and self.http_entry_row.get_text().strip():
            logger.debug("Pragma toggled, re-triggering fetch with current URL.")
            self._on_entry_row_activated(self.http_entry_row) # Or call _perform_lookup directly

    def _update_column_view_model(self, header_items: Optional[List[HeaderItem]]) -> None:
        """
        Update the ColumnView's ListStore with new header items.

        Clears existing items and appends new ones. Shows or hides the results group.

        :param header_items: A list of :class:`.HeaderItem` objects, or ``None`` to clear.
        """
        if not self.header_list_store:
            return

        self.header_list_store.remove_all()
        if header_items:
            for item in header_items:
                self.header_list_store.append(item)
            self._show_results()
        else:
            self._hide_results()

    def _show_results(self) -> None:
        """Make the HTTP results group visible."""
        if self.http_results_group:
            self.http_results_group.set_visible(True)

    def _hide_results(self) -> None:
        """Make the HTTP results group invisible."""
        if self.http_results_group:
            self.http_results_group.set_visible(False)

    def _clear_error(self) -> None:
        """Clear error styling on input field and hide global error messages."""
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error")

        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error") and callable(main_window.hide_error):
            main_window.hide_error()
        else:
            logger.debug("HttpPage: Main window or hide_error method not found/callable for _clear_error.")


    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle click of the 'Clear Results' button.

        Clears current header items, updates the view, clears errors, and clears the URL entry.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        """
        logger.info("Clear results button clicked.")
        self._current_header_items = []
        self._update_column_view_model(None) # This will also call _hide_results
        self._clear_error()
        if self.http_entry_row:
            self.http_entry_row.set_text("")
        if self.http_status_row:
            self.http_status_row.set_subtitle("Idle")


    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        """
        Handle changes to GSettings related to header colorization.

        Updates internal color attributes and refreshes the ColumnView if results are present.

        :param settings: The :class:`Gio.Settings` object that changed.
        :param key: The GSettings key that was changed.
        """
        logger.debug("HTTP color setting changed: %s", key)
        if key == "http-output-header-key-color":
            self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color":
            self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color":
            self._special_row_color = settings.get_string(key)

        if self._current_header_items: # Only refresh if there are items to re-color
            self._update_column_view_model(self._current_header_items)

    def _on_global_output_font_changed(self, settings: Gio.Settings, key: str) -> None:
        """
        Handle changes to the global output font GSetting.

        Updates the internal font description and refreshes the ColumnView.

        :param settings: The :class:`Gio.Settings` object that changed.
        :param key: The GSettings key that was changed.
        """
        if key == self._output_font_gsettings_key:
            output_font_str = settings.get_string(key)
            font_desc_str = output_font_str if output_font_str else "Sans 10"
            self._output_font_desc = Pango.FontDescription.from_string(font_desc_str)
            logger.info("HttpPage: Global output font updated. Refreshing view if results exist.")
            if self._current_header_items: # Only refresh if there are items to re-font
                self._update_column_view_model(self._current_header_items)

    def _update_user_agent_model(self) -> None:
        """
        Update the User-Agent dropdown (:class:`Adw.ComboRow`) model.

        Populates the dropdown with system default, predefined :const:`.USER_AGENTS`,
        and any custom User-Agents from GSettings. Sets the selection based on
        the 'default-user-agent-title' GSetting.
        """
        if not self.http_user_agent_row:
            logger.error("HttpPage: http_user_agent_row is None, cannot update UA model.")
            return

        self._ua_title_to_value_map.clear()
        display_titles: List[str] = [self.SYSTEM_DEFAULT_UA_TITLE]
        self._ua_title_to_value_map[self.SYSTEM_DEFAULT_UA_TITLE] = None # None signifies requests' default

        for ua_dict in USER_AGENTS: # From constants.py
            title, value = ua_dict["title"], ua_dict["value"]
            if title == self.SYSTEM_DEFAULT_UA_TITLE: # Already added
                continue
            if title not in self._ua_title_to_value_map: # Avoid duplicates from constants
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        # Add custom UAs from GSettings
        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: List[Tuple[str, str]] = []
        if variant and variant.get_type_string() == "a(ss)":
            custom_ua_pairs = list(variant.unpack())

        for title, value in custom_ua_pairs:
            if title == self.SYSTEM_DEFAULT_UA_TITLE: # Sentinel value reserved
                continue
            if title not in self._ua_title_to_value_map: # Avoid duplicates from GSettings if any
                display_titles.append(title)
            # GSettings custom UAs override predefined ones if titles match
            self._ua_title_to_value_map[title] = value

        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles))

        # Set selection based on GSettings default-user-agent-title
        gsettings_default_ua_title = self.settings.get_string("default-user-agent-title")
        title_to_select_initially = self.SYSTEM_DEFAULT_UA_TITLE # Fallback
        if gsettings_default_ua_title and gsettings_default_ua_title in self._ua_title_to_value_map:
            title_to_select_initially = gsettings_default_ua_title

        self._select_ua_in_http_page_dropdown(title_to_select_initially)
        logger.debug("HttpPage: User-Agent model updated and selection set to '%s'.", title_to_select_initially)


    def _on_http_page_ua_selection_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec) -> None:
        """
        Handle selection change in the User-Agent dropdown.

        Updates visual feedback on the ComboRow. The actual User-Agent string for
        requests is retrieved at the time of request.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        """
        selected_item_obj = combo_row.get_selected_item()
        if not isinstance(selected_item_obj, Gtk.StringObject):
            # This can happen if the model is being updated or cleared
            logger.debug("HttpPage: UA selection changed, but selected item is not a StringObject (or is None).")
            return
        # This method now only updates visual feedback. No GSettings interaction for selection persistence here.
        self._on_user_agent_changed_visual_feedback(combo_row, _gparam)


    def _on_default_ua_gsetting_changed(self, settings: Gio.Settings, key: str) -> None:
        """
        Handle programmatic changes to the 'default-user-agent-title' GSetting.

        This is primarily for external changes to the setting; user interaction with
        the dropdown on this page does not directly write to this GSetting to avoid loops
        and to make the dropdown act as an override for the current session/page.

        :param settings: The :class:`Gio.Settings` object that changed.
        :param key: The GSettings key that changed (expected to be 'default-user-agent-title').
        """
        if key == "default-user-agent-title":
            new_gsettings_default_ua_title = settings.get_string(key)
            logger.info(
                "HttpPage: Global GSettings 'default-user-agent-title' changed to: '%s'. "
                "Page dropdown selection is not automatically forced to this after initial setup "
                "to allow session-specific overrides.", new_gsettings_default_ua_title
            )
            # If desired, one could re-select here, but current design is that page dropdown
            # can override GSetting for the session. GSetting is for initial load.

    def _select_ua_in_http_page_dropdown(self, title_to_select: str) -> bool:
        """
        Select a User-Agent title in the HTTP page's User-Agent dropdown.

        If the exact title is not found, it attempts to select the system default.
        If the system default is also not found (e.g., empty model), it selects the first available item.

        :param title_to_select: The title of the User-Agent to select.
        :return: ``True`` if an item was successfully selected, ``False`` otherwise.
        """
        if not self.http_user_agent_row:
            return False
        model = self.http_user_agent_row.get_model()
        if not isinstance(model, Gtk.StringList):
            logger.error("HttpPage: User-Agent dropdown model is not a Gtk.StringList.")
            return False

        all_titles_in_dropdown = [model.get_string(i) for i in range(model.get_n_items())]
        final_title_to_select = title_to_select

        if title_to_select not in all_titles_in_dropdown:
            logger.warning("HttpPage: Title '%s' not in UA dropdown. Falling back.", title_to_select)
            final_title_to_select = self.SYSTEM_DEFAULT_UA_TITLE
            if self.SYSTEM_DEFAULT_UA_TITLE not in all_titles_in_dropdown:
                if model.get_n_items() > 0:
                    final_title_to_select = model.get_string(0) # Fallback to first item
                    logger.info("HttpPage: System default UA not in dropdown, falling back to first item: '%s'", final_title_to_select)
                else:
                    logger.warning("HttpPage: User-Agent dropdown is empty, cannot make a selection.")
                    return False
        try:
            idx = all_titles_in_dropdown.index(final_title_to_select)
            self.http_user_agent_row.set_selected(idx)
            # Update visual feedback after selection
            self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)
            return True
        except ValueError: # Should not happen if logic above is correct
            logger.error("HttpPage: Value error trying to select UA title '%s' (final: '%s').", title_to_select, final_title_to_select, exc_info=True)
            return False

    def do_dispose(self) -> None:
        """
        Clean up resources when the HttpPage is disposed.

        Disconnects GSettings handlers to prevent memory leaks or attempts
        to update a disposed widget. This is part of the GObject lifecycle.
        """
        logger.debug("Disposing HttpPage.")
        if self.current_http_task and not self.current_http_task.is_done():
            if self.current_http_task.get_cancellable():
                self.current_http_task.get_cancellable().cancel()
            logger.info("HttpPage dispose: Cancelled active HTTP task.")
            self.current_http_task = None

        # Disconnect all GSettings handlers by ID if they were stored
        if self._gsettings_ua_changed_handler_id > 0 and self.settings.is_connected(self._gsettings_ua_changed_handler_id):
            self.settings.disconnect(self._gsettings_ua_changed_handler_id)
            self._gsettings_ua_changed_handler_id = 0

        # For other handlers connected with self.settings.connect("changed::key", self.callback_method)
        # GObject's automatic signal disconnection on dispose should handle them,
        # but explicit disconnection is safer if IDs were stored.
        # Example for other direct connections (if they existed and were stored):
        # self.settings.disconnect_by_func(self._on_color_setting_changed)
        # self.settings.disconnect_by_func(self._on_global_output_font_changed)
        # self.settings.disconnect_by_func(self._update_user_agent_model) # For the lambda
        # Since we don't store all handler IDs, we rely on GObject's cleanup.
        # Or, more robustly, use self.settings.disconnect_by_func for each.
        # For now, only the stored ID is explicitly disconnected.

        super().do_dispose() # Call parent class's dispose method

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """
        Create a :class:`Gtk.SignalListItemFactory` for a :class:`Gtk.ColumnView` column.

        The factory is configured to display an attribute of a :class:`.HeaderItem`.
        It sets up a :class:`Gtk.Label` for each cell and binds its properties
        (text, font, color) based on the HeaderItem's data and application settings.

        :param attr_name: The attribute name of the :class:`.HeaderItem` to display
                          (e.g., "key" or "value").
        :param wrap_text: If ``True``, enables text wrapping for the label in the cell.
                          Defaults to ``False``.
        :return: A configured :class:`Gtk.SignalListItemFactory`.
        """
        factory = Gtk.SignalListItemFactory()

        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Set up the list item factory by creating a Gtk.Label."""
            label = Gtk.Label(xalign=0.0, hexpand=True)
            if wrap_text:
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(80) # Sensible default for wrapped value col
            list_item.set_child(label)

        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Bind function for the list item factory. Sets label text and style."""
            label_widget = list_item.get_child()
            item_obj = list_item.get_item()

            if not isinstance(label_widget, Gtk.Label): # Should always be a Gtk.Label from setup
                logger.error("HttpPage _create_factory: Expected Gtk.Label, got %s", type(label_widget))
                return
            if not isinstance(item_obj, HeaderItem): # Item in store should be HeaderItem
                logger.error("HttpPage _create_factory: Expected HeaderItem, got %s", type(item_obj))
                label_widget.set_text("Error: Invalid item type in model.")
                return

            text_to_display = getattr(item_obj, attr_name, "") # Get 'key' or 'value'

            # Font settings from Pango.FontDescription
            font_family_name = self._output_font_desc.get_family()
            pango_font_size = self._output_font_desc.get_size()

            # Ensure valid family name for Pango markup
            current_font_family = GLib.markup_escape_text(font_family_name if font_family_name else "Sans")

            # Determine color based on item type and attribute
            color_to_use: str
            is_bold: bool = False
            if item_obj.is_special_row:
                color_to_use = self._special_row_color
                is_bold = True # Make special rows bold
            elif attr_name == "key":
                color_to_use = self._header_key_color
            else: # attr_name == "value"
                color_to_use = self._header_value_color

            escaped_text = GLib.markup_escape_text(str(text_to_display))

            # Construct Pango markup string for styling
            # Breaking down the f-string for readability and to avoid E501
            markup_font_family = f"font_family='{current_font_family}'"
            markup_size = f"size='{pango_font_size}'"
            markup_foreground = f"foreground='{color_to_use}'"

            markup_parts = [
                f"<span {markup_font_family} {markup_size} {markup_foreground}>"
            ]
            if is_bold:
                markup_parts.append("<b>")
            markup_parts.append(escaped_text)
            if is_bold:
                markup_parts.append("</b>")
            markup_parts.append("</span>")

            label_widget.set_markup("".join(markup_parts))

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory

    def trigger_fetch(self) -> None:
        """
        Programmatically trigger an HTTP header fetch operation.

        This is typically connected to a keyboard shortcut (e.g., Ctrl+R).
        It simulates a click on the 'Apply' button if it's currently sensitive.
        """
        logger.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.activate() # This will call _on_entry_row_activated
        else:
            logger.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")
