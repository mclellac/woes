from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource, GLib
import logging
import re
import gi
import platform

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')

BASE_FONT_SIZE_PT = 10.0

GNOME_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
FONT_NAME_KEY = "font-name"
TEXT_SCALING_FACTOR_KEY = "text-scaling-factor"
GTK_THEME_KEY = "gtk-theme"
GNOME_A11Y_SCHEMA = "org.gnome.desktop.a11y.interface"
HIGH_CONTRAST_KEY = "high-contrast"


def apply_system_font_preferences(app_settings: Gio.Settings):
    """
    Applies font preferences, considering system-wide GNOME settings (if on Linux)
    and then applying the application's own font scaling percentage.
    """
    font_family_to_apply = None
    font_size_to_apply_pt = BASE_FONT_SIZE_PT

    if platform.system() == "Linux":
        try:
            gnome_settings = Gio.Settings.new(GNOME_INTERFACE_SCHEMA)
            font_name_str = gnome_settings.get_string(FONT_NAME_KEY)
            text_scaling_factor = gnome_settings.get_double(TEXT_SCALING_FACTOR_KEY)
            # logging.debug for font-name and text-scaling-factor removed previously

            match = re.match(r"^(.*)\s+(\d+(\.\d+)?)$", font_name_str)
            if match:
                gnome_font_family = match.group(1).strip()
                gnome_base_size_pt = float(match.group(2))

                font_family_to_apply = gnome_font_family
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
        except GLib.Error:  # e removed as it's unused after debug log removal
            # logging.debug for GNOME desktop font settings retrieval removed previously
            pass

        try:
            gnome_a11y_settings = Gio.Settings.new(GNOME_A11Y_SCHEMA)
            # is_high_contrast = gnome_a11y_settings.get_boolean(HIGH_CONTRAST_KEY) # F841
            gnome_a11y_settings.get_boolean(HIGH_CONTRAST_KEY)  # Keep call if getter has side-effects
            # logging.debug for GNOME accessibility high-contrast removed previously
        except GLib.Error:  # e removed as it's unused after debug log removal
            # logging.debug for GNOME accessibility settings retrieval removed previously
            pass

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

    if font_family_to_apply:
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


def apply_font_size(settings: Gio.Settings, font_scale_percentage_str: str):  # pylint: disable=unused-argument
    """
    Applies font preferences based on system and application settings.
    The font_scale_percentage_str argument is currently ignored as the function
    now fetches this value directly from app_settings.
    """
    # logging.debug for apply_font_size call removed previously
    apply_system_font_preferences(settings)


def apply_theme(style_manager: Adw.StyleManager, theme_preference: str):
    # logging.debug for apply_theme removed previously
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
    # logging.debug for apply_source_style_scheme call removed previously

    if source_style_scheme not in ["Adwaita", "Adwaita-dark"]:
        source_style_scheme = source_style_scheme.lower()

    scheme = scheme_manager.get_scheme(source_style_scheme)
    if scheme:
        buffer.set_style_scheme(scheme)
        applied_scheme = buffer.get_style_scheme()
        if not applied_scheme:  # logging.debug for successful application removed
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
            # logging.debug for fallback scheme removed previously
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
