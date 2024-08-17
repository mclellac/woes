import logging
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Adw, Gio, Gtk, GObject

from .constants import APP_ID, RESOURCE_PREFIX
from .style_utils import apply_font_size, apply_theme  # Ensure this line is correct


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = "Preferences"

    font_size_scale = Gtk.Template.Child("font_size_scale")
    theme_switch = Gtk.Template.Child("theme_switch")
    source_style_scheme_combo_row = Gtk.Template.Child("source_style_scheme_combo_row")

    __gsignals__ = {
        "source-style-scheme-changed": (GObject.SIGNAL_RUN_FIRST, None, (str,)),
    }

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

    def load_ui(self):
        if not all([self.font_size_scale, self.theme_switch, self.source_style_scheme_combo_row]):
            raise RuntimeError("One or more template children are not loaded")

        self.font_size_scale.connect("value-changed", self.on_font_size_changed)
        self.theme_switch.connect("state-set", self.on_theme_switch_changed)
        self.source_style_scheme_combo_row.connect("notify::selected", self.on_source_style_scheme_changed)

    def on_font_size_changed(self, scale):
        font_size = scale.get_value()
        apply_font_size(self.settings, font_size)
        self.save_preferences()

    def on_theme_switch_changed(self, switch, gparam):
        theme_enabled = switch.get_active()
        apply_theme(Adw.StyleManager.get_default(), theme_enabled)
        self.save_preferences()

    def on_source_style_scheme_changed(self, combo_row, gparam):
        selected_item = combo_row.get_selected_item()
        if isinstance(selected_item, Gtk.StringObject):
            color_scheme = selected_item.get_string()

            current_scheme = self.main_window.nmap_page.source_buffer.get_style_scheme()
            if current_scheme is None or current_scheme.get_id().lower() != color_scheme.lower():
                logging.debug(f"Applying new style scheme: {color_scheme}")
                self.main_window.nmap_page.apply_style_scheme_to_source_view(color_scheme)
                self.save_preferences()  # Save the selected scheme after applying it
            else:
                logging.debug(f"Scheme '{color_scheme}' is already applied.")


    def save_preferences(self):
        self.settings.set_int("font-size", int(self.font_size_scale.get_value()))
        dark_theme = self.theme_switch.get_active()
        self.settings.set_boolean("dark-theme", dark_theme)
        source_style_scheme = self.source_style_scheme_combo_row.get_selected_item().get_string()
        logging.debug(f"Saving source-style-scheme: {source_style_scheme}")
        self.settings.set_string("source-style-scheme", source_style_scheme)


    def load_preferences(self):
        font_size = self.settings.get_int("font-size")
        self.font_size_scale.set_value(font_size)

        dark_theme_enabled = self.settings.get_boolean("dark-theme")
        self.theme_switch.set_active(dark_theme_enabled)
        apply_theme(Adw.StyleManager.get_default(), dark_theme_enabled)

        source_style_scheme = self.settings.get_string("source-style-scheme")
        scheme_list = self.source_style_scheme_combo_row.get_model()
        for i, item in enumerate(scheme_list):
            if item.get_string() == source_style_scheme:
                self.source_style_scheme_combo_row.set_selected(i)
                break
