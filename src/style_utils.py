import logging
import gi

# GTK version requirements must be called before importing from gi.repository
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')

# Now import GTK libraries and other dependencies
# pylint: disable=wrong-import-position
from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource  # Added Gio for settings type hint

# Configure logger for the module - AFTER all imports
logger = logging.getLogger(__name__)


def apply_font_size(_settings: Gio.Settings, font_size: int):  # Prefixed unused 'settings'
    css_provider = Gtk.CssProvider()
    css = f"* {{ font-size: {font_size}pt; }}"  # f-string is fine here
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
    logger.debug(
        "apply_source_style_scheme: Called with source_style_scheme=%s", source_style_scheme
    )

    if source_style_scheme not in ["Adwaita", "Adwaita-dark"]:  # Case sensitive check
        source_style_scheme_lower = source_style_scheme.lower()
        # Check if the lowercased version is a known scheme ID, some schemes might be like 'oblivion' not 'Oblivion'
        if scheme_manager.get_scheme(source_style_scheme_lower):
            source_style_scheme = source_style_scheme_lower
        # If not, it will likely fail to get scheme and use fallback

    scheme = scheme_manager.get_scheme(source_style_scheme)
    if scheme:
        buffer.set_style_scheme(scheme)
        applied_scheme = buffer.get_style_scheme()
        if applied_scheme:
            logger.debug(
                "apply_source_style_scheme: Successfully applied scheme=%s", applied_scheme.get_id()
            )
        else:  # This case should ideally not happen if set_style_scheme was successful with a valid scheme
            logger.error(
                "apply_source_style_scheme: Failed to apply the style scheme, even though scheme object was obtained."
            )
    else:
        logger.error(
            "apply_source_style_scheme: Style scheme '%s' not found.", source_style_scheme
        )
        default_scheme_id = "Adwaita"  # Adwaita is generally available
        default_scheme = scheme_manager.get_scheme(default_scheme_id)
        if default_scheme:
            buffer.set_style_scheme(default_scheme)
            logger.debug("apply_source_style_scheme: Applied fallback scheme=%s", default_scheme_id)
        else:
            logger.error(
                "apply_source_style_scheme: Default scheme '%s' not found.", default_scheme_id
            )


def set_widget_visibility(visible: bool, *widgets):
    for widget in widgets:
        if widget:
            widget.set_visible(visible)
        else:
            logger.warning("Attempted to set visibility of a None widget")


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
        logger.error("GtkSource language definition '%s' not found.", language)

    source_buffer.set_highlight_syntax(True)
    return source_buffer
