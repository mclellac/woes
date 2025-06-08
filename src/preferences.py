import logging
import re

import gi
from gi.repository import Adw, Gio, Gtk, GLib # pylint: disable=wrong-import-position

from .constants import APP_ID, RESOURCE_PREFIX # pylint: disable=wrong-import-position

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = "Preferences"

    font_scale_combo_row = Gtk.Template.Child("font_scale_combo_row")
    theme_combo_row = Gtk.Template.Child("theme_combo_row")
    source_style_scheme_combo_row = Gtk.Template.Child("source_style_scheme_combo_row")
    dns_server_entryrow = Gtk.Template.Child("dns_server_entryrow")
    prefs_dns_apply_button = Gtk.Template.Child("prefs_dns_apply_button")
    preferences_error_banner = Gtk.Template.Child("preferences_error_banner")

    # HTTP Output Color Rows
    http_header_key_color_row = Gtk.Template.Child("http_header_key_color_row")
    http_header_value_color_row = Gtk.Template.Child("http_header_value_color_row")
    http_special_row_color_row = Gtk.Template.Child("http_special_row_color_row")

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

    def load_ui(self):
        self.font_scale_combo_row.connect(
            "notify::selected", self.on_font_scale_changed
            )
        self.theme_combo_row.connect(
            "notify::selected", self.on_theme_preference_changed
            )
        self.source_style_scheme_combo_row.connect(
            "notify::selected", self.on_source_style_scheme_changed
            )
        self.dns_server_entryrow.connect("entry-activated", self.on_dns_server_changed)
        self.prefs_dns_apply_button.connect("clicked", self.on_dns_server_changed)

        if self.http_header_key_color_row:
            self.settings.bind(
                "http-output-header-key-color",
                self.http_header_key_color_row,
                "text",
                Gio.SettingsBindFlags.DEFAULT,
                )
            logging.debug("Bound http_header_key_color_row text to GSettings.")
        else:
            logging.warning("http_header_key_color_row is None, cannot bind GSettings.")

        if self.http_header_value_color_row:
            self.settings.bind(
                "http-output-header-value-color",
                self.http_header_value_color_row,
                "text",
                Gio.SettingsBindFlags.DEFAULT,
                )
            logging.debug("Bound http_header_value_color_row text to GSettings.")
        else:
            logging.warning(
                "http_header_value_color_row is None, cannot bind GSettings."
                )

        if self.http_special_row_color_row:
            self.settings.bind(
                "http-output-special-row-color",
                self.http_special_row_color_row,
                "text",
                Gio.SettingsBindFlags.DEFAULT,
                )
            logging.debug("Bound http_special_row_color_row text to GSettings.")
        else:
            logging.warning(
                "http_special_row_color_row is None, cannot bind GSettings."
                )

    def on_error_banner_dismiss_clicked(self, _banner, *_args):
        self.hide_banner_and_clear_error_state()

    # Changed 'entryrow: Adw.EntryRow' to 'widget' as it can be a button or entry row
    def on_dns_server_changed(self, _widget): # pylint: disable=unused-argument
        dns_server = self.dns_server_entryrow.get_text().strip()

        ip_pattern = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")

        if ip_pattern.match(dns_server) and self.is_valid_ipv4(dns_server):
            self.settings.set_string("custom-dns-server", dns_server)
            logging.info("Custom DNS server set to: %s", dns_server)
            self.preferences_error_banner.set_revealed(False)
            self.dns_server_entryrow.remove_css_class("error")
        else:
            self.dns_server_entryrow.add_css_class("error")
            error_message = "Invalid IPv4 address for DNS server."
            logging.error(
                "Invalid custom DNS server IP address provided: %s", dns_server
                )

            self.preferences_error_banner.set_title(error_message)
            self.preferences_error_banner.set_revealed(True)

            self.dns_server_entryrow.set_text("")

            # Pass self.dns_server_entryrow explicitly to the timeout handler
            GLib.timeout_add_seconds(
                4, self.hide_banner_and_clear_error_state, self.dns_server_entryrow
                )

    def hide_banner_and_clear_error_state(self, entry_row_widget=None):
        """Hides the banner and clears error CSS from the entry row if provided."""
        self.preferences_error_banner.set_revealed(False)
        if entry_row_widget and isinstance(entry_row_widget, Adw.EntryRow):
            entry_row_widget.remove_css_class("error")
        elif self.dns_server_entryrow:
            self.dns_server_entryrow.remove_css_class("error")
        return GLib.SOURCE_REMOVE

    @staticmethod
    def is_valid_ipv4(ip: str) -> bool:
        """Validate if the input string is a valid IPv4 address."""
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        for part in parts:
            try:
                num = int(part)
                if num < 0 or num > 255:
                    return False
            except ValueError:
                return False
        return True

    def on_font_scale_changed(self, combo_row: Adw.ComboRow, _gparam):
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_scale_str = selected_item_obj.get_string()
            self.settings.set_string("font-scaling-percentage", selected_scale_str)
            logging.debug("Font scaling preference set to %s.", selected_scale_str)

    def on_theme_preference_changed(self, combo_row: Adw.ComboRow, _gparam):
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_theme_str = selected_item_obj.get_string()
            self.settings.set_string("theme-preference", selected_theme_str)
            logging.debug(
                "Theme preference set to %s. WoesWindow will handle the change.",
                selected_theme_str,
                )

    def on_source_style_scheme_changed(self, combo_row: Adw.ComboRow, _gparam):
        selected_item = combo_row.get_selected_item()
        if isinstance(selected_item, Gtk.StringObject):
            source_style_scheme = selected_item.get_string()
            self.settings.set_string("source-style-scheme", source_style_scheme)

    def _select_combo_row_item(
            self, combo_row: Adw.ComboRow, setting_value: str, case_sensitive: bool = True
            ) -> bool:
        """Helper to select an item in a ComboRow based on a GSettings value."""
        model = combo_row.get_model()
        if isinstance(model, Gtk.StringList):
            for i in range(model.get_n_items()):
                item_string = model.get_string(i)
                # Ensure item_string is not None before lower() if case_sensitive is False
                match_condition = (
                    (item_string == setting_value)
                    if case_sensitive
                    else (
                        item_string is not None
                        and item_string.lower() == setting_value.lower()
                        )
                    )
                if match_condition:
                    combo_row.set_selected(i)
                    return True
        return False

    def load_preferences(self):
        """Load preferences from GSettings and apply them to the UI."""
        font_scale_pref = self.settings.get_string("font-scaling-percentage")
        if not self._select_combo_row_item(self.font_scale_combo_row, font_scale_pref):
            self.font_scale_combo_row.set_selected(0)

        theme_pref_value = self.settings.get_string("theme-preference")
        if not self._select_combo_row_item(self.theme_combo_row, theme_pref_value):
            self.theme_combo_row.set_selected(0)

        source_style_scheme = self.settings.get_string("source-style-scheme")
        if not self._select_combo_row_item(
                self.source_style_scheme_combo_row,
                source_style_scheme,
                case_sensitive=False,
                ):
            logging.warning(
                "Style scheme '%s' not found in ComboRow or model is not Gtk.StringList. "
                "Defaulting might not apply or be index 0.",
                source_style_scheme,
                )
            # Original code didn't set a default for source_style_scheme_combo_row if not found, so we replicate that.
            # If a default selection (e.g., index 0) is desired, it could be added here:
            # else: self.source_style_scheme_combo_row.set_selected(0)

        dns_server = self.settings.get_string("custom-dns-server")
        self.dns_server_entryrow.set_text(dns_server)
