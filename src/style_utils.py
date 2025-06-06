import logging
import re
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource

BASE_FONT_SIZE_PT = 10.0  # Define a base font size

def apply_font_size(_settings: Gio.Settings, font_scale_percentage_str: str):  # Prefixed unused 'settings'
    logging.debug(f"apply_font_size called with: '{font_scale_percentage_str}'")
    parsed_percentage = 100.0  # Default
    match = re.match(r"(\d+)\%?", font_scale_percentage_str)
    if match:
        try:
            parsed_percentage = float(match.group(1))
        except ValueError:
            logging.warning(f"Could not parse percentage from '{font_scale_percentage_str}', defaulting to 100%.")
            parsed_percentage = 100.0
    else:
        logging.warning(f"Could not parse percentage from '{font_scale_percentage_str}', defaulting to 100%.")

    actual_font_size_pt = BASE_FONT_SIZE_PT * (parsed_percentage / 100.0)
    logging.debug(f"Applying font: base={BASE_FONT_SIZE_PT}pt, scale_pref='{font_scale_percentage_str}', calculated_size={actual_font_size_pt}pt")

    css_provider = Gtk.CssProvider()
    css = f"* {{ font-size: {actual_font_size_pt}pt; }}"
    css_provider.load_from_data(css.encode())

    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


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
