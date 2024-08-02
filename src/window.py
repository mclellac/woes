import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gdk, Gio

@Gtk.Template(resource_path='/ca/github/mclellac/WebOpsEvaluationSuite/gtk/window.ui')
class WoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'WoesWindow'

    label = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.style_manager = Adw.StyleManager.get_default()

        # Load preferences
        self.settings = Gio.Settings(schema_id='ca.github.mclellac.WebOpsEvaluationSuite')

        # Get font size preference
        font_size = self.settings.get_int('font-size')
        self.apply_font_size(font_size)

        # Get theme preference
        dark_theme_enabled = self.settings.get_boolean('dark-theme')
        self.apply_theme(dark_theme_enabled)

    def apply_font_size(self, font_size):
        """Apply the selected font size to UI elements using CSS."""
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

    def apply_theme(self, dark_theme_enabled):
        if dark_theme_enabled:
            self.style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        else:
            self.style_manager.set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)
