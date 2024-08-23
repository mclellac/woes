# style_utils.py
import logging
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import GtkSource, Gio, Adw, Gtk, Gdk


def apply_font_size(settings: Gio.Settings, font_size: int):
    css_provider = Gtk.CssProvider()
    css = f"* {{ font-size: {font_size}pt; }}"
    css_provider.load_from_data(css.encode())

    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def apply_theme(style_manager: Adw.StyleManager, dark_theme_enabled: bool):
    if dark_theme_enabled:
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
    else:
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)


def apply_source_style_scheme(
    scheme_manager: GtkSource.StyleSchemeManager,
    buffer: GtkSource.Buffer,
    source_style_scheme: str,
):
    logging.debug(
        f"apply_source_style_scheme: Called with source_style_scheme={source_style_scheme}"
    )

    if source_style_scheme not in ["Adwaita", "Adwaita-dark"]:
        source_style_scheme = source_style_scheme.lower()

    scheme = scheme_manager.get_scheme(source_style_scheme)
    if scheme:
        buffer.set_style_scheme(scheme)
        applied_scheme = buffer.get_style_scheme()
        if applied_scheme:
            logging.debug(
                f"apply_source_style_scheme: Successfully applied scheme={applied_scheme.get_id()}"
            )
        else:
            logging.error("apply_source_style_scheme: Failed to apply the style scheme.")
    else:
        logging.error(f"apply_source_style_scheme: Style scheme '{source_style_scheme}' not found.")
        default_scheme = scheme_manager.get_scheme("Adwaita")
        if default_scheme:
            buffer.set_style_scheme(default_scheme)
            logging.debug("apply_source_style_scheme: Applied fallback scheme=Adwaita")
        else:
            logging.error("apply_source_style_scheme: Default scheme 'Adwaita' not found.")


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
        logging.error(f"{language} language definition not found.")

    source_buffer.set_highlight_syntax(True)
    return source_buffer
