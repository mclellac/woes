import logging

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource

logger = logging.getLogger(__name__)


def apply_font_size(_settings: Gio.Settings, font_size: int):
    logger.debug(f"style_utils.apply_font_size: Starting with _settings: {_settings}, font_size: {font_size}")
    css_provider = Gtk.CssProvider()
    logger.debug(f"style_utils.apply_font_size: Gtk.CssProvider created: {css_provider}")
    css = f"* {{ font-size: {font_size}pt; }}"
    logger.debug(f"style_utils.apply_font_size: Generated CSS: '{css}'")
    logger.debug("style_utils.apply_font_size: Before css_provider.load_from_data()")
    css_provider.load_from_data(css.encode())
    logger.debug("style_utils.apply_font_size: After css_provider.load_from_data()")

    display = Gdk.Display.get_default()
    logger.debug(f"style_utils.apply_font_size: Gdk.Display.get_default(): {display}")
    logger.debug("style_utils.apply_font_size: Before Gtk.StyleContext.add_provider_for_display()")
    Gtk.StyleContext.add_provider_for_display(
        display,
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    logger.debug("style_utils.apply_font_size: After Gtk.StyleContext.add_provider_for_display()")
    logger.debug("style_utils.apply_font_size: Finished.")


def apply_theme(style_manager: Adw.StyleManager, dark_theme_enabled: bool):
    logger.debug(f"style_utils.apply_theme: Starting with style_manager: {style_manager}, dark_theme_enabled: {dark_theme_enabled}")
    if dark_theme_enabled:
        logger.debug("style_utils.apply_theme: Setting color scheme to Adw.ColorScheme.PREFER_DARK")
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
    else:
        logger.debug("style_utils.apply_theme: Setting color scheme to Adw.ColorScheme.PREFER_LIGHT")
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)
    logger.debug(f"style_utils.apply_theme: Color scheme set to: {style_manager.get_color_scheme()}")
    logger.debug("style_utils.apply_theme: Finished.")


def apply_source_style_scheme(
    scheme_manager: GtkSource.StyleSchemeManager,
    buffer: GtkSource.Buffer,
    source_style_scheme: str,
):
    logger.debug(
        f"style_utils.apply_source_style_scheme: Starting with scheme_manager: {scheme_manager}, "
        f"buffer: {buffer}, source_style_scheme: '{source_style_scheme}'"
    )

    original_scheme_name_for_log = source_style_scheme
    if source_style_scheme not in ["Adwaita", "Adwaita-dark"]:
        source_style_scheme_lower = source_style_scheme.lower()
        logger.debug(
            f"style_utils.apply_source_style_scheme: Scheme '{source_style_scheme}' not Adwaita(-dark), "
            f"trying lowercase '{source_style_scheme_lower}'"
        )
        if scheme_manager.get_scheme(source_style_scheme_lower):
            source_style_scheme = source_style_scheme_lower
            logger.debug(f"style_utils.apply_source_style_scheme: Lowercase scheme '{source_style_scheme}' found.")
        else:
            logger.debug(f"style_utils.apply_source_style_scheme: Lowercase scheme '{source_style_scheme_lower}' not found either.")

    logger.debug(f"style_utils.apply_source_style_scheme: Attempting to get scheme '{source_style_scheme}' from manager.")
    scheme = scheme_manager.get_scheme(source_style_scheme)
    if scheme:
        logger.debug(f"style_utils.apply_source_style_scheme: Scheme '{source_style_scheme}' found: {scheme}. Setting on buffer.")
        buffer.set_style_scheme(scheme)
        applied_scheme = buffer.get_style_scheme()
        if applied_scheme:
            logger.debug(
                "style_utils.apply_source_style_scheme: Successfully applied scheme=%s", applied_scheme.get_id()
            )
        else:
            logger.error(
                "style_utils.apply_source_style_scheme: Failed to apply the style scheme, "
                "even though scheme object was obtained."
            )
    else:
        logger.error(
            "style_utils.apply_source_style_scheme: Style scheme '%s' (tried '%s') not found.",
            original_scheme_name_for_log, source_style_scheme
        )
        default_scheme_id = "Adwaita"
        logger.debug(f"style_utils.apply_source_style_scheme: Attempting to apply fallback scheme '{default_scheme_id}'.")
        default_scheme = scheme_manager.get_scheme(default_scheme_id)
        if default_scheme:
            buffer.set_style_scheme(default_scheme)
            logger.debug("style_utils.apply_source_style_scheme: Applied fallback scheme=%s", default_scheme_id)
        else:
            logger.error(
                "style_utils.apply_source_style_scheme: Default scheme '%s' not found.", default_scheme_id
            )
    logger.debug("style_utils.apply_source_style_scheme: Finished.")


def set_widget_visibility(visible: bool, *widgets):
    logger.debug(f"style_utils.set_widget_visibility: Starting with visible: {visible}, widgets: {widgets}")
    for i, widget in enumerate(widgets):
        if widget:
            logger.debug(f"style_utils.set_widget_visibility: Setting widget {i} ({widget}) visibility to {visible}")
            widget.set_visible(visible)
        else:
            logger.warning(f"style_utils.set_widget_visibility: Attempted to set visibility of widget {i}, but it is None")
    logger.debug("style_utils.set_widget_visibility: Finished.")


def create_listbox_row(item_text: str) -> Gtk.ListBoxRow:
    logger.debug(f"style_utils.create_listbox_row: Starting with item_text: '{item_text}'")
    label = Gtk.Label(label=item_text)
    logger.debug(f"style_utils.create_listbox_row: Gtk.Label created: {label} with text '{item_text}'")
    row = Gtk.ListBoxRow()
    logger.debug(f"style_utils.create_listbox_row: Gtk.ListBoxRow created: {row}")
    row.set_child(label)
    logger.debug(f"style_utils.create_listbox_row: Label set as child of row.")
    logger.debug(f"style_utils.create_listbox_row: Returning row: {row}")
    return row


def init_source_buffer(language: str = "yaml") -> GtkSource.Buffer:
    logger.debug(f"style_utils.init_source_buffer: Starting with language: '{language}'")
    source_buffer = GtkSource.Buffer()
    logger.debug(f"style_utils.init_source_buffer: GtkSource.Buffer created: {source_buffer}")
    lang_manager = GtkSource.LanguageManager.get_default()
    logger.debug(f"style_utils.init_source_buffer: GtkSource.LanguageManager.get_default(): {lang_manager}")
    source_language = lang_manager.get_language(language)
    logger.debug(f"style_utils.init_source_buffer: Language manager get_language('{language}') returned: {source_language}")

    if source_language is not None:
        source_buffer.set_language(source_language)
        logger.debug(f"style_utils.init_source_buffer: Language for source_buffer set to: {source_language.get_name() if source_language else None}")
    else:
        logger.error(f"style_utils.init_source_buffer: GtkSource language definition '{language}' not found.") # Existing, made f-string

    source_buffer.set_highlight_syntax(True)
    logger.debug("style_utils.init_source_buffer: Syntax highlighting set to True for source_buffer.")
    logger.debug(f"style_utils.init_source_buffer: Returning source_buffer: {source_buffer}")
    return source_buffer
