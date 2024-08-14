# window.py
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk

from . import HttpPage
from .constants import APP_ID, RESOURCE_PREFIX, THEME_DARK, THEME_LIGHT
from .preferences import Preferences

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    # Define template children with proper IDs
    http_page = Gtk.Template.Child("http_page")
    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logging.debug("Initializing WoesWindow")

        self.settings = Gio.Settings(schema_id=APP_ID)

        # Initialize pages
        self.http_page = HttpPage()
        self.stack.add(self.http_page)
        logging.debug("HttpPage added to stack")

        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

        self.style_manager = Adw.StyleManager.get_default()

        # Load and apply CSS based on the current theme
        self.load_css()

        # Apply app preferences
        self.apply_preferences()

    def load_css(self):
        """Load and apply the appropriate CSS based on the current theme."""
        style_provider = Gtk.CssProvider()
        css_file = THEME_DARK if self.style_manager.get_dark() else THEME_LIGHT
        css_path = f"{RESOURCE_PREFIX}/{css_file}"

        try:
            style_provider.load_from_resource(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            logging.debug(f"CSS loaded and applied from: {css_path}")
        except Exception as e:
            logging.error(f"Failed to load CSS from {css_path}: {e}")

    def apply_preferences(self):
        """Apply user preferences such as font size and theme."""
        try:
            font_size = self.settings.get_int("font-size")
            Preferences.apply_font_size(self.settings, font_size)
            logging.debug(f"Font size set to: {font_size}")

            dark_theme_enabled = self.settings.get_boolean("dark-theme")
            Preferences.apply_theme(self.settings, dark_theme_enabled)
            logging.debug(f"Dark theme enabled: {dark_theme_enabled}")
        except Exception as e:
            logging.error(f"Error applying preferences: {e}")

    def on_page_switched(self, widget, gparam):
        """Handle page switches in the stack."""
        selected_page = self.stack.get_visible_child()
        logging.debug(f"Page switched to: {selected_page.__class__.__name__}")
        if isinstance(selected_page, HttpPage):
            # Perform actions specific to HttpPage if needed
            pass
