"""Manages the HTTP Request and Response inspection page for the Woes application.

This module defines the :class:`HttpPage` class, an :class:`Adw.PreferencesPage`
subclass that provides the user interface for constructing and sending HTTP/HTTPS
requests. Users can specify the URL, HTTP method (implicitly GET for now),
custom headers (Host, User-Agent), and toggle Akamai Pragma headers.
It utilizes the :class:`~.http_client.HttpClient` for executing requests
and displays the full response chain (redirects and final response), including
status codes and headers, in a :class:`Gtk.ColumnView`. Custom DNS resolution
is supported via the underlying HTTP client.
"""

# pylint: disable=too-many-lines
# ruff: noqa: E501
import logging
from enum import Enum
from typing import Any, Dict, List, Optional

import gi
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk, Pango

from .constants import RESOURCE_PREFIX, USER_AGENTS, APP_ID
from .utils import show_global_error, show_global_toast, is_valid_url
from .helper import Helper
from .http_client import ( # Assuming HttpFetcher was renamed to HttpClient
    HttpClient,
    HttpClientError,
    HttpRequestTimeoutError,
    HttpConnectionError,
    HttpProcessingError,
    HttpGenericRequestError,
)


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


logger = logging.getLogger(__name__)

WOES_HTTP_ERROR_DOMAIN = "woes-http-error-domain"  # Used for Gio.Task errors


class HttpErrorType(int, Enum):
    """Enumeration of HTTP error types for :class:`Gio.Task` error reporting."""

    TIMEOUT = 0
    HTTP_ERROR = 1
    CONNECTION_ERROR = 2
    REQUEST_EXCEPTION = 3
    GENERIC_UNEXPECTED = 4
    CANCELLED = 5


class HeaderItem(GObject.Object):
    """GObject representing a single header key-value pair or a special informational
    row for the :class:`Gtk.ColumnView` display.

    :ivar key: The header key or title for a special row (e.g., "URL: ...").
    :vartype key: str
    :ivar value: The header value or descriptive text for a special row (e.g., "Status: 200 OK").
    :vartype value: str
    :ivar is_special_row: ``True`` if this item represents a special informational
                          row (like URL/status or separator) rather than a standard
                          HTTP header key-value pair. Defaults to ``False``.
    :vartype is_special_row: bool
    """

    key: str
    value: str
    is_special_row: bool

    def __init__(self, key: str, value: str, is_special_row: bool = False):
        """Initializes a HeaderItem.

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
class HttpPage(Adw.PreferencesPage):
    """UI component for making HTTP requests and inspecting responses.

    This class provides the user interface for the HTTP inspection tool.
    It allows users to:
    - Input a target URL.
    - Specify custom 'Host' and 'User-Agent' headers.
    - Toggle Akamai Pragma headers for debugging.
    - Initiate HTTP GET requests (via :class:`~.http_client.HttpClient`).
    - View the full response chain, including redirects, status codes, and headers
      for each step, displayed in a :class:`Gtk.ColumnView`.
    - Manage UI states (loading indicators, error messages, results display).
    - Clear results or copy them to the clipboard.

    It leverages :class:`Gio.Task` for asynchronous network operations and
    interacts with GSettings for configurations like custom DNS (used by
    the HttpClient) and UI color schemes.
    """

    __gtype_name__ = "HttpPage"

    # --- Template Children ---
    http_entry_row: Adw.EntryRow = Gtk.Template.Child() # type: ignore
    http_apply_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    http_host_header_row: Adw.EntryRow = Gtk.Template.Child() # type: ignore
    http_user_agent_row: Adw.ComboRow = Gtk.Template.Child() # type: ignore
    http_pragma_switch_row: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    http_column_view: Gtk.ColumnView = Gtk.Template.Child() # type: ignore
    http_results_group: Adw.PreferencesGroup = Gtk.Template.Child() # type: ignore
    clear_results_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    copy_results_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    http_status_row: Adw.ActionRow = Gtk.Template.Child() # type: ignore
    http_status_spinner: Gtk.Spinner = Gtk.Template.Child() # type: ignore

    def __init__(self, **kwargs: Any):
        """Initializes the HttpPage.

        Sets up UI elements from the Gtk.Template, connects signal handlers
        for user interactions, initializes GSettings for style preferences,
        and configures the :class:`Gtk.ColumnView` for displaying headers.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage`
                       constructor.
        :type kwargs: Any
        """
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task: Optional[Gio.Task] = None
        self._current_header_items: List[HeaderItem] = [] # Cache for repopulating on style change
        self._http_task_data_for_thread: Dict[str, Any] = {} # Data passed to background task
        self._ua_title_to_value_map: Dict[str, Optional[str]] = {} # For User-Agent dropdown

        self.settings = Gio.Settings.new(APP_ID)
        # Load initial color preferences
        self._header_key_color = self.settings.get_string("http-output-header-key-color")
        self._header_value_color = self.settings.get_string("http-output-header-value-color")
        self._special_row_color = self.settings.get_string("http-output-special-row-color")

        # Connect to GSettings changes for live color updates
        self.settings.connect("changed::http-output-header-key-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-header-value-color", self._on_color_setting_changed)
        self.settings.connect("changed::http-output-special-row-color", self._on_color_setting_changed)

        # Setup ColumnView model and factories
        self.header_list_store = Gio.ListStore.new(HeaderItem)
        selection_model = Gtk.MultiSelection.new(self.header_list_store)
        self.http_column_view.set_model(selection_model)

        if not self.http_column_view.get_columns(): # Ensure columns are added only once
            header_name_factory = self._create_factory("key")
            header_value_factory = self._create_factory("value", wrap_text=True)
            col_name = Gtk.ColumnViewColumn.new("Header", header_name_factory)
            col_value = Gtk.ColumnViewColumn.new("Value", header_value_factory)
            col_value.set_expand(True) # Allow value column to take available space
            self.http_column_view.append_column(col_name)
            self.http_column_view.append_column(col_value)

        self._connect_signals()
        self._update_user_agent_model() # Populate User-Agent dropdown
        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._update_user_agent_model())

        # Initialize UI states for override indicators
        if self.http_host_header_row:
            self._on_host_header_changed(self.http_host_header_row)
        if self.http_user_agent_row:
            self._on_user_agent_changed(self.http_user_agent_row, None)

        # Attach context menu and copy support to the ColumnView
        self.column_view_helper = Helper(
            widget=self.http_column_view,
            parent_window=self.get_native()) # type: ignore

    def _connect_signals(self) -> None:
        """Connects Gtk signals for various UI elements to their handlers.

        This includes signals for button clicks, entry activation, and
        changes in dropdowns or switches.

        :return: None
        :rtype: None
        """
        self.http_entry_row.connect("activate", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_host_header_row:
            self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row:
            self.http_user_agent_row.connect("notify::selected-item", self._on_user_agent_changed)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        """Handles changes in the Host header entry row.

        Adds or removes a CSS class ("active-override") to the row to visually
        indicate if a custom Host header is currently active.

        :param entry_row: The :class:`Adw.EntryRow` for the Host header.
        :type entry_row: Adw.EntryRow
        :return: None
        :rtype: None
        """
        if not entry_row:
            return
        text = entry_row.get_text().strip()
        if text:
            entry_row.add_css_class("active-override")
        else:
            entry_row.remove_css_class("active-override")

    def _on_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        """Handles changes in the User-Agent combo row selection.

        Adds or removes a CSS class ("active-override") to the row to visually
        indicate if a non-default (i.e., not "None") User-Agent is selected.

        :param combo_row: The :class:`Adw.ComboRow` for User-Agent selection.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec, optional
        :return: None
        :rtype: None
        """
        if not combo_row:
            return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            # Check if the selected User-Agent is effectively active (not the "None" option)
            effective_ua_value = self._ua_title_to_value_map.get(selected_title)
            if effective_ua_value:  # "None" option maps to None value
                combo_row.add_css_class("active-override")
            else:
                combo_row.remove_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map: # Model might be empty
            combo_row.remove_css_class("active-override")


    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Copy Results' button.

        Constructs a string representation of all displayed headers and special rows
        (URL, status lines, redirect separators) and copies it to the clipboard.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
        :rtype: None
        """
        logger.info("Copying all headers to clipboard.")
        lines: List[str] = []
        if self.header_list_store:
            for i in range(self.header_list_store.get_n_items()):
                item = self.header_list_store.get_item(i)
                if isinstance(item, HeaderItem):
                    if item.is_special_row:
                        # For special rows, combine key and value if value is meaningful
                        if item.value and item.value.strip() and item.value != "N/A":
                            lines.append(f"{item.key} {item.value}")
                        else:
                            lines.append(item.key) # e.g., "--- Redirected To ---"
                    else: # Standard header
                        lines.append(f"{item.key}: {item.value}")
        if lines:
            text_to_copy = "\n".join(lines)
            DNSPage._copy_to_clipboard_static(text_to_copy, self) # Use staticmethod
            show_global_toast(self, "All results copied to clipboard.")
        else:
            show_global_toast(self, "No results to copy.")
            logger.info("No results available in the view to copy.")

    @staticmethod
    def _copy_to_clipboard_static(text: str, widget_for_context: Gtk.Widget) -> None:
        """Static method to copy the given text to the clipboard.

        Factored out for potential reuse if other parts of the UI need clipboard access
        without direct access to an HttpPage instance method requiring `self`.

        :param text: The text to be copied.
        :type text: str
        :param widget_for_context: A :class:`Gtk.Widget` instance used to access the clipboard.
        :type widget_for_context: Gtk.Widget
        :return: None
        :rtype: None
        """
        try:
            clipboard = widget_for_context.get_clipboard()
            if clipboard:
                clipboard.set(text)
                logger.info("Text (first 100 chars: '%s...') copied to clipboard.", text[:100])
            else:
                logger.warning("Could not get clipboard from widget: %s", widget_for_context)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error copying text to clipboard:")


    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        """Handles activation of the URL entry row (e.g., Enter key press) or
        click of the 'Fetch Headers' button.

        Validates the URL, gathers all request parameters from the UI,
        and initiates the background task to fetch HTTP headers and responses.

        :param _widget: The :class:`Gtk.Widget` that triggered the activation (unused).
        :type _widget: Gtk.Widget
        :return: None
        :rtype: None
        """
        original_url = self.http_entry_row.get_text().strip()
        # Ensure URL has a scheme, defaulting to https if missing.
        url_to_fetch = self._ensure_scheme(original_url)
        logger.info(
            "Fetching headers for URL: %s (original input: %s)",
            url_to_fetch, original_url)

        if not is_valid_url(url_to_fetch): # Validate after ensuring scheme
            logger.warning("Invalid URL provided: %s (processed as: %s)",
                           original_url, url_to_fetch)
            toast_message = ("Invalid URL format. Please enter a valid URL "
                             "(e.g., https://example.com).")
            show_global_toast(self, toast_message)
            # Fallback error display if toast is not available
            if not (self.get_native() and hasattr(self.get_native(), 'show_toast')):
                show_global_error(self, toast_message)
            self._update_column_view_model(None) # Clear previous results
            self._set_loading_state(False, "Idle - Invalid URL.")
            return

        self._clear_error() # Clear any previous error messages
        self._set_loading_state(True, "Fetching headers...") # Update UI to loading state

        # Gather all parameters for the HTTP request
        host_header = self.http_host_header_row.get_text().strip()
        user_agent_to_send: Optional[str] = None
        selected_title_obj = self.http_user_agent_row.get_selected_item()
        if isinstance(selected_title_obj, Gtk.StringObject):
            selected_title = selected_title_obj.get_string()
            user_agent_to_send = self._ua_title_to_value_map.get(selected_title)

        custom_dns_server = self.settings.get_string("custom-dns-server")

        # Store parameters for the background thread to access
        self._http_task_data_for_thread = {
            "url": url_to_fetch,
            "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header if host_header else None,
            "user_agent": user_agent_to_send, # Will be None if "None" option selected
            "custom_dns_server": custom_dns_server if custom_dns_server else None,
        }
        logger.debug("HttpPage: Starting header fetch task with data: %s",
                     self._http_task_data_for_thread)

        # Create and run the asynchronous task
        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None)
        self.current_http_task = task # Keep a reference to manage cancellation
        task.run_in_thread(self._fetch_headers_task_thread_func)

    def _fetch_headers_task_thread_func(
        self,
        task: Gio.Task,
        _source_object: GObject.Object,
        _task_data_arg: Dict[str, Any], # Unused as params are on self
        cancellable: Optional[Gio.Cancellable],
    ) -> None:
        """Background thread function for fetching HTTP headers and responses.

        This function is executed by :meth:`Gio.Task.run_in_thread`.
        It retrieves request parameters stored in `self._http_task_data_for_thread`,
        instantiates :class:`~.http_client.HttpClient`, and calls its `execute`
        method. Results or exceptions are then reported back to the main thread
        via the :class:`Gio.Task`.

        :param task: The :class:`Gio.Task` associated with this operation.
        :type task: Gio.Task
        :param _source_object: The source object that initiated the task (unused).
        :type _source_object: GObject.Object
        :param _task_data_arg: Additional data passed to the task by Gio (unused here).
        :type _task_data_arg: dict
        :param cancellable: A :class:`Gio.Cancellable` object to monitor for cancellation.
        :type cancellable: Gio.Cancellable, optional
        :return: None
        :rtype: None
        """
        # Retrieve parameters from the instance variable set before starting the thread
        current_task_data = self._http_task_data_for_thread
        url_to_fetch: str = current_task_data["url"] # Should always be present

        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.CANCELLED.value,
                "Task cancelled before fetching."
            )
            return

        # Instantiate HttpClient with parameters from the UI and task
        client = HttpClient( # Assuming HttpFetcher was renamed to HttpClient
            url=url_to_fetch,
            use_akamai_pragma=current_task_data["use_akamai_pragma"],
            host_header=current_task_data.get("host_header"),
            user_agent=current_task_data.get("user_agent"),
            custom_dns_server=current_task_data.get("custom_dns_server"),
            cancellable=cancellable # Pass the cancellable to HttpClient
        )

        try:
            processed_data = client.execute() # Renamed from fetch_headers
            # Check for cancellation again after the blocking call returns
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.CANCELLED.value,
                    "Task cancelled after fetching data."
                )
            else:
                task.return_value(processed_data)
        except HttpRequestTimeoutError as e:
            logger.warning("HttpPage Task: Timeout for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.TIMEOUT.value, str(e)
            )
        except HttpConnectionError as e:
            logger.warning("HttpPage Task: ConnectionError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.CONNECTION_ERROR.value, str(e)
            )
        except HttpProcessingError as e:
            logger.warning("HttpPage Task: HttpProcessingError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.HTTP_ERROR.value, str(e) # e already contains rich info
            )
        except HttpGenericRequestError as e:
            logger.warning("HttpPage Task: HttpGenericRequestError for '%s': %s", url_to_fetch, e)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.REQUEST_EXCEPTION.value, str(e)
            )
        except HttpClientError as e: # Catches other client-side errors including explicit cancellation
            logger.warning("HttpPage Task: HttpClientError for '%s': %s", url_to_fetch, e)
            if "cancelled" in str(e).lower(): # Check if it's a cancellation propagated from HttpClient
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.CANCELLED.value, str(e))
            else:
                task.return_new_error_literal(
                    GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                    HttpErrorType.GENERIC_UNEXPECTED.value, str(e))
        except Exception as e:  # Catch any other unexpected errors
            logger.exception("HttpPage Task: Unexpected generic error for URL '%s':", url_to_fetch)
            task.return_new_error_literal(
                GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN),
                HttpErrorType.GENERIC_UNEXPECTED.value,
                f"Unexpected internal error: {e}")
        finally:
            if hasattr(client, 'close_session'): # Ensure client has close_session if it's used
                client.close_session()


    def _fetch_headers_task_done_cb(
        self, _source_object: GObject.Object, result: Gio.AsyncResult,
        _user_data: object # type: ignore
    ) -> None:
        """Callback for when the `_fetch_headers_task_thread_func` completes.

        Processes the result from the :class:`Gio.Task` (which contains either the
        list of response data dictionaries or an exception) and updates the UI
        accordingly. Re-enables UI elements after the operation.

        :param _source_object: The source object that initiated the task (unused).
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: object
        :return: None
        :rtype: None
        """
        # Ensure we are handling the correct task if multiple are somehow launched
        # (though current_http_task should prevent this by design).
        task_being_processed = self.current_http_task
        if task_being_processed is None:
            logger.warning("_fetch_headers_task_done_cb: current_http_task is None. "
                           "This might indicate a race condition or premature clearing.")
            # Attempt to reset UI to a safe state if possible.
            self._set_loading_state(False, "Idle - Operation ended unexpectedly.")
            return
        self.current_http_task = None # Clear the reference to the completed task

        logger.info("Processing task completion in _fetch_headers_task_done_cb.")
        try:
            # propagate_value() will return the data from task.return_value()
            # or raise the GLib.Error from task.return_new_error_literal().
            returned_data = task_being_processed.propagate_value()
            actual_list_of_responses: Optional[List[Dict[str, Any]]] = None

            if isinstance(returned_data, list):
                actual_list_of_responses = returned_data
            elif hasattr(returned_data, 'value') and isinstance(returned_data.value, list): # type: ignore
                # Handle cases where Gio might wrap the result (e.g., Gio.DBusCallFlags)
                logger.debug("HttpPage: propagate_result is a wrapped object, accessing .value")
                actual_list_of_responses = returned_data.value # type: ignore
            else:
                logger.error("HttpPage: Unexpected data type from propagate_value: %s", type(returned_data))
                show_global_error(self, "Unexpected result type from background task.")
                self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None) # Clear results view
                self._set_loading_state(False, "Error: Unexpected data format.")
                return

            # Ensure the extracted data is a list of dictionaries as expected
            if actual_list_of_responses is not None: # Can be an empty list for success with no data
                logger.info("HttpPage: Successfully processed task result: %d response stages.",
                            len(actual_list_of_responses))
                processed_headers_for_store: List[HeaderItem] = []
                if not actual_list_of_responses: # Success, but no actual responses/redirects
                    logger.info("HttpPage: Received empty list of responses (e.g. HTTP 204 No Content).")
                    self._update_column_view_model(None) # Or show "No Content" message
                else:
                    for i, response_data_dict_item in enumerate(actual_list_of_responses):
                        if not isinstance(response_data_dict_item, dict):
                            logger.error("HttpPage: Expected dict item in response list, got %s. Data: %s",
                                         type(response_data_dict_item), response_data_dict_item)
                            continue # Skip malformed items

                        response_data_dict: Dict[str, Any] = response_data_dict_item
                        url_display = f"URL: {response_data_dict.get('url', 'N/A')}"
                        status_code = response_data_dict.get('status_code', 'N/A')
                        response_type = response_data_dict.get('type', 'unknown').replace("_", " ").title()
                        status_display = f"Status: {status_code} ({response_type})"

                        processed_headers_for_store.append(
                            HeaderItem(key=url_display, value=status_display, is_special_row=True))

                        headers_for_this_response = response_data_dict.get("headers", {})
                        if isinstance(headers_for_this_response, dict):
                            for key, value in headers_for_this_response.items():
                                processed_headers_for_store.append(
                                    HeaderItem(key=str(key), value=str(value), is_special_row=False))
                        else:
                            logger.warning("HttpPage: Headers data for response stage %d is not a dict: %s",
                                           i, headers_for_this_response)

                        # Add separator if this is not the last response in a redirect chain
                        if i < len(actual_list_of_responses) - 1:
                            processed_headers_for_store.append(
                                HeaderItem(key="--- Redirected To Next URL ---", value="", is_special_row=True))

                    self._current_header_items = processed_headers_for_store
                    self._update_column_view_model(processed_headers_for_store)

                self.http_entry_row.remove_css_class("error")
                self._set_loading_state(False, "Headers loaded successfully.")
            else: # actual_list_of_responses was None after attempted extraction
                logger.error("HttpPage: Failed to obtain a valid list of responses. Value was None.")
                show_global_error(self, "Failed to process data from background task.")
                self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
                self._set_loading_state(False, "Error: Failed to process data.")

        except GLib.Error as e: # Errors set by task.return_new_error_literal()
            logger.warning("HttpPage: Task failed with GLib.Error (Domain: %s, Code: %d, Message: %s)",
                           e.domain, e.code, e.message)
            display_message = e.message if e.message else "An unknown error occurred."
            # Basic sanitization for display, as messages might contain technical details
            if "<" in display_message or ">" in display_message: # Simple check for markup-like chars
                display_message = GLib.markup_escape_text(display_message)

            show_global_error(self, display_message)
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None) # Clear results on error
            self._set_loading_state(False, f"Error: {display_message.splitlines()[0]}")
        except Exception as e: # Catch any other Python exceptions from this callback itself
            logger.exception("HttpPage: Unexpected Python error in _fetch_headers_task_done_cb:")
            error_message = f"An unexpected application error occurred: {type(e).__name__}"
            show_global_error(self, error_message + " Check logs for details.")
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
            self._set_loading_state(False, f"Error: {error_message.splitlines()[0]}")
        finally:
            # Ensure UI is reset to an interactive state if not already handled.
            # _set_loading_state(False, ...) in try/except blocks should cover most cases.
            # This final call ensures it if an unexpected path was taken.
            if self.http_status_spinner.get_spinning(): # type: ignore
                 self._set_loading_state(False, "Idle - operation concluded.")


    def _set_loading_state(self, active: bool, message: str = "Idle") -> None:
        """Sets the UI loading state (spinner, status message, sensitivity of input fields).

        :param active: If ``True``, activates the loading state (shows spinner,
                       sets message, disables inputs). If ``False``, deactivates
                       loading state (hides spinner, sets message, enables inputs).
        :type active: bool
        :param message: The status message to display. Defaults to "Idle" when
                        deactivating and no specific message is provided.
        :type message: str
        :return: None
        :rtype: None
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
        """Ensures the URL has a scheme, defaulting to 'https://' if missing.

        :param url: The input URL string.
        :type url: str
        :return: The URL string, with 'https://' prepended if no scheme was present.
        :rtype: str
        """
        if "://" not in url:
            logger.debug(
                "HttpPage: URL '%s' has no scheme, prepending 'https://'.", url)
            return "https://" + url
        return url

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        """Handles toggling of the Akamai Pragma switch.

        If a URL is present in the entry row, it re-triggers the HTTP fetch operation
        to reflect the new Pragma header state.

        :param _widget: The :class:`Gtk.Switch` that was toggled (unused).
        :type _widget: Gtk.Switch
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        :return: None
        :rtype: None
        """
        logger.debug("Akamai Pragma toggled: %s. Re-fetching if URL present.",
                     self.http_pragma_switch_row.get_active())
        # Re-fetch only if there's a URL to avoid errors on empty input
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[List["HeaderItem"]]) -> None:
        """Updates the :class:`Gio.ListStore` for the header :class:`Gtk.ColumnView`.

        Clears any existing items and appends the new :class:`HeaderItem` objects
        if provided. Manages the visibility of the results group based on whether
        there are items to display.

        :param header_items: A list of :class:`HeaderItem` objects to display,
                             or ``None`` to clear the view and hide results.
        :type header_items: Optional[List[HeaderItem]]
        :return: None
        :rtype: None
        """
        self.header_list_store.remove_all()
        if header_items:
            for item in header_items:
                self.header_list_store.append(item)
            self._show_results()
        else: # No items or None passed
            self._hide_results()

    def _show_results(self) -> None:
        """Makes the HTTP results group (:class:`Adw.PreferencesGroup`) visible.

        :return: None
        :rtype: None
        """
        if self.http_results_group:
            self.http_results_group.set_visible(True)

    def _hide_results(self) -> None:
        """Makes the HTTP results group (:class:`Adw.PreferencesGroup`) invisible.

        :return: None
        :rtype: None
        """
        if self.http_results_group:
            self.http_results_group.set_visible(False)

    def _clear_error(self) -> None:
        """Clears any error state in the UI.

        Hides the main window's error banner (if available and the method exists)
        and removes the 'error' CSS class from the URL entry row.

        :return: None
        :rtype: None
        """
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error() # type: ignore
        # else:
            # This can be common if the page is not yet fully part of a window hierarchy
            # logger.debug("Could not find main_window or hide_error method to clear error.")
        if self.http_entry_row:
            self.http_entry_row.remove_css_class("error")

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Clear Results' button.

        Clears the displayed headers from the column view, hides the results group,
        clears any error messages, and resets the URL entry field.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
        :rtype: None
        """
        logger.info("Results cleared by user action.")
        self._current_header_items = [] # Clear cached items
        self._update_column_view_model(None) # Clears store and hides results
        self._clear_error()
        if self.http_entry_row:
            self.http_entry_row.set_text("")
        # Optionally reset status message if not already idle
        if self.http_status_row and self.http_status_row.get_subtitle() != "Idle":
            self._set_loading_state(False, "Idle - Results cleared.")


    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        """Handles changes to color-related GSettings (e.g., header key/value colors).

        Updates the internal color attributes used for rendering header items and
        triggers a re-population of the :class:`Gtk.ColumnView` if results are
        currently displayed, to apply the new colors.

        :param settings: The :class:`Gio.Settings` object that changed.
        :type settings: Gio.Settings
        :param key: The GSettings key that changed (e.g., "http-output-header-key-color").
        :type key: str
        :return: None
        :rtype: None
        """
        logger.debug("Color setting changed for GSettings key: %s", key)
        if key == "http-output-header-key-color":
            self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color":
            self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color":
            self._special_row_color = settings.get_string(key)

        # If results are currently displayed, re-populate the ColumnView to apply new colors
        if self._current_header_items and self.http_results_group.get_visible():
            logger.debug("Re-populating ColumnView to apply new color changes.")
            self._update_column_view_model(self._current_header_items)

    def _update_user_agent_model(self) -> None:
        """Updates the model for the User-Agent :class:`Adw.ComboRow`.

        Populates the dropdown with a "None" option (to use default),
        custom User-Agents from GSettings, and predefined User-Agents from
        :const:`~.constants.USER_AGENTS`. It attempts to preserve the
        current selection across updates.

        :return: None
        :rtype: None
        """
        if not self.http_user_agent_row:
            logger.error("HttpPage._update_user_agent_model: http_user_agent_row is None.")
            return

        self._ua_title_to_value_map.clear()
        current_selection_text: Optional[str] = None
        # Preserve current selection if model and selection exist
        if (self.http_user_agent_row.get_model() and
                self.http_user_agent_row.get_selected() != Gtk.INVALID_LIST_POSITION):
            selected_item = self.http_user_agent_row.get_selected_item()
            if isinstance(selected_item, Gtk.StringObject):
                current_selection_text = selected_item.get_string()

        display_titles: List[str] = []

        # 1. "None" option (represents no override / use requests default)
        none_title = "None (Default)" # Clarified display title
        display_titles.append(none_title)
        self._ua_title_to_value_map[none_title] = None # Actual value for "None" selection

        # 2. Custom User-Agents from GSettings
        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: List[Tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []) # type: ignore
        for title, value in custom_ua_pairs:
            if title not in self._ua_title_to_value_map: # Avoid duplicates if somehow present
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        # 3. Default User-Agents from constants
        for ua_dict in USER_AGENTS:
            title, value = ua_dict.get("title"), ua_dict.get("value")
            if title and value and title not in self._ua_title_to_value_map: # Avoid duplicates
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles))

        # Attempt to restore previous selection
        if current_selection_text and current_selection_text in display_titles:
            try:
                idx = display_titles.index(current_selection_text)
                self.http_user_agent_row.set_selected(idx)
            except ValueError: # Should not happen if current_selection_text is in display_titles
                logger.warning("Error restoring User-Agent selection: '%s' not found after update.",
                               current_selection_text)
                if display_titles: self.http_user_agent_row.set_selected(0) # Default to first
        elif display_titles: # Default to first item if no prior or unmatchable selection
            self.http_user_agent_row.set_selected(0)
        else: # Model is empty (should not happen with "None" option)
            self.http_user_agent_row.set_selected(Gtk.INVALID_LIST_POSITION)

        # Update CSS class based on current selection after model update
        self._on_user_agent_changed(self.http_user_agent_row, None)
        logging.info("User-Agent dropdown model updated with %d titles.", len(display_titles))


    def _create_factory(self, attr_name: str,
                        wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """Creates a :class:`Gtk.SignalListItemFactory` for :class:`Gtk.ColumnView` columns.

        This factory configures how items (:class:`HeaderItem` instances) are displayed
        in the cells of a column. It sets up a :class:`Gtk.Label` for each cell and
        binds its properties (text, color, style) to the corresponding attributes
        of the :class:`HeaderItem`.

        The internal ``setup_func`` creates the label, and ``bind_func_internal``
        populates the label with data from the :class:`HeaderItem`, applying
        markup for colors (from GSettings) and bolding. Special handling is
        applied for `is_special_row` items.

        :param attr_name: The attribute name of the :class:`HeaderItem` to display
                          (e.g., 'key' or 'value').
        :type attr_name: str
        :param wrap_text: Whether the text in the label should wrap if it's too long.
                          Defaults to ``False``.
        :type wrap_text: bool, optional
        :return: A configured :class:`Gtk.SignalListItemFactory`.
        :rtype: Gtk.SignalListItemFactory
        """
        factory = Gtk.SignalListItemFactory()

        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Sets up the widget (a Gtk.Label) for a cell in the ColumnView."""
            label = Gtk.Label(xalign=0.0, hexpand=True, selectable=True)
            if wrap_text:
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                # Set a sensible max width for wrapped value column to avoid excessive height
                label.set_max_width_chars(80)
            list_item.set_child(label)

        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            """Binds data from a HeaderItem to the Gtk.Label in a cell."""
            label = list_item.get_child()
            item = list_item.get_item()

            # Ensure we have the expected types before proceeding
            if not (isinstance(label, Gtk.Label) and isinstance(item, HeaderItem)):
                if isinstance(label, Gtk.Label):
                    label.set_text("Error: Invalid item or widget type in factory binding.")
                return

            text_to_display = getattr(item, attr_name, "")
            escaped_text = GLib.markup_escape_text(str(text_to_display))

            if item.is_special_row:
                # For special rows (URL/status, separators), 'key' usually holds the main text.
                # 'value' might hold secondary info (like status code part for URL row).
                if attr_name == "key":
                    key_text = GLib.markup_escape_text(str(item.key))
                    value_text = GLib.markup_escape_text(str(item.value)) if item.value else ""
                    full_text = key_text
                    # Append value if it's meaningful and not just a placeholder like "N/A"
                    if value_text.strip() and value_text != "N/A":
                        full_text += f" {value_text}"
                    label.set_markup(
                        f"<b><span foreground='{self._special_row_color}'>{full_text}</span></b>")
                else: # 'value' column for special rows is typically empty or part of 'key'
                    label.set_markup("") # Clear the label for the value column of special rows
            else: # Standard HTTP header rows
                color_to_use = (self._header_key_color if attr_name == "key"
                                else self._header_value_color)
                label.set_markup(
                    f"<span foreground='{color_to_use}'>{escaped_text}</span>")
                # Make only header keys bold, not values, for typical HTTP display
                if attr_name == "key":
                    label.set_markup(f"<b><span foreground='{color_to_use}'>{escaped_text}</span></b>")


        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory

    def trigger_fetch(self) -> None:
        """Programmatically triggers the 'Fetch Headers' action.

        This method is typically called in response to a keyboard shortcut
        or an external event. It simulates a click on the 'Fetch Headers' button
        if the button is available and sensitive, initiating an HTTP request.

        :return: None
        :rtype: None
        """
        logger.debug("HTTP fetch triggered by shortcut or external action.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.clicked()
        elif self.current_http_task and not self.current_http_task.is_done():
            show_global_toast(self, "A request is already in progress. Please wait or cancel.")
        else: # Button might be insensitive due to ongoing task handled above, or other reasons
            logger.warning(
                "HTTP fetch button not available or not sensitive, cannot trigger fetch.")
