import logging

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gdk, Gio, Gtk

from . import DNSPage, HttpPage, NmapPage, WebScanPage
from .constants import APP_ID, RESOURCE_PREFIX
from .style_utils import apply_font_size, apply_theme


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        try:
            self.settings = Gio.Settings(schema_id=APP_ID)
            logging.debug("WoesWindow: GSettings loaded successfully.")
        except Exception as e:
            logging.error(f"WoesWindow: Failed to load GSettings: {e}")
            self.settings = None
        self.style_manager = Adw.StyleManager.get_default()
        self.setup_ui()

    def setup_ui(self):
        self.load_css()
        self.apply_preferences()
        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

    def load_css(self):
        # Determine which CSS file to use based on the current theme
        css_file = (
            "style-dark.css"
            if self.style_manager.get_color_scheme() == Adw.ColorScheme.PREFER_DARK
            else "style.css"
        )
        css_path = f"{RESOURCE_PREFIX}/{css_file}"
        style_provider = Gtk.CssProvider()

        try:
            style_provider.load_from_resource(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            logging.debug(f"Loaded CSS from {css_path}")
        except Exception as e:
            logging.error(f"Failed to load CSS from {css_path}: {e}")

    def reload_css(self):
        logging.debug("Reloading CSS based on theme preference.")
        self.load_css()

    def apply_preferences(self):
        if not self.settings:
            logging.warning("WoesWindow.apply_preferences: self.settings is None, skipping.")
            return
        try:
            font_size = self.settings.get_int("font-size")
            dark_theme_enabled = self.settings.get_boolean("dark-theme")

            apply_font_size(self.settings, font_size)
            apply_theme(self.style_manager, dark_theme_enabled)
        except Exception as e:
            logging.error(f"Error applying preferences: {e}")

    def on_page_switched(self, widget, gparam):
        selected_page = self.stack.get_visible_child()
        logging.debug(f"Switched to page: {selected_page}")
