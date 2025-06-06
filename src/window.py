import logging

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gdk, Gio, Gtk, GLib

# Import pages for Gtk.Template type registration, aliasing to avoid direct use conflicts
from .dns_page import DNSPage as _DNSPage_
from .http_page import HttpPage as _HttpPage_
from .nmap_page import NmapPage as _NmapPage_
from .webscan_page import WebScanPage as _WebScanPage_
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
        logging.debug("WoesWindow.__init__: Starting")
        super().__init__(**kwargs)
        logging.debug("WoesWindow.__init__: Initializing GSettings...")
        self.settings = Gio.Settings(schema_id=APP_ID)
        logging.debug("WoesWindow.__init__: GSettings initialized.")
        logging.debug("WoesWindow.__init__: Getting StyleManager...")
        self.style_manager = Adw.StyleManager.get_default()
        logging.debug("WoesWindow.__init__: StyleManager obtained.")

        logging.debug("WoesWindow.__init__: Connecting theme-preference setting change listener...")
        self.settings.connect("changed::theme-preference", self._on_theme_preference_setting_changed)
        logging.debug("WoesWindow.__init__: theme-preference listener connected.")
        logging.debug("WoesWindow.__init__: Connecting font-scaling-percentage setting change listener...")
        self.settings.connect("changed::font-scaling-percentage", self._on_font_scaling_setting_changed)
        logging.debug("WoesWindow.__init__: font-scaling-percentage listener connected.")

        logging.debug("WoesWindow.__init__: Calling setup_ui...")
        try:
            self.setup_ui()
        except Exception as e:
            logging.exception("WoesWindow.__init__: Error during self.setup_ui()")
        logging.debug("WoesWindow.__init__: Finished")

    def setup_ui(self):
        logging.debug("WoesWindow.setup_ui: Starting")

        logging.debug("WoesWindow.setup_ui: Calling load_css...")
        try:
            self.load_css()
        except Exception as e:
            logging.exception("WoesWindow.setup_ui: Error during self.load_css()")
        logging.debug("WoesWindow.setup_ui: load_css finished.")

        logging.debug("WoesWindow.setup_ui: Calling apply_preferences...")
        try:
            self.apply_preferences()
        except Exception as e:
            logging.exception("WoesWindow.setup_ui: Error during self.apply_preferences()")
        logging.debug("WoesWindow.setup_ui: apply_preferences finished.")

        if self.switcher_title and self.stack:
            logging.debug("WoesWindow.setup_ui: Connecting switcher_title signal...")
            try:
                self.switcher_title.connect("notify::selected-page", self.on_page_switched)
                logging.debug("WoesWindow.setup_ui: switcher_title signal connected.")
            except Exception as e:
                logging.exception("WoesWindow.setup_ui: Error connecting switcher_title signal")
        else:
            logging.warning("switcher_title or stack not found during setup_ui.")
        logging.debug("WoesWindow.setup_ui: Finished")

    def _on_theme_preference_setting_changed(self, settings, key):
        logging.debug(f"WoesWindow._on_theme_preference_setting_changed: '{key}' setting changed.")
        theme_pref = settings.get_string(key)
        apply_theme(self.style_manager, theme_pref)
        self.load_css() # Reload CSS to apply potential theme-specific styles (e.g., style-dark.css)

    def _on_font_scaling_setting_changed(self, settings, key):
        logging.debug(f"WoesWindow._on_font_scaling_setting_changed: '{key}' setting changed.")
        font_scale_pref_str = settings.get_string(key)
        # The _settings parameter in apply_font_size is currently unused but part of its signature
        apply_font_size(self.settings, font_scale_pref_str)

    def load_css(self):
        logging.debug("WoesWindow.load_css: Starting")
        # Determine which CSS file to use based on the current theme
        if self.style_manager.get_dark():
            css_file = "style-dark.css"
        else:
            css_file = "style.css"
        logging.debug(f"WoesWindow.load_css: Determined css_file: {css_file}")
        css_path = f"{RESOURCE_PREFIX}/{css_file}"
        logging.debug(f"WoesWindow.load_css: css_path: {css_path}")
        style_provider = Gtk.CssProvider()

        try:
            style_provider.load_from_resource(css_path)
            logging.debug("WoesWindow.load_css: Loaded style from resource.")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        except GLib.Error as e:
            logging.error(f"Failed to load CSS resource from {css_path}: {e}")
        except Exception as e:
            logging.error(f"An unexpected error of type {type(e).__name__} occurred while loading CSS from {css_path}: {e}")
        logging.debug("WoesWindow.load_css: Finished")

    def reload_css(self):
        logging.debug("WoesWindow.reload_css: Starting")
        logging.debug("Reloading CSS based on theme preference.")
        self.load_css()
        logging.debug("WoesWindow.reload_css: Finished")

    def apply_preferences(self):
        logging.debug("WoesWindow.apply_preferences: Starting")
        try:
            font_scale_pref_str = self.settings.get_string("font-scaling-percentage")
            logging.debug(f"WoesWindow.apply_preferences: Font scale preference from settings: {font_scale_pref_str}")
            theme_pref = self.settings.get_string("theme-preference")
            logging.debug(f"WoesWindow.apply_preferences: Theme preference from settings: {theme_pref}")

            apply_font_size(self.settings, font_scale_pref_str)
            apply_theme(self.style_manager, theme_pref)
        except GLib.Error as e:
            logging.error(f"Error applying preferences (GSettings): {e}")
        except Exception as e:
            logging.error(f"An unexpected error of type {type(e).__name__} occurred while applying preferences: {e}")
        logging.debug("WoesWindow.apply_preferences: Finished")

    def on_page_switched(self, _widget, _gparam):
        logging.debug("WoesWindow.on_page_switched: Starting")
        selected_page = self.stack.get_visible_child()
        logging.debug(f"Switched to page: {selected_page}")
        logging.debug("WoesWindow.on_page_switched: Finished")
