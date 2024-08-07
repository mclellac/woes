import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, Gdk

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
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')
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

    def apply_font_size(self, font_size: int):
        """
        Apply the given font size to all widgets.
        """
        css_provider = Gtk.CssProvider()
        css = f"""
        * {{ font-size: {font_size}pt; }}  /* Apply to all widgets */
        """
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


    def apply_theme(self, dark_theme_enabled: bool):
        """
        Apply the theme based on the dark-theme setting.
        """
        if dark_theme_enabled:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        else:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)

    def on_font_size_changed(self, scale):
        font_size = scale.get_value()
        Preferences.apply_font_size(self.settings, font_size)
        self.save_preferences()

    def on_theme_switch_changed(self, switch, gparam):
        theme_enabled = switch.get_active()
        Preferences.apply_theme(self.settings, theme_enabled)
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
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK if self.theme_switch.get_active() else Adw.ColorScheme.PREFER_LIGHT)
        selected_payload_index = self.payloads_combo.get_active()
        if selected_payload_index != -1:
            model = self.payloads_combo.get_model()
            selected_payload = model[selected_payload_index][0]
            settings.set_string('json-payload', selected_payload)

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

