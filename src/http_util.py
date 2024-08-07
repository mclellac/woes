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
    header_name_factory = None
    header_value_factory = None
    header_name_column = None
    header_value_column = None

    @staticmethod
    def fetch_headers(url, use_akamai_pragma=False):
        """Fetches HTTP headers from a given URL.

        Args:
            url: The URL to fetch headers from.
            use_akamai_pragma: Whether to include Akamai Pragma headers in the request.

        Returns:
            A dictionary containing the fetched headers, or None on error.
        """
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
            response = requests.head(url, headers=headers)
            response.raise_for_status()  # Raise exception for non-2xx status codes
            return response.headers
        except requests.exceptions.RequestException as e:
            # Return error details or propagate exception
            return {'error': str(e)}

    @staticmethod
    def format_headers(headers: Dict[str, str], column_view: Gtk.ColumnView) -> None:
        """Defines and updates columns for displaying headers."""
        # Clear existing columns
        for column in column_view.get_columns():
            column_view.remove_column(column)

        # Define column factories if not already set
        if not HttpUtil.header_name_factory:
            HttpUtil.header_name_factory = Gtk.SignalListItemFactory()
            HttpUtil.header_name_factory.connect("setup", HttpUtil.setup_factory)
            HttpUtil.header_name_factory.connect("bind", HttpUtil.bind_name_factory)

        if not HttpUtil.header_value_factory:
            HttpUtil.header_value_factory = Gtk.SignalListItemFactory()
            HttpUtil.header_value_factory.connect("setup", HttpUtil.setup_factory)
            HttpUtil.header_value_factory.connect("bind", HttpUtil.bind_value_factory)

        # Define columns if not already set
        if not HttpUtil.header_name_column:
            HttpUtil.header_name_column = Gtk.ColumnViewColumn(title="HTTP Header", factory=HttpUtil.header_name_factory)

        if not HttpUtil.header_value_column:
            HttpUtil.header_value_column = Gtk.ColumnViewColumn(title="HTTP Value", factory=HttpUtil.header_value_factory)

        # Append columns to the column view
        column_view.append_column(HttpUtil.header_name_column)
        column_view.append_column(HttpUtil.header_value_column)

    @staticmethod
    def setup_factory(factory, list_item):
        """Setup factory for list item."""
        label = Gtk.Label()
        list_item.set_child(label)

    @staticmethod
    def bind_name_factory(factory, list_item):
        """Bind name data to the list item."""
        label = list_item.get_child()
        header_item = list_item.get_item()
        if header_item:
            label.set_text(header_item.key)

    @staticmethod
    def bind_value_factory(factory, list_item):
        """Bind value data to the list item."""
        label = list_item.get_child()
        header_item = list_item.get_item()
        if header_item:
            label.set_text(header_item.value)

