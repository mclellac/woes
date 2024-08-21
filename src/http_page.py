# http_page.py
import logging
import requests
import re

from .constants import RESOURCE_PREFIX
from typing import Dict, Optional
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GObject, Gtk

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

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
    http_list_box = Gtk.Template.Child("http_list_box")
    http_pragma_switch_row = Gtk.Template.Child("http_pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    http_header_frame = Gtk.Template.Child("http_header_frame")
    http_error_label = Gtk.Template.Child("http_error_label")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.http_page_init_ui()

    def http_page_init_ui(self) -> None:
        self.http_entry_row.connect("entry-activated", self.http_page_on_entry_row_activated)
        self.http_entry_row.connect("apply", self.http_page_on_entry_row_activated)
        self.http_pragma_switch_row.connect("notify::active", self.http_page_on_pragma_toggled)
        self.http_page_clear_error()
        self.http_page_hide_column_view()

    def http_page_on_entry_row_activated(self, entry_row: Gtk.Entry) -> None:
        url = entry_row.get_text().strip()
        url = self.http_page_ensure_scheme(url)

        if not self.http_page_is_valid_url(url):
            self.http_page_display_error("<b>Invalid URL format:</b> Please enter a valid URL.")
            self.http_page_update_column_view(None)
            return

        self.http_page_clear_error()
        use_akamai_pragma = self.http_pragma_switch_row.get_active()
        headers = self.http_page_fetch_headers(url, use_akamai_pragma)

        if headers and "error" not in headers:
            self.http_page_update_column_view(headers)
            self.http_entry_row.remove_css_class("error")
        else:
            self.http_page_display_error(headers.get("error", "<b>Unknown error:</b> Failed to fetch headers."))
            self.http_entry_row.add_css_class("error")
            self.http_page_update_column_view(None)

    def http_page_ensure_scheme(self, url: str) -> str:
        parsed_url = urlparse(url)
        if not parsed_url.scheme:
            url = "https://" + url
        return url

    def http_page_is_valid_url(self, url: str) -> bool:
        url_regex = re.compile(
            r'^(?:http|https)://'
            r'(?:\S+(?::\S*)?@)?'
            r'(?:[A-Za-z0-9.-]+\.[A-Za-z]{2,}|localhost|'
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|'
            r'\[?[A-Fa-f0-9]*:[A-Fa-f0-9:]+\]?)'
            r'(?::\d+)?'
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)

        return re.match(url_regex, url) is not None and bool(urlparse(url).netloc)

    def http_page_fetch_headers(self, url: str, use_akamai_pragma: bool) -> Dict[str, str]:
        headers = {}
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
            headers["Pragma"] = ", ".join(akamai_pragma_directives)

        try:
            response = requests.get(url, headers=headers, allow_redirects=False)
            response.raise_for_status()
            return dict(response.headers)
        except requests.exceptions.HTTPError as e:
            return {"error": self.http_page_format_http_error(e)}
        except requests.exceptions.ConnectionError:
            return {"error": "<b>Connection Error:</b> Failed to establish a connection."}
        except requests.exceptions.Timeout:
            return {"error": "<b>Timeout Error:</b> The request timed out."}
        except requests.exceptions.RequestException as e:
            return {"error": f"<b>Request Error:</b> {str(e)}"}

    def http_page_format_http_error(self, e: requests.exceptions.HTTPError) -> str:
        status_code = e.response.status_code
        if status_code == 404:
            return "<b>404 Not Found:</b> The requested URL was not found on this server."
        elif status_code == 403:
            return "<b>403 Forbidden:</b> You don't have permission to access this URL."
        elif status_code == 500:
            return "<b>500 Internal Server Error:</b> The server encountered an internal error."
        return f"<b>HTTP Error {status_code}:</b> {e.response.reason}."

    def http_page_on_pragma_toggled(self, widget: Gtk.Switch, gparam: GObject.ParamSpec) -> None:
        if self.http_entry_row.get_text().strip():
            self.http_page_on_entry_row_activated(self.http_entry_row)

    def http_page_update_column_view(self, headers: Optional[Dict[str, str]]) -> None:
        self.http_column_view.set_model(None)
        for column in list(self.http_column_view.get_columns()):
            self.http_column_view.remove_column(column)

        if headers and "error" not in headers:
            list_store = Gio.ListStore.new(HeaderItem)
            for key, value in headers.items():
                wrapped_value = self.http_page_wrap_text(value)
                list_store.append(HeaderItem(key, wrapped_value))

            selection_model = Gtk.SingleSelection.new(list_store)
            self.http_column_view.set_model(selection_model)

            header_name_column = Gtk.ColumnViewColumn.new("Header Name")
            header_value_column = Gtk.ColumnViewColumn.new("Header Value")

            header_name_factory = self.http_page_create_factory("key")
            header_value_factory = self.http_page_create_factory("value", wrap_text=True)

            header_name_column.set_factory(header_name_factory)
            header_value_column.set_factory(header_value_factory)
            header_value_column.set_expand(True)  # Ensure the column expands to fill available space

            self.http_column_view.append_column(header_name_column)
            self.http_column_view.append_column(header_value_column)

            self.http_page_show_column_view()
        else:
            self.http_page_hide_column_view()

    def http_page_display_error(self, message: str) -> None:
        self.http_error_label.set_markup(message)
        self.http_error_label.set_visible(True)
        self.http_entry_row.add_css_class("error")
        self.http_page_hide_column_view()

    def http_page_clear_error(self) -> None:
        self.http_error_label.set_text("")
        self.http_error_label.set_visible(False)
        self.http_entry_row.remove_css_class("error")

    def http_page_show_column_view(self) -> None:
        self.http_header_frame.set_visible(True)
        self.http_column_view.set_visible(True)

    def http_page_hide_column_view(self) -> None:
        self.http_header_frame.set_visible(False)
        self.http_column_view.set_visible(False)

    @staticmethod
    def http_page_create_factory(attr_name: str, wrap_text: bool = False) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()

        def setup_func(factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0)
            label.set_hexpand(True)
            label.set_vexpand(True)
            if wrap_text:
                label.set_wrap(True)
                label.set_max_width_chars(80)
            list_item.set_child(label)

        def bind_func(factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            item = list_item.get_item()
            text = getattr(item, attr_name, "")
            if label:
                label.set_text(text)

        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)

        return factory

    @staticmethod
    def http_page_wrap_text(text: str) -> str:
        max_line_length = 80
        wrapped_lines = []
        for line in text.splitlines():
            while len(line) > max_line_length:
                split_pos = line.rfind(" ", 0, max_line_length)
                if split_pos == -1:
                    split_pos = max_line_length
                wrapped_lines.append(line[:split_pos])
                line = line[split_pos:].strip()
            wrapped_lines.append(line)
        return "\n".join(wrapped_lines)
