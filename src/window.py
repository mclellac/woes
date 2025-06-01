import logging

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gdk, Gio, Gtk, GLib

from .constants import APP_ID, RESOURCE_PREFIX
from .dns_page import DNSPage as _DNSPage_
from .http_page import HttpPage as _HttpPage_
from .nmap_page import NmapPage as _NmapPage_
from .style_utils import apply_font_size, apply_theme
from .webscan_page import WebScanPage as _WebScanPage_

logger = logging.getLogger(__name__)

_ = _DNSPage_
_ = _HttpPage_
_ = _NmapPage_
_ = _WebScanPage_


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/window.ui")
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "WoesWindow"

    switcher_title = Gtk.Template.Child("switcher_title")
    stack = Gtk.Template.Child("stack")
    webscan_status_page = Gtk.Template.Child("webscan_status_page")

    def __init__(self, **kwargs):
        logger.debug("WoesWindow.__init__: Starting")
        super().__init__(**kwargs)
        logger.debug("WoesWindow.__init__: Before Gio.Settings(schema_id=APP_ID)")
        self.settings = Gio.Settings(schema_id=APP_ID)
        logger.debug(f"WoesWindow.__init__: After Gio.Settings, self.settings: {self.settings}")
        logger.debug("WoesWindow.__init__: Before Adw.StyleManager.get_default()")
        self.style_manager = Adw.StyleManager.get_default()
        logger.debug(f"WoesWindow.__init__: After Adw.StyleManager.get_default(), self.style_manager: {self.style_manager}")

        logger.debug("WoesWindow.__init__: Before self.settings.connect('changed::dark-theme')")
        self.settings.connect("changed::dark-theme", self._on_dark_theme_setting_changed)
        logger.debug("WoesWindow.__init__: After self.settings.connect('changed::dark-theme')")

        logger.debug("WoesWindow.__init__: Before self.setup_ui()")
        try:
            self.setup_ui()
            logger.debug("WoesWindow.__init__: After self.setup_ui()")
        except Exception:
            logger.exception("WoesWindow.__init__: Error during self.setup_ui()")

        # Instantiate WebScanPage
        # Use the imported alias _WebScanPage_ to be consistent with how other pages are handled for registration
        webscan_page_instance = _WebScanPage_()

        # Set it as the child of the AdwStatusPage meant for webscan
        if self.webscan_status_page:
            self.webscan_status_page.set_child(webscan_page_instance)
            logger.debug("WoesWindow.__init__: WebScanPage instance created and added to webscan_status_page.")
        else:
            logger.error("WoesWindow.__init__: webscan_status_page is None, cannot add WebScanPage instance.")
        logger.debug("WoesWindow.__init__: Finished")

    def setup_ui(self):
        logger.debug("WoesWindow.setup_ui: Starting")

        logger.debug("WoesWindow.setup_ui: Before self.load_css()")
        try:
            self.load_css()
            logger.debug("WoesWindow.setup_ui: After self.load_css()")
        except Exception:
            logger.exception("WoesWindow.setup_ui: Error during self.load_css()")

        logger.debug("WoesWindow.setup_ui: Before self.apply_preferences()")
        try:
            self.apply_preferences()
            logger.debug("WoesWindow.setup_ui: After self.apply_preferences()")
        except Exception:
            logger.exception("WoesWindow.setup_ui: Error during self.apply_preferences()")

        if self.switcher_title and self.stack:
            logger.debug("WoesWindow.setup_ui: Before self.switcher_title.connect(...)")
            try:
                self.switcher_title.connect("notify::selected-page", self.on_page_switched)
                logger.debug("WoesWindow.setup_ui: After self.switcher_title.connect(...)")
            except Exception:
                logger.exception("WoesWindow.setup_ui: Error connecting switcher_title signal")
        else:
            logger.warning("switcher_title or stack not found during setup_ui.")
        logger.debug("WoesWindow.setup_ui: Finished")

    def _on_dark_theme_setting_changed(self, settings, key):
        logger.debug("WoesWindow._on_dark_theme_setting_changed: '%s' setting changed.", key)
        dark_theme_enabled = settings.get_boolean(key)
        logger.debug(f"WoesWindow._on_dark_theme_setting_changed: dark_theme_enabled: {dark_theme_enabled}")
        logger.debug("WoesWindow._on_dark_theme_setting_changed: Before apply_theme()")
        apply_theme(self.style_manager, dark_theme_enabled)
        logger.debug("WoesWindow._on_dark_theme_setting_changed: After apply_theme()")
        logger.debug("WoesWindow._on_dark_theme_setting_changed: Before self.load_css()")
        self.load_css()
        logger.debug("WoesWindow._on_dark_theme_setting_changed: After self.load_css()")

    def load_css(self):
        logger.debug("WoesWindow.load_css: Starting")
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
            logger.debug(f"WoesWindow.load_css: Before style_provider.load_from_resource({css_path})")
            style_provider.load_from_resource(css_path)
            logger.debug(f"WoesWindow.load_css: After style_provider.load_from_resource({css_path})")
            logger.debug("WoesWindow.load_css: Before Gtk.StyleContext.add_provider_for_display()")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            logger.debug("WoesWindow.load_css: After Gtk.StyleContext.add_provider_for_display()")
        except GLib.Error as e:
            logger.error("Failed to load CSS resource from %s: %s", css_path, e, exc_info=True)
        except Exception as e:
            logger.error("Unexpected error loading CSS from %s: %s", css_path, e, exc_info=True)
        logger.debug("WoesWindow.load_css: Finished")

    def reload_css(self):
        logger.debug("WoesWindow.reload_css: Starting")
        logger.debug("WoesWindow.reload_css: Before self.load_css()")
        self.load_css()
        logger.debug("WoesWindow.reload_css: After self.load_css()")
        logger.debug("WoesWindow.reload_css: Finished")

    def apply_preferences(self):
        logger.debug("WoesWindow.apply_preferences: Starting")
        try:
            font_size = self.settings.get_int("font-size")
            logger.debug("WoesWindow.apply_preferences: Font size from settings: %d", font_size)
            dark_theme_enabled = self.settings.get_boolean("dark-theme")
            logger.debug("WoesWindow.apply_preferences: Dark theme from settings: %s", dark_theme_enabled)

            logger.debug("WoesWindow.apply_preferences: Before apply_font_size()")
            apply_font_size(self.settings, font_size)
            logger.debug("WoesWindow.apply_preferences: After apply_font_size()")
            logger.debug("WoesWindow.apply_preferences: Before apply_theme()")
            apply_theme(self.style_manager, dark_theme_enabled)
            logger.debug("WoesWindow.apply_preferences: After apply_theme()")
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
