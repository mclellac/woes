import gi
import logging
from urllib.parse import urlparse

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio
from . import HttpPage
from . import NmapPage
from .preferences import Preferences

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

@Gtk.Template(resource_path='/com/github/mclellac/WebOpsEvaluationSuite/gtk/window.ui')
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'WoesWindow'

    # Define template children with proper IDs
    http_page = Gtk.Template.Child('http_page')  # Update this to reflect the correct child ID
    switcher_title = Gtk.Template.Child('switcher_title')
    stack = Gtk.Template.Child('stack')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.http_page = HttpPage()  # Instantiate HttpPage
        self.stack.add(self.http_page)  # Add HttpPage to the stack

        # Connect signals
        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

        # Initialize style manager and settings
        self.style_manager = Adw.StyleManager.get_default()
        self.settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')

        font_size = self.settings.get_int('font-size')
        Preferences.apply_font_size(self.settings, font_size)
        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        Preferences.apply_theme(self.settings, dark_theme_enabled)

    def on_page_switched(self, widget, gparam):
        """
        Handle page switches in the stack.
        """
        selected_page = self.stack.get_visible_child()
        if isinstance(selected_page, HttpPage):
            # Perform actions specific to HttpPage if needed
            pass

