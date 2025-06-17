"""Test basic import of the main application window."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib # Added GLib

try:
    # Ensure src directory is in path if running from root, or adjust import path
    # This assumes the script is run from the repository root.
    from src.window import WoesWindow
    print("Successfully imported WoesWindow.")
    # Attempt to instantiate it to trigger template parsing more directly
    # app = Gtk.Application(application_id='com.example.WoesTest')
    # window = WoesWindow(application=app)
    # print("WoesWindow instantiated (simulated).")
    # GLib.idle_add(app.quit) # Ensure app quits for headless test
    # app.run(None)


except ImportError as e:
    print(f"Failed to import WoesWindow: {e}")
    print("Ensure that the script is run from the repository root,")
    print("and that the necessary Gtk/Adw libraries are available.")
    exit(1)
except Exception as e:
    print(f"An error occurred during WoesWindow import or instantiation test: {e}")
    exit(1)

print("Test script completed.")
