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

    # Apply the CSS to the default screen
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

def apply_source_style_scheme(scheme_manager: GtkSource.StyleSchemeManager, buffer: GtkSource.Buffer, scheme_name: str):
    # Convert scheme_name to lowercase except for "Adwaita" and "Adwaita-dark"
    if scheme_name not in ["Adwaita", "Adwaita-dark"]:
        scheme_name = scheme_name.lower()

    # Log all available schemes
    available_schemes = scheme_manager.get_scheme_ids()
    logging.debug(f"Available style schemes: {available_schemes}")

    scheme = scheme_manager.get_scheme(scheme_name)
    if scheme:
        logging.debug(f"Applying style scheme: {scheme.get_id()} to buffer.")
        buffer.set_style_scheme(scheme)

        # Log the current style scheme for debugging
        applied_scheme = buffer.get_style_scheme()
        if applied_scheme:
            logging.debug(f"Successfully applied scheme: {applied_scheme.get_id()}")
        else:
            logging.error("Failed to apply the style scheme.")
    else:
        logging.error(f"Style scheme '{scheme_name}' not found in GtkSource.StyleSchemeManager.")
