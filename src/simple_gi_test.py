import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gio, Gtk, Adw

print(f"Initial gi module: {gi}")
try:
    print(f"GLib module: {GLib}")
    print(f"Gio module: {Gio}")
    print(f"Gio.Resource type = {type(Gio.Resource)}")
    print(f"Gio.Resource attributes = {dir(Gio.Resource)}")
    if hasattr(Gio.Resource, 'register'):
        print("Gio.Resource has 'register' attribute.")
    else:
        print("Gio.Resource DOES NOT have 'register' attribute.")

    print(f"Gtk module: {Gtk}")
    print(f"Adw module: {Adw}")

except Exception as e:
    print(f"Error during gi import or usage: {e}")
