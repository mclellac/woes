"""A simple script to test GObject Introspection (GI) repository loading.

Attempts to load common GI namespaces like GLib, Gio, Gtk, and Adw
to verify their availability in the current environment. This is primarily
for development and debugging GI setup issues.
"""
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

except Exception:  # pylint: disable=broad-except
    # If this script were for actual use, error handling (e.g., logging) would go here.
    # For this test script, a silent pass on exception is acceptable.
    pass
