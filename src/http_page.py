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
from typing import Any, Dict, List, Optional  # Use lowercase for built-in types, e.g. list, dict

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
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
        """
        Initialize a HeaderItem.

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
    """
    Activity page for fetching and inspecting HTTP headers.

    This class manages the UI and logic for the HTTP Headers inspection tool.
    It handles user input for URL, Host header, and User-Agent selection.
    HTTP requests are performed in a background thread, and results are displayed
    in a :class:`Gtk.ColumnView`.

    The User-Agent dropdown on this page (`http_user_agent_row`) is initialized
    based on the global default User-Agent preference from GSettings
    (`default-user-agent-title`). If this global default changes (e.g., via
    the Preferences window) and the HTTP page's dropdown is currently set to
    "None" (indicating it should follow the default), the dropdown automatically
    updates to reflect the new global default. Selections made directly in this
    page's dropdown are session-specific and do not alter the global preference.

    :ivar _gsettings_ua_changed_handler_id: Stores the ID of the GSettings "changed"
                                           signal handler for `default-user-agent-title`,
                                           used for connecting/disconnecting the listener.
    :vartype _gsettings_ua_changed_handler_id: int
    :ivar http_entry_row: Entry row for the URL.
    :vartype http_entry_row: Adw.EntryRow
    :ivar http_apply_button: Button to trigger fetching headers.
    :vartype http_apply_button: Gtk.Button
    :ivar http_host_header_row: Entry row for custom Host header.
    :vartype http_host_header_row: Adw.EntryRow
    :ivar http_user_agent_row: ComboRow for User-Agent selection.
    :vartype http_user_agent_row: Adw.ComboRow
    :ivar http_pragma_switch_row: Switch for Akamai Pragma headers.
    :vartype http_pragma_switch_row: Adw.SwitchRow
    :ivar http_column_view: ColumnView for displaying headers.
    :vartype http_column_view: Gtk.ColumnView
    :ivar http_results_group: PreferencesGroup containing the results view.
    :vartype http_results_group: Adw.PreferencesGroup
    :ivar clear_results_button: Button to clear results.
    :vartype clear_results_button: Gtk.Button
    :ivar copy_results_button: Button to copy results to clipboard.
    :vartype copy_results_button: Gtk.Button
    :ivar http_status_row: :class:`Adw.ActionRow` to display status messages.
    :vartype http_status_row: Adw.ActionRow
    :ivar http_status_spinner: :class:`Gtk.Spinner` for loading indication.
    :vartype http_status_spinner: Gtk.Spinner
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
    http_status_row: Adw.ActionRow = Gtk.Template.Child()
    http_status_spinner: Gtk.Spinner = Gtk.Template.Child()

    def __init__(self, **kwargs: Any):
        logging.debug("HttpPage.__init__ called")
        """
        Initialize the HttpPage.

        Initializes UI elements, GSettings, the header list store for the
        column view, and connects signals.

        :param kwargs: Keyword arguments passed to the :class:`Gtk.Box` constructor.
        :type kwargs: Any
        """
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task: Optional[Gio.Task] = None
        self._current_header_items: list[HeaderItem] = []
        self._http_task_data_for_thread: dict[str, Any] = {}
        self._ua_title_to_value_map: dict[str, Optional[str]] = {}
        self.settings: Gio.Settings = Gio.Settings(schema_id=APP_ID)
        self._gsettings_ua_changed_handler_id = 0 # Initialize with a non-None value if Gio.Settings.connect returns 0 on error
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
            "changed::default-user-agent-title",
            self._on_default_ua_gsetting_changed
        )
        logging.debug(f"HttpPage: Connected GSettings listener for default-user-agent-title, handler ID: {self._gsettings_ua_changed_handler_id}")


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
        """
        Connect signals for UI elements to their respective handlers.

        :return: None
        """
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_host_header_row:
            self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row:
            # For visual feedback (CSS class)
            self.http_user_agent_row.connect("notify::selected-item", self._on_user_agent_changed_visual_feedback)
            # For saving preference
            self.http_user_agent_row.connect("notify::selected-item", self._save_selected_user_agent_preference)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        """
        Handle changes in the Host header entry row.

        Adds or removes a CSS class to indicate if an override is active.

        :param entry_row: The :class:`Adw.EntryRow` for the Host header.
        :type entry_row: Adw.EntryRow
        :return: None
        """
        if not entry_row:
            return
        text = entry_row.get_text().strip()
        if text:
            entry_row.add_css_class("active-override")
        else:
            entry_row.remove_css_class("active-override")

    def _on_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        """
        Handle changes in the User-Agent combo row selection.

        Adds or removes a CSS class to indicate if a non-default User-Agent is active.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: Optional[GObject.ParamSpec]
        :return: None
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
        """
        Handle visual feedback for User-Agent combo row selection changes.

        This method is responsible for adding/removing the 'active-override' CSS class.
        It is separated from the logic that saves the preference to GSettings.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: Optional[GObject.ParamSpec]
        :return: None
        """
        self._on_user_agent_changed(combo_row, _gparam)  # Call the original method for CSS

    def _save_selected_user_agent_preference(
        self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]
    ) -> None:
        """
        Handle User-Agent selection changes in the HTTP Page's dropdown.

        This method is connected to the 'notify::selected-item' signal of the
        User-Agent ComboRow on the HTTP Page. It logs the change for the current
        session. It does *not* save this selection back to the global
        `default-user-agent-title` GSettings key, ensuring that the HTTP Page's
        User-Agent choice is session-specific and does not alter the
        globally configured default User-Agent preference.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: Optional[GObject.ParamSpec]
        :return: None
        """
        if not combo_row:
            return

        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            logger.info(f"HTTP Page User-Agent selection changed to: '{selected_title}'. This is a session-specific change.")
            # The line below is commented out to prevent HTTP page selection from changing global default.
            # self.settings.set_string("default-user-agent-title", selected_title if selected_title != "None" else "")
        elif selected_item_obj is None:
            logger.info("HTTP Page User-Agent selection cleared (None). This is a session-specific change.")
            # The line below is commented out
            # self.settings.set_string("default-user-agent-title", "")


    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle the click event for the 'Copy Results' button.

        Constructs a string representation of the displayed headers and copies
        it to the clipboard.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
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

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        """
        Handle activation of the URL entry row or click of the 'Fetch' button.

        Validates the URL and gathers request parameters for the HTTP request:
        - URL: Taken from `http_entry_row`.
        - Host header: Taken from `http_host_header_row` if provided.
        - User-Agent: Determined by the following logic:
            1. If a specific User-Agent is selected in the `http_user_agent_row`
               (i.e., not "None"), that User-Agent string is used.
            2. If `http_user_agent_row` is set to "None", the method fetches the
               `default-user-agent-title` from GSettings. This title is then
               resolved to an actual User-Agent string by first checking the
               predefined `USER_AGENTS` list (from `constants.py`) and then
               the custom User-Agents stored in GSettings.
            3. If no GSettings default is set, or if the GSettings title cannot be
               resolved to a User-Agent string, a final fallback is used: either
               the first User-Agent in the `USER_AGENTS` list or a generic
               "Woes/APP_ID" User-Agent. If `USER_AGENTS` is empty, `None` is
               sent, letting the `requests` library use its own default.
        - Custom DNS server: Read from GSettings.
        - Akamai Pragma header state: Taken from `http_pragma_switch_row`.

        After gathering parameters, it initiates a background task
        (`_fetch_headers_task_thread_func`) to perform the HTTP request via
        :class:`.http_client.HttpFetcher`.

        :param _widget: The :class:`Gtk.Widget` that triggered the activation (unused).
        :type _widget: Gtk.Widget
        :return: None
        """
        original_url = self.http_entry_row.get_text().strip()
        url = self._ensure_scheme(original_url)
        logger.info("Fetching headers for URL: %s (original input: %s)", url, original_url)

        if not is_valid_url(url):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url)
            toast_message = "Invalid URL format. Please enter a valid URL (e.g., https://example.com)."
            show_global_toast(self, toast_message)  # type: ignore[arg-type]
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, "show_toast")):
                show_global_error(self, toast_message)  # type: ignore[arg-type]
            self._update_column_view_model(None)
            self._set_loading_state(False, "Idle - Invalid URL.")
            return

        self._clear_error()
        self._set_loading_state(True, "Fetching headers...")

        host_header = self.http_host_header_row.get_text().strip()
        user_agent_to_send: Optional[str] = None
        selected_ua_title_in_http_page_dropdown: Optional[str] = None

        selected_item_obj = self.http_user_agent_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_ua_title_in_http_page_dropdown = selected_item_obj.get_string()

        if selected_ua_title_in_http_page_dropdown and selected_ua_title_in_http_page_dropdown != "None":
            # User selected a specific UA in the HTTP page dropdown
            ua_found = False
            # Check in USER_AGENTS
            for ua_dict in USER_AGENTS:
                if ua_dict['title'] == selected_ua_title_in_http_page_dropdown:
                    user_agent_to_send = ua_dict['value']
                    ua_found = True
                    break
            # If not in default, check custom (self._ua_title_to_value_map includes custom ones)
            if not ua_found and selected_ua_title_in_http_page_dropdown in self._ua_title_to_value_map:
                user_agent_to_send = self._ua_title_to_value_map[selected_ua_title_in_http_page_dropdown]
                ua_found = True  # Should be true if title is in map

            if ua_found:
                logger.info(
                    f"Using User-Agent from HTTP Page UI selection: '{selected_ua_title_in_http_page_dropdown}'"
                )
            else:  # Should not happen if dropdown is populated correctly
                logger.warning(
                    f"Selected UA title '{selected_ua_title_in_http_page_dropdown}' not found in any list. Falling back."
                )
                if USER_AGENTS:
                    user_agent_to_send = USER_AGENTS[0]['value']
                    logger.info(f"Fell back to first default User-Agent: {USER_AGENTS[0]['title']}")
                else:
                    user_agent_to_send = f"Woes/{APP_ID} (Fallback)"
                    logger.info(f"Fell back to generic Woes User-Agent: {user_agent_to_send}")

        else:  # "None" selected in HTTP page, or no selection; use GSettings default
            default_ua_title_from_prefs = self.settings.get_string("default-user-agent-title")
            logger.info(
                f"HTTP Page UI set to 'None' or no selection. Using GSettings default: '{default_ua_title_from_prefs}'"
            )
            if default_ua_title_from_prefs:
                ua_found_in_prefs = False
                # Check in USER_AGENTS
                for ua_dict in USER_AGENTS:
                    if ua_dict['title'] == default_ua_title_from_prefs:
                        user_agent_to_send = ua_dict['value']
                        ua_found_in_prefs = True
                        break
                # If not in default, check custom (self._ua_title_to_value_map includes custom ones)
                if not ua_found_in_prefs and default_ua_title_from_prefs in self._ua_title_to_value_map:
                    # Need to ensure _ua_title_to_value_map is populated with custom UAs correctly
                    custom_uas_variant = self.settings.get_value("custom-user-agents")
                    custom_ua_pairs: list[tuple[str, str]] = list(
                        custom_uas_variant.unpack()
                        if custom_uas_variant and custom_uas_variant.get_type_string() == "a(ss)"
                        else []
                    )
                    for cust_title, cust_value in custom_ua_pairs:
                        if cust_title == default_ua_title_from_prefs:
                            user_agent_to_send = cust_value
                            ua_found_in_prefs = True
                            break

                if ua_found_in_prefs:
                    logger.info(f"Using default User-Agent from GSettings: '{default_ua_title_from_prefs}'")
                else:
                    logger.warning(
                        f"Default User-Agent title '{default_ua_title_from_prefs}' from GSettings not found. Falling back."
                    )

            if user_agent_to_send is None:  # Fallback if GSettings default is empty or not found
                if USER_AGENTS:
                    user_agent_to_send = USER_AGENTS[0]['value']
                    logger.info(f"Fell back to first default User-Agent: {USER_AGENTS[0]['title']}")
                else:
                    user_agent_to_send = f"Woes/{APP_ID} (Fallback)"
                    logger.info(f"Fell back to generic Woes User-Agent: {user_agent_to_send}")

        # Determine the actual User-Agent string to send
        if selected_ua_title_in_http_page_dropdown and selected_ua_title_in_http_page_dropdown != "None":
            # A specific UA is selected in the HTTP page's dropdown, use it
            user_agent_to_send = self._ua_title_to_value_map.get(selected_ua_title_in_http_page_dropdown)
            if user_agent_to_send:
                 logging.info(f"HttpPage: Using User-Agent from HTTP Page UI selection: '{selected_ua_title_in_http_page_dropdown}' -> '{user_agent_to_send[:30]}...'")
            else: # Should not happen if map is correct
                 logging.warning(f"HttpPage: Could not find value for selected title '{selected_ua_title_in_http_page_dropdown}'. Using fallback.")
                 user_agent_to_send = USER_AGENTS[0]['value'] if USER_AGENTS else f"Woes/{APP_ID}"
        else:
            # HTTP page dropdown is "None", so use the global default from GSettings
            gsettings_default_title = self.settings.get_string("default-user-agent-title")
            logging.debug(f"HttpPage: UI selection is 'None', GSettings default-user-agent-title is '{gsettings_default_title}'.")
            if gsettings_default_title:
                # Try to find in standard USER_AGENTS
                resolved_ua = next((ua['value'] for ua in USER_AGENTS if ua['title'] == gsettings_default_title), None)
                if resolved_ua:
                    user_agent_to_send = resolved_ua
                    logging.debug(f"HttpPage: Resolved GSettings default '{gsettings_default_title}' from standard UAs.")
                else:
                    # Try to find in custom UAs (from GSettings directly, _ua_title_to_value_map might not be fully up-to-date here if prefs changed)
                    custom_uas_variant = self.settings.get_value("custom-user-agents")
                    custom_ua_pairs: list[tuple[str, str]] = list(
                        custom_uas_variant.unpack() if custom_uas_variant and custom_uas_variant.get_type_string() == "a(ss)" else []
                    )
                    resolved_custom_ua = next((val for title, val in custom_ua_pairs if title == gsettings_default_title), None)
                    if resolved_custom_ua:
                        user_agent_to_send = resolved_custom_ua
                        logging.debug(f"HttpPage: Resolved GSettings default '{gsettings_default_title}' from custom UAs.")
                    else:
                        logging.warning(f"HttpPage: GSettings default UA title '{gsettings_default_title}' not found in any list. Using ultimate fallback.")
                        user_agent_to_send = USER_AGENTS[0]['value'] if USER_AGENTS else f"Woes/{APP_ID}"
            else:
                # GSettings default is also "None" (empty string)
                logging.debug("HttpPage: UI selection is 'None' and GSettings default is also 'None'. Using system/requests default UA.")
                user_agent_to_send = None # Let requests library handle default or use its own.

        logging.info(f"HttpPage: Final User-Agent string for request: {user_agent_to_send if user_agent_to_send else 'System Default'}")
        custom_dns_server = self.settings.get_string("custom-dns-server")

        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header if host_header else None,
            "user_agent": user_agent_to_send, #This can be None
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
        _task_data_arg: Dict[str, Any],
        cancellable: Optional[Gio.Cancellable],
    ) -> None:
        """
        Background thread function for fetching HTTP headers.

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
        :type _task_data_arg: dict[str, Any]
        :param cancellable: A :class:`Gio.Cancellable` object to monitor for cancellation.
        :type cancellable: Optional[Gio.Cancellable]
        :return: None
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
        """
        Handle completion of the HTTP headers fetch task.

        Processes the result from the background thread, updates the UI with headers
        or an error message, and re-enables UI elements.

        :param _source_object: The source object that initiated the task (unused).
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: Optional[Any]
        :return: None
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
            propagate_result = task_being_processed.propagate_value()  # type: ignore[union-attr]
            actual_list_of_responses: Optional[list[dict[str, Any]]] = None

            if isinstance(propagate_result, list):
                actual_list_of_responses = propagate_result
            elif hasattr(propagate_result, "value") and isinstance(propagate_result.value, list):  # type: ignore[attr-defined]
                # Handles cases where the result might be wrapped, e.g. by older PyGObject versions or specific task types
                logger.debug("HttpPage: Accessing .value from propagated result of type %s", type(propagate_result))
                actual_list_of_responses = propagate_result.value  # type: ignore[attr-defined]
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
        """
        Set the UI loading state.

        Manages the visibility of the spinner, updates the status message,
        and adjusts the sensitivity of input controls.

        :param active: If ``True``, sets the UI to a loading state; otherwise, sets it to an idle state.
        :type active: bool
        :param message: The message to display in the status row. Defaults to "Idle".
        :type message: str
        :return: None
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
        Ensure the URL has a scheme, defaulting to 'https://'.

        This provides a basic check before passing to :class:`.http_client.HttpFetcher`,
        which will perform more robust URL parsing.

        :param url: The input URL string.
        :type url: str
        :return: The URL string, with 'https://' prepended if no scheme was present.
        :rtype: str
        """
        if "://" not in url:
            logger.debug("URL '%s' has no scheme, prepending 'https://'.", url)
            return "https://" + url
        return url

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        """
        Handle toggling of the Akamai Pragma switch.

        If a URL is present in the entry row, it re-triggers the fetch.

        :param _widget: The :class:`Gtk.Switch` that was toggled (unused).
        :type _widget: Gtk.Switch
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        :return: None
        """
        logger.debug("Akamai Pragma toggled: %s. Re-fetching if URL present.", self.http_pragma_switch_row.get_active())
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[List[HeaderItem]]) -> None:
        """
        Update the :class:`Gio.ListStore` for the header :class:`Gtk.ColumnView`.

        Clears the existing items and appends new ones if provided.
        Shows or hides the results group accordingly.

        :param header_items: A list of :class:`.HeaderItem` objects to display,
                             or ``None`` to clear the view.
        :type header_items: Optional[list[HeaderItem]]
        :return: None
        """
        self.header_list_store.remove_all()  # type: ignore[attr-defined]
        if header_items:
            for item in header_items:
                self.header_list_store.append(item)  # type: ignore[attr-defined]
            self._show_results()
        else:
            self._hide_results()

    def _show_results(self) -> None:
        """
        Make the HTTP results group visible.

        :return: None
        """
        if self.http_results_group:
            self.http_results_group.set_visible(True)

    def _hide_results(self) -> None:
        """
        Make the HTTP results group invisible.

        :return: None
        """
        if self.http_results_group:
            self.http_results_group.set_visible(False)

    def _clear_error(self) -> None:
        """
        Clear any error state in the UI.

        Hides the main window's error banner and removes the 'error' CSS class
        from the URL entry row.

        :return: None
        """
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error()  # type: ignore[attr-defined]
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error")

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle the click event for the 'Clear Results' button.

        Clears the displayed headers, error state, and the URL entry.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
        """
        logger.info("Results cleared by user action.")
        self._current_header_items = []
        self._update_column_view_model(None)
        self._clear_error()
        if self.http_entry_row:
            self.http_entry_row.set_text("")

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        """
        Handle changes to color-related GSettings.

        Updates the internal color attributes and re-populates the column view
        to apply the new colors if results are currently displayed.

        :param settings: The :class:`Gio.Settings` object that changed.
        :type settings: Gio.Settings
        :param key: The GSettings key that changed.
        :type key: str
        :return: None
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
        """
        Handle changes to the global output font GSettings key.

        Updates the internal font description and re-populates the column view
        to apply the new font if results are currently displayed.

        :param settings: The :class:`Gio.Settings` object that changed.
        :type settings: Gio.Settings
        :param key: The GSettings key that changed.
        :type key: str
        :return: None
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
        """
        Update the model for the User-Agent :class:`Adw.ComboRow` on the HTTP Page.

        This method populates the dropdown with a "None" option, standard User-Agents
        from :mod:`.constants.USER_AGENTS`, and any custom User-Agents defined in
        GSettings.

        Crucially, after populating, it sets the initial selection of this dropdown
        based on the `default-user-agent-title` GSettings value. If this
        preference is empty or the specified User-Agent title is not found in the
        populated list, it defaults to selecting the "None" option. This ensures
        the HTTP Page respects the global default User-Agent preference on
        initialization.

        :return: None
        """
        if not self.http_user_agent_row:
            logger.error("HttpPage._update_user_agent_model: http_user_agent_row is None.")
            return
        self._ua_title_to_value_map.clear()
        # current_selection_text: Optional[str] = None # Removed as per new logic

        # if (
        #     self.http_user_agent_row.get_model()
        #     and self.http_user_agent_row.get_selected() != Gtk.INVALID_LIST_POSITION
        # ):
        #     selected_item_obj = self.http_user_agent_row.get_selected_item()
        #     if isinstance(selected_item_obj, Gtk.StringObject):
        #         current_selection_text = selected_item_obj.get_string()

        display_titles: list[str] = []

        none_title = "None"  # Represents using the default resolution logic (GSettings -> Fallback)
        display_titles.append(none_title)
        self._ua_title_to_value_map[none_title] = None  # Explicitly map "None" to no specific UA string override

        # Populate with USER_AGENTS from constants.py
        for ua_dict in USER_AGENTS:  # Iterate list of dictionaries
            title = ua_dict['title']
            value = ua_dict['value']
            if title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value
            else:
                logger.warning(
                    f"Default User-Agent title '{title}' conflicts with 'None' or another default UA. Skipping."
                )

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
        # logging.info(f"HttpPage._update_user_agent_model: Initial default UA from GSettings: '{default_ua_title_from_prefs}'.") # Can be verbose
        self._select_ua_in_http_page_dropdown(default_ua_title_from_prefs if default_ua_title_from_prefs else "None")
        # Visual feedback is handled by _select_ua_in_http_page_dropdown calling _on_user_agent_changed_visual_feedback

    def _on_default_ua_gsetting_changed(self, settings: Gio.Settings, key: str) -> None:
        """
        Handle changes to the 'default-user-agent-title' GSettings key.

        This method is called when the global default User-Agent preference is changed
        (e.g., from the Preferences window). If the User-Agent dropdown on this
        HTTP page is currently set to "None" (meaning it should follow the default),
        this method updates the dropdown to reflect the new global default by calling
        :meth:`._select_ua_in_http_page_dropdown`.

        If the dropdown has a specific User-Agent selected (i.e., not "None"),
        it remains unchanged, preserving the user's session-specific choice for this page.

        :param settings: The :class:`Gio.Settings` object that emitted the signal.
        :type settings: Gio.Settings
        :param key: The GSettings key that changed (expected to be "default-user-agent-title").
        :type key: str
        """
        if key == "default-user-agent-title":
            new_gsettings_default_ua_title = settings.get_string(key)
            logging.info(f"HttpPage: Notified of GSettings default-user-agent-title change to: '{new_gsettings_default_ua_title}'.")

            current_http_page_selection_obj = self.http_user_agent_row.get_selected_item()
            current_http_page_selected_title = ""
            if isinstance(current_http_page_selection_obj, Gtk.StringObject):
                current_http_page_selected_title = current_http_page_selection_obj.get_string()

            if current_http_page_selected_title == "None":
                logging.info(f"HttpPage: Current UA selection is 'None'. Updating dropdown to new GSettings default: '{new_gsettings_default_ua_title if new_gsettings_default_ua_title else 'None'}'.")
                self._select_ua_in_http_page_dropdown(new_gsettings_default_ua_title if new_gsettings_default_ua_title else "None")
            else:
                # This is an expected state if user has made a session-specific choice.
                logging.debug(f"HttpPage: Current UA selection is '{current_http_page_selected_title}' (not 'None'). Ignoring GSettings change for this page instance.")

    def _select_ua_in_http_page_dropdown(self, title_to_select: str) -> bool:
        """
        Safely select an item in the HTTP Page's User-Agent dropdown by its title.

        If the exact title is not found in the dropdown's model, this method
        falls back to selecting the "None" option. It ensures that the
        `http_user_agent_row` always has a valid selection if possible.
        After setting the selection, it calls
        :meth:`._on_user_agent_changed_visual_feedback` to update any
        associated UI styling (e.g., the 'active-override' CSS class).

        :param title_to_select: The title of the User-Agent to select in the dropdown.
        :type title_to_select: str
        :return: ``True`` if an item (either the target or fallback "None") was
                 successfully selected, ``False`` if the model is invalid or empty,
                 or if "None" could not be selected as a fallback.
        :rtype: bool
        """
        model = self.http_user_agent_row.get_model()
        if not isinstance(model, Gtk.StringList): # type: ignore
            logging.error("HttpPage: http_user_agent_row model is not Gtk.StringList.")
            return False

        all_titles_in_dropdown = [model.get_string(i) for i in range(model.get_n_items())] # type: ignore

        final_title_to_select = title_to_select
        if title_to_select not in all_titles_in_dropdown:
            logging.warning(f"HttpPage: Title '{title_to_select}' not found in dropdown. Falling back to 'None'.")
            final_title_to_select = "None" # Fallback

        if final_title_to_select in all_titles_in_dropdown:
            try:
                idx = all_titles_in_dropdown.index(final_title_to_select)
                # TODO: Consider handler_block if set_selected itself causes unwanted signal runs, though
                # _on_save_selected_user_agent_preference is now passive for GSettings.
                self.http_user_agent_row.set_selected(idx)
                # logging.info(f"HttpPage: Successfully selected '{final_title_to_select}' in its User-Agent dropdown.") # Can be verbose
                self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None) # Ensure visual style updates
                return True
            except ValueError:
                logging.error(f"HttpPage: Error selecting '{final_title_to_select}' (ValueError) despite it being in list.")
                return False
        elif model.get_n_items() > 0 : # Fallback if even "None" isn't there (highly unlikely)
             self.http_user_agent_row.set_selected(0)
             logging.error("HttpPage: Critical - could not find fallback 'None' or any items. Selected first available.")
             self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)
             return False
        return False # Model might be empty

    def do_dispose(self):
        """
        Override GObject.Object.do_dispose to disconnect signal handlers.

        Ensures that the GSettings listener for `default-user-agent-title`
        is disconnected when the HttpPage object is disposed, preventing
        potential issues if the settings object outlives the page or if
        the handler attempts to operate on destroyed widgets.
        """
        if self._gsettings_ua_changed_handler_id and self.settings.is_connected(self._gsettings_ua_changed_handler_id) :
            self.settings.disconnect(self._gsettings_ua_changed_handler_id)
            logging.debug("HttpPage: Disconnected GSettings listener for default-user-agent-title.")
        self._gsettings_ua_changed_handler_id = 0 # Set to 0 or an invalid ID state after disconnect
        super().do_dispose()


    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """
        Create a Gtk.SignalListItemFactory for Gtk.ColumnView columns.

        This factory configures how items (:class:`HeaderItem`) are displayed in the
        columns of the :class:`Gtk.ColumnView`. It sets up labels, binds them to
        the appropriate attributes of :class:`HeaderItem`, and applies styling
        (font, color, wrapping) based on the item type and application settings.

        :param attr_name: The attribute name of the :class:`.HeaderItem` to display
                          (e.g., 'key' or 'value').
        :type attr_name: str
        :param wrap_text: Whether the text in the label should wrap. Defaults to ``False``.
        :type wrap_text: bool
        :return: A configured :class:`Gtk.SignalListItemFactory`.
        :rtype: Gtk.SignalListItemFactory
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
                        full_text += f" {value_text}"
                    label.set_markup(
                        f"<b><span font_family='{GLib.markup_escape_text(current_family)}' size='{size_in_pango_units}' foreground='{self._special_row_color}'>{full_text}</span></b>"
                    )
                else:  # 'value' column for special rows is typically empty
                    label.set_markup("")
            else:  # Standard header rows
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(str(text_to_display))
                label.set_markup(
                    f"<span font_family='{GLib.markup_escape_text(current_family)}' size='{size_in_pango_units}' foreground='{color_to_use}'>{escaped_text}</span>"
                )

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory

    def trigger_fetch(self) -> None:
        """
        Programmatically trigger the 'Fetch' action.

        This method is typically called in response to a keyboard shortcut
        or an external event. It simulates a click on the 'Fetch' button
        if the button is available and sensitive.

        :return: None
        """
        logging.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.activate()
        else:
            logging.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")
