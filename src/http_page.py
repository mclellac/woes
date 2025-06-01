import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from gi import require_version
require_version('Adw', '1')
require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GObject, Gtk

from .constants import RESOURCE_PREFIX
# Removed Helper import as it's unused
# from .style_utils import set_widget_visibility # This is no longer needed

# Configure logger for the module
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
    # clear_results_button is connected via UI handler

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.debug("HttpPage initialized.")
        self._connect_signals()
        # self.column_view_helper removed
        self._clear_error() # Ensure banner is hidden on init
        self._hide_results() # Ensure results group is hidden on init

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
        url = self._ensure_scheme(original_url)
        logger.info(f"Fetching headers for URL: {url} (original: {original_url})")

        if not self._is_valid_url(url):
            logger.warning(f"Invalid URL provided: {original_url} (processed as: {url})")
            self._display_error(
                "Invalid URL format: Please enter a valid URL."
            )
            self._update_column_view_model(None) # Clear previous results
            return

        self._clear_error()
        headers = self._fetch_headers(
            url, self.http_pragma_switch_row.get_active()
        )

        if headers and "error" not in headers:
            logger.info(f"Successfully fetched headers for {url}")
            self._update_column_view_model(headers)
            self.http_entry_row.remove_css_class("error")
        else:
            error_message = headers.get("error", "Unknown error: Failed to fetch headers.")
            logger.error(f"Error fetching headers for {url}: {error_message}")
            # Remove HTML bold tags for AdwBanner, as it might handle styling differently
            error_message = error_message.replace("<b>", "").replace("</b>", "")
            self._display_error(error_message)
            self.http_entry_row.add_css_class("error")
            self._update_column_view_model(None) # Clear previous results if error

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

    def _fetch_headers(
        self, url: str, use_akamai_pragma: bool
    ) -> Dict[str, str]:
        logger.debug(f"Making GET request to {url} with Akamai headers: {use_akamai_pragma}")
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
            response = requests.get(url, headers=request_headers, allow_redirects=False, timeout=10)
            response.raise_for_status()
            return dict(response.headers)
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTPError for {url}: {e}", exc_info=True)
            return {"error": self._format_http_error(e)}
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"ConnectionError for {url}: {e}", exc_info=True)
            return {
                "error": "Connection Error: Failed to establish a connection."
            }
        except requests.exceptions.Timeout as e:
            logger.warning(f"Timeout for {url}: {e}", exc_info=True)
            return {"error": "Timeout Error: The request timed out."}
        except requests.exceptions.RequestException as e:
            logger.error(f"RequestException for {url}: {e}", exc_info=True)
            return {"error": f"Request Error: {str(e)}"}

    def _format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        status_code = e.response.status_code
        if status_code == 404:
            return "404 Not Found: The requested URL was not found on this server."
        elif status_code == 403:
            return "403 Forbidden: You don't have permission to access this URL."
        elif status_code == 500:
            return "500 Internal Server Error: The server encountered an internal error."
        return f"HTTP Error {status_code}: {e.response.reason}."

    def _on_pragma_toggled(
        self, widget: Gtk.Switch, gparam: GObject.ParamSpec
    ) -> None:
        logger.debug(f"Akamai Pragma toggled to: {widget.get_active()}")
        if self.http_entry_row.get_text().strip():
            self._on_entry_row_activated(self.http_entry_row)

    def _update_column_view_model(self, headers: Optional[Dict[str, str]]) -> None:
        current_model = self.http_column_view.get_model()
        list_store = None
        if isinstance(current_model, Gtk.MultiSelection):
            list_store = current_model.get_model()

        if not isinstance(list_store, Gio.ListStore): # If no model or wrong type, create new
            list_store = Gio.ListStore.new(HeaderItem)
            selection_model = Gtk.MultiSelection.new(list_store)
            self.http_column_view.set_model(selection_model)
            # Setup columns only if they don't exist
            if not self.http_column_view.get_columns():
                header_name_factory = self._create_factory("key")
                header_value_factory = self._create_factory("value", wrap_text=True)

                col_name = Gtk.ColumnViewColumn.new("Header", header_name_factory)
                col_value = Gtk.ColumnViewColumn.new("Value", header_value_factory)
                col_value.set_expand(True)

                self.http_column_view.append_column(col_name)
                self.http_column_view.append_column(col_value)

        list_store.remove_all() # Clear existing items

        if headers and "error" not in headers:
            for key, value in headers.items():
                list_store.append(HeaderItem(key, value)) # Use raw value
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
        self._hide_results() # Hide results on error

    def _clear_error(self) -> None:
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")
        self.http_entry_row.remove_css_class("error")

    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def _on_error_banner_dismiss(self, banner: Adw.Banner, *args):
        self._clear_error()

    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def _on_clear_results_clicked(self, button: Gtk.Button, *args):
        logger.info("Results cleared by user.")
        self._update_column_view_model(None) # Clears the view
        self._clear_error() # Clear any errors
        self.http_entry_row.set_text("") # Clear entry row


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
                label.set_max_width_chars(80) # Or adjust as needed
            list_item.set_child(label)

        def bind_func(_, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            item = list_item.get_item()
            text = getattr(item, attr_name, "")
            if label: # Check if label exists
                label.set_text(text)

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)

        return factory

