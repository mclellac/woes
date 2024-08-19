import logging
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk

from . import HttpPage, NmapPage
from .constants import APP_ID, RESOURCE_PREFIX, THEME_DARK, THEME_LIGHT
from .style_utils import apply_font_size, apply_theme


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    http_page = Gtk.Template.Child("http_page")
    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.settings = Gio.Settings(schema_id=APP_ID)

        # Initialize pages
        self.http_page = HttpPage()
        self.stack.add(self.http_page)

        self.nmap_page = NmapPage()
        self.stack.add(self.nmap_page)

        self.switcher_title.connect("notify::selected-page", self.on_page_switched)

        # Initialize style manager
        self.style_manager = Adw.StyleManager.get_default()

        # Apply CSS and user preferences
        self.load_css()
        self.apply_preferences()

    def load_css(self):
        css_file = (
            THEME_DARK
            if self.style_manager.get_color_scheme() == Adw.ColorScheme.PREFER_DARK
            else THEME_LIGHT
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
        except Exception as e:
            print(f"Failed to load CSS from {css_path}: {e}")

    def apply_preferences(self):
        try:
            font_size = self.settings.get_int("font-size")
            dark_theme_enabled = self.settings.get_boolean("dark-theme")

            apply_font_size(self.settings, font_size)
            apply_theme(self.style_manager, dark_theme_enabled)
        except Exception as e:
            print(f"Error applying preferences: {e}")

    def on_page_switched(self, widget, gparam):
        selected_page = self.stack.get_visible_child()

