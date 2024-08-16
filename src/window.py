import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource

from . import HttpPage
from .nmap_page import NmapPage
from .constants import APP_ID, RESOURCE_PREFIX, THEME_DARK, THEME_LIGHT
from .preferences import Preferences

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)

@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

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

        self.nmap_page = NmapPage()
        self.stack.add(self.nmap_page)
        logging.debug("NmapPage added to stack")

        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

        self.style_manager = Adw.StyleManager.get_default()

        self.load_css()
        self.apply_preferences()

    def load_css(self):
        style_provider = Gtk.CssProvider()
        css_file = THEME_DARK if self.style_manager.get_color_scheme() == Adw.ColorScheme.PREFER_DARK else THEME_LIGHT
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
        try:
            font_size = self.settings.get_int("font-size")
            Preferences.apply_font_size(self.settings, font_size)
            logging.debug(f"Font size set to: {font_size}")

            dark_theme_enabled = self.settings.get_boolean("dark-theme")
            Preferences.apply_theme(self.settings, dark_theme_enabled)
            logging.debug(f"Dark theme enabled: {dark_theme_enabled}")

            color_scheme = self.settings.get_string("color-scheme")
            logging.debug(f"Applying color scheme across the application: {color_scheme}")
            self.apply_color_scheme(color_scheme)

            # Connect the preferences window to handle color scheme changes
            preferences = Preferences(main_window=self)
            preferences.connect("color-scheme-changed", self.on_color_scheme_changed)

        except Exception as e:
            logging.error(f"Error applying preferences: {e}")

    def apply_color_scheme(self, scheme_name: str):
        logging.debug(f"Applying color scheme: {scheme_name}")
        style_manager = GtkSource.StyleSchemeManager.get_default()

        if scheme_name not in ["Adwaita", "Adwaita-dark"]:
            normalized_scheme_name = scheme_name.lower().replace(" ", "-")
        else:
            normalized_scheme_name = scheme_name

        style_scheme = style_manager.get_scheme(normalized_scheme_name)

        if style_scheme:
            logging.debug(f"Color scheme found: {normalized_scheme_name}")
            if hasattr(self.nmap_page, 'update_nmap_color_scheme'):
                self.nmap_page.update_nmap_color_scheme(style_scheme)
        else:
            logging.error(f"Color scheme '{normalized_scheme_name}' not found. Available schemes: {style_manager.get_scheme_ids()}")

    def on_color_scheme_changed(self, _, scheme_name: str):
        logging.debug(f"Color scheme changed to: {scheme_name}")
        self.apply_color_scheme(scheme_name)

    def on_page_switched(self, widget, gparam):
        selected_page = self.stack.get_visible_child()
        logging.debug(f"Page switched to: {selected_page.__class__.__name__}")
        if isinstance(selected_page, HttpPage):
            pass
