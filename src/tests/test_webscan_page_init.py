# GI related imports MUST come first to avoid circular import issues with _gi
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, GObject

# Now other standard imports
import unittest
import sys
import os
import logging

# Configure basic logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add src to sys.path to import application modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Try to import application modules
# These are now attempted after initial gi setup
try:
    from main import WoesApplication, _load_gresources_early
    from window import WoesWindow
    from webscan_page import WebScanPage
    from constants import PKGDATADIR, APP_ID # Needed for _load_gresources_early and app
    logger.info("Successfully imported application modules.")
except ImportError as e:
    logger.error(f"Failed to import application modules: {e}", exc_info=True)
    sys.exit(1)

# It's important that GResources are loaded before WoesWindow is created,
# as it uses Gtk.Template with a resource path.
# Also, custom widgets like WebScanPage need to be known to the type system,
# which happens when their modules (e.g., webscan_page.py) are imported.
_load_gresources_early()


class TestWebScanPageInit(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        logger.info("Setting up TestWebScanPageInit class.")
        # Set an environment variable to indicate testing if necessary for the app
        os.environ['WOES_TESTING'] = '1'

        # Create and register the application
        # Using a unique ID to avoid conflicts if the main app is somehow registered
        cls.app = WoesApplication(application_id=f"{APP_ID}.TestWebScanPageInit")
        cls.app.set_default() # Register the application with GLib

    @classmethod
    def tearDownClass(cls):
        logger.info("Tearing down TestWebScanPageInit class.")
        if hasattr(cls, 'app') and cls.app:
            # Unregister the application if possible, though GLib doesn't have a direct unregister
            # Setting default to None might help, or just let it be garbage collected
            # Gio.Application.set_default(None) # This might be problematic
            del cls.app
        del os.environ['WOES_TESTING']

    def setUp(self):
        logger.info("TestWebScanPageInit.setUp: Activating application and getting window.")
        # Activate the application. This should create the window.
        # do_activate is called internally by activate() if no windows are open.
        self.app.activate()

        # Process pending events to ensure window creation and presentation
        self.process_events()

        self.window = self.app.get_active_window()
        self.assertIsInstance(self.window, WoesWindow, "Active window is not WoesWindow.")
        logger.info(f"TestWebScanPageInit.setUp: Window obtained: {self.window}")

    def tearDown(self):
        logger.info("TestWebScanPageInit.tearDown: Closing window if it exists.")
        if self.window:
            try:
                # Attempt to close the window; hide might be safer if close is problematic in tests
                self.window.close() # or self.window.destroy() or self.window.hide()
                self.process_events() # Allow window close events to process
            except Exception as e:
                logger.error(f"Error closing window in tearDown: {e}", exc_info=True)
        # self.app.quit() # This might terminate the test runner if not careful

    def process_events(self):
        """Process all pending GTK events."""
        logger.debug("Processing GTK events.")
        while Gtk.events_pending():
            Gtk.main_iteration()
        logger.debug("Finished processing GTK events.")

    def test_webscan_page_activation_and_content(self):
        logger.info("Starting test_webscan_page_activation_and_content.")
        self.assertIsNotNone(self.window, "Window should not be None.")

        logger.debug("Calling app.switch_to_webscan().")
        # The action takes GLib.Variant as parameter, but for simple actions, it's often None.
        # Let's assume None or no args works. If not, we'll need to pass a dummy GVariant.
        # The action definition is: self.create_action("switch-to-webscan", self.switch_to_webscan, ["<primary>4"])
        # The callback is self.switch_to_webscan which takes (*_args)
        self.app.get_action("switch-to-webscan").activate(None)
        self.process_events() # Allow stack switching to complete

        self.assertIsNotNone(self.window.stack, "Window stack should not be None.")

        visible_child_name = self.window.stack.get_visible_child_name()
        logger.debug(f"Visible child name: {visible_child_name}")
        self.assertEqual(visible_child_name, "webscan_page", "Visible child is not webscan_page.")

        # Traverse to find WebScanPage instance
        # Hierarchy: AdwViewStack -> AdwViewStackPage (webscan_page) -> AdwStatusPage -> GtkBox (webscan_box) -> WebScanPage

        current_page_widget = self.window.stack.get_visible_child() # This is AdwViewStackPage
        self.assertIsNotNone(current_page_widget, "Visible child widget (AdwViewStackPage) is None.")
        logger.debug(f"Current page widget (AdwViewStackPage): {current_page_widget}")

        # AdwViewStackPage's child is AdwStatusPage
        status_page = current_page_widget.get_child()
        self.assertIsInstance(status_page, Adw.StatusPage, "Child of AdwViewStackPage is not AdwStatusPage.")
        logger.debug(f"Status page widget (AdwStatusPage): {status_page}")

        # AdwStatusPage's child is GtkBox (webscan_box)
        # Note: AdwStatusPage can have a 'child' property if set via Gtk.Builder directly.
        # Or, if it's using set_child(), it's accessible via get_child().
        # From window.ui, webscan_box is a child of webscan_status_page.
        webscan_box = status_page.get_child()
        self.assertIsInstance(webscan_box, Gtk.Box, "Child of AdwStatusPage is not GtkBox (webscan_box).")
        logger.debug(f"Webscan box widget (GtkBox): {webscan_box}")

        # GtkBox (webscan_box) should contain WebScanPage
        # A Gtk.Box can have multiple children. We expect one, the WebScanPage.
        # Gtk.Box.get_first_child() and then iterate if necessary, or if only one, it's the one.
        actual_webscan_page_widget = None
        child = webscan_box.get_first_child()
        while child is not None:
            if isinstance(child, WebScanPage):
                actual_webscan_page_widget = child
                break
            child = child.get_next_sibling()

        self.assertIsNotNone(actual_webscan_page_widget, "WebScanPage instance not found in webscan_box.")
        self.assertIsInstance(actual_webscan_page_widget, WebScanPage, "Found widget is not of type WebScanPage.")
        logger.info("Successfully verified WebScanPage instance is present and visible.")

if __name__ == '__main__':
    logger.info("Running test_webscan_page_init.py as main script.")
    # Set GSETTINGS_SCHEMA_DIR, similar to test_nmap_page_init.py, in case any components use Gio.Settings
    # This is good practice even if not immediately obvious it's needed.
    schema_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
    if os.path.exists(os.path.join(schema_dir, f"{APP_ID}.gschema.xml")) or \
       os.path.exists(os.path.join(schema_dir, f"{APP_ID}.testing.gschema.xml")): # Check if schema files exist
        os.environ['GSETTINGS_SCHEMA_DIR'] = schema_dir
        logger.info(f"Set GSETTINGS_SCHEMA_DIR to: {schema_dir}")
    else:
        logger.warning(f"GSETTINGS_SCHEMA_DIR not set: Schema directory or files not found at {schema_dir}")

    unittest.main()
