import logging
import re
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, Gtk, GLib

from .constants import APP_ID, RESOURCE_PREFIX


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = "Preferences"

    font_size_scale = Gtk.Template.Child("font_size_scale")
    theme_switch_row = Gtk.Template.Child("theme_switch_row")
    source_style_scheme_combo_row = Gtk.Template.Child("source_style_scheme_combo_row")
    dns_server_entryrow = Gtk.Template.Child("dns_server_entryrow")
    preferences_error_banner = Gtk.Template.Child("preferences_error_banner")

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

    def load_ui(self):
        self.font_size_scale.connect("value-changed", self.on_font_size_changed)
        # Connect to notify::active for AdwSwitchRow
        self.theme_switch_row.connect("notify::active", self.on_theme_switch_changed)
        self.source_style_scheme_combo_row.connect(
            "notify::selected", self.on_source_style_scheme_changed
        )
        self.dns_server_entryrow.connect("apply", self.on_dns_server_changed)
        # Banner dismiss signal is connected in UI template if handler _on_error_banner_dismiss_clicked is defined

    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def on_error_banner_dismiss_clicked(self, _banner, *_args):  # Renamed to match typical handler name
        self.hide_banner_and_clear_error_state()

    def on_dns_server_changed(self, entryrow: Adw.EntryRow):
        dns_server = entryrow.get_text().strip()

        ip_pattern = re.compile(
            r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
        )

        if ip_pattern.match(dns_server) and self.is_valid_ipv4(dns_server):
            self.settings.set_string("custom-dns-server", dns_server)
            logging.info("Custom DNS server set to: %s", dns_server)
            self.preferences_error_banner.set_revealed(False)
            entryrow.remove_css_class("error")
        else:
            entryrow.add_css_class("error")
            error_message = "Invalid IPv4 address for DNS server."
            logging.error("Invalid custom DNS server IP address provided: %s", dns_server)

            self.preferences_error_banner.set_title(error_message)
            self.preferences_error_banner.set_revealed(True)

            entryrow.set_text("")

            # Hide the banner after 4 seconds
            GLib.timeout_add_seconds(4, self.hide_banner_and_clear_error_state, entryrow)

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

    def on_font_size_changed(self, scale):
        font_size = int(scale.get_value())
        self.settings.set_int("font-size", font_size)

    def on_theme_switch_changed(self, switch_row: Adw.SwitchRow, _gparam):
        theme_enabled = switch_row.get_active()
        self.settings.set_boolean("dark-theme", theme_enabled)
        logging.debug("Dark theme preference set to %s. WoesWindow will handle the change.", theme_enabled)

    def on_source_style_scheme_changed(self, combo_row: Adw.ComboRow, _gparam):
        selected_item = combo_row.get_selected_item()
        if isinstance(selected_item, Gtk.StringObject):
            source_style_scheme = selected_item.get_string()
            self.settings.set_string("source-style-scheme", source_style_scheme)

    def load_preferences(self):
        font_size = self.settings.get_int("font-size")
        self.font_size_scale.set_value(float(font_size))

        dark_theme_enabled = self.settings.get_boolean("dark-theme")
        self.theme_switch_row.set_active(dark_theme_enabled)

        source_style_scheme = self.settings.get_string("source-style-scheme")
        model = self.source_style_scheme_combo_row.get_model()
        found_scheme = False
        if isinstance(model, Gtk.StringList):
            for i in range(model.get_n_items()):
                item_string = model.get_string(i)
                if item_string and item_string.lower() == source_style_scheme.lower():
                    self.source_style_scheme_combo_row.set_selected(i)
                    found_scheme = True
                    break
        if not found_scheme:
            logging.warning(
                "Style scheme '%s' not found or model is not Gtk.StringList.", source_style_scheme
            )

        # Custom DNS Server
        dns_server = self.settings.get_string("custom-dns-server")
        self.dns_server_entryrow.set_text(dns_server)