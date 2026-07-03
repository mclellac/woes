import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Gio # Added GLib, Gio
import os

# Register GResource bundle early so templates can be resolved
script_dir = os.path.dirname(os.path.abspath(__file__))
gresource_path = os.path.join(script_dir, "build", "src", "woes.gresource")
if os.path.exists(gresource_path):
    resource = Gio.Resource.load(gresource_path)
    Gio.Resource._register(resource)

try:
    # Ensure src directory is in path if running from root, or adjust import path
    # This assumes the script is run from the repository root.
    from src.window import WoesWindow
    print("Successfully imported WoesWindow.")
    # Attempt to instantiate it to trigger template parsing more directly
    # app = Adw.Application(application_id="com.github.mclellac.woes.test")
    # window = WoesWindow(application=app)
    # print("Successfully instantiated WoesWindow (simulated).")
    # Note: Full instantiation might require more setup (like a running Gtk.Application).
    # For now, just the import and class definition being processed by Python + GObject Introspection
    # should be enough to catch the Gtk.BuilderError if types are not registered.

except GLib.Error as e: # Changed to GLib.Error
    print(f"A GLib.Error occurred (possibly Gtk.BuilderError related): {e}")
except ImportError as e:
    print(f"ImportError occurred: {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
