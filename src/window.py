# window.py
import gi
import logging
from urllib.parse import urlparse

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, Gdk, GLib
from . import HttpPage, NmapPage
from .preferences import Preferences
from .constants import APP_ID, RESOURCE_PREFIX, THEME_LIGHT, THEME_DARK

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

@Gtk.Template(resource_path=f'{RESOURCE_PREFIX}/window.ui')
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'WoesWindow'

    # Define template children with proper IDs
    http_page = Gtk.Template.Child('http_page')  # Ensure this ID matches the one in your UI file
    switcher_title = Gtk.Template.Child('switcher_title')
    stack = Gtk.Template.Child('stack')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Gio.Settings(schema_id=APP_ID)

        self.http_page = HttpPage()
        self.stack.add(self.http_page)

        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

        self.style_manager = Adw.StyleManager.get_default()
        self.load_css()

        # Apply app preferences
        font_size = self.settings.get_int('font-size')
        Preferences.apply_font_size(self.settings, font_size)
        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        Preferences.apply_theme(self.settings, dark_theme_enabled)

    def load_css(self):
        style_provider = Gtk.CssProvider()
        css_file = THEME_DARK if self.style_manager.get_dark() else THEME_LIGHT
        style_provider.load_from_resource(f'{RESOURCE_PREFIX}/{css_file}')
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def on_page_switched(self, widget, gparam):
        """
        Handle page switches in the stack.
        """
        selected_page = self.stack.get_visible_child()
        if isinstance(selected_page, HttpPage):
            # Perform actions specific to HttpPage if needed
            pass

