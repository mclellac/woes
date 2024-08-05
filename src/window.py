import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gdk, Gio
from . import http_util

@Gtk.Template(resource_path='/com/github/mclellac/WebOpsEvaluationSuite/gtk/window.ui')
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'WoesWindow'

    # Define template children with proper IDs
    http_page = Gtk.Template.Child()
    http_entry_row = Gtk.Template.Child('http_entry_row')
    pragma_switch_row = Gtk.Template.Child('pragma_switch_row')
    json_payloads_drop_down = Gtk.Template.Child('json_payloads_drop_down')
    http_column_view = Gtk.Template.Child('column_view')
    switcher_title = Gtk.Template.Child('switcher_title')
    stack = Gtk.Template.Child('stack')

    def __init__(self, **kwargs):
        """
        Initialize the WoesWindow class.
        """
        super().__init__(**kwargs)

        # Access the Adw.Switch from the pragma_switch_row
        self.pragma_switch = self.pragma_switch_row.get_child()

        # Connect signals to methods
        self.http_entry_row.connect("activate", self.on_fetch_headers)
        self.pragma_switch.connect("notify::active", self.on_use_akamai_pragma_toggled)
        self.json_payloads_drop_down.connect("notify::selected-item", self.on_json_payloads_drop_down_changed)
        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

        # Initialize style manager and settings
        self.style_manager = Adw.StyleManager.get_default()
        self.settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')

        # Apply font size and theme based on settings
        font_size = self.settings.get_int('font-size')
        self.apply_font_size(font_size)

        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        self.apply_theme(dark_theme_enabled)

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

    def on_fetch_headers(self, widget):
        """
        Fetch headers from the URL specified in the HTTP entry row.
        Updates the column view with the fetched headers.
        """
        url = self.http_entry_row.get_text().strip()
        use_akamai_pragma = self.pragma_switch.get_active()
        try:
            self.headers = http_util.HttpUtil.fetch_headers(url, use_akamai_pragma)
            if self.headers:
                self.update_column_view(self.headers)
                self.get_title_bar().set_subtitle(f"Fetched headers for: {url}")
            else:
                self.update_column_view(None)
                self.get_title_bar().set_subtitle(f"Failed to fetch headers for: {url}")
        except Exception as e:
            self.get_title_bar().set_subtitle(f"Error: {str(e)}")
            self.update_column_view(None)

    def on_use_akamai_pragma_toggled(self, widget):
        """
        Trigger header fetching when the Akamai pragma switch is toggled.
        """
        if self.http_entry_row.get_text().strip():
            self.on_fetch_headers(None)

    def on_json_payloads_drop_down_changed(self, widget, gparam):
        """
        Handle changes in the JSON payloads dropdown.
        """
        selected_item = self.json_payloads_drop_down.get_selected_item()
        if selected_item:
            payload = selected_item.get_label()
            self.get_title_bar().set_subtitle(f"Selected payload: {payload}")
            # Update settings or application state based on the selected payload
            self.settings.set_string('json-payload', payload)

    def on_page_switched(self, widget, gparam):
        """
        Handle page switches in the stack.
        """
        selected_page = self.stack.get_visible_child()
        if selected_page:
            page_name = selected_page.get_name()
            self.get_title_bar().set_title(f"Current Page: {page_name}")
            # Perform actions or updates based on the selected page

    def update_column_view(self, headers):
        """
        Update the column view with the provided headers.
        """
        # Assuming http_column_view uses a Gtk.ListStore or similar model
        model = self.http_column_view.get_model()
        if model:
            model.clear()
        else:
            model = Gtk.ListStore(str, str)  # Columns for header name and value
            self.http_column_view.set_model(model)

        if headers:
            for key, value in headers.items():
                row = [key, value]
                model.append(row)

