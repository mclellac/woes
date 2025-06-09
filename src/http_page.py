"""Defines the HTTP Headers page for the Woes application.

This page allows users to fetch and inspect HTTP headers for a given URL,
with options for custom Host headers, User-Agent strings, and Akamai Pragma headers.
It also supports using a custom DNS server for domain resolution via a custom HTTPAdapter.
"""

# pylint: disable=too-many-lines
import logging
# import re # No longer directly used in this file.
# import ssl # Moved to custom_dns_adapter.py
# import socket # Moved to custom_dns_adapter.py
from enum import Enum
from typing import Optional, List # List is used in type hints for method params/returns.

import requests
import requests.utils  # For urlparse, urlunparse
# from requests.adapters import HTTPAdapter # CustomDNSAdapter is imported instead.

try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None  # HttpPage checks this for logging if custom DNS is set but dnspython is missing.
    logging.warning("dnspython library not found. Custom DNS functionality will be disabled for HttpPage.")

import gi
from gi.repository import Adw, Gio, GObject, Gtk, GLib, Gdk, Pango

from .constants import RESOURCE_PREFIX, USER_AGENTS, APP_ID
from .utils import show_global_error, show_global_toast, is_valid_url
from .custom_dns_adapter import CustomDNSAdapter # Import the moved class


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

try:
    import urllib3.exceptions as urllib3_exceptions
except ImportError:
    class _DummyUrllib3Exception(Exception):
        pass
    urllib3_exceptions = type("urllib3_exceptions", (), {
        "MaxRetryError": _DummyUrllib3Exception,
        "NewConnectionError": _DummyUrllib3Exception,
    })
    logging.warning("Could not import urllib3.exceptions. Connection refused detection might be limited.")


# CustomDNSAdapter class definition was moved to custom_dns_adapter.py


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
    """GObject representing a single header key-value pair for the Gtk.ColumnView."""
    key: str
    value: str
    is_special_row: bool
    def __init__(self, key: str, value: str, is_special_row: bool = False):
        super().__init__()
        self.key = key
        self.value = value
        self.is_special_row = is_special_row

@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Adw.PreferencesPage):
    """Activity page for fetching and inspecting HTTP headers."""
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self.current_http_task = None
        self._current_header_items: List[HeaderItem] = [] # Type hint for clarity
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
        if self.http_apply_button: self.http_apply_button.set_use_underline(True)
        if self.http_host_header_row: self._on_host_header_changed(self.http_host_header_row)
        if self.http_user_agent_row: self._on_user_agent_changed(self.http_user_agent_row, None)

    def _connect_signals(self) -> None:
        self.http_entry_row.connect("entry-activated", self._on_entry_row_activated)
        self.http_apply_button.connect("clicked", self._on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self._on_pragma_toggled)
        self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button: self.copy_results_button.connect("clicked", self._on_copy_results_clicked)
        if self.http_host_header_row: self.http_host_header_row.connect("changed", self._on_host_header_changed)
        if self.http_user_agent_row: self.http_user_agent_row.connect("notify::selected-item", self._on_user_agent_changed)

    def _on_host_header_changed(self, entry_row: Adw.EntryRow) -> None:
        if not entry_row: return
        text = entry_row.get_text().strip()
        if text: entry_row.add_css_class("active-override")
        else: entry_row.remove_css_class("active-override")

    def _on_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: Optional[GObject.ParamSpec]) -> None:
        if not combo_row: return
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_title = selected_item_obj.get_string()
            effective_ua_value = self._ua_title_to_value_map.get(selected_title)
            if effective_ua_value: combo_row.add_css_class("active-override")
            else: combo_row.remove_css_class("active-override")
        elif selected_item_obj is None and not self._ua_title_to_value_map: # Model might be empty initially
            combo_row.remove_css_class("active-override")

    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Copying all headers to clipboard.")
        lines = []
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
                if clipboard:
                    clipboard.set(text_to_copy)
                    logger.info("Headers copied to clipboard successfully.")
                else: logger.warning("Failed to get default clipboard for copying.")
            except Exception as e: logger.error("Error copying headers to clipboard: %s", e, exc_info=True)
        else: logger.info("No headers to copy from the results view.")

    def _on_entry_row_activated(self, _widget: Gtk.Widget) -> None:
        original_url = self.http_entry_row.get_text().strip()
        url = self._ensure_scheme(original_url)
        logger.info("Fetching headers for URL: %s (original input: %s)", url, original_url)

        if not is_valid_url(url):
            logger.warning("Invalid URL provided: %s (processed as: %s)", original_url, url)
            toast_message = "Invalid URL format. Please enter a valid URL (e.g., https://example.com)."
            show_global_toast(self, toast_message)
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, "show_toast")):
                show_global_error(self, toast_message)
            self._update_column_view_model(None)
            return

        self._clear_error()
        self.http_entry_row.set_sensitive(False)
        if hasattr(self, "http_apply_button") and self.http_apply_button:
            self.http_apply_button.set_sensitive(False)
            self.http_apply_button.set_icon_name("process-working-symbolic")
        host_header = self.http_host_header_row.get_text().strip()
        user_agent_to_send: Optional[str] = None
        selected_title_obj = self.http_user_agent_row.get_selected_item()
        if isinstance(selected_title_obj, Gtk.StringObject):
            selected_title = selected_title_obj.get_string()
            user_agent_to_send = self._ua_title_to_value_map.get(selected_title)
        custom_dns_server = self.settings.get_string("custom-dns-server")
        if custom_dns_server and dns is None:
            logger.warning("Custom DNS server ('%s') configured, but 'dnspython' not available.", custom_dns_server)

        self._http_task_data_for_thread = {
            "url": url, "use_akamai_pragma": self.http_pragma_switch_row.get_active(),
            "host_header": host_header if host_header else None, "user_agent": user_agent_to_send,
            "custom_dns_server": custom_dns_server if custom_dns_server else None,
        }
        logger.debug(f"Starting header fetch task with data: {self._http_task_data_for_thread}")
        task = Gio.Task.new(self, None, self._fetch_headers_task_done_cb, None)
        self.current_http_task = task
        task.run_in_thread(self._fetch_headers_task_thread_func)

    def _prepare_request_headers(self, use_akamai_pragma: bool, host_header_from_input: Optional[str], user_agent: Optional[str]) -> tuple[dict[str, str], dict[str, str]]:
        initial_request_specific_headers: dict[str, str] = {}
        session_headers: dict[str, str] = {}
        if host_header_from_input:
            initial_request_specific_headers["Host"] = host_header_from_input
            logger.info("Using user-provided Host header: '%s'", host_header_from_input)
        if user_agent:
            session_headers["User-Agent"] = user_agent
            logger.info("Using custom User-Agent: '%s'", user_agent)
        else: logger.info("No custom User-Agent; `requests` default will be used.")
        if use_akamai_pragma:
            directives = ["akamai-x-get-request-id", "akamai-x-get-cache-key", "akamai-x-cache-on", "akamai-x-cache-remote-on", "akamai-x-get-true-cache-key", "akamai-x-check-cacheable", "akamai-x-get-extracted-values", "akamai-x-feo-trace", "x-akamai-logging-mode: verbose"]
            session_headers["Pragma"] = ", ".join(directives)
            logger.info("Akamai Pragma headers included.")
        else: logger.info("Akamai Pragma headers not included.")
        return initial_request_specific_headers, session_headers

    def _fetch_headers_task_thread_func(self, task: Gio.Task, source_object, task_data_arg: dict, cancellable: Optional[Gio.Cancellable]):
        current_task_data = self._http_task_data_for_thread
        url_to_fetch: str = current_task_data["url"]
        use_akamai_pragma: bool = current_task_data["use_akamai_pragma"]
        host_header_from_input: Optional[str] = current_task_data.get("host_header")
        user_agent: Optional[str] = current_task_data.get("user_agent")
        custom_dns_server: Optional[str] = current_task_data.get("custom_dns_server")
        initial_request_specific_headers, session_headers = self._prepare_request_headers(use_akamai_pragma, host_header_from_input, user_agent)
        session = requests.Session()

        adapter_sni_hint: Optional[str] = None
        if host_header_from_input:
            adapter_sni_hint = host_header_from_input

        effective_custom_dns_server = custom_dns_server if dns else None
        if custom_dns_server and not dns:
            logger.warning("HttpPage: Custom DNS ('%s') configured, but dnspython missing for adapter.", custom_dns_server)

        adapter = CustomDNSAdapter(custom_dns_server=effective_custom_dns_server, default_sni=adapter_sni_hint)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        logger.info("HttpPage: CustomDNSAdapter mounted (DNS: '%s', SNI hint for IP URL: '%s').", effective_custom_dns_server, adapter_sni_hint)

        if session_headers: session.headers.update(session_headers)
        try:
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CANCELLED.value, "Task cancelled.")
                return
            response = self._execute_http_request(session, url_to_fetch, initial_request_specific_headers, cancellable)
            processed_data = self._process_http_response(response, task, url_to_fetch)
            if processed_data: task.return_value(processed_data)
        except requests.exceptions.Timeout as e:
            logger.warning("Task thread: Timeout for '%s': %s", url_to_fetch, e)
            msg = "Request timed out. Check network, server, or WAF interference."
            task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.TIMEOUT.value, msg)
        except requests.exceptions.ConnectionError as e:
            logger.warning("Task thread: ConnectionError for '%s': %s", url_to_fetch, e)
            custom_msg = self._get_detailed_connection_error_message(e, url_to_fetch)
            msg = custom_msg or "Network connection error. Check internet and URL."
            task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.CONNECTION_ERROR.value, msg)
        except requests.exceptions.RequestException as e:
            logger.warning("Task thread: RequestException for '%s': %s", url_to_fetch, e)
            msg = f"Request failed: {e}. Verify URL and network."
            task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.REQUEST_EXCEPTION.value, msg)
        except Exception:
            logger.exception("Task thread: Unexpected error for URL '%s':", url_to_fetch)
            msg = "Unexpected internal error during request."
            task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.GENERIC_UNEXPECTED.value, msg)

    def _execute_http_request(self, session: requests.Session, url_to_fetch: str, initial_request_headers: dict[str, str], cancellable: Optional[Gio.Cancellable]) -> requests.Response:
        logger.info("Executing HTTP GET to %s.", url_to_fetch)
        if cancellable and cancellable.is_cancelled():
            raise requests.exceptions.RequestException("Request cancelled before sending.")
        response = session.get(url_to_fetch, headers=(initial_request_headers if initial_request_headers else None), allow_redirects=True, timeout=5)
        return response

    def _process_http_response(self, response: requests.Response, task: Gio.Task, url_being_fetched: str) -> Optional[list[dict[str, object]]]:
        all_responses_data: list[dict[str, object]] = []
        for hist_resp in response.history: # response.history contains prior Response objects
            hist_data: dict[str, object] = {"type": "redirect", "url": str(hist_resp.url), "status_code": hist_resp.status_code, "headers": {str(k): str(v) for k, v in dict(hist_resp.headers).items()}}
            all_responses_data.append(hist_data)
        try:
            response.raise_for_status() # Raises HTTPError for 4xx/5xx
            final_data_type = "final"
        except requests.exceptions.HTTPError as http_err:
            logger.warning("HTTPError for '%s': %s", url_being_fetched, http_err)
            error_message = self._format_http_error(http_err)
            task.return_new_error_literal(GLib.quark_from_string(WOES_HTTP_ERROR_DOMAIN), HttpErrorType.HTTP_ERROR.value, error_message)
            return None # Error set on task
        final_data: dict[str, object] = {"type": final_data_type, "url": str(response.url), "status_code": response.status_code, "headers": {str(k): str(v) for k, v in dict(response.headers).items()}}
        all_responses_data.append(final_data)
        return all_responses_data

    def _get_detailed_connection_error_message(self, exc: Exception, url: str) -> Optional[str]:
        current_exc: Optional[BaseException] = exc
        found_connection_refused = False
        max_depth = 5 # Limit traversal depth
        for _depth in range(max_depth):
            if current_exc is None: break
            exc_str = str(current_exc).lower()
            # Direct check for ConnectionRefusedError
            if isinstance(current_exc, ConnectionRefusedError): found_connection_refused = True; break
            # Check specific urllib3 exceptions that often wrap ConnectionRefusedError
            if isinstance(current_exc, urllib3_exceptions.NewConnectionError):
                if "connection refused" in exc_str or "errno 111" in exc_str: found_connection_refused = True; break
                # Check original_error if present (urllib3 specific)
                if hasattr(current_exc, "original_error"):
                    original_error = getattr(current_exc, "original_error")
                    if isinstance(original_error, ConnectionRefusedError) or (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111): found_connection_refused = True; break
            if isinstance(current_exc, urllib3_exceptions.MaxRetryError):
                if hasattr(current_exc, "reason") and isinstance(current_exc.reason, urllib3_exceptions.NewConnectionError):
                    reason_exc_str = str(current_exc.reason).lower()
                    if "connection refused" in reason_exc_str or "errno 111" in reason_exc_str: found_connection_refused = True; break
                    if hasattr(current_exc.reason, "original_error"): # Check original_error of the reason
                        original_error = getattr(current_exc.reason, "original_error")
                        if isinstance(original_error, ConnectionRefusedError) or (hasattr(original_error, "errno") and getattr(original_error, "errno") == 111): found_connection_refused = True; break
            # Generic string checks (less reliable but can catch some cases)
            if (any("connection refused" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or "connection refused" in exc_str): found_connection_refused = True
            if (any("errno 111" in str(arg).lower() for arg in current_exc.args if isinstance(arg, str)) or "errno 111" in exc_str): found_connection_refused = True
            if found_connection_refused: break

            # Traverse to the next exception in the chain
            next_exc: Optional[BaseException] = None
            if hasattr(current_exc, "__cause__") and current_exc.__cause__ is not None: next_exc = current_exc.__cause__
            elif (hasattr(current_exc, "__context__") and current_exc.__context__ is not None and not getattr(current_exc, "__suppress_context__", False)): next_exc = current_exc.__context__
            if current_exc is next_exc: break # Avoid infinite loops
            current_exc = next_exc

        if found_connection_refused:
            logger.info("Connection refused condition identified for URL: %s", url)
            parsed_url_scheme = requests.utils.urlparse(url).scheme
            if parsed_url_scheme == "https": return "Connection Refused: The server at the HTTPS URL actively refused the connection. This might be an HTTP-only service. Consider trying with 'http://'."
            if parsed_url_scheme == "http": return "Connection Refused: The server at the HTTP URL actively refused the connection. The server might only support HTTPS or be down. Consider trying with 'https://'."
            return "Connection Error: The server at the specified URL actively refused the connection."
        return None

    def _fetch_headers_task_done_cb(self, _source_object, result: Gio.AsyncResult, _user_data):
        task_being_processed = self.current_http_task
        if task_being_processed is None:
            logger.warning("_fetch_headers_task_done_cb: current_http_task is None.")
            if hasattr(self, "http_entry_row") and self.http_entry_row and not self.http_entry_row.get_sensitive(): self.http_entry_row.set_sensitive(True)
            if (hasattr(self, "http_apply_button") and self.http_apply_button and not self.http_apply_button.get_sensitive()):
                self.http_apply_button.set_sensitive(True)
                self.http_apply_button.set_icon_name("")
            return
        self.current_http_task = None
        logger.info("Processing task completion in _fetch_headers_task_done_cb.")
        try:
            actual_list_of_responses = task_being_processed.propagate_value()
            # Ensure the result is a list, as expected.
            if not isinstance(actual_list_of_responses, list) and isinstance(actual_list_of_responses, tuple):
                if len(actual_list_of_responses) > 0 and isinstance(actual_list_of_responses[0], list): actual_list_of_responses = actual_list_of_responses[0]
                elif hasattr(actual_list_of_responses, "value") and isinstance(actual_list_of_responses.value, list): actual_list_of_responses = actual_list_of_responses.value

            if isinstance(actual_list_of_responses, list):
                logger.info("Successfully processed task result: %d response stages.", len(actual_list_of_responses))
                processed_headers_for_store: list[HeaderItem] = []
                if not actual_list_of_responses:
                    logger.info("Received empty list of responses (e.g. no redirects and no final data).")
                    self._update_column_view_model(None)
                else:
                    for i, response_data_dict in enumerate(actual_list_of_responses):
                        if not isinstance(response_data_dict, dict):
                            logger.error("Expected dict item in response list, got %s. Data: %s", type(response_data_dict), response_data_dict)
                            continue
                        url_display = f"URL: {response_data_dict.get('url', 'N/A')}"
                        status_code = response_data_dict.get("status_code", "N/A")
                        response_type = response_data_dict.get("type", "unknown")
                        status_display = f"Status: {status_code} ({response_type.capitalize()})"
                        processed_headers_for_store.append(HeaderItem(key=url_display, value=status_display, is_special_row=True))
                        headers_for_this_response = response_data_dict.get("headers", {})
                        if isinstance(headers_for_this_response, dict):
                            for key, value in headers_for_this_response.items(): processed_headers_for_store.append(HeaderItem(key=str(key), value=str(value), is_special_row=False))
                        else: logger.warning("Headers data for response stage %d is not a dict: %s", i, headers_for_this_response)
                        if i < len(actual_list_of_responses) - 1: # Add separator if not the last response
                            processed_headers_for_store.append(HeaderItem(key="--- Redirected To ---", value="", is_special_row=True))
                    self._current_header_items = processed_headers_for_store
                    self._update_column_view_model(processed_headers_for_store)
                if hasattr(self, "http_entry_row") and self.http_entry_row: self.http_entry_row.remove_css_class("error")
            else:
                logger.error("Result data from task is of unexpected type %s. Expected list. Data: %s", type(actual_list_of_responses), actual_list_of_responses)
                show_global_error(self, f"Failed to process task result (unexpected data structure: {type(actual_list_of_responses).__name__}). Please check logs.")
                if hasattr(self, "http_entry_row") and self.http_entry_row: self.http_entry_row.add_css_class("error")
                self._update_column_view_model(None)
        except GLib.Error as e:  # Errors set by task.return_new_error_literal
            logger.warning("Task failed with GLib.Error (Domain: %s, Code: %s, Message: %s)", e.domain, e.code, e.message)
            display_message = e.message.replace("<b>", "").replace("</b>", "") if e.message else "An unknown error occurred."
            show_global_error(self, display_message)
            if hasattr(self, "http_entry_row") and self.http_entry_row: self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        except Exception as e: # Other Python exceptions during result processing
            logger.exception("Unexpected Python error in _fetch_headers_task_done_cb:")
            error_message = "An unexpected application error occurred while displaying results."
            if hasattr(e, "message") and e.message: error_message = str(e.message)
            elif str(e): error_message = str(e)
            show_global_error(self, error_message)
            if hasattr(self, "http_entry_row") and self.http_entry_row: self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None)
        finally:
            if hasattr(self, "http_entry_row") and self.http_entry_row: self.http_entry_row.set_sensitive(True)
            if hasattr(self, "http_apply_button") and self.http_apply_button:
                self.http_apply_button.set_sensitive(True)
                self.http_apply_button.set_icon_name("") # Remove spinner

    @staticmethod
    def _ensure_scheme(url: str) -> str:
        parsed_url = requests.utils.urlparse(url)
        if not parsed_url.scheme:
            logger.debug("URL '%s' has no scheme, prepending 'https://'.", url)
            return "https://" + url
        return url

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        status_code = e.response.status_code
        reason = e.response.reason if e.response.reason else "Unknown Error"
        if status_code == 403: return f"403 Forbidden: Access to {e.request.url} denied."
        if status_code == 404: return f"404 Not Found: Resource at {e.request.url} not found."
        if status_code == 500: return f"500 Internal Server Error for {e.request.url}."
        return f"HTTP Error {status_code} ({reason}) for URL: {e.request.url}."

    def _on_pragma_toggled(self, _widget: Gtk.Switch, _gparam: GObject.ParamSpec) -> None:
        logger.debug("Akamai Pragma toggled: %s. Re-fetching if URL present.", self.http_pragma_switch_row.get_active())
        if self.http_entry_row.get_text().strip(): self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, header_items: Optional[List["HeaderItem"]]) -> None:
        self.header_list_store.remove_all()
        if header_items:
            for item in header_items: self.header_list_store.append(item)
            self._show_results()
        else: self._hide_results()

    def _show_results(self):
        if self.http_results_group: self.http_results_group.set_visible(True)

    def _hide_results(self):
        if self.http_results_group: self.http_results_group.set_visible(False)

    def _clear_error(self) -> None:
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"): main_window.hide_error()
        else: logger.warning("Could not find main window or hide_error method to clear error.")
        if self.http_entry_row: self.http_entry_row.remove_css_class("error")

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Results cleared by user action.")
        self._current_header_items = []
        self._update_column_view_model(None)
        self._clear_error()
        if self.http_entry_row: self.http_entry_row.set_text("")

    def _on_color_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        logger.debug("Color setting changed for GSettings key: %s", key)
        if key == "http-output-header-key-color": self._header_key_color = settings.get_string(key)
        elif key == "http-output-header-value-color": self._header_value_color = settings.get_string(key)
        elif key == "http-output-special-row-color": self._special_row_color = settings.get_string(key)
        if self._current_header_items: # Re-populate to apply new colors
            logger.debug("Re-populating ColumnView to apply new color changes.")
            self._update_column_view_model(self._current_header_items)

    def _update_user_agent_model(self) -> None:
        if not self.http_user_agent_row:
            logger.error("HttpPage._update_user_agent_model: http_user_agent_row is None.")
            return
        self._ua_title_to_value_map.clear()
        current_selection_text: Optional[str] = None
        if (self.http_user_agent_row.get_model() and self.http_user_agent_row.get_selected() != Gtk.INVALID_LIST_POSITION):
            selected_item = self.http_user_agent_row.get_selected_item()
            if isinstance(selected_item, Gtk.StringObject): current_selection_text = selected_item.get_string()

        display_titles: list[str] = []
        # Custom UAs from GSettings
        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(variant.unpack() if variant and variant.get_type_string() == "a(ss)" else [])
        for title, value in custom_ua_pairs:
            if title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        # "None" option
        none_title = "None"
        if none_title not in self._ua_title_to_value_map: display_titles.append(none_title)
        self._ua_title_to_value_map[none_title] = None # Represents no override

        # Default UAs from constants
        for ua_dict in USER_AGENTS:
            title, value = ua_dict.get("title"), ua_dict.get("value")
            if title and value and title not in self._ua_title_to_value_map:
                display_titles.append(title)
                self._ua_title_to_value_map[title] = value

        self.http_user_agent_row.set_model(Gtk.StringList.new(display_titles))

        # Restore selection
        if current_selection_text and current_selection_text in display_titles:
            try:
                idx = display_titles.index(current_selection_text)
                self.http_user_agent_row.set_selected(idx)
            except ValueError: # Should not happen if logic is correct
                logger.warning("Error restoring User-Agent selection: '%s' not found in new list.", current_selection_text)
                if display_titles: self.http_user_agent_row.set_selected(0)
        elif display_titles: # Default to first item if no prior or unmatchable selection
            self.http_user_agent_row.set_selected(0)

        self._on_user_agent_changed(self.http_user_agent_row, None) # Update visual cue
        logging.info("User-Agent dropdown model updated with %d titles.", len(display_titles))

    def _create_factory(self, attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        """Creates a Gtk.SignalListItemFactory for Gtk.ColumnView columns."""
        factory = Gtk.SignalListItemFactory()
        def setup_func(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0.0, hexpand=True)
            if wrap_text:
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(80) # Approximate for typical widths
            list_item.set_child(label)

        def bind_func_internal(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            item = list_item.get_item()
            if not (isinstance(label, Gtk.Label) and isinstance(item, HeaderItem)):
                if isinstance(label, Gtk.Label): label.set_text("Error: Invalid item type.")
                return

            text_to_display = getattr(item, attr_name, "")

            if item.is_special_row:
                if attr_name == "key": # For special rows, 'key' might hold combined info or just the primary part
                    key_text = GLib.markup_escape_text(str(item.key))
                    value_text = GLib.markup_escape_text(str(item.value)) if item.value else ""
                    full_text = key_text
                    if value_text.strip() and value_text != "N/A": full_text += f" {value_text}"
                    label.set_markup(f"<b><span foreground='{self._special_row_color}'>{full_text}</span></b>")
                else: # 'value' column for special rows is typically empty
                    label.set_markup("")
            else: # Standard header rows
                color_to_use = self._header_key_color if attr_name == "key" else self._header_value_color
                escaped_text = GLib.markup_escape_text(str(text_to_display))
                label.set_markup(f"<b><span foreground='{color_to_use}'>{escaped_text}</span></b>")

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func_internal)
        return factory

    def trigger_fetch(self):
        """Programmatically triggers the 'Fetch' action."""
        logging.debug("HTTP fetch triggered by shortcut.")
        if self.http_apply_button and self.http_apply_button.get_sensitive():
            self.http_apply_button.clicked()
        else:
            logging.warning("HTTP fetch button not available or not sensitive, cannot trigger fetch.")

[end of src/http_page.py]
