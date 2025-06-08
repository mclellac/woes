import gi

try:
    # Attempt to load GLib first as it's fundamental
    gi.require_version("GLib", "2.0")
    # from gi.repository import GLib # No longer used

    gi.require_version("Gio", "2.0")
    # from gi.repository import Gio # No longer used

    gi.require_version("Gtk", "4.0")
    # from gi.repository import Gtk # No longer used

    gi.require_version("Adw", "1")
    # from gi.repository import Adw # No longer used

except Exception:  # pylint: disable=broad-exception-caught
    # If this script were for actual use, error handling (e.g., logging) would go here.
    pass
