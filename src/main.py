# Basic imports
import os
import sys
import logging

# Constants import
from . import constants

# Logger setup
logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)
logger.info("Script execution started (main.py top level).")

# GI and GIO/GLib imports (needed by _perform_resource_loading)
import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GLib

# GResource Loading Function Definition (with all its diagnostics)
def _perform_resource_loading():
    """Loads GResources and logs detailed information."""
    print("DIAGNOSTIC (main.py/_perform_resource_loading): Function called.", file=sys.stderr)
    pkdatadir_from_env = os.environ.get('WOES_RUNTIME_PKGDATADIR')
    print(f"DIAGNOSTIC (main.py/_perform_resource_loading): os.environ.get('WOES_RUNTIME_PKGDATADIR') = {pkdatadir_from_env}", file=sys.stderr)
    datadir_to_use = None
    if pkdatadir_from_env:
        logger.info(f"Using WOES_RUNTIME_PKGDATADIR from environment: {pkdatadir_from_env}")
        datadir_to_use = pkdatadir_from_env
    else:
        logger.warning("WOES_RUNTIME_PKGDATADIR not found in environment. Falling back to constants.DEFAULT_PKGDATADIR_FALLBACK.")
        datadir_to_use = constants.DEFAULT_PKGDATADIR_FALLBACK

    print(f"DIAGNOSTIC (main.py/_perform_resource_loading): datadir_to_use = {datadir_to_use}", file=sys.stderr)
    if not datadir_to_use:
        print("DIAGNOSTIC (main.py/_perform_resource_loading): datadir_to_use is None or empty. Cannot load resources.", file=sys.stderr)
        sys.stderr.flush()
        logger.critical("No valid PKGDATADIR found. Cannot load resources.")
        return False # Indicate failure

    logger.debug(f"Effective PKGDATADIR for resources: {datadir_to_use}")
    resource_file_path = os.path.join(datadir_to_use, "woes.gresource")
    print(f"DIAGNOSTIC (main.py/_perform_resource_loading): Calculated resource_file_path = {resource_file_path}", file=sys.stderr)
    logger.info("Attempting to load GResource file from: %s", resource_file_path)

    if not os.path.exists(resource_file_path):
        print(f"DIAGNOSTIC (main.py/_perform_resource_loading): os.path.exists({resource_file_path}) is FALSE.", file=sys.stderr)
        sys.stderr.flush()
        logger.error("GResource file not found at %s.", resource_file_path)
        return False
    else:
        print(f"DIAGNOSTIC (main.py/_perform_resource_loading): os.path.exists({resource_file_path}) is TRUE.", file=sys.stderr)

    try:
        print(f"DIAGNOSTIC (main.py/_perform_resource_loading): Attempting Gio.Resource.load('{resource_file_path}')", file=sys.stderr)
        sys.stderr.flush()
        resource = Gio.Resource.load(resource_file_path)
        print("DIAGNOSTIC (main.py/_perform_resource_loading): Gio.Resource.load() call completed.", file=sys.stderr)
        if resource:
            print("DIAGNOSTIC (main.py/_perform_resource_loading): Resource loaded successfully. Attempting to register.", file=sys.stderr)
            Gio.Resource._register(resource) # Intended way
            print("DIAGNOSTIC (main.py/_perform_resource_loading): Resource registered successfully.", file=sys.stderr)
            sys.stderr.flush()
            logger.info("Successfully loaded and registered GResource: %s", resource_file_path)
            available_resources = resource.enumerate_children(constants.RESOURCE_PREFIX, Gio.ResourceLookupFlags.NONE)
            logger.debug("Available resources under %s: %s", constants.RESOURCE_PREFIX, available_resources)
            if not available_resources:
                logger.warning("No resources found under %s in %s.", constants.RESOURCE_PREFIX, resource_file_path)
            return True
        else:
            print(f"DIAGNOSTIC (main.py/_perform_resource_loading): Gio.Resource.load() returned None for {resource_file_path}.", file=sys.stderr)
            sys.stderr.flush()
            logger.error("Gio.Resource.load() returned None for %s.", resource_file_path)
            return False
    except GLib.Error as e:
        print(f"DIAGNOSTIC (main.py/_perform_resource_loading): GLib.Error during Gio.Resource.load(): {e}", file=sys.stderr)
        sys.stderr.flush()
        logger.error("Failed to load GResource %s: %s. Bundle invalid?", resource_file_path, e, exc_info=True)
        return False
    except FileNotFoundError: # Should be caught by os.path.exists, but as a safeguard.
        logger.exception("GResource file not found (FileNotFoundError): %s", resource_file_path)
        return False
    except Exception as e: # Generic exception
        print(f"DIAGNOSTIC (main.py/_perform_resource_loading): Unexpected Exception during Gio.Resource.load(): {e}", file=sys.stderr)
        sys.stderr.flush()
        logger.exception("Unexpected error loading GResource %s.", resource_file_path)
        return False

# Module-level call to load resources
if not _perform_resource_loading():
    logger.critical("Module-level GResource loading failed. Application may not function correctly.")
    # Depending on severity, could raise ImportError or allow app to try and fail at UI construction.

# Local module imports that use Gtk.Template or GResources
from .preferences import Preferences
from .window import WoesWindow

# Class definitions (WoesApplication, etc.) and main() function definition.
class WoesApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version=constants.VERSION, **kwargs):
        logger.info("Initializing WoesApplication.")
        self.version = version
        self.debug_enabled = False

        super().__init__(
            application_id=constants.APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            **kwargs
        )

        self.add_main_option(
            "debug",
            ord("d"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Enable debug logging",
            None
        )

        self.win = None

        self.create_action("quit", lambda *_: (logger.debug("Action 'quit' triggered."), self.quit()), ["<primary>q"])
        self.create_action("about", self.on_about_action)
        self.create_action("preferences", self.on_preferences_action)
        self.create_action("switch-to-http", self.switch_to_http, ["<primary>1"])
        self.create_action("switch-to-nmap", self.switch_to_nmap, ["<primary>2"])
        self.create_action("switch-to-dns", self.switch_to_dns, ["<primary>3"])
        self.create_action("switch-to-webscan", self.switch_to_webscan, ["<primary>4"])

    def do_startup(self):
        logger.info("WoesApplication.do_startup called.")
        Adw.Application.do_startup(self)

    def do_handle_local_options(self, options):
        logger.debug("Handling local options: %s", options)
        if options.contains('debug'):
            self.debug_enabled = True
            # Get the root logger and set its level to DEBUG
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled via command line. Logging level set to DEBUG.")
        return -1

    def do_command_line(self, command_line):
        logger.debug("WoesApplication.do_command_line entered, explicitly calling self.activate().")
        self.activate()
        return 0

    def do_activate(self):
        """Called when the application is activated."""
        logger.info("Activating WoesApplication.")
        win = self.props.active_window
        if not win:
            logger.debug("No active window, creating WoesWindow.")
            try:
                win = WoesWindow(application=self)
                logger.debug("WoesWindow created successfully.")
            except Exception as e: # Catching generic Exception
                logger.exception(f"Failed to create WoesWindow: {e}")
                # It's crucial to see this error if it happens.
                print(f"DIAGNOSTIC (main.py/do_activate): Failed to create WoesWindow: {e}", file=sys.stderr)
                sys.stderr.flush()
                # Optionally re-raise or sys.exit depending on desired behavior
                # For now, let it propagate or be handled by GTK's default exception handler
                # To ensure exit for this critical failure:
                sys.exit(1)


        if not win: # Should be redundant if above exception handling exits
            logger.error("Window object is None after creation attempt, cannot proceed.")
            sys.exit(1)

        self.win = win
        self.add_window(self.win)
        logger.debug("Presenting WoesWindow.")
        try:
            self.win.present()
            logger.debug("WoesWindow presented.")
        except GLib.Error as e: # Catching specific GLib.Error
            logger.exception(f"Error presenting WoesWindow: {e}")
            print(f"DIAGNOSTIC (main.py/do_activate): GLib.Error presenting WoesWindow: {e}", file=sys.stderr)
            sys.stderr.flush()
            sys.exit(1) # Exit on presentation error
        except Exception as e: # Catching other exceptions during present
            logger.exception(f"Unexpected error during WoesWindow.present(): {e}")
            print(f"DIAGNOSTIC (main.py/do_activate): Unexpected error presenting WoesWindow: {e}", file=sys.stderr)
            sys.stderr.flush()
            sys.exit(1) # Exit on presentation error


    def switch_to_http(self, *_args):
        logger.debug("Action 'switch-to-http' triggered.")
        if self.win and self.win.stack:
            self.win.stack.set_visible_child_name("http")
        else:
            logger.warning("Cannot switch to http_page: window or stack not available.")

    def switch_to_nmap(self, *_args):
        logger.debug("Action 'switch-to-nmap' triggered.")
        if self.win and self.win.stack:
            self.win.stack.set_visible_child_name("nmap")
        else:
            logger.warning("Cannot switch to nmap_page: window or stack not available.")

    def switch_to_dns(self, *_args):
        logger.debug("Action 'switch-to-dns' triggered.")
        if self.win and self.win.stack:
            self.win.stack.set_visible_child_name("dns")
        else:
            logger.warning("Cannot switch to dns_page: window or stack not available.")

    def switch_to_webscan(self, *_args):
        logger.debug("Action 'switch-to-webscan' triggered.")
        if self.win and self.win.stack:
            self.win.stack.set_visible_child_name("webscan")
        else:
            logger.warning("Cannot switch to webscan_page: window or stack not available.")

    def on_about_action(self, _widget, _):
        """Callback for the app.about action."""
        logger.debug("About action triggered.")
        about = Adw.AboutWindow(
            transient_for=self.props.active_window,
            application_name="woes",
            application_icon=constants.APP_ID,
            developer_name="Carey McLelland",
            version=self.version,
            developers=["Carey McLelland"],
            copyright="© 2024 Carey McLelland",
        )
        about.present()

    def on_preferences_action(self, _widget, _):
        logger.debug("Preferences action triggered.")
        if not self.win:
            logger.error("Main window not available for preferences dialog.")
            return
        preferences_dialog = Preferences(main_window=self.win)
        preferences_dialog.present()

    def create_action(self, name, callback, shortcuts=None):
        """Utility method to create and add a Gio.SimpleAction."""
        logger.debug("Creating action: app.%s", name)
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

def main(version=constants.VERSION):
    """The application's entry point."""
    logger.info("Starting Woes application main function.")
    app = WoesApplication(version=version)
    logger.info("WoesApplication instance created.")
    logger.info("Running WoesApplication.")
    exit_status = app.run(sys.argv)
    logger.info("WoesApplication finished with exit status: %s", exit_status)
    return exit_status

if __name__ == '__main__':
    sys.exit(main())
