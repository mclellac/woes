import logging
import gi

from .constants import APP_ID, RESOURCE_PREFIX

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk, GObject

@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = "Preferences"

    font_size_scale = Gtk.Template.Child("font_size_scale")
    theme_switch = Gtk.Template.Child("theme_switch")
    color_scheme_combo_row = Gtk.Template.Child("color_scheme_combo_row")

    __gsignals__ = {
        "color-scheme-changed": (GObject.SIGNAL_RUN_FIRST, None, (str,)),
    }

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

    def load_ui(self):
        if not all([self.font_size_scale, self.theme_switch, self.color_scheme_combo_row]):
            raise RuntimeError("One or more template children are not loaded")

        self.font_size_scale.connect("value-changed", self.on_font_size_changed)
        self.theme_switch.connect("state-set", self.on_theme_switch_changed)
        self.color_scheme_combo_row.connect("notify::selected", self.on_color_scheme_changed)

    def apply_font_size(self, font_size: int):
        css_provider = Gtk.CssProvider()
        css = f"* {{ font-size: {font_size}pt; }}"
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def apply_theme(self, dark_theme_enabled: bool):
        style_manager = Adw.StyleManager.get_default()
        if dark_theme_enabled:
            style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)

    def apply_color_scheme(self, color_scheme: str):
        normalized_scheme_name = color_scheme.lower().replace(" ", "-")
        self.emit("color-scheme-changed", normalized_scheme_name)

    def on_font_size_changed(self, scale):
        font_size = scale.get_value()
        self.apply_font_size(font_size)
        self.save_preferences()

    def on_theme_switch_changed(self, switch, gparam):
        theme_enabled = switch.get_active()
        self.apply_theme(theme_enabled)
        self.save_preferences()

    def on_color_scheme_changed(self, combo_row, gparam):
        selected_item = combo_row.get_selected_item()
        if isinstance(selected_item, Gtk.StringObject):
            color_scheme = selected_item.get_string()
            logging.debug(f"Emitting color-scheme-changed signal with scheme: {color_scheme}")
            self.apply_color_scheme(color_scheme)
            self.save_preferences()

    def save_preferences(self):
        self.settings.set_int("font-size", int(self.font_size_scale.get_value()))
        dark_theme = self.theme_switch.get_active()
        self.settings.set_boolean("dark-theme", dark_theme)
        color_scheme = self.color_scheme_combo_row.get_selected_item().get_string()
        self.settings.set_string("color-scheme", color_scheme)

    def load_preferences(self):
        font_size = self.settings.get_int("font-size")
        self.font_size_scale.set_value(font_size)

        dark_theme_enabled = self.settings.get_boolean("dark-theme")
        self.theme_switch.set_active(dark_theme_enabled)
        self.apply_theme(dark_theme_enabled)

        color_scheme = self.settings.get_string("color-scheme")
        scheme_list = self.color_scheme_combo_row.get_model()
        for i, item in enumerate(scheme_list):
            if item.get_string() == color_scheme:
                self.color_scheme_combo_row.set_selected(i)
                break
