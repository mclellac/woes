import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

class Preferences(Adw.PreferencesWindow):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.set_modal(True)
        self.set_title("Preferences")

        self.page = Adw.PreferencesPage()
        self.add(self.page)

        self.create_font_size_group()
        self.create_theme_group()
        self.create_payloads_group()

        self.load_preferences()

    def create_font_size_group(self):
        self.font_size_group = Adw.PreferencesGroup()
        self.page.add(self.font_size_group)

        self.font_size_row = Adw.ActionRow(title="Font Size")
        self.font_size_adjustment = Gtk.Adjustment(
            value=12, lower=8, upper=32, step_increment=1
        )
        self.font_size_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.font_size_adjustment
        )
        self.font_size_scale.set_digits(0)
        self.font_size_scale.set_hexpand(True)
        self.font_size_scale.connect("value-changed", self.on_font_size_changed)
        self.font_size_row.add_suffix(self.font_size_scale)

        self.font_size_group.add(self.font_size_row)

    def create_theme_group(self):
        self.theme_group = Adw.PreferencesGroup()
        self.page.add(self.theme_group)

        self.theme_row = Adw.ActionRow()
        self.theme_switch = Gtk.Switch()
        self.theme_switch.set_active(True)
        self.theme_switch.set_valign(Gtk.Align.CENTER)
        self.theme_switch.set_halign(Gtk.Align.END)
        self.theme_switch.connect("state-set", self.on_theme_switch_changed)

        self.theme_label = Gtk.Label(label="Dark Theme")
        self.theme_label.set_halign(Gtk.Align.START)

        self.theme_row.add_prefix(self.theme_label)
        self.theme_row.add_suffix(self.theme_switch)

        self.theme_group.add(self.theme_row)

    def create_payloads_group(self):
        self.payloads_group = Adw.PreferencesGroup()
        self.page.add(self.payloads_group)

        self.payloads_row = Adw.ActionRow(title="JSON Payloads")

        self.payloads_list = Gtk.ListStore(str)
        self.payloads_list.append(['SinglePage'])
        self.payloads_list.append(['Live Radio'])

        self.payloads_combo = Gtk.ComboBox()
        self.payloads_combo.set_model(self.payloads_list)
        self.payloads_combo.set_entry_text_column(0)

        self.payloads_row.add_prefix(Gtk.Label(label="Payload:"))
        self.payloads_row.add_suffix(self.payloads_combo)

        self.payloads_group.add(self.payloads_row)

    def on_font_size_changed(self, scale):
        font_size = scale.get_value()
        if self.main_window is not None:
            self.main_window.apply_font_size(font_size)
        self.save_preferences()

    def on_theme_switch_changed(self, switch, gparam):
        theme_enabled = switch.get_active()
        self.main_window.apply_theme(theme_enabled)
        self.save_preferences()

    def save_preferences(self):
        settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')
        settings.set_int('font-size', self.font_size_adjustment.get_value())
        settings.set_boolean('dark-theme', self.theme_switch.get_active())

        active_iter = self.payloads_combo.get_active_iter()
        if active_iter is not None:
            selected_payload = self.payloads_list[active_iter][0]
            settings.set_string('json-payload', selected_payload)

    def load_preferences(self):
        settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')
        font_size = settings.get_int('font-size')
        self.font_size_adjustment.set_value(font_size)
        dark_theme_enabled = settings.get_boolean('dark-theme')
        self.theme_switch.set_active(dark_theme_enabled)

        selected_payload = settings.get_string('json-payload')
        if selected_payload:
            for i, row in enumerate(self.payloads_list):
                if row[0] == selected_payload:
                    self.payloads_combo.set_active(i)
                    break

