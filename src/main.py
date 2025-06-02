import logging
import os
import sys

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GLib

# Updated to reflect changes in constants.py
from . import constants
# Ensure specific constants used directly are still accessible if needed,
# or adjust to use constants.VARIABLE syntax throughout.
# For this change, we primarily need constants.DEFAULT_PKGDATADIR_FALLBACK
# and constants.VERSION, constants.APP_ID, constants.RESOURCE_PREFIX
from .preferences import Preferences
from .window import WoesWindow

# Basic logging configuration can be early.
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)

logger.info("Script execution started.")

# ACTUAL_PKGDATADIR_FOR_RESOURCES removed
# _configure_resource_path removed

def _perform_resource_loading():
    """Loads GResources and logs detailed information."""
    # logger must be defined before this function if used within it.
    # Ensure logger is accessible (e.g., global logger = logging.getLogger(__name__))
    pkdatadir_from_env = os.environ.get('WOES_RUNTIME_PKGDATADIR')
    datadir_to_use = None
    if pkdatadir_from_env:
        logger.info(f"Using WOES_RUNTIME_PKGDATADIR from environment: {pkdatadir_from_env}")
        datadir_to_use = pkdatadir_from_env
    else:
        logger.warning("WOES_RUNTIME_PKGDATADIR not found in environment. Falling back to constants.DEFAULT_PKGDATADIR_FALLBACK.")
        datadir_to_use = constants.DEFAULT_PKGDATADIR_FALLBACK

    if not datadir_to_use:
        logger.critical("No valid PKGDATADIR found. Cannot load resources.")
        return False # Indicate failure

    logger.debug(f"Effective PKGDATADIR for resources: {datadir_to_use}")
    resource_file_path = os.path.join(datadir_to_use, "woes.gresource")
    logger.info("Attempting to load GResource file from: %s", resource_file_path)

    if not os.path.exists(resource_file_path):
        logger.error("GResource file not found at %s.", resource_file_path)
        return False

    try:
        resource = Gio.Resource.load(resource_file_path)
        if resource:
            # pylint: disable=protected-access # _register is the intended way for applications to manually register resources
            Gio.Resource._register(resource)
            logger.info("Successfully loaded and registered GResource: %s", resource_file_path)
            # Use constants.RESOURCE_PREFIX after import style change
            available_resources = resource.enumerate_children(constants.RESOURCE_PREFIX, Gio.ResourceLookupFlags.NONE)
            logger.debug("Available resources under %s: %s", constants.RESOURCE_PREFIX, available_resources)
            if not available_resources:
                logger.warning("No resources found under %s in %s.", constants.RESOURCE_PREFIX, resource_file_path)
            return True
        else:
            logger.error("Gio.Resource.load() returned None for %s.", resource_file_path)
            return False
    except GLib.Error as e:
        logger.error("Failed to load GResource %s: %s. Bundle invalid?", resource_file_path, e, exc_info=True)
        return False
    except FileNotFoundError: # Should be caught by os.path.exists, but as a safeguard.
        logger.exception("GResource file not found (FileNotFoundError): %s", resource_file_path)
        return False
    except Exception:
        logger.exception("Unexpected error loading GResource %s.", resource_file_path)
        return False

# Initial logger setup should be before this point
# Ensure constants is imported before this
if not _perform_resource_loading():
    # If resources fail to load at import time, the application is non-functional.
    # Log a critical error. Further actions depend on desired behavior:
    # - Could raise an ImportError/RuntimeError.
    # - Or allow import to succeed, and main() will fail later (less ideal).
    # For now, just logging. The subsequent GTK template loading will fail if resources aren't there.
    logger.critical("Module-level GResource loading failed. Application may not function correctly.")
    # Consider if sys.exit should be called here, though it's unusual for a module import to sys.exit.
    # The failure of @Gtk.Template will likely raise an error that stops the app.


class WoesApplication(Adw.Application):
    """The main application singleton class."""

    # Use constants.VERSION after import style change
    def __init__(self, version=constants.VERSION, **kwargs):
        logger.info("Initializing WoesApplication.")
        self.version = version
        self.debug_enabled = False

        super().__init__(
            application_id=constants.APP_ID, # Use constants.APP_ID
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
        # Ensure constants is available if not already imported where VERSION is used.
        # from . import constants # Not strictly needed if already at top and main passes version
        Adw.Application.do_startup(self)

    def do_handle_local_options(self, options):
        logger.debug("Handling local options: %s", options)
        if options.contains('debug'):
            self.debug_enabled = True
            logger.debug(f"Debug mode set to: {self.debug_enabled}")
            logging.basicConfig(
                level=logging.DEBUG,
                format='%(asctime)s %(levelname)s:%(name)s:%(message)s',
                force=True
            )
            logger.debug("Debug mode enabled via command line. Logging reconfigured.")
        return -1

    def do_command_line(self, command_line):
        logger.debug("WoesApplication.do_command_line entered, explicitly calling self.activate().")
        # Process arguments, e.g., by calling the superclass method
        # to handle options like --version or --help, or custom ones.
        # However, for this diagnostic, we want to ensure activate is called.
        # super_result = super().do_command_line(command_line)
        # logger.debug(f"super().do_command_line(command_line) returned {super_result}")

        # Regardless of what superclass did, ensure activation for this test
        self.activate()

        # A return value of 0 typically indicates that the command line was handled successfully
        # and the application should continue running (if it's the primary instance).
        # If activate() leads to the app showing a window, it will keep running.
        # If activate() doesn't result in a window, the app might still exit.
        return 0

    def do_activate(self):
        """Called when the application is activated."""
        logger.info("Activating WoesApplication.")
        win = self.props.active_window
        logger.debug(f"self.props.active_window: {win}")
        if not win:
            logger.debug("No active window, creating WoesWindow.")
            logger.debug("Before WoesWindow(application=self)")
            try:
                win = WoesWindow(application=self)
                logger.debug("After WoesWindow(application=self)")
                logger.debug("WoesWindow created successfully.")
            except Exception:
                logger.exception("Failed to create WoesWindow.")
                sys.exit(1)

        if not win:
            logger.error("Window object is None after creation attempt, cannot proceed.")
            sys.exit(1)

        self.win = win
        self.add_window(self.win)
        logger.debug("Presenting WoesWindow.")
        logger.debug("Before self.win.present()")
        try:
            self.win.present()
            logger.debug("After self.win.present() returns")
            logger.debug("WoesWindow presented.")
            logger.debug(f"Window visible: {self.win.is_visible()}")
            logger.debug(f"Window mapped: {self.win.get_mapped()}")
            logger.debug(f"Window width: {self.win.get_width()}, height: {self.win.get_height()}")
            logger.debug(f"Window application: {self.win.get_application()}")
            logger.debug(f"Active window on app: {self.props.active_window}")
            main_loop = GLib.MainLoop()
            logger.debug(f"GLib MainLoop running: {main_loop.is_running()}")
            # We expect it not to be running here yet in this specific spot,
            # as app.run() hasn't fully established it from this inner scope.
            # This is more of a sanity check on GLib.MainLoop() state.
        except GLib.Error:
            logger.exception("Error presenting WoesWindow.")
        except Exception:
            logger.exception("Unexpected error during WoesWindow.present().")

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
            application_icon=constants.APP_ID, # Use constants.APP_ID
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
        logger.debug(f"Connected 'activate' signal for action: app.{name}")
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version=constants.VERSION):
    """The application's entry point."""
    # _configure_resource_path and the resource loading check removed from here
    # Resource loading is now done at module import time.

    logger.info("Starting Woes application main function.")
    logger.debug(f"Before app = WoesApplication(version={version})")
    app = WoesApplication(version=version)
    logger.debug(f"After app = WoesApplication(version={version}), app: {app}")
    logger.info("WoesApplication instance created.")

    logger.info("Running WoesApplication.")
    logger.debug("Before app.run(sys.argv)")
    exit_status = app.run(sys.argv)
    logger.debug(f"app.run returned: {exit_status}")
    logger.info("WoesApplication finished with exit status: %s", exit_status)
    return exit_status


if __name__ == '__main__':
    # This block is not executed when Woes is run via `woes.in` normally,
    # but useful for direct module testing if needed.
    # Resource loading now happens at module import time using environment variables or fallbacks.
    sys.exit(main())
