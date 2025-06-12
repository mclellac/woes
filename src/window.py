"""Defines the main application window class, :class:`WoesWindow`, for the Woes application.

This module is responsible for the primary user interface structure, including
the main application window (:class:`Adw.ApplicationWindow`). The :class:`WoesWindow`
class manages page navigation via an :class:`Adw.ViewStack`, handles application-wide
actions and settings (like theme and font preferences via :class:`Gio.Settings`),
and integrates system-level settings where appropriate (e.g., GNOME desktop font).
It also provides mechanisms for displaying global notifications like error banners
and toasts.
"""
import logging
import platform
from typing import Optional, Any
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

# Page imports: These pages are registered as Gtk.Template children within the window.ui
# and are instantiated by Gtk.Builder. They are imported here to ensure their
# GType is known to the GObject type system when the UI file is parsed.
# The `# noqa: F401` comments suppress unused import warnings, as they are not
# directly referenced in this Python file but are essential for the .ui template.
from .webscan_page import WebScanPage  # noqa: F401
from .nmap_page import NmapPage  # noqa: F401
from .http_page import HttpPage  # noqa: F401
from .dns_page import DNSPage  # noqa: F401


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

logger = logging.getLogger(__name__)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    """Main application window for the Woes application.

    Manages the overall UI structure, including the main view stack for different
    application pages (DNS, HTTP, Nmap, WebScan) and a title switcher in the
    header bar. It binds window properties (size, maximized state) to
    :class:`Gio.Settings` for persistence across sessions and handles dynamic
    application of theme and font preferences. It also provides global UI
    feedback mechanisms like an error banner and toast notifications.

    :ivar switcher_title: The :class:`Adw.ViewSwitcherTitle` widget in the header bar,
                          linked to the main view stack.
    :vartype switcher_title: Adw.ViewSwitcherTitle
    :ivar stack: The :class:`Adw.ViewStack` that holds the different application pages.
    :vartype stack: Adw.ViewStack
    :ivar main_error_banner: An :class:`Adw.Banner` used to display application-wide
                             error messages at the top of the window.
    :vartype main_error_banner: Adw.Banner
    :ivar toast_overlay: An :class:`Adw.ToastOverlay` for displaying non-intrusive,
                         short-lived status messages (toasts).
    :vartype toast_overlay: Adw.ToastOverlay
    """

    __gtype_name__ = "WoesWindow"

    # --- Template Children ---
    switcher_title: Adw.ViewSwitcherTitle = Gtk.Template.Child() # type: ignore
    stack: Adw.ViewStack = Gtk.Template.Child() # type: ignore
    main_error_banner: Adw.Banner = Gtk.Template.Child() # type: ignore
    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child() # type: ignore

    def __init__(self, **kwargs: Any) -> None:
        """Initializes the WoesWindow.

        Sets up the main application window, binds its state (size, maximized)
        to :class:`Gio.Settings` for persistence. Initializes the style manager
        and connects listeners for system font changes (on Linux/GNOME) and
        application-specific GSettings for theme and font scaling. Calls
        :meth:`setup_ui` to build the rest of the UI and connect signals.

        :param kwargs: Keyword arguments passed to the :class:`Adw.ApplicationWindow`
                       constructor.
        :type kwargs: Any
        :return: None
        :rtype: None
        """
        super().__init__(**kwargs)
        self.init_template() # Initialize Gtk.Template children

        self.settings = Gio.Settings(schema_id=APP_ID)

        # Bind window state properties to GSettings for persistence.
        # GTK/Adwaita handles saving/restoring these automatically.
        self.settings.bind("window-width", self, "default-width", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-height", self, "default-height", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-is-maximized", self, "maximized", Gio.SettingsBindFlags.DEFAULT)
        logging.info("Window state properties bound to GSettings.")

        self.style_manager = Adw.StyleManager.get_default()
        self.gnome_interface_settings: Optional[Gio.Settings] = None

        # Connect to GNOME system font settings if on Linux for better integration.
        if platform.system() == "Linux":
            try:
                self.gnome_interface_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
                self.gnome_interface_settings.connect(f"changed::{FONT_NAME_KEY}", self._on_gnome_font_setting_changed)
                self.gnome_interface_settings.connect(f"changed::{TEXT_SCALING_FACTOR_KEY}", self._on_gnome_font_setting_changed)
                logging.debug("Connected to GNOME interface settings schema: %s", GNOME_INTERFACE_SCHEMA)
            except GLib.Error as e:
                logging.warning("Could not connect to GNOME interface settings (%s): %s. "
                                "System font integration will be limited.", GNOME_INTERFACE_SCHEMA, e)

        # Connect to application-specific settings changes.
        self.settings.connect("changed::theme-preference", self._on_theme_preference_setting_changed)
        self.settings.connect("changed::font-scaling-percentage", self._on_font_scaling_setting_changed)

        try:
            self.setup_ui()
        except Exception as e:  # pylint: disable=broad-except
            logging.exception("WoesWindow.__init__: Error during self.setup_ui(): %s", e)

        if self.main_error_banner: # Ensure banner exists before connecting
            self.main_error_banner.connect("button-clicked", self._on_main_error_banner_dismissed)
            self.hide_error() # Start with the error banner hidden
        else:
            logging.warning("WoesWindow.__init__: 'main_error_banner' template child not found.")


    def _on_main_error_banner_dismissed(self, _banner: Optional[Adw.Banner] = None, _data: Optional[Any] = None) -> None:
        """Handles dismissal of the main error banner.

        This callback is connected to the 'button-clicked' signal of the
        `main_error_banner` (if the banner has a button) or its 'dismissed' signal.

        :param _banner: The banner widget that emitted the signal (unused).
        :type _banner: Adw.Banner, optional
        :param _data: Additional data passed with the signal (unused).
        :type _data: Any, optional
        :return: None
        :rtype: None
        """
        self.hide_error()

    def show_error(self, message: str) -> None:
        """Displays a message in the main error banner at the top of the window.

        If the `main_error_banner` widget is available (resolved from the UI template),
        its title is set to the provided message, an 'error' CSS class is applied for
        styling (if defined in CSS), and the banner is revealed to the user.

        :param message: The error message to display in the banner.
        :type message: str
        :return: None
        :rtype: None
        """
        if self.main_error_banner:
            self.main_error_banner.set_title(message)
            self.main_error_banner.add_css_class("error")
            self.main_error_banner.set_revealed(True)
            logging.info("Main error banner shown with message: %s", message)
        else:
            logging.warning("main_error_banner not available to show message: %s. "
                            "Consider using a dialog for critical errors.", message)

    def hide_error(self) -> None:
        """Hides the main error banner and clears its title.

        If the `main_error_banner` widget is available, its 'error' CSS class
        is removed, it's hidden from view, and its title is cleared to ensure
        it doesn't display stale messages if shown again.

        :return: None
        :rtype: None
        """
        if self.main_error_banner:
            self.main_error_banner.remove_css_class("error")
            self.main_error_banner.set_revealed(False)
            self.main_error_banner.set_title("") # Clear title when hiding
            logging.debug("Main error banner hidden.") # Changed to debug as this is a common operation
        # else:
            # logging.warning("main_error_banner not available to hide.") # Can be noisy if called often

    def _on_gnome_font_setting_changed(self, settings_obj: Gio.Settings, key_name: str) -> None:
        """Handles changes to GNOME's system font settings (font name or text scaling).

        This callback is connected to changes in the `org.gnome.desktop.interface`
        GSettings schema for the `font-name` and `text-scaling-factor` keys.
        It triggers a re-application of font sizes for the application, respecting
        both system and application-specific scaling preferences.

        :param settings_obj: The :class:`Gio.Settings` object for `org.gnome.desktop.interface`
                             that changed.
        :type settings_obj: Gio.Settings
        :param key_name: The name of the GSettings key that changed (e.g., 'font-name').
        :type key_name: str
        :return: None
        :rtype: None
        """
        logging.debug("GNOME font setting '%s' changed on GSettings object %s. "
                      "Re-applying application font preferences.", key_name, settings_obj)
        apply_font_size(self.settings) # Re-apply based on app settings, which considers system settings

    def setup_ui(self) -> None:
        """Sets up the main UI components after `__init__`.

        This includes loading application-specific CSS, applying initial theme and
        font preferences, and connecting signals for UI elements like the page switcher
        if they were not set up during `init_template`.

        :return: None
        :rtype: None
        """
        try:
            self.load_css()
        except Exception as e:  # pylint: disable=broad-except
            logging.exception("WoesWindow.setup_ui: Error during self.load_css(): %s", e)

        try:
            self.apply_preferences() # Apply theme and font size initially
        except Exception as e:  # pylint: disable=broad-except
            logging.exception("WoesWindow.setup_ui: Error during self.apply_preferences(): %s", e)

        # Connect switcher title to stack if both are resolved from template
        if self.switcher_title and self.stack:
            try:
                # This signal ensures the title updates when the stack page changes
                self.switcher_title.connect("notify::selected-page", self.on_page_switched)
            except Exception as e:  # pylint: disable=broad-except
                logging.exception("WoesWindow.setup_ui: Error connecting switcher_title 'notify::selected-page' signal: %s", e)
        else:
            logging.warning("WoesWindow.setup_ui: 'switcher_title' or 'stack' template child not found. "
                            "Page switching UI might be incomplete.")

    def _on_theme_preference_setting_changed(self, settings: Gio.Settings, key: str) -> None:
        """Handles changes to the 'theme-preference' GSettings key.

        Applies the new theme using :func:`~.style_utils.apply_theme` and then
        reloads the application's CSS to ensure any theme-specific styles are
        correctly applied or updated.

        :param settings: The :class:`Gio.Settings` object for the application
                         (schema ID: :const:`APP_ID`) that changed.
        :type settings: Gio.Settings
        :param key: The name of the GSettings key that changed (should be 'theme-preference').
        :type key: str
        :return: None
        :rtype: None
        """
        theme_pref = settings.get_string(key)
        logging.info("Application theme preference changed to: '%s'. Applying.", theme_pref)
        apply_theme(self.style_manager, theme_pref)
        self.load_css()  # Reload CSS to apply potentially theme-specific styles

    def _on_font_scaling_setting_changed(self, settings: Gio.Settings, key: str) -> None: # pylint: disable=unused-argument
        """Handles changes to the 'font-scaling-percentage' GSettings key.

        Triggers re-application of font sizes for the entire application using
        :func:`~.style_utils.apply_font_size`, which considers both system and
        application-specific scaling.

        :param settings: The :class:`Gio.Settings` object for the application
                         (schema ID: :const:`APP_ID`) that changed.
        :type settings: Gio.Settings
        :param key: The name of the GSettings key that changed (should be 'font-scaling-percentage', unused).
        :type key: str
        :return: None
        :rtype: None
        """
        logging.debug("Application font scaling setting '%s' changed. Re-applying font preferences.", key)
        # apply_font_size reads the current value from self.settings
        apply_font_size(self.settings)

    def load_css(self) -> None:
        """Loads the appropriate CSS file (`style.css` or `style-dark.css`)
        based on the current Adwaita theme (light or dark).

        The CSS is loaded from GResources (compiled into the application) and
        applied globally to the default Gdk.Display. This allows for custom
        styling beyond what Adwaita provides.

        :return: None
        :rtype: None
        """
        css_file = "style-dark.css" if self.style_manager.get_dark() else "style.css"
        css_path = f"{RESOURCE_PREFIX}/{css_file}"
        logging.info("Loading CSS from resource: %s (Dark mode: %s)", css_path, self.style_manager.get_dark())

        css_provider = Gtk.CssProvider()
        try:
            css_provider.load_from_resource(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), # Apply to the default display
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION, # Ensure app CSS can override theme defaults
            )
            logging.debug("CSS successfully loaded and applied from %s.", css_path)
        except GLib.Error as e: # Handles errors like resource not found
            logging.error("Failed to load CSS resource from %s: %s", css_path, e)
        except Exception as e:  # pylint: disable=broad-except # Catch any other unexpected error
            logging.error("An unexpected error of type %s occurred while loading CSS from %s: %s",
                          type(e).__name__, css_path, e, exc_info=True)


    def reload_css(self) -> None:
        """Reloads CSS based on the current theme preference.

        This is a convenience method that simply calls :meth:`load_css`.
        It can be used if there's a need to explicitly re-apply CSS rules.

        :return: None
        :rtype: None
        """
        logging.debug("Reloading CSS explicitly.")
        self.load_css()

    def apply_preferences(self) -> None:
        """Applies stored font and theme preferences to the application.

        This method reads the 'theme-preference' from GSettings, applies the
        font size using :func:`~.style_utils.apply_font_size` (which considers
        both system and app settings), and applies the theme using
        :func:`~.style_utils.apply_theme`. It's typically called during
        initialization.

        :return: None
        :rtype: None
        """
        try:
            theme_pref = self.settings.get_string("theme-preference")
            logging.info("Applying initial preferences: Theme='%s'", theme_pref)
            apply_font_size(self.settings) # Applies based on current GSettings value
            apply_theme(self.style_manager, theme_pref) # Applies based on current GSettings value
        except GLib.Error as e: # Catch GSettings access errors
            logging.error("Error applying preferences from GSettings: %s", e)
        except Exception as e:  # pylint: disable=broad-except
            logging.error("An unexpected error of type %s occurred while applying preferences: %s",
                          type(e).__name__, e, exc_info=True)

    def on_page_switched(self, _widget: Adw.ViewSwitcherTitle, _gparam: GObject.ParamSpec) -> None:
        """Handles the 'notify::selected-page' signal from the :class:`Adw.ViewSwitcherTitle`.

        Logs the name of the newly visible child page in the main :class:`Adw.ViewStack`.

        :param _widget: The :class:`Adw.ViewSwitcherTitle` that emitted the signal (unused).
        :type _widget: Adw.ViewSwitcherTitle
        :param _gparam: The :class:`GObject.ParamSpec` of the 'selected-page' property (unused).
        :type _gparam: GObject.ParamSpec
        :return: None
        :rtype: None
        """
        if self.stack and self.stack.get_visible_child_name():
            logging.debug("Page switched to: %s", self.stack.get_visible_child_name())
        else:
            logging.warning("on_page_switched called but self.stack or visible child name is not available.")

    def show_toast(self, title: str, priority: Adw.ToastPriority = Adw.ToastPriority.NORMAL, timeout: int = 2) -> None:
        """Displays an :class:`Adw.Toast` message using the window's :class:`Adw.ToastOverlay`.

        :param title: The message to display in the toast.
        :type title: str
        :param priority: The priority of the toast (e.g., :attr:`Adw.ToastPriority.NORMAL`,
                         :attr:`Adw.ToastPriority.HIGH`). Defaults to :attr:`Adw.ToastPriority.NORMAL`.
        :type priority: Adw.ToastPriority
        :param timeout: The duration in seconds for the toast to be visible.
                        Defaults to 2 seconds. A value of 0 means the toast
                        will remain until dismissed by other means (if any).
        :type timeout: int
        :return: None
        :rtype: None
        """
        if not self.toast_overlay:
            logging.warning("ToastOverlay not found, cannot display toast: %s", title)
            return

        toast = Adw.Toast.new(title)
        toast.set_priority(priority)
        toast.set_timeout(timeout)
        # Example for future use:
        # toast.set_action_name("app.example-action")
        # toast.set_button_label("Undo")
        self.toast_overlay.add_toast(toast)
        logging.info("Toast shown: '%s' (Priority: %s, Timeout: %ds)", title, priority.value_nick, timeout)

```
