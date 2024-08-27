# preferences.py
from gi.repository import Adw, Gio, Gtk
from .constants import APP_ID, RESOURCE_PREFIX
from .style_utils import apply_font_size, apply_theme
import logging


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/preferences.ui")
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = "Preferences"

    font_size_scale = Gtk.Template.Child("font_size_scale")
    theme_switch = Gtk.Template.Child("theme_switch")
    source_style_scheme_combo_row = Gtk.Template.Child("source_style_scheme_combo_row")

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

    def load_ui(self):
        self.font_size_scale.connect("value-changed", self.on_font_size_changed)
        self.theme_switch.connect("state-set", self.on_theme_switch_changed)
        self.source_style_scheme_combo_row.connect(
            "notify::selected", self.on_source_style_scheme_changed
        )

    def on_font_size_changed(self, scale):
        font_size = scale.get_value()
        apply_font_size(self.settings, font_size)
        self.settings.set_int("font-size", font_size)

    def on_theme_switch_changed(self, switch, gparam):
        theme_enabled = switch.get_active()
        apply_theme(Adw.StyleManager.get_default(), theme_enabled)
        self.settings.set_boolean("dark-theme", theme_enabled)

        # Here, we access the main_window instance and call reload_css
        if self.main_window and hasattr(self.main_window, "reload_css"):
            self.main_window.reload_css()

    def on_source_style_scheme_changed(self, combo_row, gparam):
        selected_item = combo_row.get_selected_item()
        if isinstance(selected_item, Gtk.StringObject):
            source_style_scheme = selected_item.get_string()
            self.settings.set_string("source-style-scheme", source_style_scheme)

            if self.main_window and hasattr(
                self.main_window.nmap_page, "apply_source_style_scheme"
            ):
                self.main_window.nmap_page.apply_source_style_scheme(source_style_scheme)
            else:
                logging.error("NmapPage does not have apply_source_style_scheme method")

    def load_preferences(self):
        font_size = self.settings.get_int("font-size")
        self.font_size_scale.set_value(font_size)
        apply_font_size(self.settings, font_size)

        dark_theme_enabled = self.settings.get_boolean("dark-theme")
        self.theme_switch.set_active(dark_theme_enabled)
        apply_theme(Adw.StyleManager.get_default(), dark_theme_enabled)

        source_style_scheme = self.settings.get_string("source-style-scheme")
        normalized_scheme = (
            source_style_scheme.capitalize()
            if source_style_scheme.lower() in ["adwaita", "adwaita-dark"]
            else source_style_scheme.lower()
        )

        scheme_list = self.source_style_scheme_combo_row.get_model()
        for i, item in enumerate(scheme_list):
            item_string = item.get_string()
            item_string_normalized = (
                item_string.capitalize()
                if item_string.lower() in ["adwaita", "adwaita-dark"]
                else item_string.lower()
            )

            if item_string_normalized == normalized_scheme:
                self.source_style_scheme_combo_row.set_selected(i)
                break
        else:
            logging.warning(
                f"Style scheme '{normalized_scheme}' not found in the combo box options."
            )
