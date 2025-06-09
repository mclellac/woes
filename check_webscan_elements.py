import logging
import os
import sys

# Ensure logging is configured to see debug messages from this script
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s:%(name)s:%(message)s")

# Adjust path to import from src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

# Import Gtk and Adw first
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, Gio
except Exception as e:
    logging.error(f"Error importing gi repository: {e}")
    sys.exit(1)

# Mock Gtk.Template and Gtk.Template.Child before importing WebScanPage
_original_gtk_template = Gtk.Template
_original_gtk_template_child = getattr(Gtk.Template, 'Child', None)

Gtk.Template = lambda resource_path: (lambda cls: cls)  # Dummy decorator
Gtk.Template.Child = lambda: None  # Dummy for Gtk.Template.Child() assignments

_webscan_page_imported_successfully = False
WebScanPage = None
try:
    from src.webscan_page import WebScanPage
    _webscan_page_imported_successfully = True
    logging.info("Successfully imported src.webscan_page.WebScanPage")
except Exception as e:
    logging.error(f"Error importing WebScanPage even with Gtk.Template and Child mocked: {e}")
finally:
    # Restore Gtk.Template and Gtk.Template.Child
    Gtk.Template = _original_gtk_template
    if _original_gtk_template_child is not None and hasattr(Gtk.Template, 'Child'):
         Gtk.Template.Child = _original_gtk_template_child
    elif _original_gtk_template_child is None and hasattr(Gtk.Template, 'Child'):
        if hasattr(Gtk.Template, 'Child'):
             del Gtk.Template.Child

def attempt_programmatic_check():
    if not _webscan_page_imported_successfully or WebScanPage is None:
        logging.error("WebScanPage class not available for programmatic check.")
        return

    app = None
    try:
        # Minimal GTK app setup
        # Use a unique app ID to avoid conflicts if main app was (partially) registered
        app = Gtk.Application(application_id="com.example.test.checkelements")

        # A dummy do_activate is needed for Gtk.Application if not Adw.Application
        def do_activate(app_instance):
            logging.debug(f"Dummy activate for {app_instance.get_application_id()}")
        app.do_activate = do_activate

        app.register(None)  # Try to register the app
        logging.info("Gtk.Application registered for programmatic check.")

        # Instantiate WebScanPage
        # This is where it might fail if Gtk environment is insufficient
        logging.debug("Attempting to instantiate WebScanPage...")
        page = WebScanPage()
        logging.info("WebScanPage instantiated programmatically.")

        # Manually assign mocks for template children as Gtk.Template was dummied out
        # These would normally be instances of GTK widgets. Here, they'll be None if not set.
        # The WebScanPage class has these as class members due to Gtk.Template.Child.
        # After instantiation, they are instance members.
        # We need to check if they are present (not None due to dummy Gtk.Template.Child)
        # and then we can assign mocks if we were to call methods on them.
        # For this check, just see if the attribute exists and is not None.

        # Simulate Gtk.Template.Child() behavior for a test-like scenario:
        # The dummy Gtk.Template.Child returns None.
        # So, these attributes will be None on the instance unless we assign them.
        # Let's check if the class definition itself has them (it should).

        # The goal is to see if the *instance* has these attributes after __init__
        # Since Gtk.Template.Child was dummied to `lambda: None`, these will be `None`
        # on the instance unless the __init__ itself assigns something to them (it doesn't).
        # What Gtk.Template.Child *actually* does is set up descriptors that populate
        # these on the instance when the UI is built. Our dummying prevents this.
        # So, we can't check for "truthiness" of the actual widgets.
        # We can check if the attributes exist on the instance (they will, as None).

        # The debug log "WebScanPage initialized" from its __init__ is the main check here.
        # If that log appears, instantiation happened.

        # Check for attribute presence (will be None due to mocking strategy)
        if hasattr(page, 'url_entry') and hasattr(page, 'toast_overlay_internal'):
            logging.info("WebScanPage instance has 'url_entry' and 'toast_overlay_internal' attributes (expected to be None due to Gtk.Template.Child mocking).")
            # The key check is the "WebScanPage initialized" log from page.__init__
        else:
            logging.warning("WebScanPage instance is missing expected template child attributes.")

    except RuntimeError as e:
        if "Gtk couldn't be initialized" in str(e):
            logging.error(f"Programmatic check failed as expected: GTK couldn't be initialized. Error: {e}")
        else:
            logging.error(f"RuntimeError during programmatic check: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Unexpected error during programmatic check: {e}", exc_info=True)
    finally:
        if app and Gtk.Application.get_default() == app : # Quit only if it's the current default app
            # app.quit() # Quitting can sometimes hang or cause issues in scripts
            logging.debug("Programmatic check finished. Not quitting app explicitly.")

if __name__ == "__main__":
    attempt_programmatic_check()
