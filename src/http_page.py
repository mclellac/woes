# http_page.py
import logging
import requests

from .constants import RESOURCE_PREFIX
from typing import Dict
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GObject, Gtk

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


class HeaderItem(GObject.Object):
    """Represents an HTTP header item."""

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/http_page.ui")
class HttpPage(Gtk.Box):
    __gtype_name__ = "HttpPage"

    # Define template children with proper IDs
    http_entry_row = Gtk.Template.Child("http_entry_row")
    http_list_box = Gtk.Template.Child("http_list_box")
    pragma_switch_row = Gtk.Template.Child("pragma_switch_row")
    http_column_view = Gtk.Template.Child("http_column_view")
    frame = Gtk.Template.Child("frame")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.init_ui()

    def init_ui(self):
        # Connect signals
        self.http_entry_row.connect("entry-activated", self.on_http_entry_row_activated)
        self.http_entry_row.connect("apply", self.on_http_entry_row_activated)
        self.pragma_switch_row.connect(
            "notify::active", self.on_use_akamai_pragma_toggled
        )

    def on_http_entry_row_activated(self, entry_row):
        logging.debug("Entry activated")
        url = entry_row.get_text().strip()

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            logging.error("Invalid URL format.")
            self.update_column_view(None)
            return

        use_akamai_pragma = self.pragma_switch_row.get_active()
        headers = self.fetch_headers(url, use_akamai_pragma)

        if headers and "error" not in headers:
            self.update_column_view(headers)
        else:
            self.update_column_view(None)

    def fetch_headers(self, url: str, use_akamai_pragma: bool) -> Dict[str, str]:
        """Fetches HTTP headers from a given URL."""
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
            response = requests.head(url, headers=headers, allow_redirects=False)
            response.raise_for_status()  # Raise exception for non-2xx status codes
            return dict(response.headers)
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch headers from {url}: {e}")
            return {"error": str(e)}

    def on_use_akamai_pragma_toggled(self, widget, gparam):
        """Trigger header fetching when the Akamai pragma switch is toggled."""
        if self.http_entry_row.get_text().strip():
            self.on_http_entry_row_activated(self.http_entry_row)

    def update_column_view(self, headers):
        """Update the HTTP headers column view."""
        # Remove existing columns safely
        for column in list(self.http_column_view.get_columns()):
            self.http_column_view.remove_column(column)

        if headers and "error" not in headers:
            list_store = Gio.ListStore.new(HeaderItem)
            for key, value in headers.items():
                if key in [
                    "Content-Security-Policy",
                    "Content-Security-Policy-Report-Only",
                    "Set-Cookie",
                    "X-Akamai-Session-Info",
                ]:
                    value = self.split_header_value(value, key)
                elif key in ["X-Cache", "X-Cache-Remote"]:
                    value = value.replace(" (", "\n(")

                list_store.append(HeaderItem(key, value))

            selection_model = Gtk.SingleSelection.new(list_store)
            self.http_column_view.set_model(selection_model)

            header_name_column = Gtk.ColumnViewColumn.new("Header Name")
            header_value_column = Gtk.ColumnViewColumn.new("Header Value")

            header_name_factory = self.create_factory("key")
            header_value_factory = self.create_factory("value")

            header_name_column.set_factory(header_name_factory)
            header_value_column.set_factory(header_value_factory)

            self.http_column_view.append_column(header_name_column)
            self.http_column_view.append_column(header_value_column)
        else:
            self.http_column_view.set_model(None)
            logging.debug("No headers found, setting column view model to None")

    @staticmethod
    def create_factory(attr_name):
        """Create a Gtk.SignalListItemFactory for a given attribute."""
        factory = Gtk.SignalListItemFactory()
        factory.connect(
            "setup", lambda factory, list_item: list_item.set_child(Gtk.Label(xalign=0))
        )
        factory.connect(
            "bind",
            lambda factory, list_item: list_item.get_child().set_text(
                getattr(list_item.get_item(), attr_name)
            ),
        )
        return factory

    @staticmethod
    def split_header_value(header_value: str, header_type: str) -> str:
        """Format a header value by splitting it into separate lines."""
        if header_type not in [
            "Content-Security-Policy",
            "Content-Security-Policy-Report-Only",
            "Set-Cookie",
            "X-Akamai-Session-Info",
        ]:
            return ""

        formatted_header = []
        parts = header_value.split(";")
        parts = [part.strip() + ";" for part in parts if part.strip()]

        for part in parts:
            while len(part) > 80:
                split_pos = part.find(";", 0, 80)
                if split_pos != -1:
                    formatted_header.append(part[: split_pos + 1])
                    part = part[split_pos + 1 :].lstrip()
                else:
                    split_pos = part.find(" ", 0, 80)
                    if split_pos != -1:
                        formatted_header.append(part[:split_pos])
                        part = part[split_pos:].lstrip()
                    else:
                        formatted_header.append(part[:80])
                        part = part[80:].lstrip()

            formatted_header.append(part)

        return "\n".join(formatted_header)
