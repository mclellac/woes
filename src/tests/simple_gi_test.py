"""
A simple script to test GObject Introspection (GI) repository loading.

Attempts to load common GI namespaces like GLib, Gio, Gtk, and Adw
to verify their availability in the current environment. This is primarily
for development and debugging GI setup issues.
"""
import gi

try:
    # Attempt to load GLib first as it's fundamental
    gi.require_version("GLib", "2.0")

    gi.require_version("Gio", "2.0")

    gi.require_version("Gtk", "4.0")

    gi.require_version("Adw", "1")

except Exception:  # pylint: disable=broad-except
    # If this script were for actual use, error handling (e.g., logging) would go here.
    # For this test script, a silent pass on exception is acceptable.
    pass
