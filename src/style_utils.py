"""Utility functions for managing application styling.

This module provides functions for applying font preferences, themes,
and :class:`GtkSource.View` style schemes. It interacts with GSettings to retrieve
system and application-specific style configurations.
"""

import logging
import re
import platform
from typing import Optional, Tuple

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource, GLib, Pango

BASE_FONT_SIZE_PT = 12.0

GNOME_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
FONT_NAME_KEY = "font-name"
TEXT_SCALING_FACTOR_KEY = "text-scaling-factor"
GTK_THEME_KEY = "gtk-theme"
GNOME_A11Y_SCHEMA = "org.gnome.desktop.a11y.interface"
HIGH_CONTRAST_KEY = "high-contrast"


def _get_linux_font_preferences(
    base_font_size_pt: float,
) -> Tuple[Optional[str], float]:
    """Get font preferences from GNOME settings on Linux.

    Retrieves font family and size from ``org.gnome.desktop.interface`` schema.
    Applies GNOME's text scaling factor.

    :param base_font_size_pt: The base font size to use if system settings are unavailable.
    :type base_font_size_pt: float
    :return: A tuple containing the font family (str or ``None``) and the calculated font size in points.
    :rtype: tuple[Optional[str], float]
    """
    font_family: Optional[str] = None
    font_size_pt: float = base_font_size_pt
    try:
        gnome_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
        font_name_str = gnome_settings.get_string(FONT_NAME_KEY)
        text_scaling_factor = gnome_settings.get_double(TEXT_SCALING_FACTOR_KEY)

        if font_name_str:
            desc = Pango.FontDescription.from_string(font_name_str)
            font_family = desc.get_family()
            size = desc.get_size()
            if size > 0:
                if desc.get_size_is_absolute():
                    gnome_base_size_pt = (size / Pango.SCALE) * 0.75
                else:
                    gnome_base_size_pt = size / Pango.SCALE
            else:
                gnome_base_size_pt = base_font_size_pt
            font_size_pt = gnome_base_size_pt * text_scaling_factor
    except GLib.Error:
        pass  # GNOME desktop font settings might not be available

    try:
        gnome_a11y_settings = Gio.Settings.new(GNOME_A11Y_SCHEMA)
        gnome_a11y_settings.get_boolean(HIGH_CONTRAST_KEY)  # Keep call if getter has side-effects
    except GLib.Error:
        pass  # GNOME accessibility settings might not be available
    return font_family, font_size_pt


def _get_app_font_scaling(app_settings: Gio.Settings) -> float:
    """Get app-specific font scaling percentage from GSettings.

    Parses the 'font-scaling-percentage' string (e.g., "100%") and returns it as a float.
    Defaults to 100.0 if parsing fails.

    :param app_settings: The application's :class:`Gio.Settings` object.
    :type app_settings: Gio.Settings
    :return: The font scaling percentage as a float (e.g., 100.0, 120.0).
    :rtype: float
    """
    font_scale_percentage_str = app_settings.get_string("font-scaling-percentage")
    parsed_app_percentage: float = 100.0
    match_app_scale = re.match(r"(\d+)\%?", font_scale_percentage_str)
    if match_app_scale:
        try:
            parsed_app_percentage = float(match_app_scale.group(1))
        except ValueError:
            logging.warning(
                "Could not parse app font-scaling-percentage: '%s', defaulting to 100%%.",
                font_scale_percentage_str,
            )
    else:
        logging.warning(
            "Could not parse app font-scaling-percentage: '%s', defaulting to 100%%.",
            font_scale_percentage_str,
        )
    return parsed_app_percentage


_font_css_provider: Optional[Gtk.CssProvider] = None


def apply_system_font_preferences(app_settings: Gio.Settings):
    """Apply font preferences system-wide.

    Applies the application's font scaling percentage from GSettings
    and optionally sets the system font family on the root window using a :class:`Gtk.CssProvider`.

    :param app_settings: The application's :class:`Gio.Settings` object.
    :type app_settings: Gio.Settings
    """
    global _font_css_provider

    font_family_to_apply: Optional[str] = None
    if platform.system() == "Linux":
        font_family_to_apply, _ = _get_linux_font_preferences(BASE_FONT_SIZE_PT)

    parsed_app_percentage = _get_app_font_scaling(app_settings)

    css_rules = []
    if font_family_to_apply:
        css_font_family = f"'{font_family_to_apply}'" if " " in font_family_to_apply else font_family_to_apply
        css_rules.append(f"font-family: {css_font_family};")

    css_rules.append(f"font-size: {parsed_app_percentage:.0f}%;")

    css = f"window {{ {' '.join(css_rules)} }}"

    logging.info(
        "Applying font preferences: %s (App scale: %s%%)",
        css,
        parsed_app_percentage,
    )

    display = Gdk.Display.get_default()
    if display:
        if _font_css_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, _font_css_provider)

        _font_css_provider = Gtk.CssProvider()
        _font_css_provider.load_from_data(css.encode())

        Gtk.StyleContext.add_provider_for_display(
            display,
            _font_css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def apply_font_size(settings: Gio.Settings):
    """Apply font size preferences based on system and application settings.

    This function is a wrapper around :func:`apply_system_font_preferences`.

    :param settings: The application's :class:`Gio.Settings` object.
    :type settings: Gio.Settings
    """
    apply_system_font_preferences(settings)


def apply_theme(style_manager: Adw.StyleManager, theme_preference: str):
    """Apply the selected color scheme (theme) to the application.

    :param style_manager: The :class:`Adw.StyleManager` instance for the application.
    :type style_manager: Adw.StyleManager
    :param theme_preference: The theme preference string ("Light", "Dark", or "System").
    :type theme_preference: str
    """
    if theme_preference == "Light":
        style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    elif theme_preference == "Dark":
        style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    else:
        style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


def apply_source_style_scheme(
    scheme_manager: GtkSource.StyleSchemeManager,
    buffer: GtkSource.Buffer,
    source_style_scheme: str,
):
    """Apply the selected style scheme to a :class:`GtkSource.Buffer`.

    If the specified scheme is not found, it attempts to fall back to "Adwaita".

    :param scheme_manager: The :class:`GtkSource.StyleSchemeManager`.
    :type scheme_manager: GtkSource.StyleSchemeManager
    :param buffer: The :class:`GtkSource.Buffer` to apply the scheme to.
    :type buffer: GtkSource.Buffer
    :param source_style_scheme: The name of the style scheme to apply.
    :type source_style_scheme: str
    """
    if source_style_scheme not in ["Adwaita", "Adwaita-dark"]:
        source_style_scheme = source_style_scheme.lower()

    scheme = scheme_manager.get_scheme(source_style_scheme)
    if scheme:
        buffer.set_style_scheme(scheme)
        applied_scheme = buffer.get_style_scheme()
        if not applied_scheme:
            logging.error("apply_source_style_scheme: Failed to apply the style scheme.")
    else:
        logging.error(
            "apply_source_style_scheme: Style scheme '%s' not found.",
            source_style_scheme,
        )
        default_scheme = scheme_manager.get_scheme("Adwaita")
        if default_scheme:
            buffer.set_style_scheme(default_scheme)
        else:
            logging.error("apply_source_style_scheme: Default scheme 'Adwaita' not found.")


def set_widget_visibility(visible: bool, *widgets: Gtk.Widget):
    """Set the visibility of one or more :class:`Gtk.Widget`.

    :param visible: ``True`` to make widgets visible, ``False`` to hide them.
    :type visible: bool
    :param widgets: The :class:`Gtk.Widget`(s) to modify. ``None`` values are logged and skipped.
    :type widgets: Gtk.Widget
    """
    for widget in widgets:
        if widget:
            widget.set_visible(visible)
        else:
            logging.warning("Attempted to set visibility of a None widget")


def create_listbox_row(item_text: str) -> Gtk.ListBoxRow:
    """Create a simple :class:`Gtk.ListBoxRow` containing a :class:`Gtk.Label`.

    :param item_text: The text to display in the label of the list box row.
    :type item_text: str
    :return: A :class:`Gtk.ListBoxRow` with the specified label.
    :rtype: Gtk.ListBoxRow
    """
    label = Gtk.Label(label=item_text)
    row = Gtk.ListBoxRow()
    row.set_child(label)
    return row


def init_source_buffer(language: str = "yaml") -> GtkSource.Buffer:
    """Initialize a :class:`GtkSource.Buffer` with syntax highlighting for a given language.

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
    else:
        logging.error("%s language definition not found.", language)

    source_buffer.set_highlight_syntax(True)
    return source_buffer
