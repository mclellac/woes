import gi
import logging

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gdk, Gio
from urllib.parse import urlparse
from . import http_util
from .preferences import Preferences

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

@Gtk.Template(resource_path='/com/github/mclellac/WebOpsEvaluationSuite/gtk/window.ui')
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'WoesWindow'

    # Define template children with proper IDs
    http_page = Gtk.Template.Child()
    http_entry_row = Gtk.Template.Child('http_entry_row')
    http_list_box = Gtk.Template.Child('http_list_box')
    pragma_switch_row = Gtk.Template.Child('pragma_switch_row')
    json_payloads_drop_down = Gtk.Template.Child('json_payloads_drop_down')
    http_column_view = Gtk.Template.Child('http_column_view')
    switcher_title = Gtk.Template.Child('switcher_title')
    stack = Gtk.Template.Child('stack')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Connect signals
        self.http_entry_row.connect("entry-activated", self.on_http_entry_row_activated)
        self.http_entry_row.connect("apply", self.on_http_entry_row_activated)

        # Initialize style manager and settings
        self.style_manager = Adw.StyleManager.get_default()
        self.settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')

        self.settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')
        font_size = self.settings.get_int('font-size')
        Preferences.apply_font_size(self.settings, font_size)
        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        Preferences.apply_theme(self.settings, dark_theme_enabled)

    def on_realize(self, widget):
        super().on_realize()  # Ensure superclass behavior is preserved
        # Get the Adw.SwitchRow
        self.pragma_switch_row = self.http_list_box.get_row_at_index(1)
        self.pragma_switch_row.connect("notify::active", self.on_use_akamai_pragma_toggled)
        self.json_payloads_drop_down.connect("notify::selected-item", self.on_json_payloads_drop_down_changed)
        self.switcher_title.connect("notify::selected-page", self.on_page_switched)


    def on_http_entry_row_activated(self, entry_row):
        url = self.http_entry_row.get_text().strip()

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

    def fetch_headers(self, url: str, use_akamai_pragma: bool):
        try:
            return http_util.HttpUtil.fetch_headers(url, use_akamai_pragma)
        except Exception as e:
            logging.error(f"Error fetching headers: {e}")
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
            self.settings.set_string('json-payload', payload)

    def on_page_switched(self, widget, gparam):
        """
        Handle page switches in the stack.
        """
        selected_page = self.stack.get_visible_child()
        if selected_page:
            page_name = selected_page.get_name()

    def update_column_view(self, headers):
        # Remove existing columns safely
        for column in list(self.http_column_view.get_columns()):
            if column:
                self.http_column_view.remove_column(column)

        if headers and 'error' not in headers:
            # Create a list store and populate it with header items
            list_store = Gio.ListStore.new(http_util.HeaderItem)
            for key, value in headers.items():

                # Format headers based on their type
                if key == 'Content-Security-Policy':
                    value = http_util.HttpUtil.split_header_value(value, 'Content-Security-Policy')
                elif key == 'Set-Cookie':
                    value = http_util.HttpUtil.split_header_value(value, 'Set-Cookie')
                elif key == 'X-Akamai-Session-Info':
                    value = http_util.HttpUtil.split_header_value(value, 'X-Akamai-Session-Info')
                elif key in ['X-Cache', 'X-Cache-Remote']:
                    value = value.replace(' (', '\n(')

                list_store.append(http_util.HeaderItem(key, value))

            # Create a selection model from the list store
            selection_model = Gtk.SingleSelection.new(list_store)
            self.http_column_view.set_model(selection_model)

            # Define and add new columns
            header_name_column = Gtk.ColumnViewColumn.new("Header Name")
            header_value_column = Gtk.ColumnViewColumn.new("Header Value")

            # Create a SignalListItemFactory for each column
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
            # If no headers or error, set the model to None
            self.http_column_view.set_model(None)
            logging.debug("No headers found, setting column view model to None")
