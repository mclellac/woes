"""Manages the application's preferences window and settings persistence.

This module defines the :class:`Preferences` class, an :class:`Adw.PreferencesWindow`
subclass, which provides the user interface for configuring various application
settings. These include appearance (font scaling, theme, GtkSourceView styles),
network configurations (custom DNS server), HTTP client behavior (custom User-Agents,
output coloring), and other customizable options.

Preferences are loaded from and saved to :class:`Gio.Settings` (GSettings)
to ensure they persist across application sessions.
"""
from .constants import APP_ID, RESOURCE_PREFIX
from gi.repository import Adw, Gio, Gtk, GLib, GObject, Gdk
import logging
import re
from typing import Optional, List, Tuple, Any # Added List, Tuple, Any for type hints

import gi
# Must be called before importing from gi.repository
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


logger = logging.getLogger(__name__)

# Conditional import check for dnspython library.
# This boolean is used to enable/disable UI elements related to custom DNS.
try:
    import dns.version # Try importing a minimal part of dnspython
    dnspython_available = True
    logger.debug("dnspython library found, version: %s", dns.version.version)
except ImportError:
    dnspython_available = False
    logger.info("dnspython library not found. Custom DNS server functionality will be disabled.")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    """Provides the application's preferences window (dialog).

    This class is an :class:`Adw.PreferencesWindow` that loads its UI layout
    from a GtkBuilder XML template. It allows users to customize various
    application settings, such as:
    - Font scaling and application theme.
    - Style schemes for GtkSourceView elements.
    - Custom DNS server configuration (conditionally available if `dnspython` is installed).
    - Color preferences for HTTP output display.
    - Management of a list of custom User-Agent strings.

    User selections are typically bound to :class:`Gio.Settings` keys for persistence
    across sessions. The window also handles validation for certain inputs (e.g., DNS server IP).
    """

    __gtype_name__ = "Preferences"

    # --- Template Children Declarations ---
    font_scale_combo_row: Adw.ComboRow = Gtk.Template.Child() # type: ignore
    theme_combo_row: Adw.ComboRow = Gtk.Template.Child() # type: ignore
    source_style_scheme_combo_row: Adw.ComboRow = Gtk.Template.Child() # type: ignore
    dns_server_entryrow: Adw.EntryRow = Gtk.Template.Child() # type: ignore
    prefs_dns_apply_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    preferences_error_banner: Adw.Banner = Gtk.Template.Child() # type: ignore

    http_header_key_color_button: Gtk.ColorDialogButton = Gtk.Template.Child() # type: ignore
    http_header_value_color_button: Gtk.ColorDialogButton = Gtk.Template.Child() # type: ignore
    http_special_row_color_button: Gtk.ColorDialogButton = Gtk.Template.Child() # type: ignore

    new_custom_ua_title_entry: Gtk.Entry = Gtk.Template.Child() # type: ignore
    new_custom_ua_value_entry: Gtk.Entry = Gtk.Template.Child() # type: ignore
    add_custom_ua_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    custom_ua_list_container: Gtk.Box = Gtk.Template.Child() # type: ignore

    def __init__(self, main_window: Optional[Gtk.Window] = None, **kwargs: Any) -> None:
        """Initializes the Preferences window.

        Sets the window to be modal and transient for the main application window.
        Initializes :class:`Gio.Settings` for the application, loads the UI
        from the template using :func:`Gtk.Widget.init_template`, connects signal handlers,
        and populates the UI fields with currently saved preference values.
        Also handles conditional UI setup, such as disabling DNS server entry
        if `dnspython` is not available.

        :param main_window: The parent :class:`Gtk.Window` for this dialog.
                            Defaults to ``None``.
        :type main_window: Optional[Gtk.Window]
        :param kwargs: Additional keyword arguments passed to the :class:`Adw.PreferencesWindow` constructor.
        :type kwargs: Any
        :return: None
        :rtype: None
        """
        super().__init__(modal=True, **kwargs)
        self.main_window = main_window
        if main_window:
            self.set_transient_for(main_window)

        self.settings = Gio.Settings(schema_id=APP_ID)
        self.init_template() # Initialize Gtk.Template children
        self._load_ui_connections() # Connect signals
        self.load_preferences() # Populate UI with current settings

        self._configure_dns_section()

    def _configure_dns_section(self) -> None:
        """Configures the DNS server section based on `dnspython` availability."""
        if not self.dns_server_entryrow:
            logging.error("Preferences: 'dns_server_entryrow' template child not found during _configure_dns_section.")
            return

        # hasattr check is good practice if UI elements might vary or be optional
        if hasattr(self.dns_server_entryrow, "set_subtitle"):
            if not dnspython_available:
                self.dns_server_entryrow.set_sensitive(False)
                self.dns_server_entryrow.set_subtitle("Requires 'dnspython' library (pip install dnspython)")
                self.dns_server_entryrow.set_tooltip_text(
                    "Custom DNS functionality is disabled because the 'dnspython' library is not installed.")
                if self.prefs_dns_apply_button:
                    self.prefs_dns_apply_button.set_sensitive(False)
            else:
                self.dns_server_entryrow.set_sensitive(True)
                self.dns_server_entryrow.set_subtitle("Leave empty to use system default DNS.")
                self.dns_server_entryrow.set_tooltip_text(
                    "Enter your custom DNS server IP address (e.g., 1.1.1.1).")
                if self.prefs_dns_apply_button:
                    self.prefs_dns_apply_button.set_sensitive(True)
            logging.info("'dnspython' status: %s. Custom DNS server entry in preferences updated.",
                         "found" if dnspython_available else "not found")
        else: # Fallback if set_subtitle is not available for some reason
            logging.warning("Preferences: Adw.EntryRow 'dns_server_entryrow' does not have 'set_subtitle' method.")
            tooltip_base = "Enter custom DNS server IP. Leave empty for system default."
            if not dnspython_available:
                self.dns_server_entryrow.set_sensitive(False)
                tooltip_base = "Custom DNS disabled: 'dnspython' library not found."
                if self.prefs_dns_apply_button: self.prefs_dns_apply_button.set_sensitive(False)
            else:
                self.dns_server_entryrow.set_sensitive(True)
                if self.prefs_dns_apply_button: self.prefs_dns_apply_button.set_sensitive(True)
            self.dns_server_entryrow.set_tooltip_text(tooltip_base)


    def _load_ui_connections(self) -> None:
        """Connects signals for various UI elements to their respective handlers.

        This method is responsible for setting up the event-driven interactions
        within the preferences window, such as connecting 'notify::selected' for
        combo rows, 'clicked' for buttons, and 'entry-activated' for entry rows.
        It also sets up :class:`Gtk.ColorDialog` instances for color picker buttons
        and connects GSettings 'changed' signals for dynamic updates like the
        custom User-Agent list.

        :return: None
        :rtype: None
        """
        self.font_scale_combo_row.connect("notify::selected", self.on_font_scale_changed)
        self.theme_combo_row.connect("notify::selected", self.on_theme_preference_changed)
        self.source_style_scheme_combo_row.connect("notify::selected", self.on_source_style_scheme_changed)
        self.dns_server_entryrow.connect("activate", self.on_dns_server_changed) # Entry activated
        self.prefs_dns_apply_button.connect("clicked", self.on_dns_server_changed)

        # Setup for color picker buttons
        if self.http_header_key_color_button:
            dialog_hk = Gtk.ColorDialog(title="Select Header Key Color", modal=True, with_alpha=False)
            self.http_header_key_color_button.set_dialog(dialog_hk)
            self.http_header_key_color_button.connect("notify::rgba", self.on_http_color_changed, "http-output-header-key-color")
        if self.http_header_value_color_button:
            dialog_hv = Gtk.ColorDialog(title="Select Header Value Color", modal=True, with_alpha=False)
            self.http_header_value_color_button.set_dialog(dialog_hv)
            self.http_header_value_color_button.connect("notify::rgba", self.on_http_color_changed, "http-output-header-value-color")
        if self.http_special_row_color_button:
            dialog_sr = Gtk.ColorDialog(title="Select Special Row Color", modal=True, with_alpha=False)
            self.http_special_row_color_button.set_dialog(dialog_sr)
            self.http_special_row_color_button.connect("notify::rgba", self.on_http_color_changed, "http-output-special-row-color")

        # Custom User Agent Signals
        if self.add_custom_ua_button:
            self.add_custom_ua_button.connect("clicked", self._on_add_custom_ua_clicked)
        if self.new_custom_ua_title_entry: # Allow Enter key to add
            self.new_custom_ua_title_entry.connect("activate", self._on_add_custom_ua_clicked)
        if self.new_custom_ua_value_entry: # Allow Enter key to add
            self.new_custom_ua_value_entry.connect("activate", self._on_add_custom_ua_clicked)

        # Listen for changes to the custom User-Agents list in GSettings to re-render
        self.settings.connect("changed::custom-user-agents", lambda _s, _k: self._render_custom_ua_list())
        if self.preferences_error_banner: # Connect dismiss signal if banner exists
             self.preferences_error_banner.connect("dismiss", self.on_error_banner_dismiss_clicked)


    def on_error_banner_dismiss_clicked(self, _banner: Adw.Banner, *_args: Any) -> None:
        """Handles the 'dismiss' signal from the :class:`Adw.Banner`.

        Hides the banner and clears any associated error state from input fields.

        :param _banner: The :class:`Adw.Banner` that was dismissed (unused).
        :type _banner: Adw.Banner
        :param _args: Additional arguments from the signal (unused).
        :type _args: Any
        :return: None
        :rtype: None
        """
        self.hide_banner_and_clear_error_state()

    def on_dns_server_changed(self, _widget: Gtk.Widget) -> None:
        """Handles changes to the custom DNS server entry row.

        This callback is triggered when the user activates the entry (e.g., presses Enter)
        or clicks the 'Apply' button associated with the DNS server input. It validates
        the entered IP address. If valid, the value is saved to GSettings. If empty,
        the setting is cleared. If invalid, an error banner is displayed, and the
        entry row is marked with an 'error' CSS class.

        :param _widget: The :class:`Gtk.Widget` that triggered the signal (unused).
        :type _widget: Gtk.Widget
        :return: None
        :rtype: None
        """
        dns_server_ip = self.dns_server_entryrow.get_text().strip()

        if not dns_server_ip:  # User cleared the entry
            self.settings.set_string("custom-dns-server", "")
            logging.info("Custom DNS server setting cleared.")
            if self.preferences_error_banner: self.preferences_error_banner.set_revealed(False)
            self.dns_server_entryrow.remove_css_class("error")
            return

        # Validate non-empty input as an IPv4 address
        ip_pattern = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$") # Basic IPv4 regex
        if ip_pattern.match(dns_server_ip) and self.is_valid_ipv4(dns_server_ip):
            self.settings.set_string("custom-dns-server", dns_server_ip)
            logging.info("Custom DNS server set to: %s", dns_server_ip)
            if self.preferences_error_banner: self.preferences_error_banner.set_revealed(False)
            self.dns_server_entryrow.remove_css_class("error")
        else: # Invalid IP format
            self.dns_server_entryrow.add_css_class("error")
            error_message = "Invalid IPv4 address format for DNS server."
            logging.error("Invalid custom DNS server IP provided: %s", dns_server_ip)
            if self.preferences_error_banner:
                self.preferences_error_banner.set_title(error_message)
                self.preferences_error_banner.set_revealed(True)
            # Optionally, schedule banner to hide after a delay
            GLib.timeout_add_seconds(4, self.hide_banner_and_clear_error_state, self.dns_server_entryrow)


    def hide_banner_and_clear_error_state(self, entry_row_widget: Optional[Adw.EntryRow] = None) -> bool:
        """Hides the error banner and removes the 'error' CSS class from a specified entry row.

        This method is suitable for use as a :class:`GLib.timeout_add_seconds` callback,
        in which case it must return :attr:`GLib.SOURCE_REMOVE` to prevent repetition.

        :param entry_row_widget: The :class:`Adw.EntryRow` to clear the error state from.
                                 If ``None``, defaults to `self.dns_server_entryrow`.
        :type entry_row_widget: Optional[Adw.EntryRow]
        :return: :attr:`GLib.SOURCE_REMOVE` (which is `False`) to ensure the timeout source is removed.
        :rtype: bool
        """
        if self.preferences_error_banner:
            self.preferences_error_banner.set_revealed(False)

        target_entry_row = entry_row_widget if entry_row_widget else self.dns_server_entryrow
        if target_entry_row: # Ensure it exists
            target_entry_row.remove_css_class("error")
        return GLib.SOURCE_REMOVE # Important for GLib.timeout_add


    @staticmethod
    def is_valid_ipv4(ip_address: str) -> bool:
        """Validates if the input string is a syntactically correct IPv4 address.

        Checks for four octets separated by dots, with each octet being a number
        between 0 and 255 inclusive.

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
                if not (0 <= num <= 255): # Check range
                    return False
            except ValueError: # Not an integer
                return False
        return True

    def on_font_scale_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec) -> None:
        """Handles changes in the font scale preference :class:`Adw.ComboRow`.

        Saves the selected font scaling percentage string (e.g., "100%") to GSettings.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the "selected-item" property (unused).
        :type _gparam: GObject.ParamSpec
        :return: None
        :rtype: None
        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_scale_str = selected_item_obj.get_string()
            self.settings.set_string("font-scaling-percentage", selected_scale_str)
            logging.debug("Font scaling preference set to: %s", selected_scale_str)

    def on_theme_preference_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec) -> None:
        """Handles changes in the theme preference :class:`Adw.ComboRow`.

        Saves the selected theme name string (e.g., "Light", "Dark", "System") to GSettings.
        The actual theme application is handled by the main application window observing this setting.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the "selected-item" property (unused).
        :type _gparam: GObject.ParamSpec
        :return: None
        :rtype: None
        """
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_theme_str = selected_item_obj.get_string()
            self.settings.set_string("theme-preference", selected_theme_str)
            logging.debug("Theme preference set to: %s. Main window will apply.", selected_theme_str)

    def on_source_style_scheme_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec) -> None:
        """Handles changes in the GtkSourceView style scheme preference :class:`Adw.ComboRow`.

        Saves the selected style scheme name string to GSettings. The application
        of this scheme to source views is handled by relevant page classes.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the "selected-item" property (unused).
        :type _gparam: GObject.ParamSpec
        :return: None
        :rtype: None
        """
        selected_item = combo_row.get_selected_item()
        if isinstance(selected_item, Gtk.StringObject):
            source_style_scheme = selected_item.get_string()
            self.settings.set_string("source-style-scheme", source_style_scheme)
            logging.debug("Source style scheme preference set to: %s", source_style_scheme)

    @staticmethod
    def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
        """Converts a :class:`Gdk.RGBA` object to a hex color string (e.g., "#RRGGBB").
        Alpha component is ignored.

        :param rgba: The :class:`Gdk.RGBA` color object.
        :type rgba: Gdk.RGBA
        :return: The color as a 6-digit hex string (e.g., "#FF0000" for red).
        :rtype: str
        """
        # Gdk.RGBA components are floats from 0.0 to 1.0. Scale to 0-255.
        red = int(rgba.red * 255)
        green = int(rgba.green * 255)
        blue = int(rgba.blue * 255)
        return f"#{red:02x}{green:02x}{blue:02x}"

    def on_http_color_changed(self, button: Gtk.ColorDialogButton, _gparam: GObject.ParamSpec, gsettings_key: str) -> None:
        """Handles the 'notify::rgba' signal from a :class:`Gtk.ColorDialogButton`.

        When a color is selected, it converts the :class:`Gdk.RGBA` value to a hex
        string and saves it to the specified GSettings key.

        :param button: The :class:`Gtk.ColorDialogButton` whose color changed.
        :type button: Gtk.ColorDialogButton
        :param _gparam: The :class:`GObject.ParamSpec` of the "rgba" property (unused).
        :type _gparam: GObject.ParamSpec
        :param gsettings_key: The GSettings key where the color should be stored.
        :type gsettings_key: str
        :return: None
        :rtype: None
        """
        rgba = button.get_rgba()
        if rgba: # rgba can be None if color is cleared or dialog cancelled without selection
            color_hex_string = self._rgba_to_hex(rgba)
            self.settings.set_string(gsettings_key, color_hex_string)
            logging.debug("HTTP output color for GSettings key '%s' set to hex: %s",
                          gsettings_key, color_hex_string)

    def _load_color_button_preference(self, button: Gtk.ColorDialogButton, gsettings_key: str) -> None:
        """Loads a color preference from GSettings (stored as hex string) and applies
        it to the given :class:`Gtk.ColorDialogButton`.

        :param button: The :class:`Gtk.ColorDialogButton` to update.
        :type button: Gtk.ColorDialogButton
        :param gsettings_key: The GSettings key from which to load the color.
        :type gsettings_key: str
        :return: None
        :rtype: None
        """
        color_string = self.settings.get_string(gsettings_key)
        if color_string: # Ensure there's a value to parse
            color = Gdk.RGBA()
            try:
                # Gdk.RGBA.parse() returns True on success, False on failure.
                if color.parse(color_string):
                    button.set_rgba(color)
                else:
                    logging.warning("Gdk.RGBA.parse failed for color string '%s' from GSettings key '%s'. "
                                    "Button color not set.", color_string, gsettings_key)
            except GLib.Error as e: # Gdk.RGBA.parse can raise GLib.Error on severe parse errors
                logging.warning("Failed to parse color string '%s' for GSettings key '%s': %s. "
                                "Button color not set.", color_string, gsettings_key, e)


    def _render_custom_ua_list(self) -> None:
        """Clears and repopulates the list of custom User-Agents in the UI.

        Retrieves User-Agent pairs (title, value) from the 'custom-user-agents'
        GSettings key. For each pair, it creates an :class:`Adw.ActionRow` displaying
        the title and value, and adds a button to remove that User-Agent entry.
        These rows are then added to the `custom_ua_list_container` widget.
        This method is typically called when the window is initialized or when the
        corresponding GSettings key changes.

        :return: None
        :rtype: None
        """
        if not self.custom_ua_list_container:
            logging.error("Preferences: 'custom_ua_list_container' is None, cannot render User-Agent list.")
            return

        # Clear existing rows from the container
        child = self.custom_ua_list_container.get_first_child()
        while child:
            self.custom_ua_list_container.remove(child)
            child = self.custom_ua_list_container.get_first_child()

        variant = self.settings.get_value("custom-user-agents")
        # Unpack GSettings 'a(ss)' (array of string-string tuples)
        custom_ua_pairs: List[Tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == 'a(ss)' else [])

        for title, value in custom_ua_pairs:
            row = Adw.ActionRow(title=title, subtitle=value)
            row.set_activatable(False) # The row itself is not activatable
            remove_button = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER)
            remove_button.add_css_class("flat") # For consistent styling
            remove_button.set_tooltip_text(f"Remove User-Agent: '{title}'")
            # Connect clicked signal, passing the title as an argument to identify which UA to remove
            remove_button.connect("clicked", lambda _btn, t=title: self._on_remove_custom_ua_clicked(t))
            row.add_suffix(remove_button)
            row.set_activatable_widget(remove_button) # Make the button activatable for the row
            self.custom_ua_list_container.append(row)

    def _on_add_custom_ua_clicked(self, _widget: Gtk.Widget) -> None:
        """Handles the 'clicked' signal for the 'Add User Agent' button or 'activate'
        signal from the entry fields.

        Retrieves text from the title and value entry fields. If both are non-empty
        and the title is unique (not already present in the list), it adds the new
        User-Agent pair to GSettings. This change in GSettings then triggers
        :meth:`_render_custom_ua_list` (due to the connected 'changed' signal)
        to update the UI. Provides visual feedback (CSS class 'error') for empty
        fields or duplicate titles.

        :param _widget: The :class:`Gtk.Widget` that triggered the action (unused).
        :type _widget: Gtk.Widget
        :return: None
        :rtype: None
        """
        if not self.new_custom_ua_title_entry or not self.new_custom_ua_value_entry:
            logging.error("Preferences: User-Agent title or value entry not found.")
            return

        title_text = self.new_custom_ua_title_entry.get_text().strip()
        value_text = self.new_custom_ua_value_entry.get_text().strip()

        # Basic validation for empty fields
        title_valid = bool(title_text)
        value_valid = bool(value_text)

        self.new_custom_ua_title_entry.remove_css_class("error") # Clear previous error states
        self.new_custom_ua_value_entry.remove_css_class("error")

        if not title_valid:
            self.new_custom_ua_title_entry.add_css_class("error")
        if not value_valid:
            self.new_custom_ua_value_entry.add_css_class("error")

        if not title_valid or not value_valid:
            logging.info("Attempted to add custom User-Agent with empty title or value.")
            return

        variant = self.settings.get_value("custom-user-agents")
        current_ua_pairs: List[Tuple[str,str]] = list(
            variant.unpack() if variant and variant.get_type_string() == 'a(ss)' else []) # type: ignore

        # Check for duplicate titles
        existing_titles = [pair[0] for pair in current_ua_pairs]
        if title_text in existing_titles:
            logging.info("Custom User-Agent title '%s' already exists. Not adding.", title_text)
            self.new_custom_ua_title_entry.add_css_class("error") # Mark duplicate title as error
            # Optionally show a toast/banner here
            return

        # Add new pair and save to GSettings
        current_ua_pairs.append((title_text, value_text))
        new_variant = GLib.Variant("a(ss)", current_ua_pairs)

        if self.settings.set_value("custom-user-agents", new_variant):
            logging.info("Added custom User-Agent: '%s' -> '%s'", title_text, value_text)
            # Clear entry fields after successful addition
            self.new_custom_ua_title_entry.set_text("")
            self.new_custom_ua_value_entry.set_text("")
            # _render_custom_ua_list will be called automatically due to GSettings "changed" signal
        else:
            logging.error("Failed to save updated custom User-Agent list to GSettings.")
            # Optionally show an error to the user here

    def _on_remove_custom_ua_clicked(self, title_to_remove: str) -> None:
        """Handles the 'clicked' signal for a 'remove' button associated with a custom User-Agent.

        Removes the User-Agent pair identified by `title_to_remove` from the
        'custom-user-agents' GSettings key. The change to GSettings will then
        trigger :meth:`_render_custom_ua_list` to update the UI.

        :param title_to_remove: The title of the User-Agent entry to remove.
        :type title_to_remove: str
        :return: None
        :rtype: None
        """
        variant = self.settings.get_value("custom-user-agents")
        current_ua_pairs: List[Tuple[str,str]] = list(
            variant.unpack() if variant and variant.get_type_string() == 'a(ss)' else []) # type: ignore

        original_length = len(current_ua_pairs)
        # Filter out the pair to be removed
        updated_ua_pairs = [pair for pair in current_ua_pairs if pair[0] != title_to_remove]

        if len(updated_ua_pairs) < original_length: # If an item was actually removed
            new_variant = GLib.Variant("a(ss)", updated_ua_pairs)
            if self.settings.set_value("custom-user-agents", new_variant):
                logging.info("Removed custom User-Agent with title: %s", title_to_remove)
                # _render_custom_ua_list will update UI due to GSettings "changed" signal
            else:
                logging.error("Failed to save User-Agent list after removing title: %s", title_to_remove)
        else:
            logging.warning("Attempted to remove non-existent User-Agent with title: %s", title_to_remove)


    def _select_combo_row_item(
        self, combo_row: Adw.ComboRow, setting_value: str, case_sensitive: bool = True
    ) -> bool:
        """Selects an item in an :class:`Adw.ComboRow` based on its string value.

        Iterates through the items in the ComboRow's model (expected to be a
        :class:`Gtk.StringList`). If a match for `setting_value` is found, that
        item is selected in the ComboRow.

        :param combo_row: The :class:`Adw.ComboRow` to operate on.
        :type combo_row: Adw.ComboRow
        :param setting_value: The string value of the item to select.
        :type setting_value: str
        :param case_sensitive: Whether the string comparison should be case-sensitive.
                               Defaults to ``True``.
        :type case_sensitive: bool
        :return: ``True`` if an item was successfully found and selected, ``False`` otherwise.
        :rtype: bool
        """
        model = combo_row.get_model()
        if isinstance(model, Gtk.StringList):
            for i in range(model.get_n_items()):
                item_string = model.get_string(i)
                # Perform comparison, respecting case_sensitive flag
                strings_match = (item_string == setting_value if case_sensitive
                                 else (item_string is not None and
                                       item_string.lower() == setting_value.lower()))
                if strings_match:
                    combo_row.set_selected(i)
                    return True
        else:
            logging.warning("Preferences: Model for ComboRow is not Gtk.StringList, cannot select item by string.")
        return False

    def load_preferences(self) -> None:
        """Loads preferences from GSettings and updates UI elements accordingly.

        For each preference managed by this window (e.g., font scale, theme,
        source style scheme, DNS server, HTTP output colors, custom User-Agents),
        this method retrieves the currently saved value from :class:`Gio.Settings`
        and sets the corresponding UI control to reflect that value. This ensures
        the preferences window displays the active settings when opened.

        :return: None
        :rtype: None
        """
        # Font Scaling
        font_scale_pref = self.settings.get_string("font-scaling-percentage")
        if not self._select_combo_row_item(self.font_scale_combo_row, font_scale_pref):
            logging.warning("Failed to select font scale '%s', defaulting to first item.", font_scale_pref)
            if self.font_scale_combo_row.get_model() and self.font_scale_combo_row.get_model().get_n_items() > 0: # type: ignore
                self.font_scale_combo_row.set_selected(0)

        # Theme Preference
        theme_pref_value = self.settings.get_string("theme-preference")
        if not self._select_combo_row_item(self.theme_combo_row, theme_pref_value):
            logging.warning("Failed to select theme '%s', defaulting to first item.", theme_pref_value)
            if self.theme_combo_row.get_model() and self.theme_combo_row.get_model().get_n_items() > 0: # type: ignore
                self.theme_combo_row.set_selected(0)

        # Source Style Scheme
        source_style_scheme = self.settings.get_string("source-style-scheme")
        if not self._select_combo_row_item(self.source_style_scheme_combo_row, source_style_scheme, case_sensitive=False):
            logging.warning("Failed to select source style scheme '%s' (case-insensitive), "
                            "defaulting to first item if available.", source_style_scheme)
            if self.source_style_scheme_combo_row.get_model() and \
               self.source_style_scheme_combo_row.get_model().get_n_items() > 0: # type: ignore
                self.source_style_scheme_combo_row.set_selected(0)

        # Custom DNS Server
        dns_server = self.settings.get_string("custom-dns-server")
        self.dns_server_entryrow.set_text(dns_server)

        # HTTP Output Colors
        if self.http_header_key_color_button:
            self._load_color_button_preference(self.http_header_key_color_button, "http-output-header-key-color")
        if self.http_header_value_color_button:
            self._load_color_button_preference(self.http_header_value_color_button, "http-output-header-value-color")
        if self.http_special_row_color_button:
            self._load_color_button_preference(self.http_special_row_color_button, "http-output-special-row-color")

        # Custom User Agents (will be rendered by the GSettings 'changed' signal handler initially,
        # or can be explicitly called if needed after setup)
        self._render_custom_ua_list()
        logging.debug("Preferences loaded into UI elements.")

```
