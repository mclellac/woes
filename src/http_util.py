import requests
from gi.repository import Gtk, Gio, GObject
from typing import Dict

class HeaderItem(GObject.Object):
    """Represents an HTTP header item."""
    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value

class HttpUtil:
    """Utility class for performing basic HTTP operations."""

    @staticmethod
    def fetch_headers(url: str, use_akamai_pragma: bool = False) -> Dict[str, str]:
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

    @staticmethod
    def format_headers(headers: Dict[str, str], column_view: Gtk.ColumnView) -> None:
        """Defines and updates columns for displaying headers."""
        # Clear existing columns
        for column in column_view.get_columns():
            column_view.remove_column(column)

        # Define column factories
        header_name_factory = Gtk.SignalListItemFactory()
        header_name_factory.connect("setup", HttpUtil.setup_factory)
        header_name_factory.connect("bind", HttpUtil.bind_name_factory)

        header_value_factory = Gtk.SignalListItemFactory()
        header_value_factory.connect("setup", HttpUtil.setup_factory)
        header_value_factory.connect("bind", HttpUtil.bind_value_factory)

        # Define columns
        header_name_column = Gtk.ColumnViewColumn(title="HTTP Header", factory=header_name_factory)
        header_value_column = Gtk.ColumnViewColumn(title="HTTP Value", factory=header_value_factory)

        # Append columns to the column view
        column_view.append_column(header_name_column)
        column_view.append_column(header_value_column)

        # Create and add HeaderItem instances to the list store
        for key, value in headers.items():
            header_item = HeaderItem(key, HttpUtil.split_header_value(value, key))
            # Add header_item to your ListStore or equivalent here

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
                    # If no semicolon, split at the last space within the 80-character limit
                    split_pos = part.rfind(' ', 0, 80)
                    if split_pos == -1:
                        split_pos = 80  # Fallback to 80 characters if no space is found
                    formatted_header.append(part[:split_pos])
                    part = part[split_pos:].lstrip()

            if part:
                formatted_header.append(part)

        # Join all formatted parts with newlines
        return '\n'.join(formatted_header).strip()

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
