"""Manages the application's preferences window and settings.

Provides UI for adjusting font scaling, theme, source view style schemes,
custom DNS server, and HTTP output colors. Interacts with GSettings to
load and save these preferences.
"""
"""Manages the application's preferences window and settings.

Provides UI for adjusting font scaling, theme, source view style schemes,
custom DNS server, and HTTP output colors. Interacts with GSettings to
load and save these preferences.
"""
import logging
import re
from typing import Optional # Added for type hinting

import gi
from gi.repository import Adw, Gio, Gtk, GLib, GObject, Gdk  # pylint: disable=wrong-import-position # Added GObject and Gdk

from .constants import APP_ID, RESOURCE_PREFIX  # pylint: disable=wrong-import-position

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    """Adw.PreferencesWindow subclass for managing application settings.

    This class defines the structure and behavior of the preferences dialog,
    allowing users to customize various aspects of the application.
    It binds UI elements to GSettings for persistence.
    """

    __gtype_name__ = "Preferences"

    font_scale_combo_row = Gtk.Template.Child("font_scale_combo_row")
    theme_combo_row = Gtk.Template.Child("theme_combo_row")
    source_style_scheme_combo_row = Gtk.Template.Child("source_style_scheme_combo_row")
    dns_server_entryrow = Gtk.Template.Child("dns_server_entryrow")
    prefs_dns_apply_button = Gtk.Template.Child("prefs_dns_apply_button")
    preferences_error_banner = Gtk.Template.Child("preferences_error_banner")

    # HTTP Output Color Rows
    http_header_key_color_button = Gtk.Template.Child("http_header_key_color_button")
    http_header_value_color_button = Gtk.Template.Child("http_header_value_color_button")
    http_special_row_color_button = Gtk.Template.Child("http_special_row_color_button")

    def __init__(self, main_window: Gtk.Window = None):
        """Initialize the Preferences window.

        Args:
        ----
            main_window: The parent Gtk.Window for this dialog.

        """
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

    def load_ui(self):
        """Connect signals for UI elements and bind GSettings for color preferences.

        This method sets up connections for various combo rows and buttons
        to their respective handler functions. It also directly binds the 'text'
        property of color entry rows to GSettings keys.
        """
        self.font_scale_combo_row.connect("notify::selected", self.on_font_scale_changed)
        self.theme_combo_row.connect("notify::selected", self.on_theme_preference_changed)
        self.source_style_scheme_combo_row.connect(
            "notify::selected", self.on_source_style_scheme_changed
        )
        self.dns_server_entryrow.connect("entry-activated", self.on_dns_server_changed)
        self.prefs_dns_apply_button.connect("clicked", self.on_dns_server_changed)

        if self.http_header_key_color_button:
            dialog_hk = Gtk.ColorDialog(title="Select Header Key Colour", modal=True, with_alpha=True)
            self.http_header_key_color_button.set_dialog(dialog_hk)
            self.http_header_key_color_button.connect("notify::rgba", self.on_http_color_changed, "http-output-header-key-color")
        if self.http_header_value_color_button:
            dialog_hv = Gtk.ColorDialog(title="Select Header Value Colour", modal=True, with_alpha=True)
            self.http_header_value_color_button.set_dialog(dialog_hv)
            self.http_header_value_color_button.connect("notify::rgba", self.on_http_color_changed, "http-output-header-value-color")
        if self.http_special_row_color_button:
            dialog_sr = Gtk.ColorDialog(title="Select Special Row Colour", modal=True, with_alpha=True)
            self.http_special_row_color_button.set_dialog(dialog_sr)
            self.http_special_row_color_button.connect("notify::rgba", self.on_http_color_changed, "http-output-special-row-color")

    def on_error_banner_dismiss_clicked(self, _banner: Adw.Banner, *_args):
        """Handle the click event for dismissing the error banner.

        Args:
        ----
            _banner: The Adw.Banner that was clicked (or its dismiss button).
            *_args: Additional arguments (unused).

        """
        self.hide_banner_and_clear_error_state()

    def on_dns_server_changed(self, _widget: Gtk.Widget):  # pylint: disable=unused-argument
        """Handle changes to the custom DNS server entry.

        Validates the entered IP address. If valid, saves it to GSettings.
        If invalid, displays an error banner and clears the entry after a delay.

        Args:
        ----
            _widget: The widget that triggered the change (Adw.EntryRow or Gtk.Button).

        """
        dns_server = self.dns_server_entryrow.get_text().strip()

        if not dns_server:  # Handles empty string
            self.settings.set_string("custom-dns-server", "")
            logging.info("Custom DNS server cleared.")
            self.preferences_error_banner.set_revealed(False)
            if self.dns_server_entryrow:
                self.dns_server_entryrow.remove_css_class("error")
            return

        # Proceed with validation for non-empty strings
        ip_pattern = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")
        if ip_pattern.match(dns_server) and self.is_valid_ipv4(dns_server):
            self.settings.set_string("custom-dns-server", dns_server)
            logging.info("Custom DNS server set to: %s", dns_server) # Keep %s for compatibility if specific log parsing exists
            self.preferences_error_banner.set_revealed(False)
            if self.dns_server_entryrow:
                self.dns_server_entryrow.remove_css_class("error")
        else:
            # Invalid non-empty input
            if self.dns_server_entryrow:
                self.dns_server_entryrow.add_css_class("error")
            error_message = "Invalid IPv4 address for DNS server."
            logging.error(f"Invalid custom DNS server IP address provided: {dns_server}") # Use f-string

            self.preferences_error_banner.set_title(error_message)
            self.preferences_error_banner.set_revealed(True)
            # Do NOT clear self.dns_server_entryrow.set_text("") here.
            # Pass self.dns_server_entryrow explicitly to the timeout handler
            GLib.timeout_add_seconds(
                4, self.hide_banner_and_clear_error_state, self.dns_server_entryrow
            )

    def hide_banner_and_clear_error_state(self, entry_row_widget: Optional[Adw.EntryRow] = None) -> bool:
        """Hide the error banner and remove 'error' CSS class from an entry row.

        This method is also used as a GLib.timeout_add_seconds callback,
        in which case it must return GLib.SOURCE_REMOVE.

        Args:
        ----
            entry_row_widget: The Adw.EntryRow to clear the error state from.
                              If None, defaults to `self.dns_server_entryrow`.

        Returns:
        -------
            GLib.SOURCE_REMOVE if called as a timeout, indicating the timer should not repeat.
            Implicitly returns None otherwise.

        """
        self.preferences_error_banner.set_revealed(False)
        target_entry_row = entry_row_widget if entry_row_widget else self.dns_server_entryrow
        if target_entry_row: # Check if it exists
            target_entry_row.remove_css_class("error")
        return GLib.SOURCE_REMOVE # Suitable for GLib.timeout_add

    @staticmethod
    def is_valid_ipv4(ip_address: str) -> bool:
        """Validate if the input string is a syntactically valid IPv4 address.

        Args:
        ----
            ip_address: The string to validate.

        Returns:
        -------
            True if the string is a valid IPv4 address, False otherwise.

        """
        parts = ip_address.split(".")
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

    def on_font_scale_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec): # Changed GLib.ParamSpec to GObject.ParamSpec
        """Handle changes in the font scale preference ComboRow.

        Saves the selected font scaling percentage string to GSettings.

        Args:
        ----
            combo_row: The Adw.ComboRow whose selection changed.
            _gparam: The GLib.ParamSpec of the property that changed (unused).

        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_scale_str = selected_item_obj.get_string()
            self.settings.set_string("font-scaling-percentage", selected_scale_str)
            logging.debug("Font scaling preference set to %s.", selected_scale_str)

    def on_theme_preference_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec): # Corrected to GObject.ParamSpec
        """Handle changes in the theme preference ComboRow.

        Saves the selected theme name string (e.g., "Light", "Dark") to GSettings.

        Args:
        ----
            combo_row: The Adw.ComboRow whose selection changed.
            _gparam: The GLib.ParamSpec of the property that changed (unused).

        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_theme_str = selected_item_obj.get_string()
            self.settings.set_string("theme-preference", selected_theme_str)
            logging.debug(
                "Theme preference set to %s. WoesWindow will handle the change.",
                selected_theme_str,
            )

    def on_source_style_scheme_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec): # Ensure this is GObject.ParamSpec and distinct
        """Handle changes in the source style scheme preference ComboRow.

        Saves the selected style scheme name string to GSettings.

        Args:
        ----
            combo_row: The Adw.ComboRow whose selection changed.
            _gparam: The GLib.ParamSpec of the property that changed (unused).

        """
        selected_item = combo_row.get_selected_item()
        if isinstance(selected_item, Gtk.StringObject):
            source_style_scheme = selected_item.get_string()
            self.settings.set_string("source-style-scheme", source_style_scheme)

    @staticmethod
    def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
        """Converts a Gdk.RGBA object to a hex color string (e.g., #RRGGBB)."""
        # Ensure values are scaled to 0-255 and are integers
        red = int(rgba.red * 255)
        green = int(rgba.green * 255)
        blue = int(rgba.blue * 255)
        # Alpha is ignored for Pango foreground color in this context, typically.
        # If alpha is needed and supported, format would be #RRGGBBAA
        # and alpha = int(rgba.alpha * 255)
        return f"#{red:02x}{green:02x}{blue:02x}"

    def on_http_color_changed(self, button: Gtk.ColorDialogButton, _gparam: GObject.ParamSpec, gsettings_key: str):
        rgba = button.get_rgba()
        if rgba:
            color_hex_string = self._rgba_to_hex(rgba)
            self.settings.set_string(gsettings_key, color_hex_string)
            logging.debug(f"HTTP color for {gsettings_key} set to hex: {color_hex_string}")

    def _load_color_button_preference(self, button: Gtk.ColorDialogButton, gsettings_key: str):
        color_string = self.settings.get_string(gsettings_key)
        if color_string:
            color = Gdk.RGBA()
            try:
                if color.parse(color_string):
                    button.set_rgba(color)
                else:
                    logging.warning(f"Gdk.RGBA.parse returned false for color string '{color_string}' for GSettings key '{gsettings_key}'.")
            except GLib.Error as e:
                logging.warning(f"Failed to parse color string '{color_string}' for GSettings key '{gsettings_key}': {e}.")

    def _select_combo_row_item(
        self, combo_row: Adw.ComboRow, setting_value: str, case_sensitive: bool = True
    ) -> bool:
        """Select an item in an Adw.ComboRow based on its string value.

        Iterates through the items in the ComboRow's model (expected to be Gtk.StringList).
        If a match is found (case-sensitive or insensitive), the item is selected.

        Args:
        ----
            combo_row: The Adw.ComboRow to operate on.
            setting_value: The string value of the item to select.
            case_sensitive: Whether the string comparison should be case-sensitive.

        Returns:
        -------
            True if an item was successfully found and selected, False otherwise.

        """
        model = combo_row.get_model()
        if isinstance(model, Gtk.StringList):
            for i in range(model.get_n_items()):
                item_string = model.get_string(i)
                # Ensure item_string is not None before lower() if case_sensitive is False
                match_condition = (
                    (item_string == setting_value)
                    if case_sensitive
                    else (item_string is not None and item_string.lower() == setting_value.lower())
                )
                if match_condition:
                    combo_row.set_selected(i)
                    return True
        return False

    def load_preferences(self):
        """Load preferences from GSettings and update the UI elements accordingly.

        For each preference (font scale, theme, style scheme, DNS server),
        it retrieves the value from GSettings and sets the corresponding
        UI control (e.g., selects the correct item in a ComboRow, sets text in an EntryRow).
        """
        font_scale_pref = self.settings.get_string("font-scaling-percentage")
        if not self._select_combo_row_item(self.font_scale_combo_row, font_scale_pref):
            self.font_scale_combo_row.set_selected(0) # Default to first item if not found

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

        if self.http_header_key_color_button:
            self._load_color_button_preference(self.http_header_key_color_button, "http-output-header-key-color")
        if self.http_header_value_color_button:
            self._load_color_button_preference(self.http_header_value_color_button, "http-output-header-value-color")
        if self.http_special_row_color_button:
            self._load_color_button_preference(self.http_special_row_color_button, "http-output-special-row-color")
