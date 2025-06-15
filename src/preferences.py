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

gi.require_version("Adw", "1")  # Must be called before importing from gi.repository
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, Gtk, GLib, GObject, Gdk

# Local application imports
from .constants import APP_ID, RESOURCE_PREFIX, USER_AGENTS

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

    :ivar font_scale_combo_row: :class:`Adw.ComboRow` for font scaling.
    :vartype font_scale_combo_row: Adw.ComboRow
    :ivar theme_combo_row: :class:`Adw.ComboRow` for theme selection.
    :vartype theme_combo_row: Adw.ComboRow
    :ivar dns_server_entryrow: :class:`Adw.EntryRow` for custom DNS server.
    :vartype dns_server_entryrow: Adw.EntryRow
    :ivar prefs_dns_apply_button: :class:`Gtk.Button` to apply DNS server settings.
    :vartype prefs_dns_apply_button: Gtk.Button
    :ivar preferences_error_banner: :class:`Adw.Banner` for displaying errors.
    :vartype preferences_error_banner: Adw.Banner
    :ivar http_header_key_color_button: :class:`Gtk.ColorDialogButton` for header key color.
    :vartype http_header_key_color_button: Gtk.ColorDialogButton
    :ivar http_header_value_color_button: :class:`Gtk.ColorDialogButton` for header value color.
    :vartype http_header_value_color_button: Gtk.ColorDialogButton
    :ivar http_special_row_color_button: :class:`Gtk.ColorDialogButton` for special row color.
    :vartype http_special_row_color_button: Gtk.ColorDialogButton
    :ivar new_custom_ua_title_entry: :class:`Gtk.Entry` for new custom User-Agent title.
    :vartype new_custom_ua_title_entry: Gtk.Entry
    :ivar new_custom_ua_value_entry: :class:`Gtk.Entry` for new custom User-Agent value.
    :vartype new_custom_ua_value_entry: Gtk.Entry
    :ivar add_custom_ua_button: :class:`Gtk.Button` to add custom User-Agent.
    :vartype add_custom_ua_button: Gtk.Button
    :ivar custom_ua_list_container: :class:`Gtk.Box` (or similar container) for custom User-Agent list.
    :vartype custom_ua_list_container: Gtk.Widget
    :ivar global_output_font_button: :class:`Gtk.FontButton` for global output font.
    :vartype global_output_font_button: Gtk.FontButton
    """

    __gtype_name__ = "Preferences"

    font_scale_combo_row: Adw.ComboRow = Gtk.Template.Child("font_scale_combo_row")  # type: ignore
    theme_combo_row: Adw.ComboRow = Gtk.Template.Child("theme_combo_row")  # type: ignore
    dns_server_entryrow: Adw.EntryRow = Gtk.Template.Child("dns_server_entryrow")  # type: ignore
    prefs_dns_apply_button: Gtk.Button = Gtk.Template.Child("prefs_dns_apply_button")  # type: ignore
    preferences_error_banner: Adw.Banner = Gtk.Template.Child("preferences_error_banner")  # type: ignore

    # HTTP Output Color Rows
    http_header_key_color_button: Gtk.ColorDialogButton = Gtk.Template.Child("http_header_key_color_button")  # type: ignore
    http_header_value_color_button: Gtk.ColorDialogButton = Gtk.Template.Child("http_header_value_color_button")  # type: ignore
    http_special_row_color_button: Gtk.ColorDialogButton = Gtk.Template.Child("http_special_row_color_button")  # type: ignore

    # Custom User Agent UI
    new_custom_ua_title_entry: Gtk.Entry = Gtk.Template.Child("new_custom_ua_title_entry")  # type: ignore
    new_custom_ua_value_entry: Gtk.Entry = Gtk.Template.Child("new_custom_ua_value_entry")  # type: ignore
    add_custom_ua_button: Gtk.Button = Gtk.Template.Child("add_custom_ua_button")  # type: ignore
    custom_ua_list_container: Gtk.Box = Gtk.Template.Child("custom_ua_list_container")  # type: ignore

    # Global Font Preference UI Element
    global_output_font_button: Gtk.FontButton = Gtk.Template.Child("global_output_font_button")  # type: ignore
    user_agent_combo_row: Adw.ComboRow = Gtk.Template.Child("user_agent_combo_row") # Added for UA dropdown

    def __init__(self, main_window: Optional[Gtk.Window] = None):
        """
        Initialize the Preferences window.

        :param main_window: The parent :class:`Gtk.Window` for this dialog.
        :type main_window: Optional[Gtk.Window]
        """
        super().__init__(modal=True)
        self.main_window: Optional[Gtk.Window] = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

        if self.dns_server_entryrow:
            if hasattr(self.dns_server_entryrow, "set_subtitle"):
                if not dnspython_available:
                    self.dns_server_entryrow.set_sensitive(False)
                    self.dns_server_entryrow.set_subtitle("Requires 'dnspython' library to be installed.")
                    self.dns_server_entryrow.set_tooltip_text(
                        "Custom DNS functionality is disabled because the 'dnspython' library is not installed."
                    )
                    if self.prefs_dns_apply_button:
                        self.prefs_dns_apply_button.set_sensitive(False)
                else:
                    self.dns_server_entryrow.set_sensitive(True)
                    self.dns_server_entryrow.set_subtitle("")
                    self.dns_server_entryrow.set_tooltip_text(
                        "Enter your custom DNS server IP address. Leave empty to use system default."
                    )
                    if self.prefs_dns_apply_button:
                        self.prefs_dns_apply_button.set_sensitive(True)
                logging.info(
                    "'dnspython' status: %s. Custom DNS server entry in preferences updated.",
                    "found" if dnspython_available else "not found",
                )
            else:
                logging.warning(
                    "Adw.EntryRow 'dns_server_entryrow' does not have 'set_subtitle' method. Using tooltip fallback."
                )
                if not dnspython_available:
                    self.dns_server_entryrow.set_sensitive(False)
                    self.dns_server_entryrow.set_tooltip_text(
                        "Custom DNS disabled: 'dnspython' library not found. Install it for this feature."
                    )
                    if self.prefs_dns_apply_button:
                        self.prefs_dns_apply_button.set_sensitive(False)
                else:
                    self.dns_server_entryrow.set_sensitive(True)
                    self.dns_server_entryrow.set_tooltip_text(
                        "Enter custom DNS server IP. Leave empty for system default."
                    )
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
            self.http_header_key_color_button.connect(
                "notify::rgba", self.on_http_color_changed, "http-output-header-key-color"
            )
        if self.http_header_value_color_button:
            dialog_hv = Gtk.ColorDialog(title="Select Header Value Colour", modal=True, with_alpha=True)
            self.http_header_value_color_button.set_dialog(dialog_hv)
            self.http_header_value_color_button.connect(
                "notify::rgba", self.on_http_color_changed, "http-output-header-value-color"
            )
        if self.http_special_row_color_button:
            dialog_sr = Gtk.ColorDialog(title="Select Special Row Colour", modal=True, with_alpha=True)
            self.http_special_row_color_button.set_dialog(dialog_sr)
            self.http_special_row_color_button.connect(
                "notify::rgba", self.on_http_color_changed, "http-output-special-row-color"
            )

        # Custom User Agent Signals
        if self.add_custom_ua_button:
            self.add_custom_ua_button.connect("clicked", self._on_add_custom_ua_clicked)
        if self.new_custom_ua_title_entry:
            self.new_custom_ua_title_entry.connect("entry-activated", self._on_add_custom_ua_clicked)
        if self.new_custom_ua_value_entry:
            self.new_custom_ua_value_entry.connect("entry-activated", self._on_add_custom_ua_clicked)

        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._render_custom_ua_list())

        # Global Font Preference Signal
        if self.global_output_font_button:
            self.global_output_font_button.connect("font-set", self.on_global_font_setting_changed)

        if self.user_agent_combo_row: # Connect signal for UA dropdown
            self.user_agent_combo_row.connect("notify::selected-item", self._on_default_user_agent_changed)

    def on_global_font_setting_changed(self, font_button: Gtk.FontButton):
        """
        Handle the 'font-set' signal from the global output :class:`Gtk.FontButton`.

        Updates the 'output-font' GSettings preference with the new font
        description string. This GSettings change is then observed by
        the main window to update the CSS for relevant TextViews.

        :param font_button: The :class:`Gtk.FontButton` that emitted the signal.
        :type font_button: Gtk.FontButton
        """
        font_desc_str = font_button.get_font()  # Gets "Family [Style] Size"
        if font_desc_str:
            self.settings.set_string("output-font", font_desc_str)
            logging.debug(f"Global output font set to: {font_desc_str}")
        else:
            # This case should ideally not happen if a font is always selected.
            # Fallback to a default if it does, or log an error.
            default_font = "Sans 10"
            self.settings.set_string("output-font", default_font)
            font_button.set_font(default_font)
            logging.warning("No font description from FontButton, reset to default.")

    def on_error_banner_dismiss_clicked(self, _banner: Adw.Banner, *_args):
        """
        Handle the click event for dismissing the error banner.

        :param _banner: The :class:`Adw.Banner` that was clicked (or its dismiss button).
        :type _banner: Adw.Banner
        :param _args: Additional arguments (unused).
        :type _args: Any
        """
        self.hide_banner_and_clear_error_state()

    def on_dns_server_changed(self, _widget: Gtk.Widget):  # pylint: disable=unused-argument
        """
        Handle changes to the custom DNS server entry.

        Validates the entered IP address. If valid, saves it to GSettings.
        If invalid, displays an error banner.

        :param _widget: The :class:`Gtk.Widget` that triggered the change (:class:`Adw.EntryRow` or :class:`Gtk.Button`).
        :type _widget: Gtk.Widget
        """
        dns_server = self.dns_server_entryrow.get_text().strip()

        if not dns_server:
            self.settings.set_string("custom-dns-server", "")
            logging.info("Custom DNS server cleared.")
            self.preferences_error_banner.set_revealed(False)
            if self.dns_server_entryrow:
                self.dns_server_entryrow.remove_css_class("error")
            return

        ip_pattern = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")
        if ip_pattern.match(dns_server) and self.is_valid_ipv4(dns_server):
            self.settings.set_string("custom-dns-server", dns_server)
            logging.info("Custom DNS server set to: %s", dns_server)
            self.preferences_error_banner.set_revealed(False)
            if self.dns_server_entryrow:
                self.dns_server_entryrow.remove_css_class("error")
        else:
            if self.dns_server_entryrow:
                self.dns_server_entryrow.add_css_class("error")
            error_message = "Invalid IPv4 address for DNS server."
            logging.error(f"Invalid custom DNS server IP address provided: {dns_server}")

            self.preferences_error_banner.set_title(error_message)
            self.preferences_error_banner.set_revealed(True)
            GLib.timeout_add_seconds(4, self.hide_banner_and_clear_error_state, self.dns_server_entryrow)

    def hide_banner_and_clear_error_state(self, entry_row_widget: Optional[Adw.EntryRow] = None) -> bool:
        """
        Hide the error banner and remove 'error' CSS class from an entry row.

        This method is also used as a :func:`GLib.timeout_add_seconds` callback,
        in which case it must return :data:`GLib.SOURCE_REMOVE`.

        :param entry_row_widget: The :class:`Adw.EntryRow` to clear the error state from.
                                 If ``None``, defaults to `self.dns_server_entryrow`.
        :type entry_row_widget: Optional[Adw.EntryRow]
        :return: :data:`GLib.SOURCE_REMOVE` if called as a timeout, indicating the timer should not repeat.
                 Implicitly returns ``None`` otherwise.
        :rtype: bool
        """
        self.preferences_error_banner.set_revealed(False)
        target_entry_row = entry_row_widget if entry_row_widget else self.dns_server_entryrow
        if target_entry_row:
            target_entry_row.remove_css_class("error")
        return GLib.SOURCE_REMOVE

    @staticmethod
    def is_valid_ipv4(ip_address: str) -> bool:
        """
        Validate if the input string is a syntactically valid IPv4 address.

        :param ip_address: The string to validate.
        :type ip_address: str
        :return: ``True`` if the string is a valid IPv4 address, ``False`` otherwise.
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
        Handle changes in the font scale preference :class:`Adw.ComboRow`.

        Saves the selected font scaling percentage string to GSettings.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_scale_str = selected_item_obj.get_string()
            self.settings.set_string("font-scaling-percentage", selected_scale_str)
            logging.debug("Font scaling preference set to %s.", selected_scale_str)

    def on_theme_preference_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec):
        """
        Handle changes in the theme preference :class:`Adw.ComboRow`.

        Saves the selected theme name string (e.g., "Light", "Dark") to GSettings.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
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
        Convert a :class:`Gdk.RGBA` object to a hex color string (e.g., ``#RRGGBB``).

        :param rgba: The :class:`Gdk.RGBA` object to convert.
        :type rgba: Gdk.RGBA
        :return: The hex color string.
        :rtype: str
        """
        red = int(rgba.red * 255)
        green = int(rgba.green * 255)
        blue = int(rgba.blue * 255)
        return f"#{red:02x}{green:02x}{blue:02x}"

    def on_http_color_changed(self, button: Gtk.ColorDialogButton, _gparam: GObject.ParamSpec, gsettings_key: str):
        """
        Handle RGBA color change from a :class:`Gtk.ColorDialogButton` and save as hex.

        :param button: The :class:`Gtk.ColorDialogButton` that emitted the signal.
        :type button: Gtk.ColorDialogButton
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
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
        Load a color from GSettings (stored as hex) and apply to :class:`Gtk.ColorDialogButton`.

        :param button: The :class:`Gtk.ColorDialogButton` to apply the color to.
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
                    logging.warning(
                        f"Gdk.RGBA.parse returned false for color string '{color_string}' for GSettings key '{gsettings_key}'."
                    )
            except GLib.Error as e:
                logging.warning(
                    f"Failed to parse color string '{color_string}' for GSettings key '{gsettings_key}': {e}."
                )

    def _render_custom_ua_list(self):
        """
        Clear and repopulate the list of custom User-Agents in the UI.

        Retrieves User-Agent pairs (title, value) from GSettings,
        creates an :class:`Adw.ActionRow` for each, and adds them to the
        ``custom_ua_list_container``. Each row includes a remove button.
        """
        if not self.custom_ua_list_container:
            return

        child = self.custom_ua_list_container.get_first_child()
        while child:
            self.custom_ua_list_container.remove(child)  # type: ignore[union-attr]
            child = self.custom_ua_list_container.get_first_child()  # type: ignore[union-attr]

        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []
        )  # type: ignore[union-attr]

        for title, value in custom_ua_pairs:
            row = Adw.ActionRow(title=title, subtitle=value)
            row.set_activatable(False)  # type: ignore[no-untyped-call]
            remove_button = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER)
            remove_button.add_css_class("flat")  # type: ignore[no-untyped-call]
            remove_button.set_tooltip_text(f"Remove '{title}'")  # type: ignore[no-untyped-call]
            remove_button.connect("clicked", lambda _btn, t=title: self._on_remove_custom_ua_clicked(t))
            row.add_suffix(remove_button)  # type: ignore[no-untyped-call]
            row.set_activatable_widget(remove_button)  # type: ignore[no-untyped-call]
            self.custom_ua_list_container.append(row)  # type: ignore[union-attr]

    def _on_add_custom_ua_clicked(self, _widget: Gtk.Widget) -> None:
        """
        Handle the 'Add User Agent' button click or :class:`Gtk.Entry` activation.

        Retrieves text from the title and value entry fields.
        If both are non-empty and the title is unique, adds the new
        User-Agent pair to GSettings and updates the UI list.
        Provides visual feedback for empty fields or duplicate titles.

        :param _widget: The :class:`Gtk.Widget` that triggered the signal.
        :type _widget: Gtk.Widget
        """
        if not self.new_custom_ua_title_entry or not self.new_custom_ua_value_entry:
            return

        title_text = self.new_custom_ua_title_entry.get_text().strip()
        value_text = self.new_custom_ua_value_entry.get_text().strip()

        if not title_text or not value_text:
            logging.info("Attempted to add custom User-Agent with empty title or value.")
            if not title_text and self.new_custom_ua_title_entry:
                self.new_custom_ua_title_entry.add_css_class("error")  # type: ignore[union-attr]
            elif self.new_custom_ua_title_entry:
                self.new_custom_ua_title_entry.remove_css_class("error")  # type: ignore[union-attr]
            if not value_text and self.new_custom_ua_value_entry:
                self.new_custom_ua_value_entry.add_css_class("error")  # type: ignore[union-attr]
            elif self.new_custom_ua_value_entry:
                self.new_custom_ua_value_entry.remove_css_class("error")  # type: ignore[union-attr]
            return

        if self.new_custom_ua_title_entry:
            self.new_custom_ua_title_entry.remove_css_class("error")  # type: ignore[union-attr]
        if self.new_custom_ua_value_entry:
            self.new_custom_ua_value_entry.remove_css_class("error")  # type: ignore[union-attr]

        variant = self.settings.get_value("custom-user-agents")
        current_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []
        )  # type: ignore[union-attr]

        existing_titles = [pair[0] for pair in current_ua_pairs]
        if title_text in existing_titles:
            logging.info(f"Custom User-Agent title '{title_text}' already exists.")
            if self.new_custom_ua_title_entry:
                self.new_custom_ua_title_entry.add_css_class("error")  # type: ignore[union-attr]
            return
        elif self.new_custom_ua_title_entry:
            self.new_custom_ua_title_entry.remove_css_class("error")  # type: ignore[union-attr]

        current_ua_pairs.append((title_text, value_text))
        new_variant = GLib.Variant("a(ss)", current_ua_pairs)

        if self.settings.set_value("custom-user-agents", new_variant):
            logging.info(f"Added custom User-Agent: '{title_text}' -> '{value_text}'")
            if self.new_custom_ua_title_entry:
                self.new_custom_ua_title_entry.set_text("")  # type: ignore[union-attr]
            if self.new_custom_ua_value_entry:
                self.new_custom_ua_value_entry.set_text("")  # type: ignore[union-attr]
            self._render_custom_ua_list()
        else:
            logging.error(f"Failed to save custom User-Agent list to GSettings with new UA: {title_text}")

    def _on_remove_custom_ua_clicked(self, title_to_remove: str) -> None:
        """
        Handle the click of a 'remove' button for a custom User-Agent.

        Removes the User-Agent pair identified by ``title_to_remove`` from
        GSettings and updates the UI list.

        :param title_to_remove: The title of the User-Agent to remove.
        :type title_to_remove: str
        """
        variant = self.settings.get_value("custom-user-agents")
        current_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []
        )  # type: ignore[union-attr]

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

    def _select_combo_row_item(self, combo_row: Adw.ComboRow, setting_value: str, case_sensitive: bool = True) -> bool:
        """
        Select an item in an :class:`Adw.ComboRow` based on its string value.

        Iterates through the items in the :class:`Adw.ComboRow`'s model (expected to be :class:`Gtk.StringList`).
        If a match is found (case-sensitive or insensitive), the item is selected.

        :param combo_row: The :class:`Adw.ComboRow` to operate on.
        :type combo_row: Adw.ComboRow
        :param setting_value: The string value of the item to select.
        :type setting_value: str
        :param case_sensitive: Whether the string comparison should be case-sensitive. Defaults to ``True``.
        :type case_sensitive: bool
        :return: ``True`` if an item was successfully found and selected, ``False`` otherwise.
        :rtype: bool
        """
        model = combo_row.get_model()
        if isinstance(model, Gtk.StringList):
            for i in range(model.get_n_items()):  # type: ignore[attr-defined]
                item_string = model.get_string(i)  # type: ignore[attr-defined]
                match_condition = (
                    (item_string == setting_value)
                    if case_sensitive
                    else (item_string is not None and item_string.lower() == setting_value.lower())
                )
                if match_condition:
                    combo_row.set_selected(i)
                    return True
        return False

    def load_preferences(self) -> None:
        """
        Load preferences from GSettings and update the UI elements accordingly.

        For each preference (font scale, theme, style scheme, DNS server),
        it retrieves the value from GSettings and sets the corresponding
        UI control (e.g., selects the correct item in a :class:`Adw.ComboRow`, sets text in an :class:`Adw.EntryRow`).
        """
        font_scale_pref = self.settings.get_string("font-scaling-percentage")
        if not self._select_combo_row_item(self.font_scale_combo_row, font_scale_pref):  # type: ignore[arg-type]
            self.font_scale_combo_row.set_selected(0)  # type: ignore[union-attr]

        theme_pref_value = self.settings.get_string("theme-preference")
        if not self._select_combo_row_item(self.theme_combo_row, theme_pref_value):  # type: ignore[arg-type]
            self.theme_combo_row.set_selected(0)  # type: ignore[union-attr]

        dns_server = self.settings.get_string("custom-dns-server")
        self.dns_server_entryrow.set_text(dns_server)  # type: ignore[union-attr]

        if self.http_header_key_color_button:
            self._load_color_button_preference(self.http_header_key_color_button, "http-output-header-key-color")  # type: ignore[arg-type]
        if self.http_header_value_color_button:
            self._load_color_button_preference(self.http_header_value_color_button, "http-output-header-value-color")  # type: ignore[arg-type]
        if self.http_special_row_color_button:
            self._load_color_button_preference(self.http_special_row_color_button, "http-output-special-row-color")  # type: ignore[arg-type]

        self._render_custom_ua_list() # This will also call _populate_user_agent_combo_row

        # Default UA loading is now handled by _populate_user_agent_combo_row and subsequent explicit load
        default_ua_title = self.settings.get_string("default-user-agent-title")
        if default_ua_title:
            if not self._select_combo_row_item(self.user_agent_combo_row, default_ua_title):
                logging.warning(f"Saved default UA title '{default_ua_title}' not found in combo. Selecting first if available.")
                if self.user_agent_combo_row and self.user_agent_combo_row.get_model() and self.user_agent_combo_row.get_model().get_n_items() > 0:
                    self.user_agent_combo_row.set_selected(0)
                    first_item = self.user_agent_combo_row.get_model().get_string(0)
                    if first_item:
                         self.settings.set_string("default-user-agent-title", first_item)
                else:
                    self.settings.set_string("default-user-agent-title", "")
        elif self.user_agent_combo_row and self.user_agent_combo_row.get_model() and self.user_agent_combo_row.get_model().get_n_items() > 0:
            self.user_agent_combo_row.set_selected(0)
            first_item_title = self.user_agent_combo_row.get_model().get_string(0)
            if first_item_title:
                self.settings.set_string("default-user-agent-title", first_item_title)

        output_font_str = self.settings.get_string("output-font")
        if self.global_output_font_button:
            if output_font_str:
                self.global_output_font_button.set_font(output_font_str)  # type: ignore[union-attr]
            else:
                default_font = "Sans 10"
                self.global_output_font_button.set_font(default_font)  # type: ignore[union-attr]
                self.settings.set_string("output-font", default_font)
                logging.warning(f"GSettings 'output-font' was empty, set to default: {default_font}")

    def _populate_user_agent_combo_row(self):
        """
        Populate the 'user_agent_combo_row' with standard and custom user agents.
        """
        if not self.user_agent_combo_row:
            logging.warning("user_agent_combo_row not found, cannot populate.")
            return

        current_selection_title = None
        selected_item = self.user_agent_combo_row.get_selected_item()
        if selected_item and isinstance(selected_item, Gtk.StringObject):
            current_selection_title = selected_item.get_string()

        model = Gtk.StringList()
        all_ua_titles = []
        logging.info("PREFS_UA_DROPDOWN: Populating 'Default User Agent' dropdown...")

        logging.info("PREFS_UA_DROPDOWN: Adding default user agents (from constants.USER_AGENTS):")
        for title, _value in USER_AGENTS: # Use USER_AGENTS from constants
            model.append(title)
            all_ua_titles.append(title)
            logging.info(f"PREFS_UA_DROPDOWN: Added default title: '{title}'")

        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []
        )
        logging.info("PREFS_UA_DROPDOWN: Adding custom user agents:")
        for title, _value in custom_ua_pairs:
            if title not in all_ua_titles:
                model.append(title)
                all_ua_titles.append(title)
                logging.info(f"PREFS_UA_DROPDOWN: Added custom title: '{title}'")
            else:
                logging.warning(f"PREFS_UA_DROPDOWN: Custom UA title '{title}' conflicts with a default or another custom UA title. Skipping.")

        self.user_agent_combo_row.set_model(model)

        if current_selection_title and current_selection_title in all_ua_titles:
            if self._select_combo_row_item(self.user_agent_combo_row, current_selection_title):
                logging.debug(f"PREFS_UA_DROPDOWN: Restored previous selection in UA combo: {current_selection_title}")
                return

        default_ua_title_gsetting = self.settings.get_string("default-user-agent-title")
        if default_ua_title_gsetting and default_ua_title_gsetting in all_ua_titles:
            if self._select_combo_row_item(self.user_agent_combo_row, default_ua_title_gsetting):
                logging.debug(f"PREFS_UA_DROPDOWN: Selected default UA from GSettings: {default_ua_title_gsetting}")
                return

        if model.get_n_items() > 0:
            self.user_agent_combo_row.set_selected(0)
            first_item_title = model.get_string(0)
            logging.debug(f"PREFS_UA_DROPDOWN: No previous/GSettings default UA, selected first available: {first_item_title}")
            if first_item_title and (not default_ua_title_gsetting or default_ua_title_gsetting not in all_ua_titles) :
                 self.settings.set_string("default-user-agent-title", first_item_title)
        else:
            logging.warning("PREFS_UA_DROPDOWN: No user agents available to select.")
            if default_ua_title_gsetting:
                 self.settings.set_string("default-user-agent-title", "")

        final_selected_item_obj = self.user_agent_combo_row.get_selected_item()
        final_selected_title = final_selected_item_obj.get_string() if final_selected_item_obj else "None (or empty list)"
        logging.info(f"PREFS_UA_DROPDOWN: Population complete. Final selected title: '{final_selected_title}'")

    def _on_default_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec):
        """
        Handle changes in the default user agent selection.
        Saves the selected user agent *title* to GSettings.
        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_ua_title = selected_item_obj.get_string()
            if selected_ua_title:
                current_gsettings_val = self.settings.get_string("default-user-agent-title")
                if selected_ua_title != current_gsettings_val:
                    self.settings.set_string("default-user-agent-title", selected_ua_title)
                    logging.info(f"PREFS_UA_SAVE: Default user agent title saved to GSettings: '{selected_ua_title}'")
                else:
                    logging.info(f"PREFS_UA_SAVE: Selected title '{selected_ua_title}' is already the GSettings value. No change.")
            else:
                logging.warning("PREFS_UA_SAVE: Attempted to save an empty/None title. Current GSettings: '%s'", self.settings.get_string('default-user-agent-title'))
        elif selected_item_obj is None and combo_row.get_model() is not None and combo_row.get_model().get_n_items() == 0:
            current_gsettings_val = self.settings.get_string("default-user-agent-title")
            if current_gsettings_val != "":
                self.settings.set_string("default-user-agent-title", "")
                logging.info("PREFS_UA_SAVE: Default user agent selection cleared in GSettings as list is empty.")
            else:
                logging.info("PREFS_UA_SAVE: List is empty and GSettings default is already empty. No change.")
