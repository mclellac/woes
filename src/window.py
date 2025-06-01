import logging

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gdk, Gio, Gtk

# Import pages for Gtk.Template type registration, aliasing to avoid direct use conflicts
from . import DNSPage as _DNSPage_
from . import HttpPage as _HttpPage_
from . import NmapPage as _NmapPage_
from . import WebScanPage as _WebScanPage_
from .constants import APP_ID, RESOURCE_PREFIX
from .style_utils import apply_font_size, apply_theme

# Ensure custom page widgets are registered with the type system
# by referencing the aliased imports.
_ = _DNSPage_
_ = _HttpPage_
_ = _NmapPage_
_ = _WebScanPage_


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.style_manager = Adw.StyleManager.get_default()

        # Connect settings listener
        self.settings.connect("changed::dark-theme", self._on_dark_theme_setting_changed)

        self.setup_ui()

    def setup_ui(self):
        # load_css and apply_preferences will be called here.
        # apply_preferences reads initial settings and applies them.
        self.load_css()
        self.apply_preferences()
        if self.switcher_title and self.stack:  # Add checks for switcher_title
            self.switcher_title.connect("notify::selected-page", self.on_page_switched)
        else:
            logging.warning("switcher_title or stack not found during setup_ui.")

    def _on_dark_theme_setting_changed(self, settings, key):
        logging.debug("'%s' setting changed, reloading theme and CSS.", key)
        dark_theme_enabled = settings.get_boolean(key)
        apply_theme(self.style_manager, dark_theme_enabled)
        self.load_css()  # Reload CSS to match the potentially new theme

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
            logging.debug("Loaded CSS from %s", css_path)
        except Exception as e:
            logging.error("Failed to load CSS from %s: %s", css_path, e)

    def reload_css(self):
        logging.debug("Reloading CSS based on theme preference.")
        self.load_css()

    def apply_preferences(self):
        try:
            font_size = self.settings.get_int("font-size")
            dark_theme_enabled = self.settings.get_boolean("dark-theme")

            apply_font_size(self.settings, font_size)  # Assuming this works or is handled elsewhere

            # Apply theme directly on startup
            apply_theme(self.style_manager, dark_theme_enabled)
            # self.load_css()  # load_css is already called in setup_ui

        except Exception as e:
            logging.error("Error applying preferences: %s", e)

    def on_page_switched(self, widget, gparam):
        selected_page = self.stack.get_visible_child()
        logging.debug("Switched to page: %s", selected_page)
