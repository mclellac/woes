# preferences.py
import gi
from .constants import RESOURCE_PREFIX, APP_ID

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, Gdk

@Gtk.Template(resource_path=f'{RESOURCE_PREFIX}/preferences.ui')
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = 'Preferences'

    font_size_row = Gtk.Template.Child('font_size_row')
    font_size_scale = Gtk.Template.Child('font_size_scale')
    theme_row = Gtk.Template.Child('theme_row')
    theme_switch = Gtk.Template.Child('theme_switch')
    payloads_row = Gtk.Template.Child('payloads_row')
    payloads_combo = Gtk.Template.Child('payloads_combo')

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()

    def load_ui(self):
        if not all([self.font_size_row, self.font_size_scale, self.theme_row, self.theme_switch, self.payloads_row, self.payloads_combo]):
            raise RuntimeError("One or more template children are not loaded")

        self.font_size_scale.connect('value-changed', self.on_font_size_changed)
        self.theme_switch.connect('state-set', self.on_theme_switch_changed)
        self.payloads_combo.connect('changed', self.on_payloads_combo_changed)

        self.load_preferences()

    def apply_font_size(self, font_size: int):
        css_provider = Gtk.CssProvider()
        css = f"* {{ font-size: {font_size}pt; }}"  # Apply to all widgets
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def apply_theme(self, dark_theme_enabled: bool):
        if dark_theme_enabled:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        else:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)

    def on_font_size_changed(self, scale):
        font_size = scale.get_value()
        self.apply_font_size(font_size)
        self.save_preferences()

    def on_theme_switch_changed(self, switch, gparam):
        theme_enabled = switch.get_active()
        self.apply_theme(theme_enabled)
        self.save_preferences()

    def on_payloads_combo_changed(self, combo):
        selected_item = combo.get_active()
        if selected_item:
            payload = selected_item.get_label()
            self.get_title_bar().set_subtitle(f"Selected payload: {payload}")
            self.save_preferences()

    def save_preferences(self):
        self.settings.set_int('font-size', int(self.font_size_scale.get_value()))
        dark_theme = self.theme_switch.get_active()
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK if dark_theme else Adw.ColorScheme.PREFER_LIGHT)
        selected_payload_index = self.payloads_combo.get_active()
        if selected_payload_index != -1:
            model = self.payloads_combo.get_model()
            selected_payload = model[selected_payload_index][0]
            self.settings.set_string('json-payload', selected_payload)

    def load_preferences(self):
        font_size = self.settings.get_int('font-size')
        self.font_size_scale.set_value(font_size)
        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        self.theme_switch.set_active(dark_theme_enabled)

        selected_payload = self.settings.get_string('json-payload')
        if selected_payload:
            model = self.payloads_combo.get_model()
            for i, row in enumerate(model):
                if row[0] == selected_payload:
                    self.payloads_combo.set_active(i)
                    break

