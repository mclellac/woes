import gi
print(f"Initial gi module: {gi}")
try:
    # Attempt to load GLib first as it's fundamental
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    print(f"GLib module: {GLib}")

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    print(f"Gio module: {Gio}")
    print(f"Gio.Resource type: {type(Gio.Resource)}")
    print(f"Gio.Resource attributes: {dir(Gio.Resource)}")
    if hasattr(Gio.Resource, 'register'):
        print("Gio.Resource has 'register' attribute.")
    else:
        print("Gio.Resource DOES NOT have 'register' attribute.")

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    print(f"Gtk module: {Gtk}")

    gi.require_version("Adw", "1")
    from gi.repository import Adw
    print(f"Adw module: {Adw}")

except Exception as e:
    print(f"Error during gi import or usage: {e}")
