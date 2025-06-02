import logging
import re

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, Gtk, GLib

from .constants import APP_ID, RESOURCE_PREFIX

logger = logging.getLogger(__name__)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.blp")
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = "Preferences"

    font_size_scale = Gtk.Template.Child("font_size_scale")
    theme_switch_row = Gtk.Template.Child("theme_switch_row")
    source_style_scheme_combo_row = Gtk.Template.Child("source_style_scheme_combo_row")
    dns_server_entryrow = Gtk.Template.Child("dns_server_entryrow")
    preferences_error_banner = Gtk.Template.Child("preferences_error_banner")

    def __init__(self, main_window=None):
        logger.debug("Preferences.__init__: Starting.")
        super().__init__(modal=True)
        self.main_window = main_window
        logger.debug(f"Preferences.__init__: main_window: {main_window}")
        logger.debug("Preferences.__init__: Before self.set_transient_for()")
        self.set_transient_for(main_window)
        logger.debug("Preferences.__init__: After self.set_transient_for()")
        self.settings = Gio.Settings(schema_id=APP_ID)
        logger.debug(f"Preferences.__init__: self.settings initialized: {self.settings}")
        logger.debug("Preferences.__init__: Before self.load_ui()")
        self.load_ui() # Logs itself
        logger.debug("Preferences.__init__: After self.load_ui()")
        logger.debug("Preferences.__init__: Before self.load_preferences()")
        self.load_preferences() # Logs itself
        logger.debug("Preferences.__init__: After self.load_preferences()")
        logger.debug("Preferences.__init__: Finished.")

    def load_ui(self):
        logger.debug("Preferences.load_ui: Starting to connect signals.")
        self.font_size_scale.connect("value-changed", self.on_font_size_changed)
        logger.debug("Preferences.load_ui: Connected 'value-changed' for font_size_scale.")
        self.theme_switch_row.connect("notify::active", self.on_theme_switch_changed)
        logger.debug("Preferences.load_ui: Connected 'notify::active' for theme_switch_row.")
        self.source_style_scheme_combo_row.connect(
            "notify::selected", self.on_source_style_scheme_changed
        )
        logger.debug("Preferences.load_ui: Connected 'notify::selected' for source_style_scheme_combo_row.")
        self.dns_server_entryrow.connect("apply", self.on_dns_server_changed)
        logger.debug("Preferences.load_ui: Connected 'apply' for dns_server_entryrow.")
        self.preferences_error_banner.connect("button-clicked", self.on_error_banner_dismiss_clicked)
        logger.debug("Preferences.load_ui: Connected 'button-clicked' for preferences_error_banner.")
        logger.debug("Preferences.load_ui: Finished connecting signals.")

    def on_error_banner_dismiss_clicked(self, _banner, *_args):
        logger.debug(f"Preferences.on_error_banner_dismiss_clicked: Triggered for banner: {_banner}")
        self.hide_banner_and_clear_error_state()
        logger.debug("Preferences.on_error_banner_dismiss_clicked: Finished.")

    def on_dns_server_changed(self, entryrow: Adw.EntryRow):
        logger.debug(f"Preferences.on_dns_server_changed: Triggered for entryrow: {entryrow}")
        dns_server = entryrow.get_text().strip()
        logger.debug(f"Preferences.on_dns_server_changed: DNS server input: '{dns_server}'")

        ip_pattern = re.compile(
            r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
        )
        is_valid_format = bool(ip_pattern.match(dns_server))
        is_valid_ip = self.is_valid_ipv4(dns_server)
        logger.debug(f"Preferences.on_dns_server_changed: Format valid: {is_valid_format}, IP content valid: {is_valid_ip}")

        if is_valid_format and is_valid_ip:
            logger.debug(f"Preferences.on_dns_server_changed: Setting 'custom-dns-server' to '{dns_server}'")
            self.settings.set_string("custom-dns-server", dns_server)
            logger.debug(f"Preferences.on_dns_server_changed: Custom DNS server set to: {dns_server} in GSettings.")
            self.preferences_error_banner.set_revealed(False)
            logger.debug("Preferences.on_dns_server_changed: preferences_error_banner revealed set to False.")
            entryrow.remove_css_class("error")
            logger.debug("Preferences.on_dns_server_changed: 'error' CSS class removed from entryrow.")
        else:
            entryrow.add_css_class("error")
            logger.debug("Preferences.on_dns_server_changed: 'error' CSS class added to entryrow.")
            error_message = "Invalid IPv4 address for DNS server."
            logger.error(f"Preferences.on_dns_server_changed: {error_message}")

            self.preferences_error_banner.set_title(error_message)
            logger.debug(f"Preferences.on_dns_server_changed: preferences_error_banner title set to '{error_message}'.")
            self.preferences_error_banner.set_revealed(True)
            logger.debug("Preferences.on_dns_server_changed: preferences_error_banner revealed set to True.")

            entryrow.set_text("")
            logger.debug("Preferences.on_dns_server_changed: Entryrow text cleared.")

            logger.debug("Preferences.on_dns_server_changed: Scheduling GLib.timeout_add_seconds to hide banner.")
            GLib.timeout_add_seconds(4, self.hide_banner_and_clear_error_state, entryrow)
        logger.debug("Preferences.on_dns_server_changed: Finished.")

    def hide_banner_and_clear_error_state(self, entry_row_widget=None):
        """Hides the banner and clears error CSS from the entry row if provided."""
        logger.debug(f"Preferences.hide_banner_and_clear_error_state: Called with entry_row_widget: {entry_row_widget}")
        self.preferences_error_banner.set_revealed(False)
        logger.debug("Preferences.hide_banner_and_clear_error_state: preferences_error_banner revealed set to False.")
        if entry_row_widget and isinstance(entry_row_widget, Adw.EntryRow):
            entry_row_widget.remove_css_class("error")
            logger.debug(
                f"Preferences.hide_banner_and_clear_error_state: 'error' CSS class removed "
                f"from provided entry_row_widget: {entry_row_widget}"
            )
        elif self.dns_server_entryrow:
            self.dns_server_entryrow.remove_css_class("error")
            logger.debug(
                "Preferences.hide_banner_and_clear_error_state: 'error' CSS class removed "
                "from self.dns_server_entryrow (fallback)."
            )
        logger.debug("Preferences.hide_banner_and_clear_error_state: Finished, returning GLib.SOURCE_REMOVE.")
        return GLib.SOURCE_REMOVE

    @staticmethod
    def is_valid_ipv4(ip: str) -> bool:
        """Validate if the input string is a valid IPv4 address."""
        logger.debug(f"Preferences.is_valid_ipv4: Validating IP: '{ip}'")
        parts = ip.split(".")
        if len(parts) != 4:
            logger.debug(f"Preferences.is_valid_ipv4: Invalid, number of parts is {len(parts)} not 4. Returning False.")
            return False
        for part in parts:
            try:
                num = int(part)
                if num < 0 or num > 255:
                    logger.debug(f"Preferences.is_valid_ipv4: Invalid, part '{part}' (value {num}) is out of 0-255 range. Returning False.")
                    return False
            except ValueError:
                logger.debug(f"Preferences.is_valid_ipv4: Invalid, part '{part}' is not an integer. Returning False.")
                return False
        logger.debug(f"Preferences.is_valid_ipv4: IP '{ip}' is valid. Returning True.")
        return True

    def on_font_size_changed(self, scale):
        logger.debug(f"Preferences.on_font_size_changed: Triggered with scale: {scale}")
        font_size = int(scale.get_value())
        logger.debug(f"Preferences.on_font_size_changed: Font size from scale: {font_size}")
        self.settings.set_int("font-size", font_size)
        logger.debug(f"Preferences.on_font_size_changed: 'font-size' set to {font_size} in GSettings.")
        logger.debug("Preferences.on_font_size_changed: Finished.")

    def on_theme_switch_changed(self, switch_row: Adw.SwitchRow, _gparam):
        logger.debug(f"Preferences.on_theme_switch_changed: Triggered for switch_row: {switch_row}, param: {_gparam}")
        theme_enabled = switch_row.get_active()
        logger.debug(f"Preferences.on_theme_switch_changed: Theme enabled from switch: {theme_enabled}")
        self.settings.set_boolean("dark-theme", theme_enabled)
        logger.debug(f"Preferences.on_theme_switch_changed: 'dark-theme' set to {theme_enabled} in GSettings. WoesWindow will handle the change.")
        logger.debug("Preferences.on_theme_switch_changed: Finished.")

    def on_source_style_scheme_changed(self, combo_row: Adw.ComboRow, _gparam):
        logger.debug(f"Preferences.on_source_style_scheme_changed: Triggered for combo_row: {combo_row}, param: {_gparam}")
        selected_item = combo_row.get_selected_item()
        logger.debug(f"Preferences.on_source_style_scheme_changed: Selected item: {selected_item}")
        if isinstance(selected_item, Gtk.StringObject):
            source_style_scheme = selected_item.get_string()
            logger.debug(f"Preferences.on_source_style_scheme_changed: Source style scheme string: '{source_style_scheme}'")
            self.settings.set_string("source-style-scheme", source_style_scheme)
            logger.debug(
                f"Preferences.on_source_style_scheme_changed: 'source-style-scheme' set to '{source_style_scheme}' "
                "in GSettings."
            )
        else:
            logger.warning(
                f"Preferences.on_source_style_scheme_changed: Selected item is not a Gtk.StringObject: "
                f"{type(selected_item)}"
            )
        logger.debug("Preferences.on_source_style_scheme_changed: Finished.")

    def load_preferences(self):
        logger.debug("Preferences.load_preferences: Loading and applying settings to UI.")
        logger.debug("Preferences.load_preferences: Loading setting 'font-size'")
        font_size = self.settings.get_int("font-size")
        logger.debug(f"Preferences.load_preferences: 'font-size' from GSettings: {font_size}")
        self.font_size_scale.set_value(float(font_size))
        logger.debug(f"Preferences.load_preferences: font_size_scale value set to {float(font_size)}")

        logger.debug("Preferences.load_preferences: Loading setting 'dark-theme'")
        dark_theme_enabled = self.settings.get_boolean("dark-theme")
        logger.debug(f"Preferences.load_preferences: 'dark-theme' from GSettings: {dark_theme_enabled}")
        self.theme_switch_row.set_active(dark_theme_enabled)
        logger.debug(f"Preferences.load_preferences: theme_switch_row active set to {dark_theme_enabled}")

        logger.debug("Preferences.load_preferences: Loading setting 'source-style-scheme'")
        source_style_scheme = self.settings.get_string("source-style-scheme")
        logger.debug(f"Preferences.load_preferences: 'source-style-scheme' from GSettings: '{source_style_scheme}'")
        model = self.source_style_scheme_combo_row.get_model()
        found_scheme = False
        if isinstance(model, Gtk.StringList):
            for i in range(model.get_n_items()):
                item_string = model.get_string(i)
                if item_string and item_string.lower() == source_style_scheme.lower():
                    self.source_style_scheme_combo_row.set_selected(i)
                    logger.debug(
                        f"Preferences.load_preferences: source_style_scheme_combo_row selected index set to {i} "
                        f"for scheme '{source_style_scheme}'"
                    )
                    found_scheme = True
                    break
        if not found_scheme:
            logger.warning(
                "Preferences.load_preferences: Style scheme '%s' not found or model is not Gtk.StringList.",
                source_style_scheme
            )

        logger.debug("Preferences.load_preferences: Loading setting 'custom-dns-server'")
        dns_server = self.settings.get_string("custom-dns-server")
        logger.debug(f"Preferences.load_preferences: 'custom-dns-server' from GSettings: '{dns_server}'")
        self.dns_server_entryrow.set_text(dns_server)
        logger.debug(f"Preferences.load_preferences: dns_server_entryrow text set to '{dns_server}'")
        logger.debug("Preferences.load_preferences: Finished loading and applying settings.")