import gi
import logging
import requests
from urllib.parse import urlparse
from typing import Dict

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, GObject

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class HeaderItem(GObject.Object):
    """Represents an HTTP header item."""
    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value

@Gtk.Template(resource_path='/com/github/mclellac/WebOpsEvaluationSuite/gtk/http_page.ui')
class HttpPage(Gtk.Box):
    __gtype_name__ = 'HttpPage'

    # Define template children with proper IDs
    http_entry_row = Gtk.Template.Child('http_entry_row')
    http_list_box = Gtk.Template.Child('http_list_box')
    pragma_switch_row = Gtk.Template.Child('pragma_switch_row')
    json_payloads_drop_down = Gtk.Template.Child('json_payloads_drop_down')
    http_column_view = Gtk.Template.Child('http_column_view')
    frame = Gtk.Template.Child('frame')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.init_ui()

    def init_ui(self):
        # Connect signals
        self.http_entry_row.connect("entry-activated", self.on_http_entry_row_activated)
        self.http_entry_row.connect("apply", self.on_http_entry_row_activated)
        self.pragma_switch_row.connect("notify::active", self.on_use_akamai_pragma_toggled)
        self.json_payloads_drop_down.connect("notify::selected-item", self.on_json_payloads_drop_down_changed)

    def on_http_entry_row_activated(self, entry_row):
        logging.debug("Entry activated")
        url = entry_row.get_text().strip()

        # Default to https:// if not provided
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # Sanity check: validate URL format
        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            logging.error("Invalid URL format.")
            self.update_column_view(None)
            return

        use_akamai_pragma = self.pragma_switch_row.get_active()
        headers = self.fetch_headers(url, use_akamai_pragma)

        if headers and 'error' not in headers:
            self.update_column_view(headers)
        else:
            self.update_column_view(None)

    def fetch_headers(self, url: str, use_akamai_pragma: bool) -> Dict[str, str]:
        """Fetches HTTP headers from a given URL."""
        headers = {}
        if use_akamai_pragma:
            akamai_pragma_directives = [
                'akamai-x-get-request-id',
                'akamai-x-get-cache-key',
                'akamai-x-cache-on',
                'akamai-x-cache-remote-on',
                'akamai-x-get-true-cache-key',
                'akamai-x-check-cacheable',
                'akamai-x-get-extracted-values',
                'akamai-x-feo-trace',
                'x-akamai-logging-mode: verbose'
            ]
            headers['Pragma'] = ', '.join(akamai_pragma_directives)
        try:
            response = requests.head(url, headers=headers, allow_redirects=False)
            response.raise_for_status()  # Raise exception for non-2xx status codes
            return dict(response.headers)
        except requests.exceptions.RequestException as e:
            # Return error details or propagate exception
            return {'error': str(e)}

    def on_use_akamai_pragma_toggled(self, widget, gparam):
        """
        Trigger header fetching when the Akamai pragma switch is toggled.
        """
        if self.http_entry_row.get_text().strip():
            self.on_http_entry_row_activated(self.http_entry_row)

    def on_json_payloads_drop_down_changed(self, widget, gparam):
        """
        Handle changes in the JSON payloads dropdown.
        """
        selected_item = self.json_payloads_drop_down.get_selected_item()
        if selected_item:
            payload = selected_item.get_label()
            # Assuming settings are managed somewhere else, but you can add it if needed

    def update_column_view(self, headers):
        # Remove existing columns safely
        for column in list(self.http_column_view.get_columns()):
            if column:
                self.http_column_view.remove_column(column)

        if headers and 'error' not in headers:
            # Create a ListStore with the HeaderItem class
            list_store = Gio.ListStore.new(HeaderItem)
            for key, value in headers.items():
                # Format headers based on their type using HttpPage methods
                if key == 'Content-Security-Policy':
                    value = self.split_header_value(value, 'Content-Security-Policy')
                elif key == 'Set-Cookie':
                    value = self.split_header_value(value, 'Set-Cookie')
                elif key == 'X-Akamai-Session-Info':
                    value = self.split_header_value(value, 'X-Akamai-Session-Info')
                elif key in ['X-Cache', 'X-Cache-Remote']:
                    value = value.replace(' (', '\n(')

                list_store.append(HeaderItem(key, value))

            # Wrap ListStore in a Gtk.SingleSelection
            selection_model = Gtk.SingleSelection.new(list_store)
            self.http_column_view.set_model(selection_model)

            # Define and add new columns
            header_name_column = Gtk.ColumnViewColumn.new("Header Name")
            header_value_column = Gtk.ColumnViewColumn.new("Header Value")

            # Factory for creating list items
            def create_factory(attr_name):
                factory = Gtk.SignalListItemFactory()
                factory.connect("setup", lambda factory, list_item: list_item.set_child(Gtk.Label(xalign=0)))
                factory.connect("bind", lambda factory, list_item: list_item.get_child().set_text(getattr(list_item.get_item(), attr_name)))
                return factory

            header_name_factory = create_factory('key')
            header_value_factory = create_factory('value')

            header_name_column.set_factory(header_name_factory)
            header_value_column.set_factory(header_value_factory)

            # Add columns to the ColumnView
            self.http_column_view.append_column(header_name_column)
            self.http_column_view.append_column(header_value_column)
        else:
            # Set the model to None if no headers or error
            self.http_column_view.set_model(None)
            logging.debug("No headers found, setting column view model to None")

    @staticmethod
    def setup_factory(list_item: Gtk.ListItem) -> None:
        # Setup factory for list item
        list_item.set_child(Gtk.Label(xalign=0))

    @staticmethod
    def bind_name_factory(list_item: Gtk.ListItem, header_item: HeaderItem) -> None:
        # Bind header name factory
        label = list_item.get_child()
        label.set_text(header_item.key)

    @staticmethod
    def bind_value_factory(list_item: Gtk.ListItem, header_item: HeaderItem) -> None:
        # Bind header value factory
        label = list_item.get_child()
        label.set_text(header_item.value)

    @staticmethod
    def split_header_value(header_value: str, header_type: str) -> str:
        """
        Formats a header value by splitting it into separate lines based on semicolons
        and limiting each line to 80 characters. Lines longer than 80 characters are
        split at the nearest space character if no semicolon is found. This method supports
        'Content-Security-Policy', 'X-Akamai-Session-Info', and 'Set-Cookie' headers.

        :param header_value: The value of the header to be formatted.
        :param header_type: The type of the header ('Content-Security-Policy', 'Set-Cookie', or 'X-Akamai-Session-Info').
        :return: A formatted string with each directive or cookie separated by a newline.
        """
        if header_type not in ['Content-Security-Policy', 'Set-Cookie', 'X-Akamai-Session-Info']:
            return ""  # Return empty string for unsupported header types

        formatted_header = []

        # Split the header value into parts based on semicolons, preserving semicolons
        parts = header_value.split(';')
        parts = [part.strip() + ';' for part in parts if part.strip()]  # Re-add semicolons to parts

        for part in parts:
            while len(part) > 80:
                # Find the position to split: prioritize semicolons, then space if necessary
                split_pos = part.find(';', 0, 80)
                if split_pos != -1:
                    # Include semicolon in the output
                    formatted_header.append(part[:split_pos + 1])
                    part = part[split_pos + 1:].lstrip()
                else:
                    split_pos = part.find(' ', 0, 80)
                    if split_pos != -1:
                        # Include space in the output
                        formatted_header.append(part[:split_pos])
                        part = part[split_pos:].lstrip()
                    else:
                        # Split at 80 character boundary
                        formatted_header.append(part[:80])
                        part = part[80:].lstrip()

            formatted_header.append(part)

        return '\n'.join(formatted_header)

