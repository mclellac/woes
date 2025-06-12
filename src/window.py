"""Defines the main application window for Woes.

This module contains the :class:`WoesWindow` class, which is the primary
:class:`Adw.ApplicationWindow`. It handles UI setup, GSettings bindings for
window state and theme preferences, and integrates system font settings.
"""
import logging
import platform
from typing import Optional, Any  # Added Optional, Any
import gi
from gi.repository import Adw, Gdk, Gio, Gtk, GLib, GObject

from .constants import (
    APP_ID,
    RESOURCE_PREFIX,
    GNOME_INTERFACE_SCHEMA,
    FONT_NAME_KEY,
    TEXT_SCALING_FACTOR_KEY,
)
from .style_utils import apply_font_size, apply_theme
from .webscan_page import WebScanPage  # noqa: F401
from .nmap_page import NmapPage  # noqa: F401
from .http_page import HttpPage  # noqa: F401
from .dns_page import DNSPage  # noqa: F401


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    """Main application window for the Woes application.

    Manages the overall UI structure, including the main stack view and
    title switcher. It binds window properties (size, state) to :class:`Gio.Settings`
    and handles theme and font preference changes.

    Attributes
    ----------
    switcher_title : Adw.ViewSwitcherTitle
        The title switcher widget in the header bar.
    stack : Adw.ViewStack
        The main view stack that holds different pages of the application.
    main_error_banner : Adw.Banner
        A banner widget used to display application-wide error messages.
    toast_overlay : Adw.ToastOverlay
        An overlay for displaying non-intrusive toast messages.

    """

    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")
    main_error_banner = Gtk.Template.Child("main_error_banner")
    toast_overlay = Gtk.Template.Child("toast_overlay")

    def __init__(self, **kwargs: GObject.GObject):
        """Initialize the WoesWindow.

        :param kwargs: Keyword arguments passed to the :class:`Adw.ApplicationWindow`
                       constructor.
        :type kwargs: GObject.GObject
        """
        super().__init__(**kwargs)
        self.settings = Gio.Settings(schema_id=APP_ID)

        # Bind window state properties to GSettings keys
        # This allows GTK/Adwaita to automatically manage saving and restoring window state
        self.settings.bind("window-width", self,
                           "default-width", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-height", self,
                           "default-height", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-is-maximized", self,
                           "maximized", Gio.SettingsBindFlags.DEFAULT)
        logging.info(
            "Window state properties (default-width, default-height, maximized) bound to GSettings.")

        self.style_manager = Adw.StyleManager.get_default()
        self.gnome_interface_settings = None

        if platform.system() == "Linux":
            try:
                self.gnome_interface_settings = Gio.Settings.new(
                    GNOME_INTERFACE_SCHEMA)
                self.gnome_interface_settings.connect(
                    f"changed::{FONT_NAME_KEY}", self._on_gnome_font_setting_changed)
                self.gnome_interface_settings.connect(
                    f"changed::{TEXT_SCALING_FACTOR_KEY}", self._on_gnome_font_setting_changed)
                logging.debug(
                    "Successfully connected to GNOME interface settings schema: %s", GNOME_INTERFACE_SCHEMA)
            except GLib.Error as e:
                logging.warning(
                    "Could not connect to GNOME interface settings (%s): %s. System font integration will be limited.",
                    GNOME_INTERFACE_SCHEMA, e
                )

        self.settings.connect("changed::theme-preference",
                              self._on_theme_preference_setting_changed)
        self.settings.connect("changed::font-scaling-percentage",
                              self._on_font_scaling_setting_changed)

        try:
            self.setup_ui()
        except Exception as e:  # pylint: disable=broad-except
            logging.exception(
                "WoesWindow.__init__: Error during self.setup_ui(): %s", e)

        if self.main_error_banner:
            self.main_error_banner.connect(
                "button-clicked", self._on_main_error_banner_dismissed)
            self.hide_error()

    def _on_main_error_banner_dismissed(self, _banner: Optional[Adw.Banner] = None, _data: Optional[Any] = None):
        """Handle dismissal of the main error banner.

        This callback is connected to the 'button-clicked' signal of the
        `main_error_banner`.

        :param _banner: The banner widget that emitted the signal (unused).
        :type _banner: Adw.Banner, optional
        :param _data: Additional data passed with the signal (unused).
        :type _data: Any, optional
        """
        self.hide_error()

    def show_error(self, message: str):
        """Display a message in the main error banner.

        If the `main_error_banner` widget is available, its title is set to
        the provided message, an 'error' CSS class is added, and it's revealed.

        :param message: The error message to display.
        :type message: str
        """
        if self.main_error_banner:
            self.main_error_banner.set_title(message)
            self.main_error_banner.add_css_class("error")  # type: ignore
            self.main_error_banner.set_revealed(True)
            logging.info("Main error banner shown with message: %s", message)
        else:
            logging.warning(
                "main_error_banner not available to show message: %s", message)

    def hide_error(self):
        """Hides the main error banner and clears its title.

        If the `main_error_banner` widget is available, its 'error' CSS class
        is removed, it's hidden, and its title is cleared.
        """
        if self.main_error_banner:
            self.main_error_banner.remove_css_class("error")  # type: ignore
            self.main_error_banner.set_revealed(False)
            self.main_error_banner.set_title("")
            logging.info("Main error banner hidden.")
        else:
            logging.warning("main_error_banner not available to hide.")

    def _on_gnome_font_setting_changed(self, gnome_settings_obj: Gio.Settings, key_name: str):
        """Handle changes to GNOME's system font settings.

        This callback is connected to changes in `org.gnome.desktop.interface`
        for `font-name` and `text-scaling-factor`. It triggers re-application
        of font sizes based on application and system settings.

        :param gnome_settings_obj: The :class:`Gio.Settings` object for
                                   `org.gnome.desktop.interface` that changed.
        :type gnome_settings_obj: Gio.Settings
        :param key_name: The name of the GSettings key that changed
                         (e.g., 'font-name', 'text-scaling-factor').
        :type key_name: str
        """
        logging.debug(
            "GNOME font setting '%s' changed on object %s. Re-applying font preferences.",
            key_name,
            gnome_settings_obj
        )
        apply_font_size(self.settings)

    def setup_ui(self):
        """Set up the main UI components.

        This includes loading CSS, applying initial preferences, and connecting
        signals for UI elements like the page switcher.
        """
        try:
            self.load_css()
        except Exception as e:  # pylint: disable=broad-except
            logging.exception(
                "WoesWindow.setup_ui: Error during self.load_css(): %s", e)

        try:
            self.apply_preferences()
        except Exception as e:  # pylint: disable=broad-except
            logging.exception(
                "WoesWindow.setup_ui: Error during self.apply_preferences(): %s", e)

        if self.switcher_title and self.stack:
            try:
                self.switcher_title.connect(
                    "notify::selected-page", self.on_page_switched)
            except Exception as e:  # pylint: disable=broad-except
                logging.exception(
                    "WoesWindow.setup_ui: Error connecting switcher_title signal: %s", e)
        else:
            logging.warning(
                "switcher_title or stack not found during setup_ui.")

    def _on_theme_preference_setting_changed(self, settings: Gio.Settings, key: str):
        """Handle changes to the 'theme-preference' GSettings key.

        Applies the new theme and reloads CSS to ensure theme-specific styles
        are correctly applied.

        :param settings: The :class:`Gio.Settings` object for the application
                         (schema ID: :const:`APP_ID`) that changed.
        :type settings: Gio.Settings
        :param key: The name of the GSettings key that changed (should be 'theme-preference').
        :type key: str
        """
        theme_pref = settings.get_string(key)
        apply_theme(self.style_manager, theme_pref)
        self.load_css()  # Reload CSS to ensure theme-specific styles are applied

    def _on_font_scaling_setting_changed(self, settings: Gio.Settings, key: str):  # pylint: disable=unused-argument
        """Handle changes to the 'font-scaling-percentage' GSettings key.

        Triggers re-application of font sizes based on the new scaling factor.

        :param settings: The :class:`Gio.Settings` object for the application
                         (schema ID: :const:`APP_ID`) that changed.
        :type settings: Gio.Settings
        :param key: The name of the GSettings key that changed (should be 'font-scaling-percentage').
        :type key: str
        """
        # The actual scaling value is read directly by apply_font_size from app_settings.
        logging.debug(
            "App font scaling setting '%s' changed on %s. Re-applying font preferences via apply_font_size.",
            key,
            settings
        )
        apply_font_size(self.settings)

    def load_css(self):
        """Load the appropriate CSS file (style.css or style-dark.css)
        based on the current theme (light or dark).

        The CSS is loaded from GResources and applied to the application.
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
        except GLib.Error as e:
            logging.error(
                "Failed to load CSS resource from %s: %s", css_path, e)
        except Exception as e:  # pylint: disable=broad-except
            logging.error(
                "An unexpected error of type %s occurred while loading CSS from %s: %s",
                type(e).__name__, css_path, e,
            )

    def reload_css(self):
        """Reload CSS based on the current theme preference.

        This is a convenience method that calls :meth:`load_css`.
        """
        logging.debug("Reloading CSS based on theme preference.")
        self.load_css()

    def apply_preferences(self):
        """Apply stored font and theme preferences to the application.

        This method reads 'theme-preference' from GSettings, applies the
        font size using :func:`.style_utils.apply_font_size`, and applies
        the theme using :func:`.style_utils.apply_theme`.
        """
        try:
            theme_pref = self.settings.get_string("theme-preference")
            apply_font_size(self.settings)
            apply_theme(self.style_manager, theme_pref)
        except GLib.Error as e:
            logging.error("Error applying preferences (GSettings): %s", e)
        except Exception as e:  # pylint: disable=broad-except
            logging.error(
                "An unexpected error of type %s occurred while applying preferences: %s", type(e).__name__, e)

    def on_page_switched(self, widget: Adw.ViewSwitcherTitle, _gparam: GObject.ParamSpec):
        """Handle the page switch event from the :class:`Adw.ViewSwitcherTitle`.

        Logs the name of the newly visible child page in the main stack.

        :param widget: The :class:`Adw.ViewSwitcherTitle` that emitted the
                       'notify::selected-page' signal.
        :type widget: Adw.ViewSwitcherTitle
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        if self.stack:
            logging.debug(
                "Page switched via %s, new visible page: %s",
                widget,
                self.stack.get_visible_child_name()
            )
        else:
            logging.warning(
                "on_page_switched called but self.stack is not available.")

    def show_toast(self, title: str, priority: Adw.ToastPriority = Adw.ToastPriority.NORMAL, timeout: int = 2):
        """Display an :class:`Adw.Toast` message using the window's :class:`Adw.ToastOverlay`.

        :param title: The message to display in the toast.
        :type title: str
        :param priority: The priority of the toast (e.g., NORMAL, HIGH).
                         Defaults to :attr:`Adw.ToastPriority.NORMAL`.
        :type priority: Adw.ToastPriority, optional
        :param timeout: The duration in seconds for the toast to be visible.
                        Defaults to 2 seconds. A value of 0 means the toast
                        will remain until dismissed.
        :type timeout: int, optional
        """
        if not self.toast_overlay:
            logging.warning(
                "ToastOverlay not found, cannot display toast: %s", title)
            return

        toast = Adw.Toast.new(title)  # type: ignore
        toast.set_priority(priority)
        toast.set_timeout(timeout)
        # toast.set_action_name("app.example-action") # Optional: if toast needs an action button
        # toast.set_button_label("Example")         # Optional: label for the action button
        self.toast_overlay.add_toast(toast)  # type: ignore
        logging.info("Toast shown: %s (Priority: %s, Timeout: %s)",
                     title, priority, timeout)
