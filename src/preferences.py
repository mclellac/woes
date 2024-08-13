import gi
from .constants import RESOURCE_PREFIX, APP_ID

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, Gdk

@Gtk.Template(resource_path=f'{RESOURCE_PREFIX}/preferences.ui')
class Preferences(Adw.PreferencesWindow):
    __gtype_name__ = 'Preferences'

    font_size_scale = Gtk.Template.Child('font_size_scale')
    theme_switch = Gtk.Template.Child('theme_switch')

    def __init__(self, main_window=None):
        super().__init__(modal=True)
        self.main_window = main_window
        self.set_transient_for(main_window)
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.load_ui()
        self.load_preferences()

    def load_ui(self):
        # Ensure template children are properly initialized
        if not all([self.font_size_scale, self.theme_switch]):
            raise RuntimeError("One or more template children are not loaded")

        # Connect signals
        self.font_size_scale.connect('value-changed', self.on_font_size_changed)
        self.theme_switch.connect('state-set', self.on_theme_switch_changed)

    def apply_font_size(self, font_size: int):
        """Apply the selected font size to the application."""
        css_provider = Gtk.CssProvider()
        css = f"* {{ font-size: {font_size}pt; }}"
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def apply_theme(self, dark_theme_enabled: bool):
        """Apply the selected theme based on user preference."""
        if dark_theme_enabled:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        else:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)

    def on_font_size_changed(self, scale):
        """Handle changes to the font size slider."""
        font_size = scale.get_value()
        self.apply_font_size(font_size)
        self.save_preferences()

    def on_theme_switch_changed(self, switch, gparam):
        """Handle toggling the dark theme switch."""
        theme_enabled = switch.get_active()
        self.apply_theme(theme_enabled)
        self.save_preferences()

    def save_preferences(self):
        """Save the current preferences to Gio.Settings."""
        self.settings.set_int('font-size', int(self.font_size_scale.get_value()))
        dark_theme = self.theme_switch.get_active()
        self.settings.set_boolean('dark-theme', dark_theme)

    def load_preferences(self):
        """Load preferences from Gio.Settings and apply them to the UI."""
        font_size = self.settings.get_int('font-size')
        self.font_size_scale.set_value(font_size)

        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        self.theme_switch.set_active(dark_theme_enabled)
        self.apply_theme(dark_theme_enabled)

