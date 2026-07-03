"""Defines the HTTP Headers page for the Woes application.

This module provides the :class:`.HttpPage` class, which allows users to fetch and
inspect HTTP headers for a given URL. It includes options for custom Host
headers, User-Agent string selection, and Akamai Pragma header toggling.
The page also integrates with GSettings for persisting user preferences
and uses a background thread for network operations to keep the UI responsive.
"""

import logging
import os
from enum import Enum
from typing import Any, Optional

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk, Pango

from .constants import RESOURCE_PREFIX, APP_ID, USER_AGENTS
from .utils import show_global_error, show_global_toast, is_valid_url, unwrap_task_result
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
    """:class:`GObject.Object` representing a single header key-value pair for the :class:`Gtk.ColumnView`."""

    key: str
    value: str
    is_special_row: bool

    def __init__(self, key: str, value: str, is_special_row: bool = False):
        """Initialize a HeaderItem.

        :param key: The header key or special row title.
        :type key: str
        :param value: The header value or special row description.
        :type value: str
        :param is_special_row: Whether this item represents a special row
                               (e.g., URL/status line, separator) rather than
                               a standard key-value header. Defaults to ``False``.
        :type is_special_row: bool
        """
        super().__init__()
        self.key = key
        self.value = value
        self.is_special_row = is_special_row


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Gtk.Box):
    """Activity page for fetching and inspecting HTTP headers.

    This class manages the UI and logic for the HTTP Headers inspection tool.
    It handles user input for URL, Host header, and User-Agent selection.
    HTTP requests are performed in a background thread, and results are displayed
    in a :class:`Gtk.ColumnView`.

    **User-Agent Handling:**
    The User-Agent dropdown (`http_user_agent_row`) on this page allows for
    session-specific User-Agent selection while also reacting to changes in the
    global default User-Agent preference (defined in GSettings via
    `default-user-agent-title`). The behavior is governed by the
    `_http_page_ua_is_following_gsettings_default` flag:

    - **Initialization (`_update_user_agent_model`):**
        - The dropdown is populated with "None", standard, and custom User-Agents.
        - `_http_page_ua_is_following_gsettings_default` is set to `True`.
        - The dropdown selection is initialized to match the current global
          GSettings default (or "None" if the GSetting is empty).
    - **User Interaction (`_on_http_page_ua_selection_changed`):**
        - If the user selects "None":
            `_http_page_ua_is_following_gsettings_default` becomes `True`.
            The dropdown immediately updates to reflect the current GSettings default
            (which might also be "None").
        - If the user selects a User-Agent that matches the current GSettings default:
            `_http_page_ua_is_following_gsettings_default` becomes `True`.
        - If the user selects a User-Agent different from the current GSettings default:
            `_http_page_ua_is_following_gsettings_default` becomes `False` (session override).
    - **GSettings Change (`_on_default_ua_gsetting_changed`):**
        - If `default-user-agent-title` GSettings key changes:
            - If `_http_page_ua_is_following_gsettings_default` is `True`, the page's
              dropdown updates to this new global default.
            - If `False`, the page's dropdown selection is preserved (session override).
    - **Request Time (`_on_entry_row_activated`):**
        - If the page's dropdown is "None", the User-Agent is resolved based on the
          current GSettings default (constants -> custom UAs -> `requests` default).
        - Otherwise, the User-Agent selected in the page's dropdown is used.
    - Selections on this page *do not* modify the global `default-user-agent-title` GSetting.
    """

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
    http_export_results_button: Gtk.Button = Gtk.Template.Child()
    http_status_row: Adw.ActionRow = Gtk.Template.Child()
    http_status_spinner: Gtk.Spinner = Gtk.Template.Child()

    def __init__(self, **kwargs: Any):
        """Initialize the HttpPage.

        Initializes UI elements, GSettings, the header list store for the
        column view, and connects signals.

        :param kwargs: Keyword arguments passed to the :class:`Gtk.Box` constructor.
        """
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task: Optional[Gio.Task] = None
        self._current_header_items: list[HeaderItem] = []
        self._http_task_data_for_thread: dict[str, Any] = {}
        self._ua_title_to_value_map: dict[str, Optional[str]] = {}  # Maps display titles to actual UA strings
        self.settings: Gio.Settings = Gio.Settings(schema_id=APP_ID)
        self._http_page_ua_is_following_gsettings_default = True  # Default to following GSettings UA preference
        self._gsettings_ua_changed_handler_id = 0  # Handler ID for GSettings 'default-user-agent-title'
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

        self.header_list_store: Gio.ListStore = Gio.ListStore.new(HeaderItem)  # type: ignore[attr-defined]
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
        # logging.debug(f"HttpPage: Connected GSettings listener for default-user-agent-title, handler ID: {self._gsettings_ua_changed_handler_id}") # Reduced verbosity

        if self.http_apply_button:
            self.http_apply_button.get_style_context().add_class("suggested-action")
        if self.clear_results_button:
            self.clear_results_button.get_style_context().add_class("destructive-action")
        if self.copy_results_button:
            self.copy_results_button.get_style_context().add_class("flat")

        if self.http_host_header_row:
            self._on_host_header_changed(self.http_host_header_row)
        # Initial call to _on_user_agent_changed is mainly for CSS,
        # GSetting persistence is handled by _save_selected_user_agent_preference
        if self.http_user_agent_row:
            self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)

        self.column_view_helper = Helper(widget=self.http_column_view, parent_window=self.get_native())

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_export_results_button:
            self.http_export_results_button.connect("clicked", self._on_export_results_clicked)
        if self.http_host_header_row:
            self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row:
            # For visual feedback (CSS class) - now handled by _on_http_page_ua_selection_changed
            # self.http_user_agent_row.connect("notify::selected-item", self._on_user_agent_changed_visual_feedback)
            # For saving preference (now deprecated GSettings write) - now handled by _on_http_page_ua_selection_changed
            # self.http_user_agent_row.connect("notify::selected-item", self._save_selected_user_agent_preference)
            self.http_user_agent_row.connect("notify::selected-item", self._on_http_page_ua_selection_changed)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        """Handle changes in the Host header entry row.

        Adds or removes a CSS class to indicate if an override is active.

        :param entry_row: The :class:`Adw.EntryRow` for the Host header.
        """
        if not entry_row:
            return
        text = entry_row.get_text().strip()
        if text:
            entry_row.add_css_class("active-override")
        else:
            entry_row.remove_css_class("active-override")

    def _on_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        """Handle changes in the User-Agent combo row selection.

        Adds or removes a CSS class to indicate if a non-default User-Agent is active.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        """
        if not combo_row:
            return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            effective_ua_value = self._ua_title_to_value_map.get(selected_title)
            if effective_ua_value:  # Check if the value is not None (i.e., not the "None" option)
                combo_row.add_css_class("active-override")
            else:
                combo_row.remove_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map:  # Model might be empty
            combo_row.remove_css_class("active-override")

    def _on_user_agent_changed_visual_feedback(
        self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]
    ) -> None:
        """Handle visual feedback for User-Agent combo row selection changes.

        This method is responsible for adding/removing the 'active-override' CSS class.
        It is separated from the logic that saves the preference to GSettings.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        """
        self._on_user_agent_changed(combo_row, _gparam)  # Call the original method for CSS

    def _save_selected_user_agent_preference(
        self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]
    ) -> None:
        """Handle User-Agent selection changes in the HTTP Page's dropdown.

        This method is connected to the 'notify::selected-item' signal of the
        User-Agent ComboRow on the HTTP Page. It logs the change for the current
        session. It does *not* save this selection back to the global
        `default-user-agent-title` GSettings key, ensuring that the HTTP Page's
        User-Agent choice is session-specific and does not alter the
        globally configured default User-Agent preference.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        """
        if not combo_row:
            return

        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            logger.info(
                "HTTP Page User-Agent selection changed to: '%s'. This is a session-specific change.", selected_title
            )
            # The line below is commented out to prevent HTTP page selection from changing global default.
            # self.settings.set_string("default-user-agent-title", selected_title if selected_title != "None" else "")
        elif selected_item_obj is None:
            logger.info("HTTP Page User-Agent selection cleared (None). This is a session-specific change.")
            # The line below is commented out
            # self.settings.set_string("default-user-agent-title", "")

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle the click event for the 'Copy Results' button.

        Constructs a string representation of the displayed headers and copies
        it to the clipboard.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        """
        logger.info("Copying all headers to clipboard.")
        lines: list[str] = []
        if self.header_list_store:
            for i in range(self.header_list_store.get_n_items()):  # type: ignore[attr-defined]
                item = self.header_list_store.get_item(i)  # type: ignore[attr-defined]
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
                    clipboard.set(text_to_copy)
                    logger.info("Headers copied to clipboard successfully.")
                else:
                    logger.warning("Failed to get default clipboard for copying.")
            except Exception as e:
                logger.error("Error copying headers to clipboard: %s", e, exc_info=True)
        else:
            logger.info("No headers to copy from the results view.")

    def _on_export_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle the click event for the 'Save Results' button.

        Opens a file chooser dialog and writes the displayed headers to the selected file.
        """
        logger.info("Opening export headers dialog.")
        lines: list[str] = []
        if self.header_list_store:
            for i in range(self.header_list_store.get_n_items()):  # type: ignore[attr-defined]
                item = self.header_list_store.get_item(i)  # type: ignore[attr-defined]
                if isinstance(item, HeaderItem):
                    if item.is_special_row:
                        if item.value and item.value.strip():
                            lines.append(f"{item.key} {item.value}")
                        else:
                            lines.append(item.key)
                    else:
                        lines.append(f"{item.key}: {item.value}")
        if not lines:
            show_global_toast(self, "No results to export.")  # type: ignore
            return

        text_to_save = "\n".join(lines)

        dialog = Gtk.FileChooserNative.new(
            "Save HTTP Headers", self.get_native(), Gtk.FileChooserAction.SAVE, "_Save", "_Cancel"
        )

        def on_dialog_response(dialog_obj, response_id, _user_data_unused):
            if response_id == Gtk.ResponseType.ACCEPT:
                file_obj = dialog_obj.get_file()
                if file_obj:
                    filepath = file_obj.get_path()
                    if filepath:
                        try:
                            with open(filepath, "w", encoding="utf-8") as f:
                                f.write(text_to_save)
                            show_global_toast(self, f"Saved to {os.path.basename(filepath)}")  # type: ignore
                        except Exception as e:
                            logger.error("Error saving HTTP headers file: %s", e, exc_info=True)
                            show_global_error(self, f"Failed to save file: {e}")  # type: ignore
            dialog_obj.destroy()

        dialog.connect("response", on_dialog_response, None)
        dialog.show()

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        """Handle activation of the URL entry row or click of the 'Fetch' button.

        Validates the URL and gathers request parameters for the HTTP request:
        - URL: Taken from `http_entry_row`.
        - Host header: Taken from `http_host_header_row` if provided.
        - User-Agent: Determined by the following logic:
            1. If a specific User-Agent is selected in `http_user_agent_row` (i.e., not "None"),
               that User-Agent string (obtained from `_ua_title_to_value_map`) is used.
            2. If `http_user_agent_row` is set to "None" (or no selection), it signifies
               that the global GSettings default should be used. The method then:
               a. Fetches `default-user-agent-title` from GSettings.
               b. If this title is non-empty, it attempts to resolve it to an actual
                  User-Agent string by first checking the predefined `USER_AGENTS` list
                  (from :mod:`.constants`) and then the custom User-Agents list from GSettings
                  (`custom-user-agents`).
               c. If the GSettings title is empty (representing "None" behavior) or cannot be
                  resolved from either list, `user_agent_to_send` is set to `None`,
                  which means the `requests` library will use its own default User-Agent.
        - Custom DNS server: Read from GSettings.
        - Akamai Pragma header state: Taken from `http_pragma_switch_row`.

        After gathering parameters, it initiates a background task
        (`_fetch_headers_task_thread_func`) to perform the HTTP request via
        :class:`.http_client.HttpFetcher`. The final User-Agent string to be sent is
        logged via `logging.info`.

        :param _widget: The :class:`Gtk.Widget` that triggered the activation (unused).
        """
        original_url = self.http_entry_row.get_text().strip()
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error")
        url = self._ensure_scheme(original_url)
        # logger.info("Fetching headers for URL: %s (original input: %s)", url, original_url) # Reduced verbosity

        if not is_valid_url(url):
            logger.warning("HTTP Page: Invalid URL provided: %s (processed as: %s)", original_url, url)
            toast_message = "Invalid URL format. Please enter a valid URL (e.g., https://example.com)."
            show_global_toast(self, toast_message)  # type: ignore[arg-type]
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, "show_toast")):
                show_global_error(self, toast_message)  # type: ignore[arg-type]
            if self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
            self._set_loading_state(False, "Idle - Invalid URL.")
            return

        self._clear_error()
        self._set_loading_state(True, "Fetching headers...")

        host_header = self.http_host_header_row.get_text().strip()
        user_agent_to_send: Optional[str] = None  # Initialize
        selected_ua_title_in_http_page_dropdown: Optional[str] = None

        selected_item_obj = self.http_user_agent_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_ua_title_in_http_page_dropdown = selected_item_obj.get_string()

        # logging.debug("HTTP Page: UA dropdown selection: '%s'", selected_ua_title_in_http_page_dropdown) # Reduced verbosity

        if selected_ua_title_in_http_page_dropdown and selected_ua_title_in_http_page_dropdown != "None":
            user_agent_to_send = self._ua_title_to_value_map.get(selected_ua_title_in_http_page_dropdown)
            logging.info(
                "HTTP Page: Using User-Agent from page dropdown selection: '%s'", selected_ua_title_in_http_page_dropdown
            )
            if (
                user_agent_to_send is None and selected_ua_title_in_http_page_dropdown != "None"
            ):  # Should not happen if map is correct
                logging.warning(
                    "HTTP Page: UA title '%s' in dropdown but not in map. This is unexpected. Sending no specific UA (requests default).", selected_ua_title_in_http_page_dropdown
                )
        else:
            logging.info("HTTP Page: Page dropdown selection is 'None'. Using GSettings default User-Agent.")
            gsettings_default_title = self.settings.get_string("default-user-agent-title")
            # logging.debug("HTTP Page: GSettings default-user-agent-title is: '%s'", gsettings_default_title) # Reduced verbosity

            if gsettings_default_title:  # An explicit default is set in GSettings
                ua_found_in_constants = False
                for ua_dict in USER_AGENTS:
                    if ua_dict["title"] == gsettings_default_title:
                        user_agent_to_send = ua_dict["value"]
                        ua_found_in_constants = True
                        break
                if ua_found_in_constants:
                    logging.info(
                        "HTTP Page: Resolved GSettings default '%s' from standard User-Agents.", gsettings_default_title
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
                            "HTTP Page: Resolved GSettings default '%s' from custom User-Agents.", gsettings_default_title
                        )
                    else:
                        logging.warning(
                            "HTTP Page: GSettings default User-Agent title '%s' not found in constants or custom UAs. Sending no specific UA (requests default).", gsettings_default_title
                        )
                        user_agent_to_send = None
            else:  # GSettings default is empty string, meaning "None" (use requests default)
                logging.info(
                    "HTTP Page: GSettings default User-Agent is 'None' (empty string). Sending no specific UA (requests default)."
                )
                user_agent_to_send = None

        custom_dns_server = self.settings.get_string("custom-dns-server")
        logging.info(
            "HttpPage: Final User-Agent for request task: %s", (user_agent_to_send if user_agent_to_send else 'None (requests default will be used)')
        )

        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header if host_header else None,
            "user_agent": user_agent_to_send,  # This can be None
            "custom_dns_server": custom_dns_server if custom_dns_server else None,
        }
        logger.debug("HttpPage: Starting header fetch task with data: %s", self._http_task_data_for_thread)

        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None)  # type: ignore[arg-type]
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func)  # type: ignore[arg-type]

    def _fetch_headers_task_thread_func(
        self,
        task: Gio.Task,
        _source_object: GObject.Object,
        _task_data_arg: dict[str, Any],
        cancellable: Optional[Gio.Cancellable],
    ) -> None:
        """Background thread function for fetching HTTP headers.

        This function is executed by :meth:`Gio.Task.run_in_thread`.
        It instantiates :class:`.http_client.HttpFetcher` with necessary parameters
        and calls its main fetching method. Results or exceptions are reported
        back to the main thread via the :class:`Gio.Task`.

        :param task: The :class:`Gio.Task` associated with this operation.
        :type task: Gio.Task
        :param _source_object: The source object that initiated the task (unused).
        :type _source_object: GObject.Object
        :param _task_data_arg: Additional data passed to the task (unused).
                               The actual data is retrieved from ``self._http_task_data_for_thread``.
        :param cancellable: A :class:`Gio.Cancellable` object to monitor for cancellation.
        """
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
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.CANCELLED.value,
                    "Task cancelled after fetching.",
                )
            else:
                task.return_value(processed_data)
        except HttpRequestTimeoutError as e:
            logger.warning("HttpPage Task: Timeout for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.TIMEOUT.value, str(e)
            )
        except HttpConnectionError as e:
            logger.warning("HttpPage Task: ConnectionError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CONNECTION_ERROR.value, str(e)
            )
        except HttpProcessingError as e:
            logger.warning("HttpPage Task: HttpProcessingError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.HTTP_ERROR.value, str(e)
            )
        except HttpGenericRequestError as e:
            logger.warning("HttpPage Task: HttpGenericRequestError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.REQUEST_EXCEPTION.value, str(e)
            )
        except HttpClientError as e:
            logger.warning("HttpPage Task: HttpClientError for '%s': %s", url_to_fetch, e)
            if "cancelled" in str(e).lower():
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, str(e)
                )
            else:
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, str(e)
                )
        except Exception as e:
            logger.exception("HttpPage Task: Unexpected generic error for URL '%s':", url_to_fetch)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.GENERIC_UNEXPECTED.value,
                f"Unexpected internal error: {e}",
            )

    def _fetch_headers_task_done_cb(
        self, _source_object: GObject.Object, _result: Gio.AsyncResult, _user_data: Optional[Any] = None
    ) -> None:
        """Handle completion of the HTTP headers fetch task.

        Processes the result from the background thread, updates the UI with headers
        or an error message, and re-enables UI elements.

        :param _source_object: The source object that initiated the task (unused).
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :param _user_data: User data passed with the callback (unused).
        """
        task_being_processed = self.current_http_task
        if task_being_processed is None:
            logger.warning(
                "_fetch_headers_task_done_cb: current_http_task is None, possibly already handled or cancelled."
            )
            # Ensure UI is not stuck in loading state if this callback is somehow invoked late
            if self.http_status_row and self.http_status_row.get_subtitle() == "Fetching headers...":
                self._set_loading_state(False, "Idle.")
            return

        self.current_http_task = None  # Clear the current task reference
        logger.info("Processing task completion in _fetch_headers_task_done_cb.")

        try:
            propagate_result = unwrap_task_result(task_being_processed)  # type: ignore[arg-type]
            actual_list_of_responses: Optional[list[dict[str, Any]]] = None

            if isinstance(propagate_result, list):
                actual_list_of_responses = propagate_result
            else:
                logger.error("HttpPage: Unexpected type from propagate_value: %s", type(propagate_result))
                show_global_error(self, "Unexpected result type from background task.")  # type: ignore[arg-type]
                if self.http_entry_row:
                    self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                self._set_loading_state(False, "Error: Unexpected data format.")
                return

            if actual_list_of_responses is not None:  # This check is now more robust
                logger.info(
                    "HttpPage: Successfully processed task result: %d response stages.", len(actual_list_of_responses)
                )
                processed_headers_for_store: list[HeaderItem] = []
                if not actual_list_of_responses:
                    logger.info("HttpPage: Received empty list of responses.")
                    self._update_column_view_model(None)
                else:
                    for i, response_data_dict_item in enumerate(actual_list_of_responses):
                        if not isinstance(response_data_dict_item, dict):
                            logger.error(
                                "HttpPage: Expected dict item in response list, got %s. Data: %s",
                                type(response_data_dict_item),
                                response_data_dict_item,
                            )
                            continue
                        response_data_dict: dict[str, Any] = response_data_dict_item
                        url_display = f"URL: {response_data_dict.get('url', 'N/A')}"
                        status_code = response_data_dict.get("status_code", "N/A")  # type: ignore
                        response_type = response_data_dict.get("type", "unknown")
                        status_display = f"Status: {status_code} ({str(response_type).capitalize()})"
                        processed_headers_for_store.append(
                            HeaderItem(key=url_display, value=status_display, is_special_row=True)
                        )
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
                                        processed_headers_for_store.append(
                                            HeaderItem(key=str(key), value=part_content, is_special_row=False)
                                        )
                                        first_part_processed = True
                                    else:
                                        processed_headers_for_store.append(
                                            HeaderItem(key="", value=part_content, is_special_row=False)
                                        )
                        else:
                            logger.warning(
                                "HttpPage: Headers data for response stage %d is not a dict: %s",
                                i,
                                headers_for_this_response,
                            )
                        if i < len(actual_list_of_responses) - 1:
                            processed_headers_for_store.append(
                                HeaderItem(key="--- Redirected To ---", value="", is_special_row=True)
                            )
                    self._current_header_items = processed_headers_for_store
                    self._update_column_view_model(processed_headers_for_store)
                if self.http_entry_row:
                    self.http_entry_row.remove_css_class("error")
                self._set_loading_state(False, "Headers loaded successfully.")
            else:
                logger.error(
                    "HttpPage: Failed to obtain a valid list of responses. Value: %s", actual_list_of_responses
                )
                show_global_error(self, "Failed to process data from background task.")  # type: ignore[arg-type]
                if self.http_entry_row:
                    self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                self._set_loading_state(False, "Error: Failed to process data.")
        except GLib.Error as e:
            logger.warning(
                "HttpPage: Task failed with GLib.Error (Domain: %s, Code: %d, Message: %s)", e.domain, e.code, e.message
            )
            display_message = e.message if e.message else "An unknown error occurred."
            if "<b>" in display_message or "<" in display_message:
                display_message = GLib.markup_escape_text(display_message)

            show_global_error(self, display_message)  # type: ignore[arg-type]
            if self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
            self._set_loading_state(False, f"Error: {display_message.splitlines()[0]}")
        except Exception as e:
            logger.exception("HttpPage: Unexpected Python error in _fetch_headers_task_done_cb:")
            error_message = f"An unexpected application error occurred: {e}"
            show_global_error(self, error_message)  # type: ignore[arg-type]
            if self.http_entry_row:
                self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
            self._set_loading_state(False, f"Error: {error_message.splitlines()[0]}")
        finally:
            if self.current_http_task is None:
                if self.http_status_row and self.http_status_row.get_subtitle() == "Fetching headers...":
                    self._set_loading_state(False, "Idle - operation ended.")

    def _set_loading_state(self, active: bool, message: str = "Idle") -> None:
        """Set the UI loading state.

        Manages the visibility of the spinner, updates the status message,
        and adjusts the sensitivity of input controls.

        :param active: If ``True``, sets the UI to a loading state; otherwise, sets it to an idle state.
        :param message: The message to display in the status row. Defaults to "Idle".
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
        """Ensure the URL has a scheme, defaulting to 'https://'.

        This provides a basic check before passing to :class:`.http_client.HttpFetcher`,
        which will perform more robust URL parsing.

        :param url: The input URL string.
        :return: The URL string, with 'https://' prepended if no scheme was present.
        """
        if "://" not in url:
            logger.debug("URL '%s' has no scheme, prepending 'https://'.", url)
            return "https://" + url
        return url

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        """Handle toggling of the Akamai Pragma switch.

        If a URL is present in the entry row, it re-triggers the fetch.

        :param _widget: The :class:`Gtk.Switch` that was toggled (unused).
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        """
        logger.debug("Akamai Pragma toggled: %s. Re-fetching if URL present.", self.http_pragma_switch_row.get_active())
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[list[HeaderItem]]) -> None:
        """Update the :class:`Gio.ListStore` for the header :class:`Gtk.ColumnView`.

        Clears the existing items and appends new ones if provided.
        Shows or hides the results group accordingly.

        :param header_items: A list of :class:`.HeaderItem` objects to display,
                             or ``None`` to clear the view.
        """
        self.header_list_store.remove_all()  # type: ignore[attr-defined]
        if header_items:
            for item in header_items:
                self.header_list_store.append(item)  # type: ignore[attr-defined]
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
        """Clear any error state in the UI.

        Hides the main window's error banner and removes the 'error' CSS class
        from the URL entry row.
        """
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error()  # type: ignore[attr-defined]
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error")

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle the click event for the 'Clear Results' button.

        Clears the displayed headers, error state, and the URL entry.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        """
        logger.info("Results cleared by user action.")
        self._current_header_items = []
        self._update_column_view_model(None)
        self._clear_error()
        if self.http_entry_row:
            self.http_entry_row.set_text("")

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        """Handle changes to color-related GSettings.

        Updates the internal color attributes and re-populates the column view
        to apply the new colors if results are currently displayed.

        :param settings: The :class:`Gio.Settings` object that changed.
        :param key: The GSettings key that changed.
        """
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
        """Handle changes to the global output font GSettings key.

        Updates the internal font description and re-populates the column view
        to apply the new font if results are currently displayed.

        :param settings: The :class:`Gio.Settings` object that changed.
        :param key: The GSettings key that changed.
        """
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
        """Update the model for the User-Agent :class:`Adw.ComboRow` on the HTTP Page.

        This method populates the dropdown with a "None" option, standard User-Agents
        from :mod:`.constants.USER_AGENTS`, and any custom User-Agents defined in
        GSettings.

        Crucially, after populating the model, this method sets the
        `_http_page_ua_is_following_gsettings_default` flag to `True` (to ensure
        the page initially tries to follow the global default). It then calls
        :meth:`._select_ua_in_http_page_dropdown` with the current GSettings default
        User-Agent title (or "None" if the GSetting is empty). This action
        correctly initializes the dropdown's visible selection to match the global
        default preference.
        """
        if not self.http_user_agent_row:
            logger.error("HttpPage: _update_user_agent_model: http_user_agent_row is None, cannot update model.")
            return
        self._ua_title_to_value_map.clear()

        display_titles: list[str] = []
        display_titles.append("None")  # UI representation of system default / no override
        self._ua_title_to_value_map["None"] = None  # Explicitly map "None" display title to an actual None value

        # Populate with standard USER_AGENTS from constants.py
        for ua_dict in USER_AGENTS:  # Iterate list of dictionaries
            title = ua_dict["title"]
            value = ua_dict["value"]
            if title not in self._ua_title_to_value_map:  # Ensures "None" isn't overwritten if a UA is titled "None"
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value
            else:  # This case implies title == "None" and was already added.
                # Or a duplicate title in USER_AGENTS.
                if title == "None":  # If a standard UA is literally named "None"
                    logger.warning(
                        "A standard User-Agent is titled 'None'. This may cause confusion with the system default option."
                    )
                    # Allow it, but it will override the placeholder map if it wasn't done carefully above.
                    # The current logic (checking `title not in self._ua_title_to_value_map`) handles this.
                else:  # True duplicate
                    logger.warning("Standard User-Agent title '%s' is a duplicate. Skipping.", title)

        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []
        )
        for title, value in custom_ua_pairs:
            if title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles))

        default_ua_title_from_prefs = self.settings.get_string("default-user-agent-title")
        # logging.info("HttpPage._update_user_agent_model: Initial default UA from GSettings: '%s'.", default_ua_title_from_prefs) # Can be verbose
        # self._select_ua_in_http_page_dropdown(default_ua_title_from_prefs if default_ua_title_from_prefs else "None")
        # Visual feedback is handled by _select_ua_in_http_page_dropdown calling _on_user_agent_changed_visual_feedback

        self._http_page_ua_is_following_gsettings_default = True  # Start by following
        default_ua_title_from_prefs = self.settings.get_string("default-user-agent-title")
        effective_default_title = default_ua_title_from_prefs if default_ua_title_from_prefs else "None"
        # logging.info("HttpPage._update_user_agent_model: Initial default UA from GSettings: '%s' (effective: '%s'). Setting dropdown.", default_ua_title_from_prefs, effective_default_title) # Reduced verbosity
        self._select_ua_in_http_page_dropdown(effective_default_title)

    def _on_http_page_ua_selection_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec) -> None:
        """Handle user selection changes in the HTTP Page's User-Agent dropdown.

        This method updates the `_http_page_ua_is_following_gsettings_default`
        flag based on the user's selection:
        - If "None" is selected, the page will follow the GSettings default, and the
          dropdown is updated to reflect the current GSettings default.
        - If the selected UA matches the current GSettings default, the page follows.
        - If a specific UA different from the GSettings default is chosen, this
          becomes a session override, and the page stops following GSettings.
        It also calls for visual feedback update via :meth:`._on_user_agent_changed_visual_feedback`.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        selected_item_obj = combo_row.get_selected_item()
        if not isinstance(selected_item_obj, Gtk.StringObject):
            # This might happen briefly if model is being changed.
            # logging.warning("HttpPage: _on_http_page_ua_selection_changed called with non-StringObject selection.") # Reduced verbosity
            return

        selected_title_on_page = selected_item_obj.get_string()
        gsettings_default_ua_title = self.settings.get_string("default-user-agent-title")
        effective_gsettings_default = gsettings_default_ua_title if gsettings_default_ua_title else "None"

        if selected_title_on_page == "None":
            self._http_page_ua_is_following_gsettings_default = True
            logging.info(
                "HttpPage: UA selection is 'None'. Now following GSettings. Current GSettings default: '%s'.", effective_gsettings_default
            )
            self._select_ua_in_http_page_dropdown(effective_gsettings_default)
        elif selected_title_on_page == effective_gsettings_default:
            self._http_page_ua_is_following_gsettings_default = True
            logging.info(
                "HttpPage: UA selection '%s' matches GSettings default. Now following GSettings.", selected_title_on_page
            )
        else:
            self._http_page_ua_is_following_gsettings_default = False
            logging.info(
                "HttpPage: UA selection is '%s'. This is a session override. Not following GSettings default ('%s').", selected_title_on_page, effective_gsettings_default
            )

        self._on_user_agent_changed_visual_feedback(combo_row, _gparam)

    def _on_default_ua_gsetting_changed(self, settings: Gio.Settings, key: str) -> None:
        """Handle changes to the 'default-user-agent-title' GSettings key.

        If `_http_page_ua_is_following_gsettings_default` is `True`, this method
        updates the User-Agent dropdown on this HTTP page (by calling
        :meth:`._select_ua_in_http_page_dropdown`) to reflect the new global
        default User-Agent. Otherwise, it respects the user's session-specific
        choice and does not change the dropdown.

        :param settings: The :class:`Gio.Settings` object that emitted the signal.
        :type settings: Gio.Settings
        :param key: The GSettings key that changed (expected to be "default-user-agent-title").
        :type key: str
        """
        if key == "default-user-agent-title":
            new_gsettings_default_ua_title = settings.get_string(key)
            effective_new_gsettings_default = (
                new_gsettings_default_ua_title if new_gsettings_default_ua_title else "None"
            )
            # logging.info("HttpPage: GSettings default-user-agent-title changed to: '%s' (effective: '%s').", new_gsettings_default_ua_title, effective_new_gsettings_default) # Can be verbose

            if self._http_page_ua_is_following_gsettings_default:
                logging.info(
                    "HttpPage: Currently following GSettings default. Updating dropdown to new GSettings default: '%s'.", effective_new_gsettings_default
                )
                self._select_ua_in_http_page_dropdown(effective_new_gsettings_default)
            else:
                current_http_page_selection_obj = self.http_user_agent_row.get_selected_item()
                current_http_page_selected_title = "Unknown"
                if isinstance(current_http_page_selection_obj, Gtk.StringObject):
                    current_http_page_selected_title = current_http_page_selection_obj.get_string()
                logging.info(
                    "HttpPage: Not following GSettings default (current page selection: '%s'). Ignoring GSettings change for dropdown update.", current_http_page_selected_title
                )

    def _select_ua_in_http_page_dropdown(self, title_to_select: str) -> bool:
        """Safely select an item in the HTTP Page's User-Agent dropdown by its title.

        If the exact `title_to_select` is not found in the dropdown's model, this
        method falls back to selecting the "None" option. It ensures that the
        `http_user_agent_row` always has a valid selection if its model is populated.
        After setting the selection, it calls
        :meth:`._on_user_agent_changed_visual_feedback` to update any
        associated UI styling (e.g., the 'active-override' CSS class).
        This method does not change the `_http_page_ua_is_following_gsettings_default` flag;
        that state is managed by user interactions or initial setup.

        :param title_to_select: The title of the User-Agent to select in the dropdown.
                                If this title is not found, "None" will be selected as a fallback.
        :return: ``True`` if an item (either the target or fallback "None") was
                 successfully selected, ``False`` if the model is invalid or empty,
                 or if "None" could not be selected as a fallback.
        """
        model = self.http_user_agent_row.get_model()
        if not isinstance(model, Gtk.StringList):  # type: ignore
            logging.error("HttpPage: http_user_agent_row model is not Gtk.StringList, cannot select UA.")
            return False

        all_titles_in_dropdown = [model.get_string(i) for i in range(model.get_n_items())]  # type: ignore

        final_title_to_select = title_to_select
        if title_to_select not in all_titles_in_dropdown:
            logging.warning("HttpPage: UA Title '%s' not found in dropdown. Falling back to 'None'.", title_to_select)
            final_title_to_select = "None"

        if final_title_to_select in all_titles_in_dropdown:
            try:
                idx = all_titles_in_dropdown.index(final_title_to_select)
                self.http_user_agent_row.set_selected(idx)
                self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)
                # logging.debug("HttpPage: Successfully selected '%s' in dropdown.", final_title_to_select) # Reduced verbosity
                return True
            except ValueError:
                logging.error(
                    "HttpPage: Error selecting '%s' (ValueError) despite it being in list. This is unexpected.", final_title_to_select
                )
                return False
        elif model.get_n_items() > 0:
            self.http_user_agent_row.set_selected(0)
            logging.error(
                "HttpPage: Critical - Could not find target UA nor fallback 'None' in dropdown. Selected first available item."
            )
            self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)
            return False

        logging.warning(
            "HttpPage: Could not select any UA in dropdown (model might be empty or 'None' option missing)."
        )
        return False

    def do_dispose(self):
        """Override GObject.Object.do_dispose to disconnect signal handlers.

        Ensures that the GSettings listener for `default-user-agent-title`
        is disconnected when the HttpPage object is disposed, preventing
        potential issues if the settings object outlives the page or if
        the handler attempts to operate on destroyed widgets.
        """
        if self._gsettings_ua_changed_handler_id and self.settings.is_connected(self._gsettings_ua_changed_handler_id):
            self.settings.disconnect(self._gsettings_ua_changed_handler_id)
            logging.debug("HttpPage: Disconnected GSettings listener for default-user-agent-title.")
        self._gsettings_ua_changed_handler_id = 0  # Set to 0 or an invalid ID state after disconnect
        super().do_dispose()

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """Create a Gtk.SignalListItemFactory for Gtk.ColumnView columns.

        This factory configures how items (:class:`HeaderItem`) are displayed in the
        columns of the :class:`Gtk.ColumnView`. It sets up labels, binds them to
        the appropriate attributes of :class:`HeaderItem`, and applies styling
        (font, color, wrapping) based on the item type and application settings.

        :param attr_name: The attribute name of the :class:`.HeaderItem` to display
                          (e.g., 'key' or 'value').
        :param wrap_text: Whether the text in the label should wrap. Defaults to ``False``.
        :return: A configured :class:`Gtk.SignalListItemFactory`.
        """
        factory = Gtk.SignalListItemFactory()

        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Set up the list item factory. Create and configure a :class:`Gtk.Label`."""
            label = Gtk.Label(xalign=0.0, hexpand=True)
            if wrap_text:
                label.set_wrap(True)  # type: ignore[attr-defined]
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(80)
            list_item.set_child(label)  # type: ignore[attr-defined]

        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Bind function for the list item factory. Sets the label's text and style."""
            label = list_item.get_child()  # type: ignore[attr-defined]
            item = list_item.get_item()  # type: ignore[attr-defined]
            if not (isinstance(label, Gtk.Label) and isinstance(item, HeaderItem)):  # type: ignore[attr-defined]
                if isinstance(label, Gtk.Label):
                    label.set_text("Error: Invalid item type.")  # type: ignore[attr-defined]
                return

            text_to_display = getattr(item, attr_name, "")
            family = self._output_font_desc.get_family()
            size_in_pango_units = self._output_font_desc.get_size()
            current_family = family if family else "Sans"

            if item.is_special_row:
                if attr_name == "key":  # For special rows, 'key' might hold combined info or just the primary part
                    key_text = GLib.markup_escape_text(str(item.key))
                    value_text = GLib.markup_escape_text(str(item.value)) if item.value else ""
                    full_text = key_text
                    if value_text.strip() and value_text != "N/A":
                        full_text += " " + value_text # Concatenation
                    label.set_markup(
                        "<b><span font_family='%s' size='%d' foreground='%s'>%s</span></b>" % (GLib.markup_escape_text(current_family), size_in_pango_units, self._special_row_color, full_text)
                    )
                else:  # 'value' column for special rows is typically empty
                    label.set_markup("")
            else:  # Standard header rows
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(str(text_to_display))
                label.set_markup(
                    "<span font_family='%s' size='%d' foreground='%s'>%s</span>" % (GLib.markup_escape_text(current_family), size_in_pango_units, color_to_use, escaped_text)
                )

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory

    def trigger_fetch(self) -> None:
        """Programmatically trigger the 'Fetch' action.

        This method is typically called in response to a keyboard shortcut
        or an external event. It simulates a click on the 'Fetch' button
        if the button is available and sensitive.
        """
        logging.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.activate()
        else:
            logging.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")
