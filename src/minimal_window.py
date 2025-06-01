import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gtk

class MinimalWoesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "MinimalWoesWindow"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(600, 400)
        self.set_title("Minimal Woes Test Window") # Added a title
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_content(content)
        label = Gtk.Label(label="Minimal Window Test - If you see this, basic Adw.ApplicationWindow works.")
        content.append(label)
        # No GSettings, no complex UI loading, no custom child widgets from templates.
