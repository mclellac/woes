import sys
import os
import logging
import gi
from gi.repository import Adw, Gio, GLib
from .constants import APP_ID, VERSION, RESOURCE_PREFIX, PKGDATADIR

# Basic logging configuration, will be updated in WoesApplication based on debug flag
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)  # Use a module-level logger

logger.info("Script execution started.")

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


def _load_gresources_early():
    """Loads GResources and logs detailed information."""
    resource_file_path = os.path.join(PKGDATADIR, "woes.gresource")
    logger.info("Attempting to load GResource file from: %s", resource_file_path)

    if not os.path.exists(resource_file_path):
        logger.error("GResource file not found at %s.", resource_file_path)
        return

    try:
        resource = Gio.Resource.load(resource_file_path)
        if resource:
            Gio.Resource._register(resource)  # pylint: disable=protected-access
            logger.info("Successfully loaded and registered GResource: %s", resource_file_path)
            available_resources = resource.enumerate_children(RESOURCE_PREFIX, Gio.ResourceLookupFlags.NONE)
            logger.debug("Available resources under %s: %s", RESOURCE_PREFIX, available_resources)
            if not available_resources:
                logger.warning("No resources found under %s in %s.", RESOURCE_PREFIX, resource_file_path)
        else:
            logger.error("Gio.Resource.load() returned None for %s.", resource_file_path)
    except GLib.Error as e:
        logger.error("Failed to load GResource %s: %s. Bundle invalid?", resource_file_path, e, exc_info=True)
    except FileNotFoundError:  # Should be caught by os.path.exists, but good practice
        logger.exception("GResource file not found (FileNotFoundError): %s", resource_file_path)
    except Exception:  # Catching general Exception for unexpected issues
        logger.exception("Unexpected error loading GResource %s.", resource_file_path)


_load_gresources_early()


# pylint: disable=wrong-import-position
from .preferences import Preferences  # Now import other local modules
from .window import WoesWindow  # pylint: disable=wrong-import-position


class WoesApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version=VERSION, **kwargs):
        logger.info("Initializing WoesApplication.")
        self.version = version
        self.debug_enabled = False

        # Logging level will be updated in do_handle_local_options if --debug is passed.
        # The initial basicConfig at the top of the file sets a default.

        super().__init__(
            application_id=APP_ID,
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

        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("about", self.on_about_action)
        self.create_action("preferences", self.on_preferences_action)
        self.create_action("switch-to-http", self.switch_to_http, ["<primary>1"])
        self.create_action("switch-to-nmap", self.switch_to_nmap, ["<primary>2"])
        self.create_action("switch-to-dns", self.switch_to_dns, ["<primary>3"])
        self.create_action("switch-to-webscan", self.switch_to_webscan, ["<primary>4"])

    def do_handle_local_options(self, options):
        logger.debug("Handling local options: %s", options)
        if options.contains('debug'):
            self.debug_enabled = True
            logging.getLogger().setLevel(logging.DEBUG)  # Set root logger level
            for handler in logging.getLogger().handlers:  # Update all handlers
                handler.setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled via command line.")
        # Normal activation proceeds
        return -1

    def do_command_line(self, command_line):
        """Overrides the do_command_line virtual method."""
        options = command_line.get_options_dict()
        logger.debug("Application started with command line options: %s", options)
        # do_handle_local_options is automatically called by GLib before do_activate
        # if command line options are present.
        # We don't need to call it explicitly if we're just activating.
        # However, if we wanted to process options *before* the default handler, this is where.
        # For now, standard activation flow is fine.
        self.activate()
        return 0

    def do_activate(self):
        """Called when the application is activated."""
        logger.info("Activating WoesApplication.")
        # Ensure active_window is used if available, otherwise create new
        win = self.props.active_window
        if not win:
            logger.debug("No active window, creating WoesWindow.")
            try:
                win = WoesWindow(application=self)
                logger.debug("WoesWindow created successfully.")
            except Exception:  # More specific exceptions could be RuntimeError, TypeError etc.
                logger.exception("Failed to create WoesWindow.")
                sys.exit(1)  # Critical failure

        if not win:
            logger.error("Window object is None after creation attempt, cannot proceed.")
            sys.exit(1)

        self.win = win
        logger.debug("Presenting WoesWindow.")
        try:
            self.win.present()
            logger.debug("WoesWindow presented.")
        except GLib.Error:  # Gtk.Window.present() can raise GLib.Error
            logger.exception("Error presenting WoesWindow.")
        except Exception:
            logger.exception("Unexpected error during WoesWindow.present().")

    def switch_to_http(self, *_args):
        if self.win and self.win.stack:
            self.win.stack.set_visible_child_name("http")
        else:
            logger.warning("Cannot switch to http_page: window or stack not available.")

    def switch_to_nmap(self, *_args):
        if self.win and self.win.stack:
            self.win.stack.set_visible_child_name("nmap")
        else:
            logger.warning("Cannot switch to nmap_page: window or stack not available.")

    def switch_to_dns(self, *_args):
        if self.win and self.win.stack:
            self.win.stack.set_visible_child_name("dns")
        else:
            logger.warning("Cannot switch to dns_page: window or stack not available.")

    def switch_to_webscan(self, *_args):
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
            application_icon=APP_ID,
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


def main(version=VERSION):
    """The application's entry point."""
    # Logging is configured at the top of the script.
    # _load_gresources_early() is called after initial imports.
    # GLib.Application handles command-line options like --debug.

    logger.info("Starting Woes application main function.")
    app = WoesApplication(version=version)
    logger.info("WoesApplication instance created.")

    logger.info("Running WoesApplication.")
    exit_status = app.run(sys.argv)
    logger.info("WoesApplication finished with exit status: %s", exit_status)
    return exit_status


if __name__ == '__main__':
    # The initial logging configuration is done when the script is first imported/run.
    # No specific logging needed here unless it's for this exact block,
    # but logger.info at the start of main() should cover it.
    sys.exit(main())
