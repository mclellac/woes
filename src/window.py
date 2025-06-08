"""Defines the main application window for Woes.

This module contains the WoesWindow class, which is the primary Adw.ApplicationWindow.
It handles UI setup, GSettings bindings for window state and theme preferences,
and integrates system font settings.
"""
import logging
import platform
import gi
from gi.repository import Adw, Gdk, Gio, Gtk, GLib

from .constants import (
    APP_ID,
    RESOURCE_PREFIX,
    GNOME_INTERFACE_SCHEMA,
    FONT_NAME_KEY,
    TEXT_SCALING_FACTOR_KEY,
)
from .style_utils import apply_font_size, apply_theme
from .webscan_page import WebScanPage  # noqa: F401 # pylint: disable=unused-import
from .nmap_page import NmapPage  # noqa: F401 # pylint: disable=unused-import
from .http_page import HttpPage  # noqa: F401 # pylint: disable=unused-import
from .dns_page import DNSPage  # noqa: F401 # pylint: disable=unused-import


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    """Main application window for the Woes application.

    Manages the overall UI structure, including the main stack view and
    title switcher. It binds window properties (size, state) to GSettings
    and handles theme and font preference changes.
    """

    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        """Initialize the WoesWindow.

        Sets up GSettings bindings for window state, connects to system
        font and theme preference changes, and initializes the UI.

        Args:
        ----
            **kwargs: Keyword arguments for Adw.ApplicationWindow.

        """
        super().__init__(**kwargs)
        self.settings = Gio.Settings(schema_id=APP_ID)

        # Bind window state properties to GSettings keys
        # This allows GTK/Adwaita to automatically manage saving and restoring window state
        self.settings.bind("window-width", self, "default-width", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-height", self, "default-height", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-is-maximized", self, "maximized", Gio.SettingsBindFlags.DEFAULT)
        logging.info(
            "Window state properties (default-width, default-height, maximized) bound to GSettings."
        )
        self.style_manager = Adw.StyleManager.get_default()

        self.gnome_interface_settings = None
        # self.gnome_a11y_settings = None # Placeholder for if a11y settings are needed later

        if platform.system() == "Linux":
            try:
                self.gnome_interface_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
                self.gnome_interface_settings.connect(
                    f"changed::{FONT_NAME_KEY}", self._on_gnome_font_setting_changed
                )
                self.gnome_interface_settings.connect(
                    f"changed::{TEXT_SCALING_FACTOR_KEY}",
                    self._on_gnome_font_setting_changed,
                )
                logging.debug(
                    "Successfully connected to GNOME interface settings schema: %s",
                    GNOME_INTERFACE_SCHEMA,
                )
            except GLib.Error as _e:  # noqa: F841
                logging.warning(
                    "Could not connect to GNOME interface settings (%s): %s. "
                    "System font integration will be limited.",
                    GNOME_INTERFACE_SCHEMA,
                    _e,
                )
            # Optionally, initialize and connect to GNOME_A11Y_SCHEMA here if needed for high-contrast etc.

        self.settings.connect(
            "changed::theme-preference", self._on_theme_preference_setting_changed
        )
        self.settings.connect(
            "changed::font-scaling-percentage", self._on_font_scaling_setting_changed
        )

        try:
            self.setup_ui()
        except Exception as _e:  # pylint: disable=broad-except
            logging.exception("WoesWindow.__init__: Error during self.setup_ui()") # noqa: F841

    def _on_gnome_font_setting_changed(self, _gnome_settings_obj, key_name: str):
        logging.debug("GNOME font setting changed: %s. Re-applying font preferences.", key_name)
        # Call apply_font_size, which now internally handles system font integration
        apply_font_size(self.settings)

    def setup_ui(self):
        """Set up the main UI components.

        Loads CSS, applies initial preferences, and connects signals for UI elements
        like the page switcher.
        """
        try:
            self.load_css()
        except Exception as _e:  # pylint: disable=broad-except
            logging.exception("WoesWindow.setup_ui: Error during self.load_css()") # noqa: F841

        try:
            self.apply_preferences()
        except Exception as _e:  # pylint: disable=broad-except
            logging.exception("WoesWindow.setup_ui: Error during self.apply_preferences()") # noqa: F841

        if self.switcher_title and self.stack:
            try:
                self.switcher_title.connect("notify::selected-page", self.on_page_switched)
            except Exception as _e:  # pylint: disable=broad-except
                logging.exception("WoesWindow.setup_ui: Error connecting switcher_title signal") # noqa: F841
        else:
            logging.warning("switcher_title or stack not found during setup_ui.")

    def _on_theme_preference_setting_changed(self, settings: Gio.Settings, key: str):
        """Handle changes to the 'theme-preference' GSettings key.

        Applies the new theme and reloads CSS.

        Args:
        ----
            settings: The Gio.Settings object that changed.
            key: The name of the GSettings key that changed (should be "theme-preference").

        """
        theme_pref = settings.get_string(key)
        apply_theme(self.style_manager, theme_pref)
        self.load_css() # Reload CSS to ensure theme-specific styles are applied

    def _on_font_scaling_setting_changed(self, settings: Gio.Settings, key: str):  # pylint: disable=unused-argument
        """Handle changes to the 'font-scaling-percentage' GSettings key.

        Re-applies font preferences. The actual scaling value is read directly
        by `apply_font_size` from settings.

        Args:
        ----
            settings: The Gio.Settings object that changed (unused directly, but signals the change).
            key: The name of the GSettings key that changed (should be "font-scaling-percentage").

        """
        # font_scale_pref_str is no longer needed from settings.get_string(key)
        # as apply_font_size will now use apply_system_font_preferences
        # which gets this value from app_settings directly.
        # The key argument is still passed by the 'changed' signal.
        logging.debug(
            "App font scaling setting changed. Re-applying font preferences via apply_font_size."
        )
        apply_font_size(self.settings)

    def load_css(self):
        """Load the appropriate CSS file (light or dark) based on the current theme.

        Applies the CSS to the entire display using a Gtk.CssProvider.
        """
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
        except GLib.Error as e:  # noqa: F841
            logging.error("Failed to load CSS resource from %s: %s", css_path, e)
        except Exception as _e:  # pylint: disable=broad-except
            logging.error(
                "An unexpected error of type %s occurred while loading CSS from %s: %s",
                type(_e).__name__,
                css_path,
                _e,
            )

    def reload_css(self):
        """Reload CSS based on the current theme preference.

        Convenience method that calls `load_css`.
        """
        logging.debug("Reloading CSS based on theme preference.")
        self.load_css()

    def apply_preferences(self):
        """Apply stored font and theme preferences to the application.

        Retrieves settings from GSettings and calls appropriate utility functions
        to apply them.
        """
        try:
            # font_scale_pref_str = self.settings.get_string("font-scaling-percentage") # No longer needed here
            theme_pref = self.settings.get_string("theme-preference")

            apply_font_size(self.settings)
            apply_theme(self.style_manager, theme_pref)

        except GLib.Error as e:  # noqa: F841
            logging.error("Error applying preferences (GSettings): %s", e)
        except Exception as _e:  # pylint: disable=broad-except
            logging.error(
                "An unexpected error of type %s occurred while applying preferences: %s",
                type(_e).__name__,
                _e,
            )

    def on_page_switched(self, _widget: Adw.ViewSwitcherTitle, _gparam: GLib.ParamSpec):
        """Handle the page switch event from the Adw.ViewSwitcherTitle.

        Logs the name of the newly visible child in the Adw.ViewStack.

        Args:
        ----
            _widget: The Adw.ViewSwitcherTitle that emitted the signal.
            _gparam: The GLib.ParamSpec of the property that changed (unused).

        """
        logging.debug("Switched to page: %s", self.stack.get_visible_child_name())
