import logging
import re
import gi
import platform # Added

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource

BASE_FONT_SIZE_PT = 10.0

# GNOME GSettings schemas and keys
GNOME_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
FONT_NAME_KEY = "font-name"
TEXT_SCALING_FACTOR_KEY = "text-scaling-factor"
GTK_THEME_KEY = "gtk-theme" # For future high contrast check fallback
GNOME_A11Y_SCHEMA = "org.gnome.desktop.a11y.interface"
HIGH_CONTRAST_KEY = "high-contrast"


def apply_system_font_preferences(app_settings: Gio.Settings):
    """
    Applies font preferences, considering system-wide GNOME settings (if on Linux)
    and then applying the application's own font scaling percentage.
    """
    font_family_to_apply = None
    font_size_to_apply_pt = BASE_FONT_SIZE_PT  # Start with the application's base

    if platform.system() == "Linux":
        try:
            gnome_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
            font_name_str = gnome_settings.get_string(FONT_NAME_KEY)
            text_scaling_factor = gnome_settings.get_double(TEXT_SCALING_FACTOR_KEY)
            logging.debug(
                f"GNOME settings: font-name='{font_name_str}', text-scaling-factor={text_scaling_factor}"
            )

            # Parse font_name_str
            match = re.match(r"^(.*)\s+(\d+(\.\d+)?)$", font_name_str)
            if match:
                gnome_font_family = match.group(1).strip()
                gnome_base_size_pt = float(match.group(2))

                font_family_to_apply = gnome_font_family
                # Use GNOME font size and scaling factor as the new base
                font_size_to_apply_pt = gnome_base_size_pt * text_scaling_factor
                logging.info(
                    f"Applied GNOME font: Family='{font_family_to_apply}', "
                    f"Base Size (after GNOME scaling)={font_size_to_apply_pt:.2f}pt"
                )
            else:
                logging.warning(
                    f"Could not parse GNOME font-name string: '{font_name_str}'. "
                    f"Using application base font size {font_size_to_apply_pt}pt."
                )
        except GLib.Error as e: # GSettings schema not found / other GSettings error
            logging.debug(
                f"Could not retrieve GNOME desktop font settings (schema '{GNOME_INTERFACE_SCHEMA}'): {e}. "
                f"Using application base font size {font_size_to_apply_pt}pt."
            )

        # High Contrast check (placeholder logging for now)
        try:
            gnome_a11y_settings = Gio.Settings.new(GNOME_A11Y_SCHEMA)
            is_high_contrast = gnome_a11y_settings.get_boolean(HIGH_CONTRAST_KEY)
            logging.debug(f"GNOME accessibility: high-contrast={is_high_contrast}")
            # Actual theme application for high contrast is complex and usually handled
            # by GTK itself based on theme capabilities or Adw.StyleManager.
            # This log is for future reference if specific high-contrast CSS is needed.
        except GLib.Error as e:
            logging.debug(f"Could not retrieve GNOME accessibility settings (schema '{GNOME_A11Y_SCHEMA}'): {e}")


    # Apply application's own scaling percentage on top of the determined base font size
    font_scale_percentage_str = app_settings.get_string("font-scaling-percentage")
    parsed_app_percentage = 100.0
    match_app_scale = re.match(r"(\d+)\%?", font_scale_percentage_str)
    if match_app_scale:
        try:
            parsed_app_percentage = float(match_app_scale.group(1))
        except ValueError:
            logging.warning(
                f"Could not parse app font-scaling-percentage: '{font_scale_percentage_str}', defaulting to 100%."
            )
            parsed_app_percentage = 100.0
    else:
        logging.warning(
            f"Could not parse app font-scaling-percentage: '{font_scale_percentage_str}', defaulting to 100%."
        )

    final_font_size_pt = font_size_to_apply_pt * (parsed_app_percentage / 100.0)

    # Construct CSS
    if font_family_to_apply:
        # Ensure font family name is quoted if it contains spaces
        css_font_family = f"'{font_family_to_apply}'" if ' ' in font_family_to_apply else font_family_to_apply
        css = f"* {{ font-family: {css_font_family}; font-size: {final_font_size_pt:.2f}pt; }}"
    else:
        css = f"* {{ font-size: {final_font_size_pt:.2f}pt; }}"

    logging.info(
        f"Applying final CSS: {css} (Base size: {font_size_to_apply_pt:.2f}pt, App scale: {parsed_app_percentage}%)"
    )

    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(css.encode())

    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )

def apply_font_size(settings: Gio.Settings, font_scale_percentage_str: str): # pylint: disable=unused-argument
    """
    Applies font preferences based on system and application settings.
    The font_scale_percentage_str argument is currently ignored as the function
    now fetches this value directly from app_settings.
    """
    logging.debug(
        f"apply_font_size called (font_scale_percentage_str='{font_scale_percentage_str}' is ignored, "
        "will use value from Gio.Settings directly)."
        )
    apply_system_font_preferences(settings)


def apply_theme(style_manager: Adw.StyleManager, theme_preference: str):
    logging.debug("Applying theme preference: %s", theme_preference)
    if theme_preference == "Light":
        style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    elif theme_preference == "Dark":
        style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    else:  # Default to "System" or any other unexpected value
        style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


def apply_source_style_scheme(
    scheme_manager: GtkSource.StyleSchemeManager,
    buffer: GtkSource.Buffer,
    source_style_scheme: str,
):
    logging.debug(
        "apply_source_style_scheme: Called with source_style_scheme=%s", source_style_scheme
    )

    if source_style_scheme not in ["Adwaita", "Adwaita-dark"]:
        source_style_scheme = source_style_scheme.lower()

    scheme = scheme_manager.get_scheme(source_style_scheme)
    if scheme:
        buffer.set_style_scheme(scheme)
        applied_scheme = buffer.get_style_scheme()
        if applied_scheme:
            logging.debug(
                "apply_source_style_scheme: Successfully applied scheme=%s", applied_scheme.get_id()
            )
        else:
            logging.error(
                "apply_source_style_scheme: Failed to apply the style scheme."
            )
    else:
        logging.error(
            "apply_source_style_scheme: Style scheme '%s' not found.", source_style_scheme
        )
        default_scheme = scheme_manager.get_scheme("Adwaita")
        if default_scheme:
            buffer.set_style_scheme(default_scheme)
            logging.debug("apply_source_style_scheme: Applied fallback scheme=Adwaita")
        else:
            logging.error(
                "apply_source_style_scheme: Default scheme 'Adwaita' not found."
            )


def set_widget_visibility(visible: bool, *widgets):
    for widget in widgets:
        if widget:
            widget.set_visible(visible)
        else:
            logging.warning("Attempted to set visibility of a None widget")


def create_listbox_row(item_text: str) -> Gtk.ListBoxRow:
    label = Gtk.Label(label=item_text)
    row = Gtk.ListBoxRow()
    row.set_child(label)
    return row


def init_source_buffer(language: str = "yaml") -> GtkSource.Buffer:
    source_buffer = GtkSource.Buffer()
    lang_manager = GtkSource.LanguageManager.get_default()
    source_language = lang_manager.get_language(language)

    if source_language is not None:
        source_buffer.set_language(source_language)
    else:
        logging.error("%s language definition not found.", language)

    source_buffer.set_highlight_syntax(True)
    return source_buffer
