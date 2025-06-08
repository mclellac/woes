from .style_utils import apply_font_size, apply_theme
from .constants import APP_ID, RESOURCE_PREFIX, GNOME_INTERFACE_SCHEMA, FONT_NAME_KEY, TEXT_SCALING_FACTOR_KEY
from .webscan_page import WebScanPage  # noqa: F401
from .nmap_page import NmapPage  # noqa: F401
from .http_page import HttpPage  # noqa: F401
from .dns_page import DNSPage  # noqa: F401
from gi.repository import Adw, Gdk, Gio, Gtk, GLib
import logging
import platform

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.style_manager = Adw.StyleManager.get_default()
        self._save_state_timer_id = 0

        self.gnome_interface_settings = None
        # self.gnome_a11y_settings = None # Placeholder for if a11y settings are needed later

        if platform.system() == "Linux":
            try:
                self.gnome_interface_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
                self.gnome_interface_settings.connect(f"changed::{FONT_NAME_KEY}", self._on_gnome_font_setting_changed)
                self.gnome_interface_settings.connect(
                    f"changed::{TEXT_SCALING_FACTOR_KEY}",
                    self._on_gnome_font_setting_changed)
                logging.debug(f"Successfully connected to GNOME interface settings schema: {GNOME_INTERFACE_SCHEMA}")
            except GLib.Error as e:  # noqa: F841
                logging.warning(
                    f"Could not connect to GNOME interface settings ({GNOME_INTERFACE_SCHEMA}): {e}. "
                    "System font integration will be limited."
                    )
            # Optionally, initialize and connect to GNOME_A11Y_SCHEMA here if needed for high-contrast etc.

        self.settings.connect("changed::theme-preference", self._on_theme_preference_setting_changed)
        self.settings.connect("changed::font-scaling-percentage", self._on_font_scaling_setting_changed)

        # Connect signals for saving window state
        self.connect("notify::default-width", self._schedule_save_window_state)
        self.connect("notify::default-height", self._schedule_save_window_state)
        self.connect("notify::maximized", self._schedule_save_window_state)
        self.connect("notify::fullscreened", self._schedule_save_window_state)

        try:
            self.setup_ui()
        except Exception as e:  # pylint: disable=broad-except # noqa: F841
            logging.exception("WoesWindow.__init__: Error during self.setup_ui()")

    def _on_gnome_font_setting_changed(self, _gnome_settings_obj, key_name: str):
        logging.debug(f"GNOME font setting changed: {key_name}. Re-applying font preferences.")
        # Call apply_font_size, which now internally handles system font integration
        apply_font_size(self.settings, "")  # Second arg is ignored

    def setup_ui(self):
        try:
            self.load_css()
        except Exception as e:  # pylint: disable=broad-except # noqa: F841
            logging.exception("WoesWindow.setup_ui: Error during self.load_css()")

        try:
            self.apply_preferences()
        except Exception as e:  # pylint: disable=broad-except # noqa: F841
            logging.exception("WoesWindow.setup_ui: Error during self.apply_preferences()")

        if self.switcher_title and self.stack:
            try:
                self.switcher_title.connect("notify::selected-page", self.on_page_switched)
            except Exception as e:  # pylint: disable=broad-except # noqa: F841
                logging.exception("WoesWindow.setup_ui: Error connecting switcher_title signal")
        else:
            logging.warning("switcher_title or stack not found during setup_ui.")

    def _on_theme_preference_setting_changed(self, settings, key):
        theme_pref = settings.get_string(key)
        apply_theme(self.style_manager, theme_pref)
        self.load_css()

    def _on_font_scaling_setting_changed(self, settings, key):  # pylint: disable=unused-argument
        # font_scale_pref_str is no longer needed from settings.get_string(key)
        # as apply_font_size will now use apply_system_font_preferences
        # which gets this value from app_settings directly.
        # The key argument is still passed by the 'changed' signal.
        logging.debug("App font scaling setting changed. Re-applying font preferences via apply_font_size.")
        apply_font_size(self.settings, "")  # Pass "" as the second argument, it will be ignored.

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
        except Exception as e:  # pylint: disable=broad-except
            logging.error(
                f"An unexpected error of type {type(e).__name__} occurred while loading CSS from {css_path}: {e}")

    def reload_css(self):
        logging.debug("Reloading CSS based on theme preference.")
        self.load_css()

    def apply_preferences(self):
        try:
            # font_scale_pref_str = self.settings.get_string("font-scaling-percentage") # No longer needed here
            theme_pref = self.settings.get_string("theme-preference")

            apply_font_size(self.settings, "")  # Second argument is ignored by the updated style_utils.apply_font_size
            apply_theme(self.style_manager, theme_pref)

            # Restore window size and state
            saved_width = self.settings.get_int("window-width")
            saved_height = self.settings.get_int("window-height")
            is_maximized = self.settings.get_boolean("window-is-maximized")

            if is_maximized:
                self.maximize()
            else:
                if saved_width > 0 and saved_height > 0:
                    self.set_default_size(saved_width, saved_height)
                # else, it will use the default size from the UI file or Adwaita defaults
            logging.debug(f"Applied window state: maximized={is_maximized}, width={saved_width}, height={saved_height}")

        except GLib.Error as e:
            logging.error(f"Error applying preferences (GSettings): {e}")
        except Exception as e:  # pylint: disable=broad-except
            logging.error(f"An unexpected error of type {type(e).__name__} occurred while applying preferences: {e}")

    def on_page_switched(self, _widget, _gparam):
        # selected_page = self.stack.get_visible_child() # F841
        logging.debug(f"Switched to page: {self.stack.get_visible_child_name()}")

    # Window state saving methods
    def _schedule_save_window_state(self, *args):
        if self._save_state_timer_id > 0:
            GLib.source_remove(self._save_state_timer_id)
        self._save_state_timer_id = GLib.timeout_add(300, self._save_window_state_actual)

    def _save_window_state_actual(self):
        self._save_state_timer_id = 0  # Reset timer ID

        current_gdk_state = self.get_surface().get_state()

        if current_gdk_state & Gdk.WindowState.MINIMIZED:
            logging.debug("Window is minimized, skipping state save.")
            return GLib.SOURCE_REMOVE  # Or False

        is_maximized = self.is_maximized() # bool(current_gdk_state & Gdk.WindowState.MAXIMIZED)
        is_fullscreen = self.is_fullscreen() # bool(current_gdk_state & Gdk.WindowState.FULLSCREEN)

        if is_maximized or is_fullscreen:
            logging.debug(f"Window is maximized or fullscreen. Saving state: is_maximized=True")
            self.settings.set_boolean("window-is-maximized", True)
        else:
            width = self.get_width() # Or self.get_default_width()
            height = self.get_height() # Or self.get_default_height()
            logging.debug(f"Window is in normal state. Saving state: width={width}, height={height}, is_maximized=False")
            self.settings.set_boolean("window-is-maximized", False)
            self.settings.set_int("window-width", width)
            self.settings.set_int("window-height", height)

        return GLib.SOURCE_REMOVE # Or False, to ensure it doesn't run again automatically
