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

NONE_OPTION_TITLE = "None"  # Module-level constant for "None"


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    """
    Adw.PreferencesWindow subclass for managing application settings.

    This class defines the structure and behavior of the preferences dialog,
    organized into "Appearance" and "Behavior" pages. It allows users to
    customize various aspects of the application, such as theme, font sizes,
    User-Agent strings, and other tool-specific settings.
    Preferences are persisted via GSettings.

    The `user_agent_combo_row` for selecting the default User-Agent is handled
    carefully to prevent its "notify::selected-item" signal (connected to
    `_on_default_user_agent_changed`) from triggering GSettings writes during
    programmatic model population or preference loading. This is achieved by:
    1. Storing the signal handler ID in `_ua_combo_handler_id` upon connecting.
    2. Using `self.user_agent_combo_row.handler_block(self._ua_combo_handler_id)`
       before any code that programmatically changes the `user_agent_combo_row`'s
       model or selected item (e.g., in `_populate_user_agent_combo_row` and
       `load_preferences`).
    3. Setting a boolean flag, `_is_programmatically_changing_ua_combo`, to `True`
       before such programmatic changes.
    4. The `_on_default_user_agent_changed` method checks this flag at the beginning;
       if `True`, the method returns early, preventing GSettings writes.
    5. Unblocking the handler via `handler_unblock` and resetting the
       `_is_programmatically_changing_ua_combo` flag to `False` after the
       programmatic changes are complete.
    This ensures that GSettings are only updated when the user directly interacts
    with the `user_agent_combo_row`.

    :ivar _ua_combo_handler_id: Stores the ID of the "notify::selected-item" signal
                                handler for `user_agent_combo_row`. Used to block/unblock
                                the signal during programmatic updates.
    :vartype _ua_combo_handler_id: Optional[int]
    :ivar _is_programmatically_changing_ua_combo: A flag to indicate that changes to
                                                  `user_agent_combo_row`'s selection
                                                  are programmatic and should not trigger
                                                  the GSettings save logic in
                                                  `_on_default_user_agent_changed`.
    :vartype _is_programmatically_changing_ua_combo: bool

    :ivar NONE_OPTION_TITLE: Class attribute to consistently refer to the "None" option title
                             (representing system default or no override).
    :vartype NONE_OPTION_TITLE: str
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
    :ivar user_agent_combo_row: :class:`Adw.ComboRow` for default user agent selection.
    :vartype user_agent_combo_row: Adw.ComboRow
    :ivar global_output_font_button: :class:`Gtk.FontButton` for global output font.
    :vartype global_output_font_button: Gtk.FontButton
    """

    __gtype_name__ = "Preferences"

    NONE_OPTION_TITLE = "None"  # Class attribute

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
    user_agent_combo_row: Adw.ComboRow = Gtk.Template.Child("user_agent_combo_row")  # type: ignore
    new_custom_ua_title_entry: Gtk.Entry = Gtk.Template.Child("new_custom_ua_title_entry")  # type: ignore
    new_custom_ua_value_entry: Gtk.Entry = Gtk.Template.Child("new_custom_ua_value_entry")  # type: ignore
    add_custom_ua_button: Gtk.Button = Gtk.Template.Child("add_custom_ua_button")  # type: ignore
    custom_ua_list_container: Gtk.Box = Gtk.Template.Child("custom_ua_list_container")  # type: ignore

    # Global Font Preference UI Element
    global_output_font_button: Gtk.FontButton = Gtk.Template.Child("global_output_font_button")  # type: ignore

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
        self._ua_combo_handler_id = None
        self._is_programmatically_changing_ua_combo = False
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

        if self.user_agent_combo_row:
            self._ua_combo_handler_id = self.user_agent_combo_row.connect(
                "notify::selected-item", self._on_default_user_agent_changed
            )
            # logging.debug(f"Connected _on_default_user_agent_changed with handler ID: {self._ua_combo_handler_id}") # Reduced verbosity
        else:
            self._ua_combo_handler_id = None  # Ensure it's None if row doesn't exist
            logging.error(
                "user_agent_combo_row not found during load_ui, cannot connect signal for default User-Agent."
            )

        # Custom HTTP Header Signals

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

        logging.debug(
            "_render_custom_ua_list: Refreshing default UA dropdown by calling _populate_user_agent_combo_row."
        )
        self._populate_user_agent_combo_row()  # Keep dropdown in sync

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

        # Check for duplicate titles (Default UAs + Custom UAs)
        all_existing_titles = [ua_dict["title"] for ua_dict in USER_AGENTS] + [pair[0] for pair in current_ua_pairs]
        if title_text in all_existing_titles:
            logging.info(f"Custom User-Agent title '{title_text}' already exists or conflicts with a default UA.")
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
            # self._render_custom_ua_list() is called by GSettings "changed::custom-user-agents" signal
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
                # self._render_custom_ua_list() is called by GSettings "changed::custom-user-agents" signal
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
                # logging.debug(f"_select_combo_row_item: Comparing '{item_string}' with '{setting_value}'. Match: {match_condition}") # Reduced verbosity
                if match_condition:
                    combo_row.set_selected(i)
                    return True
        return False

    def load_preferences(self) -> None:
        """
        Load preferences from GSettings and update the UI elements accordingly.

        For each preference (font scale, theme, style scheme, DNS server),
        it retrieves the value from GSettings and sets the corresponding
        UI control (e.g., selects the correct item in a :class:`Adw.ComboRow`,
        sets text in an :class:`Adw.EntryRow`).
        For the User-Agent ComboBox, signal handlers are blocked and a flag is set
        to prevent GSettings writes during this programmatic update. The "None"
        option in the UI corresponds to an empty string in GSettings for
        `default-user-agent-title`.
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

        # Call to _render_custom_ua_list also calls _populate_user_agent_combo_row
        # which handles loading and setting the default user agent.
        self._render_custom_ua_list()  # This will call _populate_user_agent_combo_row

        self._is_programmatically_changing_ua_combo = True
        if self._ua_combo_handler_id and self.user_agent_combo_row:
            self.user_agent_combo_row.handler_block(self._ua_combo_handler_id)
            # logging.debug(f"load_preferences: UA ComboBox handler {self._ua_combo_handler_id} blocked.") # Reduced verbosity

        default_ua_title_gsettings = self.settings.get_string("default-user-agent-title")
        logging.info(
            f"Preferences: Loading default User-Agent title from GSettings: '{default_ua_title_gsettings if default_ua_title_gsettings else 'None (empty string)'}'"
        )
        model = self.user_agent_combo_row.get_model()

        if default_ua_title_gsettings == "":  # User explicitly wants "None" (system default)
            was_selected = self._select_combo_row_item(self.user_agent_combo_row, self.NONE_OPTION_TITLE)
            # logging.info(f"load_preferences: Attempted to select '{self.NONE_OPTION_TITLE}'. Success: {was_selected}") # Reduced verbosity
            if not was_selected:
                logging.error(
                    f"load_preferences: Critical - '{self.NONE_OPTION_TITLE}' not found in user_agent_combo_row."
                )
                if model and model.get_n_items() > 0:
                    self.user_agent_combo_row.set_selected(0)
        elif default_ua_title_gsettings:  # A specific UA title is saved
            was_selected = self._select_combo_row_item(self.user_agent_combo_row, default_ua_title_gsettings)
            # logging.info(f"load_preferences: Attempted to select '{default_ua_title_gsettings}'. Success: {was_selected}") # Reduced verbosity
            if not was_selected:
                logging.warning(
                    f"load_preferences: Saved default UA title '{default_ua_title_gsettings}' not found. Falling back to '{self.NONE_OPTION_TITLE}'."
                )
                self.settings.set_string("default-user-agent-title", "")
                if not self._select_combo_row_item(self.user_agent_combo_row, self.NONE_OPTION_TITLE):
                    logging.error(f"load_preferences: Critical - Fallback '{self.NONE_OPTION_TITLE}' not found.")
        else:  # GSetting is empty, but not explicitly ""
            if not self._select_combo_row_item(self.user_agent_combo_row, self.NONE_OPTION_TITLE):
                logging.error(f"load_preferences: Critical - '{self.NONE_OPTION_TITLE}' not found during initial load.")
            self.settings.set_string("default-user-agent-title", "")

        if self._ua_combo_handler_id and self.user_agent_combo_row:
            self.user_agent_combo_row.handler_unblock(self._ua_combo_handler_id)
        self._is_programmatically_changing_ua_combo = False
        # logging.debug("load_preferences: UA ComboBox handler unblocked, programmatic change flag cleared.") # Reduced verbosity

        output_font_str = self.settings.get_string("output-font")
        if self.global_output_font_button:
            if output_font_str:
                self.global_output_font_button.set_font(output_font_str)  # type: ignore[union-attr]
            else:
                default_font = "Sans 10"
                self.global_output_font_button.set_font(default_font)  # type: ignore[union-attr]
                self.settings.set_string("output-font", default_font)
                logging.warning(f"GSettings 'output-font' was empty, set to default: {default_font}")

        # HTTP Headers

    def _populate_user_agent_combo_row(self):
        """
        Populate the ``user_agent_combo_row`` with available User-Agent choices.

        This method clears and then reconstructs the list of User-Agent titles
        for the dropdown. It includes a "None" option (representing system default,
        which maps to an empty string in GSettings), standard UAs from
        :mod:`.constants.USER_AGENTS`, and custom UAs from GSettings.
        Signal handlers for the combobox (`_ua_combo_handler_id`) are blocked,
        and the `_is_programmatically_changing_ua_combo` flag is set during model
        updates to prevent `_on_default_user_agent_changed` from incorrectly
        triggering GSettings writes. The actual selection based on GSettings
        is handled by `load_preferences`.
        """
        if not self.user_agent_combo_row:
            logging.warning("Preferences: user_agent_combo_row not found, cannot populate.")
            return

        self._is_programmatically_changing_ua_combo = True
        if self._ua_combo_handler_id and self.user_agent_combo_row:
            self.user_agent_combo_row.handler_block(self._ua_combo_handler_id)
            # logging.debug(f"_populate_user_agent_combo_row: Blocked handler for user_agent_combo_row.") # Reduced verbosity

        all_ua_titles = []

        # 1. Add "None" option (System Default)
        all_ua_titles.append(self.NONE_OPTION_TITLE)

        # 2. Add standard user agents from constants.py
        for ua_dict in USER_AGENTS:
            title = ua_dict["title"]
            if title not in all_ua_titles:  # Avoid duplicates
                all_ua_titles.append(title)
            else:
                logging.warning(
                    f"Standard User-Agent title '{title}' conflicts with '{self.NONE_OPTION_TITLE}' or another standard UA. Skipping."
                )

        # 3. Add custom user agents from GSettings
        variant = self.settings.get_value("custom-user-agents")
        custom_ua_pairs: list[tuple[str, str]] = list(
            variant.unpack() if variant and variant.get_type_string() == "a(ss)" else []
        )

        for title, _value in custom_ua_pairs:
            if title not in all_ua_titles:
                all_ua_titles.append(title)
            else:
                logging.warning(
                    f"Custom UA title '{title}' conflicts with a standard or another custom UA title. Skipping."
                )

        model = Gtk.StringList.new(all_ua_titles)
        # freeze_notify was here, but handler_block is used at the start of the method.
        self.user_agent_combo_row.set_model(model)
        # Removed detailed logging after each step of population and model setting.

        # Crucial Check: The selection restoration logic previously here might be redundant.
        # load_preferences should handle the initial GSettings-based selection.
        # For now, this method only populates. If selection needs to be preserved
        # during dynamic updates (not initial load), that needs specific handling.
        # The current task is to ensure load_preferences correctly sets the initial state.

        # Fallback selection if nothing else gets selected by load_preferences
        # This ensures the combo box always has a valid selection if items exist.
        # REMOVED set_selected(0) from here as it should be handled by load_preferences or initial state.
        # if model.get_n_items() > 0 and self.user_agent_combo_row.get_selected() == Gtk.INVALID_LIST_POSITION:
        #      self.user_agent_combo_row.set_selected(0) # Select NONE_OPTION_TITLE
        #      # logging.debug(f"_populate_user_agent_combo_row: Fallback - Selected first item '{all_ua_titles[0]}'.") # Optional: keep if needed

        # thaw_notify was here, corresponding handler_unblock is at the end of the method.
        # Ensure unblock is at the very end
        if self._ua_combo_handler_id and self.user_agent_combo_row:
            self.user_agent_combo_row.handler_unblock(self._ua_combo_handler_id)
        self._is_programmatically_changing_ua_combo = False
        # logging.debug("_populate_user_agent_combo_row: Cleared programmatic change flag and unblocked handler.") # Reduced verbosity

    def _on_default_user_agent_changed(self, combo_row: Adw.ComboRow, _gparam: GObject.ParamSpec):
        """
        Handle direct user changes in the default User-Agent selection ComboBox.

        This method is connected to the "notify::selected-item" signal of the
        `user_agent_combo_row`. It saves the selected User-Agent title to GSettings
        under the "default-user-agent-title" key. If the user selects the
        "None" option (as defined by `self.NONE_OPTION_TITLE`), an empty string
        is saved to GSettings, signifying that the system default (or no specific UA)
        should be used.

        A crucial guard, `_is_programmatically_changing_ua_combo`, prevents this
        method from executing its GSettings write logic when the ComboBox selection
        is being changed programmatically (e.g., during model population in
        `_populate_user_agent_combo_row` or when loading preferences in
        `load_preferences`). This ensures that GSettings are only updated in
        response to direct user interaction with this ComboBox.

        :param combo_row: The :class:`Adw.ComboRow` whose selection changed.
        :type combo_row: Adw.ComboRow
        :param _gparam: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _gparam: GObject.ParamSpec
        """
        if self._is_programmatically_changing_ua_combo:
            logging.debug(
                f"_on_default_user_agent_changed: Ignoring signal for '{combo_row.get_selected_item().get_string() if combo_row.get_selected_item() else 'N/A'}' due to _is_programmatically_changing_ua_combo flag."
            )
            return
        # logging.debug(f"_on_default_user_agent_changed: Processing signal for '{combo_row.get_selected_item().get_string() if combo_row.get_selected_item() else 'N/A'}'. Flag is False.")
        selected_item_obj = combo_row.get_selected_item()
        if isinstance(selected_item_obj, Gtk.StringObject):
            selected_ua_title = selected_item_obj.get_string()
            # logging.debug(f"_on_default_user_agent_changed: Raw selected title from dropdown: '{selected_ua_title}'")
            if selected_ua_title:
                current_gsettings_val = self.settings.get_string("default-user-agent-title")
                gsetting_to_save = ""  # Assume self.NONE_OPTION_TITLE
                if selected_ua_title != self.NONE_OPTION_TITLE:
                    gsetting_to_save = selected_ua_title

                # logging.debug(f"_on_default_user_agent_changed: Current GSettings value for default-user-agent-title: '{current_gsettings_val}'")
                # logging.debug(f"_on_default_user_agent_changed: Intended gsetting_to_save: '{gsetting_to_save}' (based on selected_ua_title: '{selected_ua_title}', self.NONE_OPTION_TITLE: '{self.NONE_OPTION_TITLE}')")

                if gsetting_to_save != current_gsettings_val:
                    logging.info(f"Preferences: Saving default User-Agent title to GSettings: '{gsetting_to_save}'")
                    self.settings.set_string("default-user-agent-title", gsetting_to_save)
                    # logging.info(f"_on_default_user_agent_changed: Value read back from GSettings default-user-agent-title immediately after set: '{self.settings.get_string('default-user-agent-title')}'")
            else:  # selected_ua_title is None or empty string (should not happen with Gtk.StringList of valid titles)
                logging.warning("Selected UA title is None or empty, which is unexpected. Setting GSettings to empty.")
                self.settings.set_string("default-user-agent-title", "")

        elif selected_item_obj is None:
            # This case could happen if the model is cleared or selection is programmatically set to invalid.
            # If the list becomes empty, GSettings should reflect "no preference" or "system default".
            model = combo_row.get_model()
            if model is None or model.get_n_items() == 0:  # type: ignore[attr-defined]
                current_gsettings_val = self.settings.get_string("default-user-agent-title")
                if current_gsettings_val != "":  # Only update if it's not already empty
                    self.settings.set_string("default-user-agent-title", "")
                    logging.debug("Default user agent selection cleared as list is empty or selection is invalid.")

    # --- End HTTP Header Management ---
