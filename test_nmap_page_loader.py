import gi
gi.require_version('Gtk', '4.0')  # Gtk might still be needed by Adw or other implicit parts
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib  # Removed Gtk as it's directly unused
import os  # For manipulating paths if needed
import sys

# This is important: Gtk.Application needs to be able to find the .gresource file.
# The .gresource file is compiled at src/woes.gresources
# We need to tell Gio how to find this file.
# One way is to load it manually before the application runs.
# The resource paths inside are absolute (e.g., /com/github/mclellac/woes/gtk/nmap_page.ui)

try:
    # Construct the absolute path to the .gresource file
    # Assuming this script is run from the repository root /app/
    current_dir = os.getcwd()
    gresource_path = os.path.join(current_dir, "src", "woes.gresources")

    if not os.path.exists(gresource_path):
        print(f"Error: Compiled GResource file not found at {gresource_path}")
        print("Please ensure 'glib-compile-resources --target=src/woes.gresources "
              "src/woes.gresource.xml --sourcedir=src/' has been run.")
        exit(1)

    resource = Gio.Resource.load(gresource_path)
    Gio.resources_register(resource)
    print(f"Successfully loaded and registered {gresource_path}")

except GLib.Error as e:
    print(f"GLib.Error loading or registering GResource: {e}")
    # This can happen if the file is corrupt or not a valid gresource bundle
    exit(1)
except Exception as e:
    print(f"Unexpected error loading or registering GResource: {e}")
    exit(1)


# Mock necessary Gtk Application and Window if needed for instantiation
class MockApplication(Adw.Application):  # Changed to Adw.Application as Adw.PreferencesPage might need it
    def __init__(self, **kwargs):
        # We need to ensure the application_id matches what's used by Gio.Settings (APP_ID from constants)
        # or that settings are not strictly required for basic instantiation.
        # From nmap_page.py: self.settings = Gio.Settings.new(APP_ID)
        # APP_ID = "com.github.mclellac.woes"
        super().__init__(application_id="com.github.mclellac.woes", **kwargs)
        # self.props.resource_base_path is less relevant when manually loading gresource like above
        # but doesn't hurt.
        # self.props.resource_base_path = "/app/src" # Path to directory containing gresource file

    def do_activate(self):
        # _win = Gtk.Window(application=self) # Adw.ApplicationWindow might be better
        _win = Adw.ApplicationWindow(application=self)

        print("MockApplication activated. Attempting to import and instantiate NmapPage...")
        try:
            from src.nmap_page import NmapPage
            print("NmapPage class imported successfully.")

            # NmapPage.__init__ takes **kwargs, and its super Adw.PreferencesPage also takes them.
            # It also initializes self.settings = Gio.Settings.new(APP_ID)
            # This requires the GSchema to be compiled and present for that APP_ID.
            # For a simple UI parsing test, this might be too much.
            # The Gtk.Template parsing happens when the class is defined, so import success is key.

            # To avoid issues with Gio.Settings requiring compiled schemas for this test,
            # we can try to bypass the part of __init__ that uses it, if possible,
            # or ensure schema is available. For now, let's see if it blows up.
            _nmap_page_instance = NmapPage()
            print("NmapPage instantiated successfully.")
            # _win.set_child(_nmap_page_instance) # Optional: realize the widget

        except Exception as e:
            print(f"Error during NmapPage class import or instantiation: {e}")
            sys.exit(1)  # Use sys.exit for cleaner exit status handling

        # If we reach here, it's good. We can exit the app.
        self.quit()


print("Starting direct instantiation test via MockApplication...")
app = MockApplication()
exit_status = app.run([])  # sys.exit(app.run(sys.argv)) is typical, but [] is fine for no args.
sys.exit(exit_status)

# The original script's direct test without app.run() is less robust for Gtk.Template
# because Gtk.Application handles resource initialization context.
# The class definition (import src.nmap_page) is where @Gtk.Template does its magic.
# If that line passes, the .ui file was parsed correctly from the gresources.
# Instantiation then tests the __init__ logic.
# If GSettings are an issue, the instantiation will fail.
# If the .ui file has issues with object definitions not matching Template.Child, it will fail instantiation.

# Final check as per original script's simplified idea (but less reliable for templates):
# print("Attempting simplified class import test (less reliable for Gtk.Template issues)...")
# try:
# Gtk.init() # Ensure GTK is initialized
# from src.nmap_page import NmapPage
# print("NmapPage class imported successfully (simplified test).")
# except Exception as e:
# print(f"Error during NmapPage class definition (simplified test implies UI parsing error): {e}")
# sys.exit(1)
# print("UI file seems okay based on simplified class import (simplified test).")
# sys.exit(0)
