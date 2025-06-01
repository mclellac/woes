import gi
# Attempt to ensure Gtk is imported first, which can resolve some _gi import issues
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5') # Added as it's in nmap_page.py
from gi.repository import Adw, Gio, GLib # Added Gio, GLib
import sys
import os
import logging

# Configure basic logging to see messages from the modules
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Assuming the script is in src/tests/, we need to add src/ to sys.path
# to import modules from src like 'nmap_page'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Attempt to import constants first
try:
    from constants import APP_ID, RESOURCE_PREFIX
    logger.info(f"Successfully imported APP_ID: {APP_ID} and RESOURCE_PREFIX: {RESOURCE_PREFIX}")
except ImportError as e:
    logger.error(f"Failed to import from constants: {e}. Attempting to set fallback values.")
    # These fallbacks are based on values seen in the project.
    APP_ID = "com.github.mclellac.woes"
    RESOURCE_PREFIX = "/com/github/mclellac/woes/gtk"
    # This is a critical dependency. If constants.py is complex, this might not be enough.

# Mock Gio.Settings
class MockSettings:
    def __init__(self, schema_id):
        logger.debug(f"MockSettings: Initializing with schema_id: {schema_id}")
        self.schema_id = schema_id
        self._settings = {
            "source-style-scheme": "default-style" # Provide a default
        }
        if schema_id != APP_ID:
            logger.warning(f"MockSettings: schema_id '{schema_id}' does not match expected APP_ID '{APP_ID}'")

    def get_string(self, key):
        val = self._settings.get(key, "")
        logger.debug(f"MockSettings: get_string('{key}') called, returning '{val}'.")
        return val

    def connect(self, signal, callback):
        logger.debug(f"MockSettings: connect for signal '{signal}' called with callback {callback}.")
        pass # No-op

    # Add other getters if NmapPage initialization requires them
    def get_boolean(self, key, default=False):
        val = self._settings.get(key, default)
        logger.debug(f"MockSettings: get_boolean('{key}') returning {val}")
        return val

    def get_int(self, key, default=0):
        val = self._settings.get(key, default)
        logger.debug(f"MockSettings: get_int('{key}') returning {val}")
        return val

# Replace Gio.Settings with our mock *before* NmapPage is imported
Gio.Settings = MockSettings

# Now try to import NmapPage
try:
    from nmap_page import NmapPage
    logger.info("Successfully imported NmapPage.")
except Exception as e:
    logger.error(f"Failed to import NmapPage: {e}", exc_info=True)
    sys.exit(1)

class TestApplication(Adw.Application):
    def __init__(self, **kwargs):
        logger.info("TestApplication: Initializing.")
        # Use the imported or fallback APP_ID
        super().__init__(application_id=APP_ID, **kwargs)
        self.window = None
        self.nmap_page_instance = None # Store instance for checks

    def do_activate(self):
        logger.info("TestApplication: do_activate called.")
        # Ensure GResources are loaded. This is tricky.
        # For this test, we rely on Gtk.Template finding the .ui file.
        # The fact that the original error was "Child not found" rather than "Template not found"
        # suggests the .ui file *is* being accessed by Gtk.Template.

        if not self.window:
            logger.info("TestApplication: Creating dummy window and attempting NmapPage instantiation.")
            # NmapPage is an Adw.PreferencesPage. It needs to be in a context.
            # We don't need a full window for this test, just the instantiation.
            try:
                logger.info("Attempting to instantiate NmapPage...")
                self.nmap_page_instance = NmapPage()
                logger.info("Successfully instantiated NmapPage.")

                # Check if error_banner exists and is not None
                if hasattr(self.nmap_page_instance, 'error_banner') and self.nmap_page_instance.error_banner is not None:
                    logger.info("NmapPage.error_banner found and is not None.")
                else:
                    logger.error("NmapPage.error_banner is None or not found! This is an issue.")
                    # For the test to "pass" on the critical error, this should not be None
                    # For now, we'll just log and not exit with error, to see other checks.

                # Check if nmap_target_listbox exists and is not None
                if hasattr(self.nmap_page_instance, 'nmap_target_listbox') and self.nmap_page_instance.nmap_target_listbox is not None:
                    logger.info("NmapPage.nmap_target_listbox found and is not None.")
                else:
                    logger.error("NmapPage.nmap_target_listbox is None or not found! This is an issue.")

                # Check the YAML source view warning
                # This requires inspecting logs or mocking logger, which is complex here.
                # We'll rely on visual inspection of logs from the test run.

            except Exception as e:
                logger.error(f"Error during NmapPage instantiation or check: {e}", exc_info=True)
                self.quit() # Ensure application quits on error
                sys.exit(1) # Exit with error if instantiation fails

            logger.info("Test completed. Quitting application.")
            self.quit() # Quit after successful test run

if __name__ == "__main__":
    logger.info("Starting test_nmap_page_init.py script.")
    # The GResource path is defined in constants.py as RESOURCE_PREFIX.
    # Gtk.Template uses this. For Gtk.Template to find resources,
    # the .gresource file needs to be compiled and loaded.
    # This is usually done by the build system (meson) and when the app starts.
    # A simple `python src/tests/test_nmap_page_init.py` might not load them.
    # However, the error we fixed was an AttributeError after template loading was attempted.
    # If the GResource is not loaded, Gtk.Template will fail to find nmap_page.ui,
    # leading to nmap_target_listbox being None.

    # We need to ensure that the application can find its gresources.
    # One way is to run the test in an environment where gresources are already loaded,
    # or to load them explicitly.
    # The `src/woes.gresource.xml` needs to be compiled to `woes.gresource`.
    # And then loaded using Gio.Resource.load() and Gio.Resource.register().
    # This is often done in main.py or window.py.

    # Let's check if `main.py` loads resources and try to mimic it if simple enough
    # Or, assume the execution environment for the subtask might handle it.
    # For now, the script will run and we'll see if Gtk.Template finds the UI file.

    # Ensure GSETTINGS_SCHEMA_DIR is set for Gio.Settings mock, though our mock doesn't use it.
    # In a real scenario, it would be important.
    schema_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
    os.environ['GSETTINGS_SCHEMA_DIR'] = schema_dir
    logger.info(f"Set GSETTINGS_SCHEMA_DIR to: {schema_dir}")

    app = TestApplication()
    exit_status = app.run(sys.argv)
    logger.info(f"TestApplication finished with exit_status: {exit_status}")

    # Perform checks on the instance after app has run (and quit)
    # This is a bit unconventional as app.run() blocks.
    # The checks are better inside do_activate or connected signals.
    # For this script, the logs during do_activate are the primary output.
    if app.nmap_page_instance:
        if not (hasattr(app.nmap_page_instance, 'error_banner') and app.nmap_page_instance.error_banner is not None):
            logger.error("Final check: NmapPage.error_banner is None or not found after app run.")
            sys.exit(1) # Indicate failure
        if not (hasattr(app.nmap_page_instance, 'nmap_target_listbox') and app.nmap_page_instance.nmap_target_listbox is not None):
            logger.error("Final check: NmapPage.nmap_target_listbox is None or not found after app run.")
            sys.exit(1) # Indicate failure
        logger.info("Final checks passed: error_banner and nmap_target_listbox are present.")
    else:
        logger.error("Final check: nmap_page_instance was not created.")
        sys.exit(1)

    sys.exit(0) # Explicitly exit with 0 if all checks pass (or seemed to pass via logs)
