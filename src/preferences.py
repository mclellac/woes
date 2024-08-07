import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio

@Gtk.Template(resource_path='/com/github/mclellac/WebOpsEvaluationSuite/gtk/preferences.ui')
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = 'Preferences'

    # Define template children with proper IDs
    font_size_row = Gtk.Template.Child('font_size_row')
    font_size_scale = Gtk.Template.Child('font_size_scale')
    theme_row = Gtk.Template.Child('theme_row')
    theme_switch = Gtk.Template.Child('theme_switch')
    payloads_row = Gtk.Template.Child('payloads_row')
    payloads_combo = Gtk.Template.Child('payloads_combo')

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.set_transient_for(main_window)
        self.load_ui()

    def load_ui(self):
        # Ensure all template children are loaded
        if not all([self.font_size_row, self.font_size_scale, self.theme_row, self.theme_switch, self.payloads_row, self.payloads_combo]):
            raise RuntimeError("One or more template children are not loaded")

        # Connect signals
        self.font_size_scale.connect('value-changed', self.on_font_size_changed)
        self.theme_switch.connect('state-set', self.on_theme_switch_changed)
        self.payloads_combo.connect('changed', self.on_payloads_combo_changed)

        # Load preferences
        self.load_preferences()

    def on_font_size_changed(self, scale):
        font_size = scale.get_value()
        if self.main_window:
            self.main_window.apply_font_size(font_size)
        self.save_preferences()

    def on_theme_switch_changed(self, switch, gparam):
        theme_enabled = switch.get_active()
        if self.main_window:
            self.main_window.apply_theme(theme_enabled)
        self.save_preferences()

    def on_payloads_combo_changed(self, combo):
        selected_item = combo.get_active()
        if selected_item:
            payload = selected_item.get_label()
            self.get_title_bar().set_subtitle(f"Selected payload: {payload}")
            self.save_preferences()

    def save_preferences(self):
        settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')
        settings.set_int('font-size', self.font_size_scale.get_value())
        settings.set_boolean('dark-theme', self.theme_switch.get_active())
        selected_payload = self.payloads_combo.get_active()
        if selected_payload:
            settings.set_string('json-payload', selected_payload.get_label())

    def load_preferences(self):
        settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')
        font_size = settings.get_int('font-size')
        self.font_size_scale.set_value(font_size)
        dark_theme_enabled = settings.get_boolean('dark-theme')
        self.theme_switch.set_active(dark_theme_enabled)

        selected_payload = settings.get_string('json-payload')
        if selected_payload:
            model = self.payloads_combo.get_model()
            for i, row in enumerate(model):
                if row[0] == selected_payload:
                    self.payloads_combo.set_active(i)
                    break

