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
from typing import Any, Dict, List, Optional

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk, Pango

from .constants import RESOURCE_PREFIX, APP_ID, USER_AGENTS
from .utils import show_global_error, show_global_toast, is_valid_url, process_task_result # Import new utility
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

WOES_HTTP_ERROR_DOMAIN = "woes-http-error-domain" # Not used by process_task_result directly, but good for context


class HttpErrorType(int, Enum): # Not used by process_task_result directly
    """Enumeration of HTTP error types for Gio.Task error reporting."""
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
class HttpPage(Gtk.Box):
    __gtype_name__ = "HttpPage"

    SYSTEM_DEFAULT_UA_TITLE = "System Default (requests)"

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
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task: Optional[Gio.Task] = None
        self._current_header_items: list[HeaderItem] = []
        self._http_task_data_for_thread: dict[str, Any] = {}
        self._ua_title_to_value_map: dict[str, Optional[str]] = {}
        self.settings: Gio.Settings = Gio.Settings(schema_id=APP_ID)

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

        self.header_list_store = Gio.ListStore(item_type=HeaderItem) # Use constructor
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
        self._update_user_agent_model() # This now handles initial selection based on GSettings

        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._update_user_agent_model())
        self._gsettings_ua_changed_handler_id = self.settings.connect(
            "changed::default-user-agent-title", self._on_default_ua_gsetting_changed
        )

        if self.http_apply_button: self.http_apply_button.get_style_context().add_class("suggested-action")
        if self.clear_results_button: self.clear_results_button.get_style_context().add_class("destructive-action")
        if self.copy_results_button: self.copy_results_button.get_style_context().add_class("flat")
        if self.http_host_header_row: self._on_host_header_changed(self.http_host_header_row)
        if self.http_user_agent_row: self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)

        self.column_view_helper = Helper(widget=self.http_column_view, parent_window=self.get_native())

    def _connect_signals(self) -> None:
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button: self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_host_header_row: self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row: self.http_user_agent_row.connect("notify::selected-item", self._on_http_page_ua_selection_changed)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        if not entry_row: return
        if entry_row.get_text().strip(): entry_row.add_css_class("active-override")
        else: entry_row.remove_css_class("active-override")

    def _on_user_agent_changed_visual_feedback(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        if not combo_row: return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            if selected_title == self.SYSTEM_DEFAULT_UA_TITLE: # Use sentinel
                combo_row.remove_css_class("active-override")
            else:
                combo_row.add_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map:
            combo_row.remove_css_class("active-override")

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Copying all headers to clipboard.")
        lines: list[str] = []
        if self.header_list_store:
            for i in range(self.header_list_store.get_n_items()):
                item = self.header_list_store.get_item(i)
                if isinstance(item, HeaderItem):
                    if item.is_special_row:
                        if item.value and item.value.strip(): lines.append(f"{item.key} {item.value}")
                        else: lines.append(item.key)
                    else: lines.append(f"{item.key}: {item.value}")
        if lines:
            text_to_copy = "\n".join(lines)
            try:
                clipboard = Gdk.Display.get_default().get_clipboard()
                if clipboard: clipboard.set(text_to_copy); logger.info("Headers copied to clipboard successfully.")
                else: logger.warning("Failed to get default clipboard for copying.")
            except Exception as e: logger.error("Error copying headers to clipboard: %s", e, exc_info=True)
        else: logger.info("No headers to copy from the results view.")

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        original_url = self.http_entry_row.get_text().strip()
        url = self._ensure_scheme(original_url)
        if not is_valid_url(url):
            logger.warning("HTTP Page: Invalid URL provided: %s (processed as: %s)", original_url, url)
            toast_message = "Invalid URL format. Please enter a valid URL (e.g., https://example.com)."
            show_global_toast(self, toast_message)
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, "show_toast")): show_global_error(self, toast_message)
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

        if selected_ua_title_in_http_page_dropdown == self.SYSTEM_DEFAULT_UA_TITLE:
            user_agent_to_send = None
            logging.info(f"HTTP Page: Using system default User-Agent (requests library default).")
        elif selected_ua_title_in_http_page_dropdown:
            user_agent_to_send = self._ua_title_to_value_map.get(selected_ua_title_in_http_page_dropdown)
            logging.info(f"HTTP Page: Using User-Agent from page dropdown selection: '{selected_ua_title_in_http_page_dropdown}'")
            if user_agent_to_send is None and selected_ua_title_in_http_page_dropdown != self.SYSTEM_DEFAULT_UA_TITLE:
                 logging.warning(f"HTTP Page: Selected UA title '{selected_ua_title_in_http_page_dropdown}' not found in map. Sending no specific UA.")
        else:
            logging.warning("HTTP Page: No User-Agent selected. Using system default.")
            user_agent_to_send = None

        custom_dns_server = self.settings.get_string("custom-dns-server")
        logging.info(f"HttpPage: Final User-Agent for request task: {user_agent_to_send if user_agent_to_send else 'None (requests default)'}")
        self._http_task_data_for_thread = {
            "url": url, "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header if host_header else None, "user_agent": user_agent_to_send,
            "custom_dns_server": custom_dns_server if custom_dns_server else None,
        }
        logger.debug("HttpPage: Starting header fetch task with data: %s", self._http_task_data_for_thread)
        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None) # Removed type: ignore
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func) # Removed type: ignore

    def _fetch_headers_task_thread_func(self, task: Gio.Task, _s_obj: GObject.Object, _t_data: Any, cancellable: Optional[Gio.Cancellable] = None) -> None: # Changed _t_data type, added default for cancellable
        current_task_data = self._http_task_data_for_thread
        url_to_fetch: str = current_task_data["url"]
        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Task cancelled before fetching.")
            return
        fetcher = HttpFetcher(
            url=url_to_fetch, use_akamai_pragma=current_task_data["use_akamai_pragma"],
            host_header=current_task_data.get("host_header"), user_agent=current_task_data.get("user_agent"),
            custom_dns_server=current_task_data.get("custom_dns_server"), cancellable=cancellable,
        )
        try:
            processed_data = fetcher.fetch_headers()
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Task cancelled after fetching.")
            else: task.return_value(processed_data)
        except HttpRequestTimeoutError as e: task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.TIMEOUT.value, str(e))
        except HttpConnectionError as e: task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CONNECTION_ERROR.value, str(e))
        except HttpProcessingError as e: task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.HTTP_ERROR.value, str(e))
        except HttpGenericRequestError as e: task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.REQUEST_EXCEPTION.value, str(e))
        except HttpClientError as e:
            if "cancelled" in str(e).lower(): task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, str(e))
            else: task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, str(e))
        except Exception as e:
            logger.exception("HttpPage Task: Unexpected generic error for URL '%s':", url_to_fetch)
            task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, f"Unexpected internal error: {e}")

    def _fetch_headers_task_done_cb(self, _source_object: GObject.Object, result: Gio.AsyncResult, _user_data: Any = None) -> None: # Changed _user_data type
        task_being_processed = self.current_http_task
        if task_being_processed is None:
            if self.http_status_row and self.http_status_row.get_subtitle() == "Fetching headers...": self._set_loading_state(False, "Idle.")
            return
        self.current_http_task = None

        data, error_msg = process_task_result(task_being_processed, result, logger)

        if error_msg:
            show_global_error(self, error_msg)
            if self.http_entry_row: self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
            self._set_loading_state(False, f"Error: {error_msg.splitlines()[0]}")
        elif data is not None:
            actual_list_of_responses: Optional[list[dict[str, Any]]] = None
            if isinstance(data, list): actual_list_of_responses = data
            elif hasattr(data, "value") and isinstance(data.value, list): actual_list_of_responses = data.value # type: ignore
            else:
                logger.error("HttpPage: Unexpected data type from process_task_result: %s", type(data))
                show_global_error(self, "Unexpected result data type from background task.")
                if self.http_entry_row: self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                self._set_loading_state(False, "Error: Unexpected data format.")
                return

            if actual_list_of_responses is not None:
                processed_headers_for_store: list[HeaderItem] = []
                if not actual_list_of_responses: self._update_column_view_model(None)
                else:
                    for i, response_data_dict_item in enumerate(actual_list_of_responses):
                        if not isinstance(response_data_dict_item, dict): continue
                        response_data_dict: dict[str, Any] = response_data_dict_item
                        url_display = f"URL: {response_data_dict.get('url', 'N/A')}"
                        status_code = response_data_dict.get("status_code", "N/A")
                        response_type = response_data_dict.get("type", "unknown")
                        status_display = f"Status: {status_code} ({str(response_type).capitalize()})"
                        processed_headers_for_store.append(HeaderItem(key=url_display, value=status_display, is_special_row=True))
                        headers_for_this_response = response_data_dict.get("headers", {})
                        if isinstance(headers_for_this_response, dict):
                            for key, value in headers_for_this_response.items():
                                original_value_str = str(value); display_parts = []; current_value_segment = original_value_str
                                if not original_value_str.strip(): display_parts.append("")
                                else:
                                    while True:
                                        idx = current_value_segment.find(";")
                                        if idx != -1: display_parts.append(current_value_segment[:idx+1].strip()); current_value_segment = current_value_segment[idx+1:]
                                        else: display_parts.append(current_value_segment.strip()); break
                                first_part_processed = False
                                for part_content in display_parts:
                                    if not first_part_processed: processed_headers_for_store.append(HeaderItem(key=str(key), value=part_content, is_special_row=False)); first_part_processed = True
                                    else: processed_headers_for_store.append(HeaderItem(key="", value=part_content, is_special_row=False))
                        if i < len(actual_list_of_responses) - 1: processed_headers_for_store.append(HeaderItem(key="--- Redirected To ---", value="", is_special_row=True))
                    self._current_header_items = processed_headers_for_store
                    self._update_column_view_model(processed_headers_for_store)
                if self.http_entry_row: self.http_entry_row.remove_css_class("error")
                self._set_loading_state(False, "Headers loaded successfully.")
            else: # Should be caught by process_task_result if data is None and error_msg is also None
                show_global_error(self, "Failed to process data from background task (data is None).")
                if self.http_entry_row: self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                self._set_loading_state(False, "Error: Failed to process data.")
        finally:
            if self.current_http_task is None: # Ensure UI reset if task was cleared before finally
                if self.http_status_row and self.http_status_row.get_subtitle() == "Fetching headers...":
                    self._set_loading_state(False, "Idle - operation ended.")

    def _set_loading_state(self, active: bool, message: str = "Idle") -> None:
        if self.http_status_spinner:
            self.http_status_spinner.set_visible(active)
            if active: self.http_status_spinner.start()
            else: self.http_status_spinner.stop()
        if self.http_status_row: self.http_status_row.set_subtitle(message)
        sensitive = not active
        if self.http_entry_row: self.http_entry_row.set_sensitive(sensitive)
        if self.http_apply_button: self.http_apply_button.set_sensitive(sensitive)
        if self.http_host_header_row: self.http_host_header_row.set_sensitive(sensitive)
        if self.http_user_agent_row: self.http_user_agent_row.set_sensitive(sensitive)
        if self.http_pragma_switch_row: self.http_pragma_switch_row.set_sensitive(sensitive)

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        if "://" not in url: return "https://" + url
        return url

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        if self.http_entry_row.get_text().strip(): self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[List[HeaderItem]]) -> None:
        self.header_list_store.remove_all()
        if header_items:
            for item in header_items: self.header_list_store.append(item)
            self._show_results()
        else: self._hide_results()

    def _show_results(self) -> None:
        if self.http_results_group: self.http_results_group.set_visible(True)
    def _hide_results(self) -> None:
        if self.http_results_group: self.http_results_group.set_visible(False)
    def _clear_error(self) -> None:
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"): main_window.hide_error()
        if self.http_entry_row: self.http_entry_row.remove_css_class("error")

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        self._current_header_items = []
        self._update_column_view_model(None)
        self._clear_error()
        if self.http_entry_row: self.http_entry_row.set_text("")

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        if key == "http-output-header-key-color": self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color": self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color": self._special_row_color = settings.get_string(key)
        if self._current_header_items: self._update_column_view_model(self._current_header_items)

    def _on_global_output_font_changed(self, settings: Gio.Settings, key: str) -> None:
        if key == self._output_font_gsettings_key:
            output_font_str = settings.get_string(key)
            self._output_font_desc = Pango.FontDescription.from_string(output_font_str if output_font_str else "Sans 10")
            if self._current_header_items: self._update_column_view_model(self._current_header_items)

    def _update_user_agent_model(self) -> None:
        if not self.http_user_agent_row: return
        self._ua_title_to_value_map.clear()
        display_titles: list[str] = [self.SYSTEM_DEFAULT_UA_TITLE]
        self._ua_title_to_value_map[self.SYSTEM_DEFAULT_UA_TITLE] = None
        for ua_dict in USER_AGENTS:
            title, value = ua_dict["title"], ua_dict["value"]
            if title == self.SYSTEM_DEFAULT_UA_TITLE: continue
            if title not in self._ua_title_to_value_map: display_titles.append(title); self._ua_title_to_value_map[title] = value
        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(variant.unpack() if variant and variant.get_type_string() == "a(ss)" else [])
        for title, value in custom_ua_pairs:
            if title == self.SYSTEM_DEFAULT_UA_TITLE: continue
            if title not in self._ua_title_to_value_map: display_titles.append(title); self._ua_title_to_value_map[title] = value
        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles))

        gsettings_default_ua_title = self.settings.get_string("default-user-agent-title")
        title_to_select_initially = self.SYSTEM_DEFAULT_UA_TITLE
        if gsettings_default_ua_title and gsettings_default_ua_title in self._ua_title_to_value_map:
            title_to_select_initially = gsettings_default_ua_title
        self._select_ua_in_http_page_dropdown(title_to_select_initially)

    def _on_http_page_ua_selection_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec) -> None:
        selected_item_obj = combo_row.get_selected_item()
        if not isinstance(selected_item_obj, Gtk.StringObject): return
        # This method now only updates visual feedback. No GSettings interaction.
        self._on_user_agent_changed_visual_feedback(combo_row, _gparam)

    def _on_default_ua_gsetting_changed(self, settings: Gio.Settings, key: str) -> None:
        if key == "default-user-agent-title":
            new_gsettings_default_ua_title = settings.get_string(key)
            logger.info(f"HttpPage: Global GSettings 'default-user-agent-title' changed to: '{new_gsettings_default_ua_title}'. Page dropdown no longer auto-follows post-init.")
            # The dropdown is no longer forced to follow after initial setup.

    def _select_ua_in_http_page_dropdown(self, title_to_select: str) -> bool:
        model = self.http_user_agent_row.get_model()
        if not isinstance(model, Gtk.StringList): return False
        all_titles_in_dropdown = [model.get_string(i) for i in range(model.get_n_items())]
        final_title_to_select = title_to_select
        if title_to_select not in all_titles_in_dropdown:
            final_title_to_select = self.SYSTEM_DEFAULT_UA_TITLE
            if self.SYSTEM_DEFAULT_UA_TITLE not in all_titles_in_dropdown and model.get_n_items() > 0:
                 final_title_to_select = model.get_string(0) # Fallback to first if sentinel is missing
            elif model.get_n_items() == 0: return False # Cannot select if empty
        try:
            idx = all_titles_in_dropdown.index(final_title_to_select)
            self.http_user_agent_row.set_selected(idx)
            self._on_user_agent_changed_visual_feedback(self.http_user_agent_row, None)
            return True
        except ValueError: return False

    def do_dispose(self):
        if self._gsettings_ua_changed_handler_id and self.settings.is_connected(self._gsettings_ua_changed_handler_id):
            self.settings.disconnect(self._gsettings_ua_changed_handler_id)
        self._gsettings_ua_changed_handler_id = 0
        # Disconnect other GSettings handlers if any were stored similarly
        # For example, if self.settings.connect("changed::custom-user-agents", ...) returned an ID and it was stored.
        # However, the lambda for custom-user-agents doesn't store its ID.
        # GObject should handle those managed by its lifecycle if not explicitly disconnected.
        super().do_dispose()

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()
        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0.0, hexpand=True)
            if wrap_text: label.set_wrap(True); label.set_wrap_mode(Pango.WrapMode.WORD_CHAR); label.set_max_width_chars(80)
            list_item.set_child(label)
        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child(); item = list_item.get_item()
            if not (isinstance(label, Gtk.Label) and isinstance(item, HeaderItem)):
                if isinstance(label, Gtk.Label): label.set_text("Error: Invalid item type.")
                return
            text_to_display = getattr(item, attr_name, "")
            family = self._output_font_desc.get_family(); size_in_pango_units = self._output_font_desc.get_size()
            current_family = family if family else "Sans"
            if item.is_special_row:
                if attr_name == "key":
                    key_text = GLib.markup_escape_text(str(item.key)); value_text = GLib.markup_escape_text(str(item.value)) if item.value else ""
                    full_text = key_text
                    if value_text.strip() and value_text != "N/A": full_text += f" {value_text}"
                    label.set_markup(f"<b><span font_family='{GLib.markup_escape_text(current_family)}' size='{size_in_pango_units}' foreground='{self._special_row_color}'>{full_text}</span></b>")
                else: label.set_markup("")
            else:
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(str(text_to_display))
                label.set_markup(f"<span font_family='{GLib.markup_escape_text(current_family)}' size='{size_in_pango_units}' foreground='{color_to_use}'>{escaped_text}</span>")
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory

    def trigger_fetch(self) -> None:
        logging.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive(): self.http_apply_button.activate()
        else: logger.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")
