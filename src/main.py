"""Main application module for Woes.

Handles application initialization, GResource loading, command-line option parsing,
and launching the main application window and services.
"""
import sys
import os
import logging
from typing import Optional, List # Added List
import gi
gi.require_version("Gtk", "4.0") # Moved require_version before repository imports
gi.require_version("Adw", "1")   # Moved require_version before repository imports
from gi.repository import Adw, Gio, GLib

from .window import WoesWindow
from .preferences import Preferences
from .constants import APP_ID, VERSION, RESOURCE_PREFIX, PKGDATADIR


def _load_gresources_early():
    """Loads GResources."""
    resource_file_path = os.path.join(PKGDATADIR, "woes.gresource")
    logging.info(
        "Attempting to load GResource file from: %s (derived from PKGDATADIR: %s)",
        resource_file_path,
        PKGDATADIR,
    )
    if not os.path.exists(resource_file_path):
        logging.critical(
            "GResource file not found at %s. Application will now exit.",
            resource_file_path,
        )
        sys.exit(1)

    try:
        resource = Gio.Resource.load(resource_file_path)
        if not resource:
            logging.critical(
                "Gio.Resource.load() returned None for %s. Application will now exit.",
                resource_file_path,
            )
            sys.exit(1)

        Gio.Resource._register(resource)  # pylint: disable=protected-access
        logging.info("Successfully loaded and registered GResource: %s", resource_file_path)

        available_resources = resource.enumerate_children(
            RESOURCE_PREFIX, Gio.ResourceLookupFlags.NONE
        )
        if not available_resources:
            logging.warning(
                "No resources found under prefix %s after loading %s.",
                RESOURCE_PREFIX,
                resource_file_path,
            )
        else:
            logging.debug("Available resources under %s: %s", RESOURCE_PREFIX, available_resources)

    except GLib.Error as e:
        logging.critical(
            "Error loading or registering GResource %s: %s. "
            "Check if the file is a valid GResource bundle. Application will now exit.",
            resource_file_path,
            e,
            exc_info=True,
        )
        sys.exit(1)
    except Exception as e:  # pylint: disable=broad-except
        logging.critical(
            "An unexpected error occurred during GResource loading: %s. Application will now exit.",
            e,
            exc_info=True,
        )
        sys.exit(1)


_load_gresources_early()


class WoesApplication(Adw.Application):
    """The main application singleton class for Woes.

    Manages the application lifecycle, actions, and the main window.
    """

    def __init__(self, version=VERSION, **kwargs):
        """Initialize the WoesApplication.

        Args:
        ----
            version (str): The application version.
            **kwargs: Additional keyword arguments for Adw.Application.

        """
        logging.info("WoesApplication.__init__ entered")
        self.version = version
        self.debug_enabled = False
        self.win = None

        # Configure basic logging. If --debug is passed, it will be reconfigured.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS, **kwargs)

        self.add_main_option(
            "debug",
            ord("d"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Enable debug logging",
            None,
        )

        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("about", self.on_about_action)
        self.create_action("preferences", self.on_preferences_action)
        self.create_action("switch-to-http", self.switch_to_http, ["<primary>1"])
        self.create_action("switch-to-nmap", self.switch_to_nmap, ["<primary>2"])
        self.create_action("switch-to-dns", self.switch_to_dns, ["<primary>3"])
        self.create_action("switch-to-webscan", self.switch_to_webscan, ["<primary>4"])

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        """Handle local command-line options.

        Currently supports a '--debug' option to enable debug logging.

        Args:
        ----
            options: A GLib.VariantDict containing the command-line options.

        Returns:
        -------
            -1 to indicate that command line processing is not finished, allowing
            further processing (like `do_command_line`) to occur.

        """
        if options.contains("debug"):
            self.debug_enabled = True
            # Reconfigure logging for DEBUG level if --debug is present
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(levelname)s:%(name)s:%(message)s",
                force=True,
            )
            logging.debug("Debug mode enabled via command line.")
        return -1  # Indicates that command line processing is not finished

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """Override the do_command_line virtual method to handle command line arguments.

        This method processes options passed to the application and then activates the application.

        Args:
        ----
            command_line: The Gio.ApplicationCommandLine object.

        Returns:
        -------
            The exit status of the command line processing. 0 for success.

        """
        options = command_line.get_options_dict()
        # This will call do_handle_local_options if options are present
        if self.handle_local_options(options):
            logging.debug("Local options handled.")

        self.activate()
        return 0

    def do_activate(self):
        """Create and present the main application window.

        This method is called when the application is activated.
        It ensures the main window (WoesWindow) is created and shown.
        """
        logging.info("WoesApplication.do_activate entered")
        win = self.props.active_window
        if not win:
            logging.info("WoesApplication.do_activate: Creating WoesWindow...")
            try:
                win = WoesWindow(application=self)
            except Exception:  # pylint: disable=broad-except
                logging.exception("WoesApplication.do_activate: Error creating WoesWindow instance")
                sys.exit(1)

        if win:
            logging.info("WoesApplication.do_activate: Presenting window...")
            try:
                win.present()
            except Exception:  # pylint: disable=broad-except
                logging.exception("WoesApplication.do_activate: Error during win.present()")
            self.win = win  # Keep a reference to the window
        else:
            logging.error(
                "WoesApplication.do_activate: Window object is None after creation attempt, cannot proceed."
            )

    def switch_to_http(self, *_args):
        """Switch the main window's view to the HTTP page."""
        if self.win and hasattr(self.win, "stack"):
            self.win.stack.set_visible_child_name("http")
        else:
            logging.warning("Cannot switch to http_page: window or stack not available.")

    def switch_to_nmap(self, *_args):
        """Switch the main window's view to the Nmap page."""
        if self.win and hasattr(self.win, "stack"):
            self.win.stack.set_visible_child_name("nmap")
        else:
            logging.warning("Cannot switch to nmap_page: window or stack not available.")

    def switch_to_dns(self, *_args):
        """Switch the main window's view to the DNS page."""
        if self.win and hasattr(self.win, "stack"):
            self.win.stack.set_visible_child_name("dns")
        else:
            logging.warning("Cannot switch to dns_page: window or stack not available.")

    def switch_to_webscan(self, *_args):
        """Switch the main window's view to the WebScan page."""
        if self.win and hasattr(self.win, "stack"):
            self.win.stack.set_visible_child_name("webscan")
        else:
            logging.warning("Cannot switch to webscan_page: window or stack not available.")

    def on_about_action(self, _widget: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """Handle the 'about' action activation.

        Displays the application's About dialog.

        Args:
        ----
            _widget: The Gio.SimpleAction that was activated.
            _param: Optional GLib.Variant parameter (unused).

        """
        about = Adw.AboutWindow(
            transient_for=self.props.active_window,
            application_name="woes",
            application_icon=APP_ID,
            developer_name="Carey McLelland",
            version=self.version,
            developers=["Carey McLelland"],
            copyright="© 2025 Carey McLelland",
        )
        about.present()

    def on_preferences_action(self, _widget: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """Handle the 'preferences' action activation.

        Displays the application's Preferences dialog.

        Args:
        ----
            _widget: The Gio.SimpleAction that was activated.
            _param: Optional GLib.Variant parameter (unused).

        """
        if not self.win:
            logging.error("Main window not available for preferences.")
            return
        preferences_dialog = Preferences(main_window=self.win)
        preferences_dialog.present()

    def create_action(self, name: str, callback, shortcuts: Optional[List[str]] = None): # Changed to typing.List
        """Create and add a Gio.SimpleAction to the application.

        Args:
        ----
            name: The name of the action (e.g., "quit", "about").
            callback: The function to call when the action is activated.
            shortcuts: An optional list of keyboard shortcuts for the action
                       (e.g., ["<primary>q"]).

        """
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version: str = VERSION) -> int:
    """Run the Woes application.

    This is the main entry point for the application. It initializes logging,
    creates the WoesApplication instance, and runs it.

    Args:
    ----
        version: The version string of the application.

    Returns:
    -------
        The exit status of the application.

    """
    # Basic configuration, will be overridden if --debug is passed.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    logging.info("Application main() function entered.")

    app = WoesApplication(version=version)
    logging.info("WoesApplication instance created.")

    logging.info("Calling app.run().")
    exit_status = app.run(sys.argv)
    logging.info("app.run() finished with status %s.", exit_status)
    return exit_status


if __name__ == "__main__":
    sys.exit(main())
