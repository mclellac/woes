import logging
import platform # Added

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gdk, Gio, Gtk, GLib

from .dns_page import DNSPage
from .http_page import HttpPage
from .nmap_page import NmapPage
from .webscan_page import WebScanPage
from .constants import APP_ID, RESOURCE_PREFIX, GNOME_INTERFACE_SCHEMA, FONT_NAME_KEY, TEXT_SCALING_FACTOR_KEY # GNOME_A11Y_SCHEMA, HIGH_CONTRAST_KEY (if used later)
from .style_utils import apply_font_size, apply_theme # apply_system_font_preferences could be imported if called directly


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.style_manager = Adw.StyleManager.get_default()

        self.gnome_interface_settings = None
        # self.gnome_a11y_settings = None # Placeholder for if a11y settings are needed later

        if platform.system() == "Linux":
            try:
                self.gnome_interface_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
                self.gnome_interface_settings.connect(f"changed::{FONT_NAME_KEY}", self._on_gnome_font_setting_changed)
                self.gnome_interface_settings.connect(f"changed::{TEXT_SCALING_FACTOR_KEY}", self._on_gnome_font_setting_changed)
                logging.debug(f"Successfully connected to GNOME interface settings schema: {GNOME_INTERFACE_SCHEMA}")
            except GLib.Error as e:
                logging.warning(
                    f"Could not connect to GNOME interface settings ({GNOME_INTERFACE_SCHEMA}): {e}. "
                    "System font integration will be limited."
                )
            # Optionally, initialize and connect to GNOME_A11Y_SCHEMA here if needed for high-contrast etc.

        self.settings.connect("changed::theme-preference", self._on_theme_preference_setting_changed)
        self.settings.connect("changed::font-scaling-percentage", self._on_font_scaling_setting_changed)

        try:
            self.setup_ui()
        except Exception as e: # pylint: disable=broad-except
            logging.exception("WoesWindow.__init__: Error during self.setup_ui()")

    def _on_gnome_font_setting_changed(self, _gnome_settings_obj, key_name: str):
        logging.debug(f"GNOME font setting changed: {key_name}. Re-applying font preferences.")
        # Call apply_font_size, which now internally handles system font integration
        apply_font_size(self.settings, "") # Second arg is ignored

    def setup_ui(self):
        try:
            self.load_css()
        except Exception as e: # pylint: disable=broad-except
            logging.exception("WoesWindow.setup_ui: Error during self.load_css()")

        try:
            self.apply_preferences()
        except Exception as e: # pylint: disable=broad-except
            logging.exception("WoesWindow.setup_ui: Error during self.apply_preferences()")

        if self.switcher_title and self.stack:
            try:
                self.switcher_title.connect("notify::selected-page", self.on_page_switched)
            except Exception as e: # pylint: disable=broad-except
                logging.exception("WoesWindow.setup_ui: Error connecting switcher_title signal")
        else:
            logging.warning("switcher_title or stack not found during setup_ui.")

    def _on_theme_preference_setting_changed(self, settings, key):
        theme_pref = settings.get_string(key)
        apply_theme(self.style_manager, theme_pref)
        self.load_css()

    def _on_font_scaling_setting_changed(self, settings, key): # pylint: disable=unused-argument
        # font_scale_pref_str is no longer needed from settings.get_string(key)
        # as apply_font_size will now use apply_system_font_preferences
        # which gets this value from app_settings directly.
        # The key argument is still passed by the 'changed' signal.
        logging.debug("App font scaling setting changed. Re-applying font preferences via apply_font_size.")
        apply_font_size(self.settings, "") # Pass "" as the second argument, it will be ignored.

    def load_css(self):
        # Determine which CSS file to use based on the current theme
        if self.style_manager.get_dark():
            css_file = "style-dark.css"
        else:
            css_file = "style.css"
        css_path = f"{RESOURCE_PREFIX}/{css_file}"
        style_provider = Gtk.CssProvider()

        try:
            style_provider.load_from_resource(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        except GLib.Error as e:
            logging.error(f"Failed to load CSS resource from {css_path}: {e}")
        except Exception as e: # pylint: disable=broad-except
            logging.error(f"An unexpected error of type {type(e).__name__} occurred while loading CSS from {css_path}: {e}")

    def reload_css(self):
        logging.debug("Reloading CSS based on theme preference.")
        self.load_css()

    def apply_preferences(self):
        try:
            # font_scale_pref_str = self.settings.get_string("font-scaling-percentage") # No longer needed here
            theme_pref = self.settings.get_string("theme-preference")

            apply_font_size(self.settings, "") # Second argument is ignored by the updated style_utils.apply_font_size
            apply_theme(self.style_manager, theme_pref)
        except GLib.Error as e:
            logging.error(f"Error applying preferences (GSettings): {e}")
        except Exception as e: # pylint: disable=broad-except
            logging.error(f"An unexpected error of type {type(e).__name__} occurred while applying preferences: {e}")

    def on_page_switched(self, _widget, _gparam):
        selected_page = self.stack.get_visible_child()
        logging.debug(f"Switched to page: {selected_page}")
