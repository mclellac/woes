"""Utility functions for managing application styling and themes.

This module provides helper functions for interacting with GSettings to
retrieve and apply style-related preferences such as fonts, GTK themes,
and GtkSourceView style schemes. It also includes utilities for common
UI manipulations like setting widget visibility.
"""
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource, GLib
import logging
import re
import platform
from typing import Optional, Tuple

import gi
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

logger = logging.getLogger(__name__)

BASE_FONT_SIZE_PT = 12.0  # Default base font size in points.

# GSettings schemas and keys for system-wide interface preferences.
GNOME_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
FONT_NAME_KEY = "font-name"
TEXT_SCALING_FACTOR_KEY = "text-scaling-factor"
GTK_THEME_KEY = "gtk-theme" # Potentially used for Adw.ColorScheme.DEFAULT syncing
GNOME_A11Y_SCHEMA = "org.gnome.desktop.a11y.interface"
HIGH_CONTRAST_KEY = "high-contrast" # For accessibility considerations


def _get_linux_font_preferences(
    base_font_size_pt: float,
) -> Tuple[Optional[str], float]:
    """Retrieves font preferences from GNOME settings on Linux systems.

    This function queries the ``org.gnome.desktop.interface`` GSettings schema
    to get the system-defined font family and base size. It then applies
    the GNOME text scaling factor to the base size.

    If the GNOME settings cannot be accessed or the font name string cannot
    be parsed, it logs a warning and defaults appropriately.

    :param base_font_size_pt: The base font size (in points) to use as a
                              fallback if system settings are unavailable or
                              unparseable.
    :type base_font_size_pt: float
    :return: A tuple containing the font family name (or None if not found/parsed)
             and the calculated font size in points (adjusted for scaling).
    :rtype: Tuple[Optional[str], float]
    """
    font_family = None
    font_size_pt = base_font_size_pt
    try:
        gnome_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
        font_name_str = gnome_settings.get_string(FONT_NAME_KEY)
        text_scaling_factor = gnome_settings.get_double(
            TEXT_SCALING_FACTOR_KEY)

        # Parse "Font Family Name Size" string, e.g., "Ubuntu 11"
        match = re.match(r"^(.*)\s+(\d+(\.\d+)?)$", font_name_str)
        if match:
            gnome_font_family = match.group(1).strip()
            gnome_base_size_pt = float(match.group(2))
            font_family = gnome_font_family
            font_size_pt = gnome_base_size_pt * text_scaling_factor
            logging.info("Applied GNOME font: Family='%s', Base Size "
                         "(after GNOME scaling)=%.2fpt", font_family,
                         font_size_pt)
        else:
            logging.warning("Could not parse GNOME font-name string: '%s'. "
                            "Using application base font size %.2fpt.", # Corrected format specifier
                            font_name_str, base_font_size_pt) # Use base_font_size_pt for logging default
    except GLib.Error:
        # GNOME desktop font settings might not be available (e.g., non-GNOME DE)
        logging.debug("GNOME font settings schema '%s' not found. Using defaults.", GNOME_INTERFACE_SCHEMA)
        pass

    try:
        gnome_a11y_settings = Gio.Settings.new(GNOME_A11Y_SCHEMA)
        # Example of reading a related setting, though not directly used for font size here.
        # Could be used in the future to adjust styles for high contrast.
        gnome_a11y_settings.get_boolean(HIGH_CONTRAST_KEY)
    except GLib.Error:
        # GNOME accessibility settings might not be available.
        logging.debug("GNOME accessibility schema '%s' not found.", GNOME_A11Y_SCHEMA)
        pass
    return font_family, font_size_pt


def _get_app_font_scaling(app_settings: Gio.Settings) -> float:
    """Retrieves the application-specific font scaling percentage from GSettings.

    Parses the 'font-scaling-percentage' string (e.g., "100%") stored in
    the application's GSettings and returns it as a float. If the setting
    is missing or cannot be parsed, it defaults to 100.0.

    :param app_settings: The application's :class:`Gio.Settings` object.
    :type app_settings: Gio.Settings
    :return: The font scaling percentage as a float (e.g., 100.0 for 100%).
    :rtype: float
    """
    font_scale_percentage_str = app_settings.get_string(
        "font-scaling-percentage")
    parsed_app_percentage = 100.0 # Default value
    match_app_scale = re.match(r"(\d+)\%?", font_scale_percentage_str)
    if match_app_scale:
        try:
            parsed_app_percentage = float(match_app_scale.group(1))
        except ValueError:
            logging.warning("Could not parse app font-scaling-percentage: "
                            "'%s', defaulting to 100%%.",
                            font_scale_percentage_str)
    else:
        logging.warning("Could not parse app font-scaling-percentage: '%s', "
                        "defaulting to 100%%.", font_scale_percentage_str)
    return parsed_app_percentage


def apply_system_font_preferences(app_settings: Gio.Settings) -> None:
    """Applies system-wide font preferences to the application.

    This function determines the appropriate font family and size by:
    1. Querying system-wide GNOME settings (on Linux) for a base font.
    2. Applying an application-specific scaling factor from GSettings.
    The resulting font style (family and size) is then applied globally
    to the application using a :class:`Gtk.CssProvider`.

    :param app_settings: The application's :class:`Gio.Settings` object.
    :type app_settings: Gio.Settings
    :return: None
    :rtype: None
    """
    font_family_to_apply: Optional[str] = None
    # Start with the application's base font size.
    font_size_to_apply_pt: float = BASE_FONT_SIZE_PT

    if platform.system() == "Linux":
        # On Linux, try to get system font settings.
        font_family_to_apply, font_size_to_apply_pt = _get_linux_font_preferences(
            font_size_to_apply_pt # Pass current base as fallback
        )

    # Get application-specific scaling factor.
    parsed_app_percentage = _get_app_font_scaling(app_settings)
    # Apply app scaling to the determined base size (system or default).
    final_font_size_pt = font_size_to_apply_pt * (parsed_app_percentage /
                                                  100.0)

    # Construct CSS to apply the font settings.
    if font_family_to_apply:
        # Quote font family if it contains spaces.
        css_font_family = (f"'{font_family_to_apply}'"
                           if " " in font_family_to_apply
                           else font_family_to_apply)
        css = (f"* {{ font-family: {css_font_family}; "
               f"font-size: {final_font_size_pt:.2f}pt; }}")
    else:
        css = f"* {{ font-size: {final_font_size_pt:.2f}pt; }}"

    logging.info("Applying font preferences. Final effective font size: "
                 "%.2fpt (Base system/default size: %.2fpt, App scale: %.0f%%)",
                 final_font_size_pt, font_size_to_apply_pt,
                 parsed_app_percentage)

    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(css.encode()) # CSS must be bytes

    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), # Get the default display
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION, # Apply with application priority
    )


def apply_font_size(settings: Gio.Settings) -> None:
    """Applies font size preferences based on system and application settings.

    This function is a wrapper around :func:`apply_system_font_preferences`
    to provide a simpler interface for applying the combined font sizing.

    :param settings: The application's :class:`Gio.Settings` object.
    :type settings: Gio.Settings
    :return: None
    :rtype: None
    """
    apply_system_font_preferences(settings)


def apply_theme(style_manager: Adw.StyleManager, theme_preference: str) -> None:
    """Applies the selected color scheme (theme) to the application.

    Sets the application's color scheme based on the user's preference,
    which can be "Light", "Dark", or "System" (default).

    :param style_manager: The :class:`Adw.StyleManager` instance for the application.
    :type style_manager: Adw.StyleManager
    :param theme_preference: The theme preference string ("Light", "Dark", or "System").
    :type theme_preference: str
    :return: None
    :rtype: None
    """
    if theme_preference == "Light":
        style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    elif theme_preference == "Dark":
        style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    else: # "System" or any other value defaults to following system settings
        style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


def apply_source_style_scheme(
    scheme_manager: GtkSource.StyleSchemeManager,
    buffer: GtkSource.Buffer,
    source_style_scheme: str,
) -> None:
    """Applies the selected style scheme to a :class:`GtkSource.Buffer`.

    If the specified scheme name is not found, it attempts to fall back to
    "Adwaita". It handles common variations of Adwaita theme names by converting
    the input scheme name to lowercase if it's not a direct Adwaita match.

    :param scheme_manager: The :class:`GtkSource.StyleSchemeManager`.
    :type scheme_manager: GtkSource.StyleSchemeManager
    :param buffer: The :class:`GtkSource.Buffer` to apply the style scheme to.
    :type buffer: GtkSource.Buffer
    :param source_style_scheme: The name of the style scheme to apply.
    :type source_style_scheme: str
    :return: None
    :rtype: None
    """
    # Normalize common Adwaita variations if not an exact match initially.
    # This helps if user settings store "adwaita" but GtkSource uses "Adwaita".
    if source_style_scheme not in ["Adwaita", "Adwaita-dark"]:
        # Case-insensitive check for Adwaita variants.
        if source_style_scheme.lower() == "adwaita":
            source_style_scheme = "Adwaita"
        elif source_style_scheme.lower() == "adwaita-dark":
            source_style_scheme = "Adwaita-dark"
        # For other schemes, use the name as is (case might matter for some scheme IDs).

    scheme = scheme_manager.get_scheme(source_style_scheme)
    if scheme:
        buffer.set_style_scheme(scheme)
        # Verify if scheme was actually applied (it might fail silently in some rare GtkSource versions/setups)
        applied_scheme = buffer.get_style_scheme()
        if not applied_scheme or applied_scheme.get_id() != scheme.get_id():
            logging.error("apply_source_style_scheme: Failed to apply the "
                          "style scheme '%s' even though it was found.", source_style_scheme)
    else:
        logging.warning("apply_source_style_scheme: Style scheme '%s' not found. " # Changed to warning
                        "Falling back to 'Adwaita'.", source_style_scheme)
        default_scheme = scheme_manager.get_scheme("Adwaita") # Default fallback
        if default_scheme:
            buffer.set_style_scheme(default_scheme)
        else:
            # This would be very unusual if GtkSourceView is working at all.
            logging.error("apply_source_style_scheme: Default scheme 'Adwaita' "
                          "also not found. Cannot apply any style scheme.")


def set_widget_visibility(visible: bool, *widgets: Gtk.Widget) -> None:
    """Sets the visibility of one or more :class:`Gtk.Widget` instances.

    Iterates through the provided widgets and sets their visibility state.
    Logs a warning if any of the provided widget references are None.

    :param visible: ``True`` to make widgets visible, ``False`` to hide them.
    :type visible: bool
    :param widgets: A variable number of :class:`Gtk.Widget` instances to modify.
    :type widgets: Gtk.Widget
    :return: None
    :rtype: None
    """
    for widget in widgets:
        if widget:
            widget.set_visible(visible)
        else:
            # This helps catch issues where a Gtk.Template.Child might not have been correctly bound.
            logging.warning("Attempted to set visibility of a None widget.")


def create_listbox_row(item_text: str) -> Gtk.ListBoxRow:
    """Creates a simple :class:`Gtk.ListBoxRow` containing a :class:`Gtk.Label`.

    :param item_text: The text to display in the label of the list box row.
    :type item_text: str
    :return: A :class:`Gtk.ListBoxRow` with the specified label.
    :rtype: Gtk.ListBoxRow
    """
    label = Gtk.Label(label=item_text, halign=Gtk.Align.START, xalign=0) # Ensure label is left-aligned
    row = Gtk.ListBoxRow()
    row.set_child(label)
    return row


def init_source_buffer(language: str = "yaml") -> GtkSource.Buffer:
    """Initializes a :class:`GtkSource.Buffer` with syntax highlighting for a given language.

    If the specified language definition is not found by the
    :class:`GtkSource.LanguageManager`, an error is logged, and syntax
    highlighting may not be applied effectively.

    :param language: The language ID for syntax highlighting (e.g., "yaml", "python").
                     Defaults to "yaml".
    :type language: str
    :return: A :class:`GtkSource.Buffer` configured for the specified language.
    :rtype: GtkSource.Buffer
    """
    source_buffer = GtkSource.Buffer()
    lang_manager = GtkSource.LanguageManager.get_default()
    source_language = lang_manager.get_language(language)

    if source_language is not None:
        source_buffer.set_language(source_language)
        source_buffer.set_highlight_syntax(True) # Ensure highlighting is enabled
    else:
        logging.error("GtkSource language definition for '%s' not found. "
                      "Syntax highlighting will be disabled for this buffer.", language)
        source_buffer.set_highlight_syntax(False) # Explicitly disable if language not found

    return source_buffer
