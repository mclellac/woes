import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gdk, Gio
from . import http_util  # Import http_util from the same folder

@Gtk.Template(resource_path='/com/github/mclellac/WebOpsEvaluationSuite/gtk/window.ui')
class WoesWindow(Adw.ApplicationWindow):
  __gtype_name__ = 'WoesWindow'

  label = Gtk.Template.Child()
  http_entry = Gtk.Template.Child()  # Assume this is an entry widget for URL
  pragma_switch = Gtk.Template.Child()  # Assume this is a switch for Akamai Pragma
  json_payloads_drop_down = Gtk.Template.Child()  # Optional: dropdown for JSON payloads
  column_view = Gtk.Template.Child()  # Assume this is a column view to display headers

  def __init__(self, **kwargs):
    super().__init__(**kwargs)

    # Existing code for applying font size and theme preferences
    self.style_manager = Adw.StyleManager.get_default()

    self.settings = Gio.Settings(schema_id='com.github.mclellac.WebOpsEvaluationSuite')

    font_size = self.settings.get_int('font-size')
    self.apply_font_size(font_size)

    dark_theme_enabled = self.settings.get_boolean('dark-theme')
    self.apply_theme(dark_theme_enabled)

    # Connect callbacks for UI elements
    self.http_entry.connect("activate", self.on_fetch_headers)
    self.pragma_switch.connect("toggled", self.on_use_akamai_pragma_toggled)

  def apply_font_size(self, font_size):
    """Apply the selected font size to UI elements using CSS."""
    # ... (Existing code for applying font size)

  def apply_theme(self, dark_theme_enabled):
    """Apply the selected theme (light or dark) to the UI."""
    # ... (Existing code for applying theme)

  def on_fetch_headers(self, widget):
    """Callback for when the URL entry is activated (e.g., Enter is pressed)."""
    url = self.http_entry.get_text().strip()
    use_akamai_pragma = self.pragma_switch.get_active()
    self.headers = http_util.HttpUtil.fetch_headers(url, use_akamai_pragma)

    if self.headers:
      self.update_column_view(self.headers)
      self.get_title_bar().set_subtitle(f"Fetched headers for: {url}")
    else:
      self.update_column_view(None)
      self.get_title_bar().set_subtitle(f"Failed to fetch headers for: {url}")

  def on_use_akamai_pragma_toggled(self, widget):
    """Callback for when the Akamai Pragma switch is toggled."""
    # Re-fetch headers if the URL entry already has a value
    if self.http_entry.get_text().strip():
      self.on_fetch_headers(None)

  def update_column_view(self, headers):
    """Updates the column view with the fetched headers."""
    self.column_view.clear()
    if headers:
      for key, value in headers.items():
        text_row = Gtk.TextRow(key, value)
        self.column_view.append(text_row)

