import gi
import logging

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gdk, Gio

from . import http_util

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

        # Initialize columns
        self.header_name_column = None
        self.header_value_column = None

        # Connect signals
        self.http_entry_row.connect("entry-activated", self.on_http_entry_row_activated)
        self.http_entry_row.connect("apply", self.on_http_entry_row_activated)

        # Initialize style manager and settings
        self.style_manager = Adw.StyleManager.get_default()
        self.settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')

        # Apply font size and theme based on settings
        font_size = self.settings.get_int('font-size')
        self.apply_font_size(font_size)

        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        self.apply_theme(dark_theme_enabled)

    def on_realize(self, widget):
        super().on_realize()  # Ensure superclass behavior is preserved
        # Get the Adw.SwitchRow
        self.pragma_switch_row = self.http_list_box.get_row_at_index(1)
        self.pragma_switch_row.connect("notify::active", self.on_use_akamai_pragma_toggled)
        self.json_payloads_drop_down.connect("notify::selected-item", self.on_json_payloads_drop_down_changed)
        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

    def apply_font_size(self, font_size):
        """
        Apply the given font size to all widgets.
        """
        css_provider = Gtk.CssProvider()
        css = f"""
        * {{ font-size: {font_size}pt; }}  /* Apply to all widgets */
        """
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def apply_theme(self, dark_theme_enabled):
        """
        Apply the theme based on the dark-theme setting.
        """
        if dark_theme_enabled:
            self.style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        else:
            self.style_manager.set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)

    def on_http_entry_row_activated(self, entry_row):
        logging.debug("HTTP entry changed or Enter key pressed, fetching headers.")
        url = self.http_entry_row.get_text()
        logging.debug(f"HTTP entry row text: {url}")
        use_akamai_pragma = self.pragma_switch_row.get_active()

        headers = http_util.HttpUtil.fetch_headers(url, use_akamai_pragma)
        if headers and 'error' not in headers:
            self.update_column_view(headers)
        else:
            self.update_column_view(None)

        self.on_fetch_headers(None)

    def on_fetch_headers(self, entry):
        """
        Fetch headers from the URL specified in the HTTP entry row.
        Updates the column view with the fetched headers.
        """
        logging.debug("on_fetch_headers called")
        url = self.http_entry_row.get_text().strip()
        logging.debug(f"URL entered: {url}")
        use_akamai_pragma = self.pragma_switch_row.get_active()
        logging.debug(f"Akamai Pragma enabled: {use_akamai_pragma}")

        if url:
            try:
                self.headers = http_util.HttpUtil.fetch_headers(url, use_akamai_pragma)
                logging.debug(f"Fetched headers: {self.headers}")
                if self.headers and 'error' not in self.headers:
                    self.update_column_view(self.headers)
                    logging.debug(f"Updated titlebar with subtitle: Fetched headers for: {url}")
                else:
                    self.update_column_view(None)
                    logging.debug(f"Updated titlebar with subtitle: Failed to fetch headers for: {url}")
            except Exception as e:
                logging.error(f"Error fetching headers: {str(e)}")
                self.update_column_view(None)
                logging.debug(f"Updated titlebar with subtitle: Error: {str(e)}")
            else:
                logging.debug(f"Updated titlebar with subtitle: Fetched headers for: {url}")

    def on_use_akamai_pragma_toggled(self, widget, gparam):
        """
        Trigger header fetching when the Akamai pragma switch is toggled.
        """
        if self.http_entry_row.get_text().strip():
            logging.debug("Akamai pragma switch toggled, fetching headers.")
            self.on_fetch_headers(None)
            logging.debug("on_fetch_headers called after toggle")

    def on_json_payloads_drop_down_changed(self, widget, gparam):
        """
        Handle changes in the JSON payloads dropdown.
        """
        selected_item = self.json_payloads_drop_down.get_selected_item()
        if selected_item:
            payload = selected_item.get_label()
            logging.debug(f"Selected payload: {payload}")
            logging.debug(f"Updated titlebar with subtitle: Selected payload: {payload}")
            # Update settings or application state based on the selected payload
            self.settings.set_string('json-payload', payload)
            logging.debug(f"Settings updated with payload: {payload}")

    def on_page_switched(self, widget, gparam):
        """
        Handle page switches in the stack.
        """
        selected_page = self.stack.get_visible_child()
        if selected_page:
            page_name = selected_page.get_name()
            logging.debug(f"Page switched to: {page_name}")
            logging.debug(f"Updated titlebar with title: Current Page: {page_name}")
            # Perform actions or updates based on the selected page

    def update_column_view(self, headers):
        logging.debug("Updating column view")

        # Remove existing columns
        columns = self.http_column_view.get_columns()
        for column in columns:
            if column:
                self.http_column_view.remove_column(column)

        # Define and add new columns
        header_name_column = Gtk.ColumnViewColumn(title="HTTP Header", factory=http_util.HttpUtil.header_name_factory)
        header_value_column = Gtk.ColumnViewColumn(title="HTTP Value", factory=http_util.HttpUtil.header_value_factory)

        self.http_column_view.append_column(header_name_column)
        self.http_column_view.append_column(header_value_column)

        if headers and 'error' not in headers:
            # Create a list store and populate it with header items
            list_store = Gio.ListStore.new(http_util.HeaderItem)
            for key, value in headers.items():
                list_store.append(http_util.HeaderItem(key, value))

            # Create a selection model from the list store
            selection_model = Gtk.SingleSelection.new(list_store)
            self.http_column_view.set_model(selection_model)
        else:
            # If no headers or error, set the model to None
            self.http_column_view.set_model(None)

