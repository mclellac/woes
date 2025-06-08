import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
import sys


class MinimalApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.example.minimal")

    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self, title="Minimal Test")
        label = Gtk.Label(label="Hello, GTK4!")
        window.set_child(label)
        window.present()
        print("Minimal GTK app window should be conceptually present.")
        # In a non-headless environment, this would show a window.
        # For this test, if it runs without error up to here, it's a success.
        self.quit()  # Exit the app


print("Running minimal GTK4 app test...")
app = MinimalApp()
exit_status = app.run([])
sys.exit(exit_status)
