"""
Manages the application's preferences window and settings.

This module provides the UI and logic for adjusting application-wide
preferences such as font scaling, color themes, source view style schemes,
custom DNS server settings, and HTTP output colors. It interacts with
GSettings to persist these preferences.
"""
import logging
import re
from typing import Optional

import gi
gi.require_version("Adw", "1") # Must be called before importing from gi.repository
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, Gtk, GLib, GObject, Gdk

# Local application imports
from .constants import APP_ID, RESOURCE_PREFIX

# Conditional import for dnspython
try:
    # import dns.resolver # Commented out as it's unused
    dnspython_available = True
except ImportError:
    dnspython_available = False


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    """
    Adw.PreferencesWindow subclass for managing application settings.

    This class defines the structure and behavior of the preferences dialog,
    allowing users to customize various aspects of the application.
    It binds UI elements to GSettings for persistence.

    :ivar font_scale_combo_row: ComboRow for font scaling.
    :ivar theme_combo_row: ComboRow for theme selection.
    :ivar source_style_scheme_combo_row: ComboRow for source style scheme.
    :ivar dns_server_entryrow: EntryRow for custom DNS server.
    :ivar prefs_dns_apply_button: Button to apply DNS server settings.
    :ivar preferences_error_banner: Banner for displaying errors.
    :ivar http_header_key_color_button: Button for header key color.
    :ivar http_header_value_color_button: Button for header value color.
    :ivar http_special_row_color_button: Button for special row color.
    :ivar new_custom_ua_title_entry: Entry for new custom User-Agent title.
    :ivar new_custom_ua_value_entry: Entry for new custom User-Agent value.
    :ivar add_custom_ua_button: Button to add custom User-Agent.
    :ivar custom_ua_list_container: Container for custom User-Agent list.
    """

    __gtype_name__ = "Preferences"

    font_scale_combo_row = Gtk.Template.Child("font_scale_combo_row")
    theme_combo_row = Gtk.Template.Child("theme_combo_row")
    dns_server_entryrow = Gtk.Template.Child("dns_server_entryrow")
    prefs_dns_apply_button = Gtk.Template.Child("prefs_dns_apply_button")
    preferences_error_banner = Gtk.Template.Child("preferences_error_banner")

    # HTTP Output Color Rows
    http_header_key_color_button = Gtk.Template.Child("http_header_key_color_button")
    http_header_value_color_button = Gtk.Template.Child("http_header_value_color_button")
    http_special_row_color_button = Gtk.Template.Child("http_special_row_color_button")

    # Custom User Agent UI
    new_custom_ua_title_entry = Gtk.Template.Child("new_custom_ua_title_entry")
    new_custom_ua_value_entry = Gtk.Template.Child("new_custom_ua_value_entry")
    add_custom_ua_button = Gtk.Template.Child("add_custom_ua_button")
    custom_ua_list_container = Gtk.Template.Child("custom_ua_list_container")

    # Font Preference UI Elements
    http_output_font_button = Gtk.Template.Child("http_output_font_button")
    http_output_font_size_spinrow = Gtk.Template.Child("http_output_font_size_spinrow")
    dns_output_font_button = Gtk.Template.Child("dns_output_font_button")
    dns_output_font_size_spinrow = Gtk.Template.Child("dns_output_font_size_spinrow")
    nmap_output_font_button = Gtk.Template.Child("nmap_output_font_button")
    nmap_output_font_size_spinrow = Gtk.Template.Child("nmap_output_font_size_spinrow")
    nmap_raw_output_font_button = Gtk.Template.Child("nmap_raw_output_font_button")
    nmap_raw_output_font_size_spinrow = Gtk.Template.Child("nmap_raw_output_font_size_spinrow")
    webscan_output_font_button = Gtk.Template.Child("webscan_output_font_button")
    webscan_output_font_size_spinrow = Gtk.Template.Child("webscan_output_font_size_spinrow")


    def __init__(self, main_window: Optional[Gtk.Window] = None):
        """
        Initialize the Preferences window.

        :param main_window: The parent Gtk.Window for this dialog.
        :type main_window: Optional[Gtk.Window]
        """
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

        # Handle dnspython absence for DNS server entry
        if self.dns_server_entryrow: # Ensure the row object exists
            if hasattr(self.dns_server_entryrow, "set_subtitle"):
                if not dnspython_available:
                    self.dns_server_entryrow.set_sensitive(False)
                    self.dns_server_entryrow.set_subtitle("Requires 'dnspython' library to be installed.")
                    self.dns_server_entryrow.set_tooltip_text("Custom DNS functionality is disabled because the 'dnspython' library is not installed.")
                    if self.prefs_dns_apply_button:
                        self.prefs_dns_apply_button.set_sensitive(False)
                else:
                    self.dns_server_entryrow.set_sensitive(True)
                    self.dns_server_entryrow.set_subtitle("") # Clear subtitle
                    self.dns_server_entryrow.set_tooltip_text("Enter your custom DNS server IP address. Leave empty to use system default.")
                    if self.prefs_dns_apply_button:
                        self.prefs_dns_apply_button.set_sensitive(True)
                logging.info(
                    "'dnspython' status: %s. Custom DNS server entry in preferences updated.",
                    "found" if dnspython_available else "not found"
                )
            else:
                logging.warning("Adw.EntryRow 'dns_server_entryrow' does not have 'set_subtitle' method. Using tooltip fallback.")
                if not dnspython_available:
                    self.dns_server_entryrow.set_sensitive(False)
                    self.dns_server_entryrow.set_tooltip_text("Custom DNS disabled: 'dnspython' library not found. Install it for this feature.")
                    if self.prefs_dns_apply_button:
                        self.prefs_dns_apply_button.set_sensitive(False)
                else:
                    self.dns_server_entryrow.set_sensitive(True)
                    self.dns_server_entryrow.set_tooltip_text("Enter custom DNS server IP. Leave empty for system default.")
        else:
            logging.error("'dns_server_entryrow' template child not found during __init__.")


    def load_ui(self):
        """
        Connect signals for UI elements.

        This method sets up connections for various UI elements to their
        respective handler functions, and initializes GSettings listeners.
        """
        self.font_scale_combo_row.connect("notify::selected", self.on_font_scale_changed)
        self.theme_combo_row.connect("notify::selected", self.on_theme_preference_changed)
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

        # Custom User Agent Signals
        if self.add_custom_ua_button:
            self.add_custom_ua_button.connect("clicked", self._on_add_custom_ua_clicked)
        if self.new_custom_ua_title_entry:
            self.new_custom_ua_title_entry.connect("entry-activated", self._on_add_custom_ua_clicked)
        if self.new_custom_ua_value_entry:
            self.new_custom_ua_value_entry.connect("entry-activated", self._on_add_custom_ua_clicked)

        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._render_custom_ua_list())

        # Font Preference Signals
        if self.http_output_font_button:
            self.http_output_font_button.connect("font-set", self.on_font_setting_changed, "http-output-font-family")
        if self.http_output_font_size_spinrow:
            self.http_output_font_size_spinrow.connect("notify::value", self.on_font_size_setting_changed, "http-output-font-size")

        if self.dns_output_font_button:
            self.dns_output_font_button.connect("font-set", self.on_font_setting_changed, "dns-output-font-family")
        if self.dns_output_font_size_spinrow:
            self.dns_output_font_size_spinrow.connect("notify::value", self.on_font_size_setting_changed, "dns-output-font-size")

        if self.nmap_output_font_button:
            self.nmap_output_font_button.connect("font-set", self.on_font_setting_changed, "nmap-output-font-family")
        if self.nmap_output_font_size_spinrow:
            self.nmap_output_font_size_spinrow.connect("notify::value", self.on_font_size_setting_changed, "nmap-output-font-size")

        if self.nmap_raw_output_font_button:
            self.nmap_raw_output_font_button.connect("font-set", self.on_font_setting_changed, "nmap-raw-output-font-family")
        if self.nmap_raw_output_font_size_spinrow:
            self.nmap_raw_output_font_size_spinrow.connect("notify::value", self.on_font_size_setting_changed, "nmap-raw-output-font-size")

        if self.webscan_output_font_button:
            self.webscan_output_font_button.connect("font-set", self.on_font_setting_changed, "webscan-output-font-family")
        if self.webscan_output_font_size_spinrow:
            self.webscan_output_font_size_spinrow.connect("notify::value", self.on_font_size_setting_changed, "webscan-output-font-size")


    def on_error_banner_dismiss_clicked(self, _banner: Adw.Banner, *_args):
        """
        Handle the click event for dismissing the error banner.

        :param _banner: The Adw.Banner that was clicked (or its dismiss button).
        :type _banner: Adw.Banner
        :param _args: Additional arguments (unused).
        """
        self.hide_banner_and_clear_error_state()

    def on_dns_server_changed(self, _widget: Gtk.Widget):  # pylint: disable=unused-argument
        """
        Handle changes to the custom DNS server entry.

        Validates the entered IP address. If valid, saves it to GSettings.
        If invalid, displays an error banner.

        :param _widget: The widget that triggered the change (Adw.EntryRow or Gtk.Button).
        :type _widget: Gtk.Widget
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
            logging.error(f"Invalid custom DNS server IP address provided: {dns_server}")

            self.preferences_error_banner.set_title(error_message)
            self.preferences_error_banner.set_revealed(True)
            # Do NOT clear self.dns_server_entryrow.set_text("") here.
            # Pass self.dns_server_entryrow explicitly to the timeout handler
            GLib.timeout_add_seconds(
                4, self.hide_banner_and_clear_error_state, self.dns_server_entryrow
            )

    def hide_banner_and_clear_error_state(self, entry_row_widget: Optional[Adw.EntryRow] = None) -> bool:
        """
        Hide the error banner and remove 'error' CSS class from an entry row.

        This method is also used as a GLib.timeout_add_seconds callback,
        in which case it must return GLib.SOURCE_REMOVE.

        :param entry_row_widget: The Adw.EntryRow to clear the error state from.
                                 If None, defaults to `self.dns_server_entryrow`.
        :type entry_row_widget: Optional[Adw.EntryRow]
        :return: GLib.SOURCE_REMOVE if called as a timeout, indicating the timer should not repeat.
                 Implicitly returns None otherwise.
        :rtype: bool
        """
        self.preferences_error_banner.set_revealed(False)
        target_entry_row = entry_row_widget if entry_row_widget else self.dns_server_entryrow
        if target_entry_row:
            target_entry_row.remove_css_class("error")
        return GLib.SOURCE_REMOVE # Suitable for GLib.timeout_add

    @staticmethod
    def is_valid_ipv4(ip_address: str) -> bool:
        """
        Validate if the input string is a syntactically valid IPv4 address.

        :param ip_address: The string to validate.
        :type ip_address: str
        :return: True if the string is a valid IPv4 address, False otherwise.
        :rtype: bool
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

    def on_font_scale_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec):
        """
        Handle changes in the font scale preference ComboRow.

        Saves the selected font scaling percentage string to GSettings.

        :param combo_row: The Adw.ComboRow whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The GLib.ParamSpec of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_scale_str = selected_item_obj.get_string()
            self.settings.set_string("font-scaling-percentage", selected_scale_str)
            logging.debug("Font scaling preference set to %s.", selected_scale_str)

    def on_theme_preference_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec):
        """
        Handle changes in the theme preference ComboRow.

        Saves the selected theme name string (e.g., "Light", "Dark") to GSettings.

        :param combo_row: The Adw.ComboRow whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The GLib.ParamSpec of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_theme_str = selected_item_obj.get_string()
            self.settings.set_string("theme-preference", selected_theme_str)
            logging.debug(
                "Theme preference set to %s. WoesWindow will handle the change.",
                selected_theme_str,
            )

    @staticmethod
    def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
        """
        Convert a Gdk.RGBA object to a hex color string (e.g., #RRGGBB).

        :param rgba: The Gdk.RGBA object to convert.
        :type rgba: Gdk.RGBA
        :return: The hex color string.
        :rtype: str
        """
        # Ensure values are scaled to 0-255 and are integers
        red = int(rgba.red * 255)
        green = int(rgba.green * 255)
        blue = int(rgba.blue * 255)
        # Alpha is ignored for Pango foreground color in this context, typically.
        # If alpha is needed and supported, format would be #RRGGBBAA
        # and alpha = int(rgba.alpha * 255)
        return f"#{red:02x}{green:02x}{blue:02x}"

    def on_http_color_changed(self, button: Gtk.ColorDialogButton, _gparam: GObject.ParamSpec, gsettings_key: str):
        """
        Handle RGBA color change from a Gtk.ColorDialogButton and save as hex.

        :param button: The Gtk.ColorDialogButton that emitted the signal.
        :type button: Gtk.ColorDialogButton
        :param _gparam: The GLib.ParamSpec of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        :param gsettings_key: The GSettings key to save the color to.
        :type gsettings_key: str
        """
        rgba = button.get_rgba()
        if rgba:
            color_hex_string = self._rgba_to_hex(rgba)
            self.settings.set_string(gsettings_key, color_hex_string)
            logging.debug(f"HTTP color for {gsettings_key} set to hex: {color_hex_string}")

    def _load_color_button_preference(self, button: Gtk.ColorDialogButton, gsettings_key: str):
        """
        Load a color from GSettings (stored as hex) and apply to Gtk.ColorDialogButton.

        :param button: The Gtk.ColorDialogButton to apply the color to.
        :type button: Gtk.ColorDialogButton
        :param gsettings_key: The GSettings key to load the color from.
        :type gsettings_key: str
        """
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

    def on_font_setting_changed(self, font_button: Gtk.FontButton, gsettings_key_family: str):
        """
        Handle changes in a Gtk.FontButton selection.

        Parses the font family name from the selected font string (e.g., "Sans 12")
        and saves it to the specified GSettings key.

        :param font_button: The Gtk.FontButton whose selection changed.
        :type font_button: Gtk.FontButton
        :param gsettings_key_family: The GSettings key for the font family.
        :type gsettings_key_family: str
        """
        font_desc_str = font_button.get_font() # Gets "Family List Style Options Size"
        if not font_desc_str:
            logging.warning(f"No font description string obtained from {font_button} for {gsettings_key_family}.")
            return

        # Attempt to parse the family name.
        # Pango font description: "Family [Style Options] Size"
        # The family is everything before the last space-separated number (size).
        # If only family is present (e.g. "Sans"), it's used as is.
        parts = font_desc_str.split(' ')
        font_family_name = font_desc_str # Default to full string if parsing fails

        if len(parts) > 1:
            # Check if the last part is a number (size)
            if parts[-1].isdigit():
                font_family_name = ' '.join(parts[:-1])
            else:
                # If last part is not a number, it's part of the family name (e.g., "DejaVu Sans Mono")
                font_family_name = ' '.join(parts)
        elif len(parts) == 1: # Only family name, no size (e.g. "Monospace")
             font_family_name = parts[0]


        self.settings.set_string(gsettings_key_family, font_family_name)
        logging.debug(f"Font family for {gsettings_key_family} set to '{font_family_name}'.")
        # Update the other widget (size spinrow) to reflect the new font family's default size,
        # or better, reload the combined font string for the button.
        self._load_font_preference(font_button, gsettings_key_family, self._get_size_spinrow_for_font_button(font_button))


    def on_font_size_setting_changed(self, spin_row: Adw.SpinRow, _gparam: GObject.ParamSpec, gsettings_key_size: str):
        """
        Handle changes in an Adw.SpinRow for font size.

        Saves the selected font size to the specified GSettings key.

        :param spin_row: The Adw.SpinRow whose value changed.
        :type spin_row: Adw.SpinRow
        :param _gparam: The GLib.ParamSpec of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        :param gsettings_key_size: The GSettings key for the font size.
        :type gsettings_key_size: str
        """
        font_size = int(spin_row.get_value())
        self.settings.set_int(gsettings_key_size, font_size)
        logging.debug(f"Font size for {gsettings_key_size} set to {font_size}pt.")
        # Update the corresponding font button to reflect the new size
        self._load_font_preference(self._get_font_button_for_size_spinrow(spin_row), self._get_family_key_for_size_spinrow(spin_row), spin_row)


    def _get_size_spinrow_for_font_button(self, font_button: Gtk.FontButton) -> Optional[Adw.SpinRow]:
        """Helper to find the corresponding size spinrow for a given font button."""
        if font_button == self.http_output_font_button: return self.http_output_font_size_spinrow
        if font_button == self.dns_output_font_button: return self.dns_output_font_size_spinrow
        if font_button == self.nmap_output_font_button: return self.nmap_output_font_size_spinrow
        if font_button == self.nmap_raw_output_font_button: return self.nmap_raw_output_font_size_spinrow
        if font_button == self.webscan_output_font_button: return self.webscan_output_font_size_spinrow
        return None

    def _get_font_button_for_size_spinrow(self, spin_row: Adw.SpinRow) -> Optional[Gtk.FontButton]:
        """Helper to find the corresponding font button for a given size spinrow."""
        if spin_row == self.http_output_font_size_spinrow: return self.http_output_font_button
        if spin_row == self.dns_output_font_size_spinrow: return self.dns_output_font_button
        if spin_row == self.nmap_output_font_size_spinrow: return self.nmap_output_font_button
        if spin_row == self.nmap_raw_output_font_size_spinrow: return self.nmap_raw_output_font_button
        if spin_row == self.webscan_output_font_size_spinrow: return self.webscan_output_font_button
        return None

    def _get_family_key_for_size_spinrow(self, spin_row: Adw.SpinRow) -> Optional[str]:
        """Helper to get the font family GSettings key associated with a size spinrow."""
        if spin_row == self.http_output_font_size_spinrow: return "http-output-font-family"
        if spin_row == self.dns_output_font_size_spinrow: return "dns-output-font-family"
        if spin_row == self.nmap_output_font_size_spinrow: return "nmap-output-font-family"
        if spin_row == self.nmap_raw_output_font_size_spinrow: return "nmap-raw-output-font-family"
        if spin_row == self.webscan_output_font_size_spinrow: return "webscan-output-font-family"
        return None


    def _load_font_preference(self, font_button: Optional[Gtk.FontButton], gsettings_key_family: str, size_spin_row: Optional[Adw.SpinRow]):
        """
        Load font family and size from GSettings and update the UI elements.

        :param font_button: The Gtk.FontButton to update.
        :type font_button: Gtk.FontButton
        :param gsettings_key_family: The GSettings key for the font family.
        :type gsettings_key_family: str
        :param size_spin_row: The Adw.SpinRow for font size to update.
        :type size_spin_row: Adw.SpinRow
        """
        if not font_button or not size_spin_row:
            logging.warning(f"Missing font_button or size_spin_row for GSettings key: {gsettings_key_family}")
            return

        gsettings_key_size = gsettings_key_family.replace("-family", "-size") # Derive size key

        font_family = self.settings.get_string(gsettings_key_family)
        font_size = self.settings.get_int(gsettings_key_size)

        # Ensure defaults if keys are somehow missing (should not happen if schema is correct)
        if not font_family:
            font_family = "Sans" # A sensible default
            logging.warning(f"Missing GSettings value for {gsettings_key_family}, using default '{font_family}'.")
        if font_size == 0: # GSettings default for int if not set might be 0
            font_size = 10 # A sensible default
            logging.warning(f"Missing GSettings value for {gsettings_key_size}, using default {font_size}pt.")


        font_description = f"{font_family} {font_size}"
        font_button.set_font(font_description)
        # font_button.set_preview_text(f"{font_family} {font_size}pt") # Optional: Set preview text

        size_spin_row.set_value(font_size)
        logging.debug(f"Loaded font for {gsettings_key_family}: '{font_description}' ({font_size}pt).")


    def _render_custom_ua_list(self):
        """
        Clear and repopulate the list of custom User-Agents in the UI.

        Retrieves User-Agent pairs (title, value) from GSettings,
        creates an Adw.ActionRow for each, and adds them to the
        custom_ua_list_container. Each row includes a remove button.
        """
        if not self.custom_ua_list_container:
            return

        # Clear existing rows
        child = self.custom_ua_list_container.get_first_child()
        while child:
            self.custom_ua_list_container.remove(child)
            child = self.custom_ua_list_container.get_first_child()

        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs = list(variant.unpack() if variant and variant.get_type_string() == 'a(ss)' else [])

        for title, value in custom_ua_pairs:
            row = Adw.ActionRow(title=title, subtitle=value) # Display title and value
            row.set_activatable(False)
            remove_button = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER)
            remove_button.add_css_class("flat")
            remove_button.set_tooltip_text(f"Remove '{title}'")
            # Pass the title (assuming titles are unique for removal)
            remove_button.connect("clicked", lambda _btn, t=title: self._on_remove_custom_ua_clicked(t))
            row.add_suffix(remove_button)
            row.set_activatable_widget(remove_button)
            self.custom_ua_list_container.append(row)

    def _on_add_custom_ua_clicked(self, _widget: Gtk.Widget):
        """
        Handle the 'Add User Agent' button click or entry activation.

        Retrieves text from the title and value entry fields.
        If both are non-empty and the title is unique, adds the new
        User-Agent pair to GSettings and updates the UI list.
        Provides visual feedback for empty fields or duplicate titles.

        :param _widget: The widget that triggered the signal.
        :type _widget: Gtk.Widget
        """
        if not self.new_custom_ua_title_entry or not self.new_custom_ua_value_entry:
            return

        title_text = self.new_custom_ua_title_entry.get_text().strip()
        value_text = self.new_custom_ua_value_entry.get_text().strip()

        if not title_text or not value_text:
            logging.info("Attempted to add custom User-Agent with empty title or value.")
            if not title_text and self.new_custom_ua_title_entry:
                self.new_custom_ua_title_entry.add_css_class("error")
            elif self.new_custom_ua_title_entry:
                 self.new_custom_ua_title_entry.remove_css_class("error")
            if not value_text and self.new_custom_ua_value_entry:
                self.new_custom_ua_value_entry.add_css_class("error")
            elif self.new_custom_ua_value_entry:
                self.new_custom_ua_value_entry.remove_css_class("error")
            return

        if self.new_custom_ua_title_entry:
            self.new_custom_ua_title_entry.remove_css_class("error")
        if self.new_custom_ua_value_entry:
            self.new_custom_ua_value_entry.remove_css_class("error")

        variant = self.settings.get_value("custom-user-agents")
        current_ua_pairs = list(variant.unpack() if variant and variant.get_type_string() == 'a(ss)' else [])

        existing_titles = [pair[0] for pair in current_ua_pairs]
        if title_text in existing_titles:
            logging.info(f"Custom User-Agent title '{title_text}' already exists.")
            if self.new_custom_ua_title_entry:
                 self.new_custom_ua_title_entry.add_css_class("error")
            return
        elif self.new_custom_ua_title_entry:
            self.new_custom_ua_title_entry.remove_css_class("error")

        current_ua_pairs.append((title_text, value_text))
        new_variant = GLib.Variant("a(ss)", current_ua_pairs)

        if self.settings.set_value("custom-user-agents", new_variant):
            logging.info(f"Added custom User-Agent: '{title_text}' -> '{value_text}'")
            if self.new_custom_ua_title_entry:
                self.new_custom_ua_title_entry.set_text("")
            if self.new_custom_ua_value_entry:
                self.new_custom_ua_value_entry.set_text("")
            self._render_custom_ua_list()
        else:
            logging.error(f"Failed to save custom User-Agent list to GSettings with new UA: {title_text}")

    def _on_remove_custom_ua_clicked(self, title_to_remove: str):
        """
        Handle the click of a 'remove' button for a custom User-Agent.

        Removes the User-Agent pair identified by title_to_remove from
        GSettings and updates the UI list.

        :param title_to_remove: The title of the User-Agent to remove.
        :type title_to_remove: str
        """
        variant = self.settings.get_value("custom-user-agents")
        current_ua_pairs = list(variant.unpack() if variant and variant.get_type_string() == 'a(ss)' else [])

        original_length = len(current_ua_pairs)
        updated_ua_pairs = [pair for pair in current_ua_pairs if pair[0] != title_to_remove]

        if len(updated_ua_pairs) < original_length:
            new_variant = GLib.Variant("a(ss)", updated_ua_pairs)
            if self.settings.set_value("custom-user-agents", new_variant):
                logging.info(f"Removed custom User-Agent with title: {title_to_remove}")
                self._render_custom_ua_list()
            else:
                logging.error(f"Failed to save custom User-Agent list after removing title: {title_to_remove}")
        else:
            logging.warning(f"Attempted to remove non-existent User-Agent with title: {title_to_remove}")

    def _select_combo_row_item(
        self, combo_row: Adw.ComboRow, setting_value: str, case_sensitive: bool = True
    ) -> bool:
        """
        Select an item in an Adw.ComboRow based on its string value.

        Iterates through the items in the ComboRow's model (expected to be Gtk.StringList).
        If a match is found (case-sensitive or insensitive), the item is selected.

        :param combo_row: The Adw.ComboRow to operate on.
        :type combo_row: Adw.ComboRow
        :param setting_value: The string value of the item to select.
        :type setting_value: str
        :param case_sensitive: Whether the string comparison should be case-sensitive.
        :type case_sensitive: bool
        :return: True if an item was successfully found and selected, False otherwise.
        :rtype: bool
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
        """
        Load preferences from GSettings and update the UI elements accordingly.

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

        dns_server = self.settings.get_string("custom-dns-server")
        self.dns_server_entryrow.set_text(dns_server)

        if self.http_header_key_color_button:
            self._load_color_button_preference(self.http_header_key_color_button, "http-output-header-key-color")
        if self.http_header_value_color_button:
            self._load_color_button_preference(self.http_header_value_color_button, "http-output-header-value-color")
        if self.http_special_row_color_button:
            self._load_color_button_preference(self.http_special_row_color_button, "http-output-special-row-color")

        self._render_custom_ua_list()

        # Load Font Preferences
        if self.http_output_font_button and self.http_output_font_size_spinrow:
            self._load_font_preference(self.http_output_font_button, "http-output-font-family", self.http_output_font_size_spinrow)
        if self.dns_output_font_button and self.dns_output_font_size_spinrow:
            self._load_font_preference(self.dns_output_font_button, "dns-output-font-family", self.dns_output_font_size_spinrow)
        if self.nmap_output_font_button and self.nmap_output_font_size_spinrow:
            self._load_font_preference(self.nmap_output_font_button, "nmap-output-font-family", self.nmap_output_font_size_spinrow)
        if self.nmap_raw_output_font_button and self.nmap_raw_output_font_size_spinrow:
            self._load_font_preference(self.nmap_raw_output_font_button, "nmap-raw-output-font-family", self.nmap_raw_output_font_size_spinrow)
        if self.webscan_output_font_button and self.webscan_output_font_size_spinrow:
            self._load_font_preference(self.webscan_output_font_button, "webscan-output-font-family", self.webscan_output_font_size_spinrow)
