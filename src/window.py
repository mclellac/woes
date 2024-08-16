import logging
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource

from . import HttpPage
from .nmap_page import NmapPage
from .constants import APP_ID, RESOURCE_PREFIX, THEME_DARK, THEME_LIGHT
from .style_utils import apply_font_size, apply_theme, apply_source_style_scheme

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

        # Initialize style managers
        self.style_manager = Adw.StyleManager.get_default()
        self.source_style_manager = GtkSource.StyleSchemeManager.get_default()

        # Apply CSS and user preferences
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
            color_scheme = self.settings.get_string("source-style-scheme")
            logging.debug(f"Loaded source style scheme: {color_scheme}")

            if color_scheme not in ["Adwaita", "Adwaita-dark"]:
                color_scheme = color_scheme.lower()

            current_scheme = self.nmap_page.source_buffer.get_style_scheme()
            if current_scheme is None or current_scheme.get_id().lower() != color_scheme:
                scheme = self.source_style_manager.get_scheme(color_scheme)
                if scheme:
                    logging.debug(f"Applying scheme: {scheme.get_id()} to source view.")
                    self.nmap_page.apply_style_scheme_to_source_view(scheme.get_id())
                else:
                    logging.warning(f"Scheme '{color_scheme}' not found. Reverting to 'Adwaita'.")
                    self.nmap_page.apply_style_scheme_to_source_view("Adwaita")

        except Exception as e:
            logging.error(f"Error applying preferences: {e}")


    def on_page_switched(self, widget, gparam):
        selected_page = self.stack.get_visible_child()
        logging.debug(f"Page switched to: {selected_page.__class__.__name__}")
        if isinstance(selected_page, HttpPage):
            pass
