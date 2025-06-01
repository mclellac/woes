import logging
import gi

# GTK version requirements
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')

# Now import GTK libraries
# pylint: disable=wrong-import-position
from gi.repository import Adw, Gdk, Gio, Gtk, GLib  # Added GLib for load_css exception

# Local application imports
# Import pages for Gtk.Template type registration, aliasing to avoid direct use conflicts
# pylint: disable=wrong-import-position
from .dns_page import DNSPage as _DNSPage_
# pylint: disable=wrong-import-position
from .http_page import HttpPage as _HttpPage_
# pylint: disable=wrong-import-position
from .nmap_page import NmapPage as _NmapPage_
# pylint: disable=wrong-import-position
from .webscan_page import WebScanPage as _WebScanPage_
# pylint: disable=wrong-import-position
from .constants import APP_ID, RESOURCE_PREFIX
# pylint: disable=wrong-import-position
from .style_utils import apply_font_size, apply_theme

# Initialize logger and other module-level assignments after all imports
logger = logging.getLogger(__name__)

# Ensure custom page widgets are registered with the type system
# by referencing the aliased imports. This ensures Gtk.Template can find them.
_ = _DNSPage_
_ = _HttpPage_
_ = _NmapPage_
_ = _WebScanPage_
# The lines above also suppress W0611 (unused-import) for _DNSPage_, etc. in Pylint.


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")

    def __init__(self, **kwargs):
        logger.debug("WoesWindow.__init__: Starting")
        super().__init__(**kwargs)
        logger.debug("WoesWindow.__init__: Initializing GSettings...")
        self.settings = Gio.Settings(schema_id=APP_ID)
        logger.debug("WoesWindow.__init__: GSettings initialized.")
        logger.debug("WoesWindow.__init__: Getting StyleManager...")
        self.style_manager = Adw.StyleManager.get_default()
        logger.debug("WoesWindow.__init__: StyleManager obtained.")

        logger.debug("WoesWindow.__init__: Connecting dark-theme setting change listener...")
        self.settings.connect("changed::dark-theme", self._on_dark_theme_setting_changed)
        logger.debug("WoesWindow.__init__: dark-theme listener connected.")

        logger.debug("WoesWindow.__init__: Calling setup_ui...")
        try:
            self.setup_ui()
        except Exception:  # Catching general Exception during critical UI setup
            logger.exception("WoesWindow.__init__: Error during self.setup_ui()")
        logger.debug("WoesWindow.__init__: Finished")

    def setup_ui(self):
        logger.debug("WoesWindow.setup_ui: Starting")

        logger.debug("WoesWindow.setup_ui: Calling load_css...")
        try:
            self.load_css()
        except Exception:  # General catch during setup_ui for load_css
            logger.exception("WoesWindow.setup_ui: Error during self.load_css()")
        logger.debug("WoesWindow.setup_ui: load_css finished.")

        logger.debug("WoesWindow.setup_ui: Calling apply_preferences...")
        try:
            self.apply_preferences()
        except Exception:  # General catch during setup_ui for apply_preferences
            logger.exception("WoesWindow.setup_ui: Error during self.apply_preferences()")
        logger.debug("WoesWindow.setup_ui: apply_preferences finished.")

        if self.switcher_title and self.stack:
            logger.debug("WoesWindow.setup_ui: Connecting switcher_title signal...")
            try:
                self.switcher_title.connect("notify::selected-page", self.on_page_switched)
                logger.debug("WoesWindow.setup_ui: switcher_title signal connected.")
            except Exception:  # General catch for signal connection
                logger.exception("WoesWindow.setup_ui: Error connecting switcher_title signal")
        else:
            logger.warning("switcher_title or stack not found during setup_ui.")
        logger.debug("WoesWindow.setup_ui: Finished")

    def _on_dark_theme_setting_changed(self, settings, key):
        logger.debug("WoesWindow._on_dark_theme_setting_changed: '%s' setting changed.", key)
        dark_theme_enabled = settings.get_boolean(key)
        apply_theme(self.style_manager, dark_theme_enabled)
        self.load_css()  # Reload CSS to apply theme-specific styles

    def load_css(self):
        logger.debug("WoesWindow.load_css: Starting")
        # Determine which CSS file to use based on the current theme
        css_file = (
            "style-dark.css"
            if self.style_manager.get_color_scheme() == Adw.ColorScheme.PREFER_DARK
            else "style.css"
        )
        logger.debug("WoesWindow.load_css: Determined css_file: %s", css_file)
        css_path = f"{RESOURCE_PREFIX}/{css_file}"
        logger.debug("WoesWindow.load_css: css_path: %s", css_path)
        style_provider = Gtk.CssProvider()

        try:
            style_provider.load_from_resource(css_path)
            logger.debug("WoesWindow.load_css: Loaded style from resource: %s", css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        except GLib.Error as e:
            logger.error("Failed to load CSS resource from %s: %s", css_path, e, exc_info=True)
        except Exception as e:  # General fallback for unexpected errors
            logger.error("Unexpected error loading CSS from %s: %s", css_path, e, exc_info=True)
        logger.debug("WoesWindow.load_css: Finished")

    def reload_css(self):
        logger.debug("WoesWindow.reload_css: Starting")
        logger.debug("Reloading CSS based on theme preference.")
        self.load_css()
        logger.debug("WoesWindow.reload_css: Finished")

    def apply_preferences(self):
        logger.debug("WoesWindow.apply_preferences: Starting")
        try:
            font_size = self.settings.get_int("font-size")
            logger.debug("WoesWindow.apply_preferences: Font size from settings: %d", font_size)
            dark_theme_enabled = self.settings.get_boolean("dark-theme")
            logger.debug("WoesWindow.apply_preferences: Dark theme from settings: %s", dark_theme_enabled)

            apply_font_size(self.settings, font_size)
            apply_theme(self.style_manager, dark_theme_enabled)
        except GLib.Error as e:  # Specific error for GSettings issues
            logger.error("Error applying preferences (GSettings): %s", e, exc_info=True)
        except Exception as e:  # General fallback for other unexpected errors
            logger.error("Unexpected error applying preferences: %s", e, exc_info=True)
        logger.debug("WoesWindow.apply_preferences: Finished")

    def on_page_switched(self, _widget, _gparam):  # Prefixed unused arguments
        logger.debug("WoesWindow.on_page_switched: Starting")
        selected_page = self.stack.get_visible_child()
        page_name = "Unknown"
        if selected_page and hasattr(selected_page, 'props') and hasattr(selected_page.props, 'name'):
            page_name = selected_page.props.name
        logger.debug("Switched to page: %s (Object: %s)", page_name, selected_page)
        logger.debug("WoesWindow.on_page_switched: Finished")
