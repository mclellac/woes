"""Defines the HTTP Headers page for the Woes application.

This page allows users to fetch and inspect HTTP headers for a given URL,
with options for custom Host headers, User-Agent strings, and Akamai Pragma
headers. It also supports using a custom DNS server for domain resolution
via a :class:`.custom_dns_adapter.CustomDNSAdapter`.
"""

# pylint: disable=too-many-lines
# ruff: noqa: E501
import logging
from enum import Enum
from typing import Optional, List, Any # Added Any

# Removed requests, requests.utils, dns.resolver, urllib3.exceptions as they moved to http_client
# CustomDNSAdapter is now used by HttpFetcher, not directly here.
# dns variable for checking dnspython availability is also implicitly handled by HttpFetcher
import gi
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk, Pango

from .constants import RESOURCE_PREFIX, USER_AGENTS, APP_ID
from .utils import show_global_error, show_global_toast, is_valid_url
from .http_client import (
    HttpFetcher,
    HttpClientError,
    HttpRequestTimeoutError,
    HttpConnectionError,
    HttpProcessingError,
    HttpGenericRequestError,
)


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


logger = logging.getLogger(__name__)

WOES_HTTP_ERROR_DOMAIN = "woes-http-error-domain" # Used for Gio.Task errors


class HttpErrorType(int, Enum):
    """Enumeration of HTTP error types for :class:`Gio.Task` error reporting."""

    TIMEOUT = 0
    HTTP_ERROR = 1
    CONNECTION_ERROR = 2
    REQUEST_EXCEPTION = 3
    GENERIC_UNEXPECTED = 4
    CANCELLED = 5


class HeaderItem(GObject.Object):
    """GObject representing a single header key-value pair for the :class:`Gtk.ColumnView`."""

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
                               a standard key-value header. Defaults to False.
        :type is_special_row: bool
        """
        super().__init__()
        self.key = key
        self.value = value
        self.is_special_row = is_special_row


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Adw.PreferencesPage):
    """Activity page for fetching and inspecting HTTP headers.

    This class manages the UI and logic for the HTTP Headers inspection tool,
    including handling user input, performing HTTP requests in a background
    thread, and displaying the results.
    """

    __gtype_name__ = "HttpPage"
    http_entry_row = Gtk.Template.Child("http_entry_row")
    http_apply_button = Gtk.Template.Child("http_apply_button")
    http_host_header_row = Gtk.Template.Child("http_host_header_row")
    http_user_agent_row = Gtk.Template.Child("http_user_agent_row")
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    http_results_group = Gtk.Template.Child("http_results_group")
    clear_results_button = Gtk.Template.Child("clear_results_button")
    copy_results_button = Gtk.Template.Child()

    def __init__(self, **kwargs: GObject.GObject):
        """Initialize the HttpPage.

        Initializes UI elements, GSettings, the header list store for the
        column view, and connects signals.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage`
                       constructor.
        :type kwargs: GObject.GObject
        """
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task: Optional[Gio.Task] = None
        self._current_header_items: List[HeaderItem] = []
        self._http_task_data_for_thread: dict = {}
        self._ua_title_to_value_map: dict[str, Optional[str]] = {}
        self.settings = Gio.Settings(schema_id=APP_ID)
        self._header_key_color = self.settings.get_string("http-output-header-key-color")
        self._header_value_color = self.settings.get_string("http-output-header-value-color")
        self._special_row_color = self.settings.get_string("http-output-special-row-color")
        self.settings.connect("changed::http-output-header-key-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-header-value-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-special-row-color", self._on_color_setting_changed)
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
        self._update_user_agent_model()
        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._update_user_agent_model())
        if self.http_apply_button:
            self.http_apply_button.set_use_underline(True)
        if self.http_host_header_row:
            self._on_host_header_changed(self.http_host_header_row)
        if self.http_user_agent_row:
            self._on_user_agent_changed(self.http_user_agent_row, None)

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled) # type: ignore
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_host_header_row:
            self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row:
            self.http_user_agent_row.connect("notify::selected-item", self._on_user_agent_changed)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        """Handle changes in the Host header entry row.

        Adds or removes a CSS class to indicate if an override is active.

        :param entry_row: The :class:`Adw.EntryRow` for the Host header.
        :type entry_row: Adw.EntryRow
        """
        if not entry_row:
            return
        text = entry_row.get_text().strip() # type: ignore
        if text:
            entry_row.add_css_class("active-override")
        else:
            entry_row.remove_css_class("active-override")

    def _on_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        """Handle changes in the User-Agent combo row selection.

        Adds or removes a CSS class to indicate if a non-default User-Agent is active.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec, optional
        """
        if not combo_row:
            return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject): # type: ignore
            selected_title = selected_item_obj.get_string() # type: ignore
            effective_ua_value = self._ua_title_to_value_map.get(selected_title)
            if effective_ua_value: # i.e., not the "None" option which maps to None
                combo_row.add_css_class("active-override")
            else:
                combo_row.remove_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map:  # Model might be empty
            combo_row.remove_css_class("active-override")

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle the click event for the 'Copy Results' button.

        Constructs a string representation of the displayed headers and copies
        it to the clipboard.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        """
        logger.info("Copying all headers to clipboard.")
        lines = []
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
        """Handle activation of the URL entry row or click of the 'Fetch' button.

        Validates the URL, gathers request parameters, and starts the
        background task to fetch HTTP headers.

        :param _widget: The widget that triggered the activation (unused).
        :type _widget: Gtk.Widget
        """
        original_url = self.http_entry_row.get_text().strip() # type: ignore
        url = self._ensure_scheme(original_url)
        logger.info("Fetching headers for URL: %s (original input: %s)", url, original_url)

        if not is_valid_url(url):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url)
            toast_message = "Invalid URL format. Please enter a valid URL (e.g., https://example.com)."
            show_global_toast(self, toast_message) # type: ignore
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, "show_toast")):
                show_global_error(self, toast_message) # type: ignore
            self._update_column_view_model(None)
            return

        self._clear_error()
        self.http_entry_row.set_sensitive(False) # type: ignore
        if hasattr(self, "http_apply_button") and self.http_apply_button:
            self.http_apply_button.set_sensitive(False)
            self.http_apply_button.set_icon_name("process-working-symbolic")
        host_header = self.http_host_header_row.get_text().strip() # type: ignore
        user_agent_to_send: Optional[str] = None
        selected_title_obj = self.http_user_agent_row.get_selected_item() # type: ignore
        if isinstance(selected_title_obj, Gtk.StringObject): # type: ignore
            selected_title = selected_title_obj.get_string() # type: ignore
            user_agent_to_send = self._ua_title_to_value_map.get(selected_title)
        custom_dns_server = self.settings.get_string("custom-dns-server")
        # HttpPage no longer needs to check for `dns` module availability directly for HttpFetcher,
        # but it was checked above for the logger warning. This is fine.
        # Store parameters for the thread to access
        self._http_task_data_for_thread = {
            "url": url,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(), # type: ignore
            "host_header": host_header if host_header else None,
            "user_agent": user_agent_to_send,
            "custom_dns_server": custom_dns_server if custom_dns_server else None, # Retain for HttpFetcher
        }
        logger.debug(f"HttpPage: Starting header fetch task with data: {self._http_task_data_for_thread}")

        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None) # type: ignore
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func) # type: ignore

    # _prepare_request_headers moved to HttpFetcher
    # _execute_http_request moved to HttpFetcher
    # _process_http_response moved to HttpFetcher
    # _get_detailed_connection_error_message moved to HttpFetcher
    # _format_http_error moved to HttpFetcher

    def _fetch_headers_task_thread_func(
        self,
        task: Gio.Task, # The task itself, passed by run_in_thread
        _source_object: GObject.Object, # type: ignore
        _task_data_arg: dict, # type: ignore # Not used, self._http_task_data_for_thread is used
        cancellable: Optional[Gio.Cancellable], # Provided by Gio.Task
    ) -> None:
        """Background thread function for fetching HTTP headers.

        This function is executed by :meth:`Gio.Task.run_in_thread`.
        It instantiates :class:`.http_client.HttpFetcher` and calls its
        main fetching method. Results or exceptions are then reported back
        to the main thread via the :class:`Gio.Task`.

        :param task: The :class:`Gio.Task` associated with this operation.
        :type task: Gio.Task
        :param _source_object: The source object that initiated the task (unused).
        :type _source_object: GObject.Object
        :param _task_data_arg: Additional data passed to the task (unused).
        :type _task_data_arg: dict
        :param cancellable: A :class:`Gio.Cancellable` object to monitor for cancellation.
        :type cancellable: Gio.Cancellable, optional
        """
        current_task_data = self._http_task_data_for_thread
        url_to_fetch: str = current_task_data["url"]

        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal( # type: ignore
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.CANCELLED.value,
                "Task cancelled before fetching."
            )
            return

        # Instantiate the HttpFetcher with parameters from the UI and task
        fetcher = HttpFetcher(
            url=url_to_fetch,
            use_akamai_pragma=current_task_data["use_akamai_pragma"],
            host_header=current_task_data.get("host_header"),
            user_agent=current_task_data.get("user_agent"),
            custom_dns_server=current_task_data.get("custom_dns_server"),
            cancellable=cancellable  # Pass the cancellable to HttpFetcher
        )

        try:
            # Execute the fetch operation via HttpFetcher
            processed_data = fetcher.fetch_headers()
            if cancellable and cancellable.is_cancelled(): # Check again after fetch_headers returns
                task.return_new_error_literal( # type: ignore
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Task cancelled after fetching."
                )
            else:
                task.return_value(processed_data) # type: ignore
        except HttpRequestTimeoutError as e:
            logger.warning("HttpPage Task: Timeout for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal( # type: ignore
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.TIMEOUT.value, str(e)
            )
        except HttpConnectionError as e:
            logger.warning("HttpPage Task: ConnectionError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal( # type: ignore
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CONNECTION_ERROR.value, str(e)
            )
        except HttpProcessingError as e: # Raised by HttpFetcher for HTTP 4xx/5xx errors
            logger.warning("HttpPage Task: HttpProcessingError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal( # type: ignore
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.HTTP_ERROR.value, str(e)
            )
        except HttpGenericRequestError as e: # Other requests library errors
            logger.warning("HttpPage Task: HttpGenericRequestError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal( # type: ignore
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.REQUEST_EXCEPTION.value, str(e)
            )
        except HttpClientError as e: # Catches other client errors including cancellation from HttpFetcher
            logger.warning("HttpPage Task: HttpClientError for '%s': %s", url_to_fetch, e)
            if "cancelled" in str(e).lower(): # Check if it's a cancellation error from HttpFetcher
                 task.return_new_error_literal( # type: ignore
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, str(e)
                )
            else:
                task.return_new_error_literal( # type: ignore # General client error
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, str(e)
                )
        except Exception as e: # Catch any other unexpected errors from HttpFetcher or this layer
            logger.exception("HttpPage Task: Unexpected generic error for URL '%s':", url_to_fetch)
            task.return_new_error_literal( # type: ignore
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, f"Unexpected internal error: {e}"
            )

    def _fetch_headers_task_done_cb(
        self, _source_object: GObject.Object, result: Gio.AsyncResult, _user_data: object # type: ignore
    ) -> None:
        """Callback for when the :meth:`_fetch_headers_task_thread_func` completes.

        Processes the result (header data or an exception reported by `HttpFetcher`
        via `Gio.Task`) and updates the UI. Re-enables UI elements.

        :param _source_object: The source object that initiated the task (unused).
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: object
        """
        task_being_processed = self.current_http_task
        if task_being_processed is None:
            logger.warning("_fetch_headers_task_done_cb: current_http_task is None.")
            if hasattr(self, "http_entry_row") and self.http_entry_row and not self.http_entry_row.get_sensitive(): # type: ignore
                self.http_entry_row.set_sensitive(True) # type: ignore
            if (
                hasattr(self, "http_apply_button")
                and self.http_apply_button
                and not self.http_apply_button.get_sensitive()
            ):
                self.http_apply_button.set_sensitive(True)
                self.http_apply_button.set_icon_name("")
            return
        self.current_http_task = None
        logger.info("Processing task completion in _fetch_headers_task_done_cb.")
        try:
            propagate_result = task_being_processed.propagate_value()
            # actual_list_of_responses is now the direct result from HttpFetcher if successful
            actual_list_of_responses: Optional[List[Dict[str, Any]]] = None

            if isinstance(propagate_result, Gio.DBusCallFlags):
                 # This was a workaround, ideally HttpFetcher's result is directly a list or raises.
                 # If HttpFetcher returns list directly, this might not be needed.
                 # For now, keeping it if task.return_value() could still wrap.
                 logger.debug("HttpPage: propagate_result is Gio.DBusCallFlags, accessing .value")
                 actual_list_of_responses = propagate_result.value # type: ignore
            elif isinstance(propagate_result, list):
                 actual_list_of_responses = propagate_result
            else:
                logger.error(f"HttpPage: Unexpected type from propagate_value: {type(propagate_result)}")
                # This path implies an error that wasn't caught and converted to GLib.Error by the thread func
                # or HttpFetcher didn't return a list as expected upon success.
                show_global_error(self, "Unexpected result type from background task.") # type: ignore
                if hasattr(self, "http_entry_row") and self.http_entry_row:
                    self.http_entry_row.add_css_class("error") # type: ignore
                self._update_column_view_model(None)
                return # Early exit

            # This block is reached if propagate_result was successfully coerced into a list or was already a list.
            if actual_list_of_responses is not None: # It could be an empty list on success
                logger.info("HttpPage: Successfully processed task result: %d response stages.", len(actual_list_of_responses))
                processed_headers_for_store: list[HeaderItem] = []
                if not actual_list_of_responses: # Empty list is a valid success case (e.g. no data)
                    logger.info("HttpPage: Received empty list of responses.")
                    self._update_column_view_model(None) # Clear view or show "no data"
                else:
                    for i, response_data_dict_item in enumerate(actual_list_of_responses):
                        if not isinstance(response_data_dict_item, dict):
                            logger.error(
                                "HttpPage: Expected dict item in response list from HttpFetcher, got %s. Data: %s",
                                type(response_data_dict_item), response_data_dict_item,
                            )
                            continue
                        response_data_dict: dict[str, Any] = response_data_dict_item
                        url_display = f"URL: {response_data_dict.get('url', 'N/A')}"
                        status_code = response_data_dict.get('status_code', 'N/A')
                        response_type = response_data_dict.get('type', 'unknown')
                        status_display = f"Status: {status_code} ({str(response_type).capitalize()})"
                        processed_headers_for_store.append(
                            HeaderItem(key=url_display, value=status_display, is_special_row=True)
                        )
                        headers_for_this_response = response_data_dict.get("headers", {})
                        if isinstance(headers_for_this_response, dict):
                            for key, value in headers_for_this_response.items():
                                processed_headers_for_store.append(
                                    HeaderItem(key=str(key), value=str(value), is_special_row=False)
                                )
                        else:
                            logger.warning(
                                "HttpPage: Headers data for response stage %d is not a dict: %s", i, headers_for_this_response
                            )
                        if i < len(actual_list_of_responses) - 1:
                            processed_headers_for_store.append(
                                HeaderItem(key="--- Redirected To ---", value="", is_special_row=True)
                            )
                    self._current_header_items = processed_headers_for_store
                    self._update_column_view_model(processed_headers_for_store)
                if hasattr(self, "http_entry_row") and self.http_entry_row:
                    self.http_entry_row.remove_css_class("error") # type: ignore
            # No 'else' here for actual_list_of_responses is None, as it's handled by the type check above.
        except GLib.Error as e:  # Errors set by task.return_new_error_literal in _fetch_headers_task_thread_func
            logger.warning(
                "HttpPage: Task failed with GLib.Error (Domain: %s, Code: %d, Message: %s)",
                 e.domain, e.code, e.message
            )
            display_message = e.message if e.message else "An unknown error occurred."
            # Basic sanitization, could be more robust if needed
            if "<b>" in display_message or "<" in display_message:
                display_message = GLib.markup_escape_text(display_message)

            show_global_error(self, display_message) # type: ignore
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.add_css_class("error") # type: ignore
                self._update_column_view_model(None)
        except Exception as e:  # Catch any other Python exceptions from this callback itself
            logger.exception("HttpPage: Unexpected Python error in _fetch_headers_task_done_cb:")
            error_message = f"An unexpected application error occurred: {e}"
            show_global_error(self, error_message) # type: ignore
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.add_css_class("error") # type: ignore
            self._update_column_view_model(None)
        finally:
            if hasattr(self, "http_entry_row") and self.http_entry_row:
                self.http_entry_row.set_sensitive(True) # type: ignore
            if hasattr(self, "http_apply_button") and self.http_apply_button:
                self.http_apply_button.set_sensitive(True)
                self.http_apply_button.set_icon_name("")  # Remove spinner

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        """Ensure the URL has a scheme, defaulting to 'https://'.

        This provides a basic check before passing to `HttpFetcher`, which
        will perform more robust URL parsing.

        :param url: The input URL string.
        :type url: str
        :return: The URL string, with 'https://' prepended if no scheme was present.
        :rtype: str
        """
        if "://" not in url: # Basic check
            logger.debug("HttpPage: URL '%s' has no scheme, prepending 'https://'.", url)
            return "https://" + url
        return url

    # _format_http_error was moved to HttpFetcher and its functionality is now part of HttpFetcher's exceptions.

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        """Handle toggling of the Akamai Pragma switch.

        If a URL is present in the entry row, it re-triggers the fetch.

        :param _widget: The :class:`Gtk.Switch` that was toggled (unused).
        :type _widget: Gtk.Switch
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        logger.debug("Akamai Pragma toggled: %s. Re-fetching if URL present.", self.http_pragma_switch_row.get_active()) # type: ignore
        if self.http_entry_row.get_text().strip(): # type: ignore
            self._on_entry_row_activated(self.http_entry_row) # type: ignore

    def _update_column_view_model(self, header_items: Optional[List["HeaderItem"]]) -> None:
        """Update the :class:`Gio.ListStore` for the header :class:`Gtk.ColumnView`.

        Clears the existing items and appends new ones if provided.
        Shows or hides the results group accordingly.

        :param header_items: A list of :class:`HeaderItem` objects to display,
                             or ``None`` to clear the view.
        :type header_items: list[:class:`HeaderItem`], optional
        """
        self.header_list_store.remove_all()
        if header_items:
            for item in header_items:
                self.header_list_store.append(item)
            self._show_results()
        else:
            self._hide_results()

    def _show_results(self):
        """Make the HTTP results group visible."""
        if self.http_results_group:
            self.http_results_group.set_visible(True)

    def _hide_results(self):
        """Make the HTTP results group invisible."""
        if self.http_results_group:
            self.http_results_group.set_visible(False)

    def _clear_error(self) -> None:
        """Clear any error state in the UI.

        Hides the main window's error banner and removes the 'error' CSS class
        from the URL entry row.
        """
        main_window = self.get_native() # type: ignore
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error() # type: ignore
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error") # type: ignore

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """Handle the click event for the 'Clear Results' button.

        Clears the displayed headers, error state, and the URL entry.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        """
        logger.info("Results cleared by user action.")
        self._current_header_items = []
        self._update_column_view_model(None)
        self._clear_error()
        if self.http_entry_row:
            self.http_entry_row.set_text("") # type: ignore

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        """Handle changes to color-related GSettings.

        Updates the internal color attributes and re-populates the column view
        to apply the new colors if results are currently displayed.

        :param settings: The :class:`Gio.Settings` object that changed.
        :type settings: Gio.Settings
        :param key: The GSettings key that changed.
        :type key: str
        """
        logger.debug("Color setting changed for GSettings key: %s", key)
        if key == "http-output-header-key-color":
            self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color":
            self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color":
            self._special_row_color = settings.get_string(key)
        if self._current_header_items:  # Re-populate to apply new colors
            logger.debug("Re-populating ColumnView to apply new color changes.")
            self._update_column_view_model(self._current_header_items)

    def _update_user_agent_model(self) -> None:
        """Update the model for the User-Agent :class:`Adw.ComboRow`.

        Populates the dropdown with custom User-Agents from GSettings,
        a "None" option, and default User-Agents from constants.
        Attempts to preserve the current selection.
        """
        if not self.http_user_agent_row:
            logger.error("HttpPage._update_user_agent_model: http_user_agent_row is None.")
            return
        self._ua_title_to_value_map.clear()
        current_selection_text: Optional[str] = None
        if (
            self.http_user_agent_row.get_model() # type: ignore
            and self.http_user_agent_row.get_selected() != Gtk.INVALID_LIST_POSITION # type: ignore
        ):
            selected_item = self.http_user_agent_row.get_selected_item() # type: ignore
            if isinstance(selected_item, Gtk.StringObject): # type: ignore
                current_selection_text = selected_item.get_string() # type: ignore

        display_titles: list[str] = []
        # Custom UAs from GSettings
        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else [] # type: ignore
        )
        for title, value in custom_ua_pairs:
            if title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        # "None" option
        none_title = "None" # This is the display title for "no override"
        if none_title not in self._ua_title_to_value_map:
            display_titles.append(none_title)
        self._ua_title_to_value_map[none_title] = None  # Actual value is None

        # Default UAs from constants
        for ua_dict in USER_AGENTS:
            title, value = ua_dict.get("title"), ua_dict.get("value")
            if title and value and title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles)) # type: ignore

        # Restore selection
        if current_selection_text and current_selection_text in display_titles:
            try:
                idx = display_titles.index(current_selection_text)
                self.http_user_agent_row.set_selected(idx) # type: ignore
            except ValueError:
                logger.warning(
                    "Error restoring User-Agent selection: '%s' not found in new list.", current_selection_text
                )
                if display_titles: # Default to first if old selection is gone
                    self.http_user_agent_row.set_selected(0) # type: ignore
        elif display_titles:  # Default to first item if no prior or unmatchable selection
            self.http_user_agent_row.set_selected(0) # type: ignore
        else: # Model is empty
             self.http_user_agent_row.set_selected(Gtk.INVALID_LIST_POSITION) # type: ignore


        self._on_user_agent_changed(self.http_user_agent_row, None)  # Update visual cue
        logging.info("User-Agent dropdown model updated with %d titles.", len(display_titles))

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """Creates a :class:`Gtk.SignalListItemFactory` for :class:`Gtk.ColumnView` columns.

        This factory is responsible for setting up and binding :class:`Gtk.Label`
        widgets within the column view cells to display :class:`HeaderItem` data.
        It handles text wrapping and applies custom colors based on GSettings.

        The internal ``setup_func`` creates the label, and ``bind_func_internal``
        populates the label with data from the :class:`HeaderItem`, applying
        markup for colors and bolding.

        :param attr_name: The attribute name of the :class:`HeaderItem` to display
                          (e.g., 'key' or 'value').
        :type attr_name: str
        :param wrap_text: Whether the text in the label should wrap.
                          Defaults to False.
        :type wrap_text: bool, optional
        :return: A configured :class:`Gtk.SignalListItemFactory`.
        :rtype: Gtk.SignalListItemFactory
        """
        factory = Gtk.SignalListItemFactory()

        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0.0, hexpand=True)
            if wrap_text:
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(80)  # Approximate for typical widths
            list_item.set_child(label)

        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            item = list_item.get_item()
            if not (isinstance(label, Gtk.Label) and isinstance(item, HeaderItem)):
                if isinstance(label, Gtk.Label):
                    label.set_text("Error: Invalid item type.")
                return

            text_to_display = getattr(item, attr_name, "")

            if item.is_special_row:
                if attr_name == "key":  # For special rows, 'key' might hold combined info or just the primary part
                    key_text = GLib.markup_escape_text(str(item.key))
                    value_text = GLib.markup_escape_text(str(item.value)) if item.value else ""
                    full_text = key_text
                    if value_text.strip() and value_text != "N/A":
                        full_text += f" {value_text}"
                    label.set_markup(f"<b><span foreground='{self._special_row_color}'>{full_text}</span></b>")
                else:  # 'value' column for special rows is typically empty
                    label.set_markup("")
            else:  # Standard header rows
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(str(text_to_display))
                label.set_markup(f"<b><span foreground='{color_to_use}'>{escaped_text}</span></b>") # type: ignore

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory

    def trigger_fetch(self) -> None:
        """Programmatically triggers the 'Fetch' action.

        This method is typically called in response to a keyboard shortcut
        or an external event. It simulates a click on the 'Fetch' button
        if the button is available and sensitive.
        """
        logging.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.clicked()
        else:
            logging.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")
