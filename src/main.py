"""Main entry point and core application class for the Woes application.

This module is responsible for the overall application lifecycle, including:
- Early loading of GResources.
- Setting up basic logging.
- Defining and initializing the :class:`WoesApplication` class, which is derived
  from :class:`Adw.Application`.
- Handling command-line arguments (e.g., for debug mode).
- Managing application-level actions (e.g., quit, about, preferences).
- Creating and presenting the main application window (:class:`~.window.WoesWindow`).
- Orchestrating page switching and page-specific actions.
"""
import sys
import os
import logging
from typing import Callable, List, Optional, Any # Added Any for type hints
import gi

# GResource loading must happen before any modules that use Gtk.Template are imported,
# including .window and .preferences which are imported by this module later.
# Therefore, _load_gresources_early() is called before those imports.

# --- Early GResource Loading ---
# Attempt to load GResources. This is critical for Gtk.Template based UI.
# It tries a development path and an installed path. Exits if not found.
def _load_gresources_early() -> None:
    """Loads GResources early in the application startup sequence.

    This function is critical for ensuring that :class:`Gtk.Template`-based
    widgets can be loaded correctly from compiled GResource files. It attempts
    to load ``woes.gresource`` from a development path (relative to this file,
    expecting a specific build directory structure) or a standard installed path
    (defined by `PKGDATADIR` from constants).

    The application will exit with a status code of 1 if the GResource file
    cannot be found or if there's an error during its loading or registration.

    :raises SystemExit: If the GResource file cannot be found or loaded.
    :return: None
    :rtype: None
    """
    # Path for running from source tree (e.g., relative to src/main.py)
    # Assumes main.py is in src/ and resources are in ../build/src/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dev_resource_path = os.path.normpath(
        os.path.join(script_dir, "..", "build", "src", "woes.gresource"))

    # Path for installed version (PKGDATADIR is from .constants)
    # This path needs to be defined before _load_gresources_early is called if constants.py is imported after.
    # For now, assuming constants.py can be imported before this function is run, or PKGDATADIR is hardcoded/passed.
    # To ensure PKGDATADIR is available, we import it specifically here, or ensure main() passes it.
    # For simplicity, this example assumes constants.py can be imported before this call.
    # If not, PKGDATADIR would need to be defined differently here.
    try:
        from .constants import PKGDATADIR as CONST_PKGDATADIR
        from .constants import RESOURCE_PREFIX as CONST_RESOURCE_PREFIX
    except ImportError:
        # Fallback if constants cannot be imported yet (e.g. circular dependency if constants needs something from main)
        # This is a less ideal scenario. Prefer structuring imports to avoid this.
        logging.error("Could not import PKGDATADIR from .constants for GResource loading. Using fallback.")
        CONST_PKGDATADIR = "/usr/local/share/woes" # Hardcoded fallback
        CONST_RESOURCE_PREFIX = "/com/github/mclellac/woes/gtk" # Hardcoded fallback

    installed_resource_path = os.path.join(CONST_PKGDATADIR, "woes.gresource")

    resource_file_path: Optional[str] = None
    if os.path.exists(dev_resource_path):
        logging.info("Development GResource path found: %s", dev_resource_path)
        resource_file_path = dev_resource_path
    elif os.path.exists(installed_resource_path):
        logging.info("Installed GResource path found: %s", installed_resource_path)
        resource_file_path = installed_resource_path
    else:
        logging.critical("GResource file not found at development path ('%s') or "
                         "installed path ('%s'). Application will now exit.",
                         dev_resource_path, installed_resource_path)
        sys.exit(1)

    logging.info("Attempting to load GResource file from: %s", resource_file_path)
    try:
        resource = Gio.Resource.load(resource_file_path)
        if not resource: # Gio.Resource.load can return None on failure
            logging.critical("Gio.Resource.load() returned None for path '%s'. "
                             "File may be corrupted or not a valid GResource. Application will exit.",
                             resource_file_path)
            sys.exit(1)
        Gio.Resource._register(resource)  # pylint: disable=protected-access
        logging.info("Successfully loaded and registered GResource: %s", resource_file_path)

        # Verify some resources are available (optional sanity check)
        available_resources = resource.enumerate_children(CONST_RESOURCE_PREFIX, Gio.ResourceLookupFlags.NONE)
        if not available_resources:
            logging.warning("No resources found under prefix '%s' after loading '%s'. "
                            "UI files might be missing.", CONST_RESOURCE_PREFIX, resource_file_path)
        else:
            logging.debug("Available resources under '%s': %s", CONST_RESOURCE_PREFIX, available_resources)

    except GLib.Error as e: # GLib.Error can be raised by Gio.Resource.load
        logging.critical("Error loading or registering GResource '%s': %s. "
                         "Check if the file is a valid GResource bundle. Application will exit.",
                         resource_file_path, e, exc_info=True)
        sys.exit(1)
    except Exception as e:  # pylint: disable=broad-except # Catch any other unexpected error
        logging.critical("An unexpected error occurred during GResource loading from '%s': %s. "
                         "Application will exit.", resource_file_path, e, exc_info=True)
        sys.exit(1)

_load_gresources_early()

# Now that GResources are loaded, other application modules can be imported.
from .preferences import Preferences
from .window import WoesWindow
from .constants import (
    APP_ID,
    VERSION,
    # RESOURCE_PREFIX and PKGDATADIR already imported/handled in _load_gresources_early context
    APP_WEBSITE_URL,
    APP_LICENSE_TYPE,
    APP_DESCRIPTION,
    APP_ISSUES_URL,
)
from gi.repository import Adw, Gio, GLib, GObject # GObject for type hints

gi.require_version("Gtk", "4.0") # Ensure GTK version is set
gi.require_version("Adw", "1") # Ensure Adwaita version is set


class WoesApplication(Adw.Application):
    """The main application class for Woes, derived from :class:`Adw.Application`.

    This class manages the application's lifecycle, global state, and core actions.
    It is responsible for:
    - Initializing the application with a unique ID and flags.
    - Handling command-line options (e.g., ``--debug``).
    - Setting up and managing application-level actions (e.g., 'quit', 'about',
      'preferences', page navigation, page-specific triggers).
    - Overriding virtual methods like :meth:`do_activate`
      to create and present the main application window (:class:`~.window.WoesWindow`).
    - Storing a reference to the main window.
    """

    win: Optional[WoesWindow] # Reference to the main application window

    def __init__(self, version: str = VERSION, **kwargs: Any) -> None:
        """Initializes the WoesApplication.

        Sets up application ID, version, logging, command-line options,
        and global application actions.

        :param version: The application version string. Defaults to the version
                        from :mod:`~.constants`.
        :type version: str
        :param kwargs: Additional keyword arguments for the :class:`Adw.Application` constructor.
        :type kwargs: Any
        :return: None
        :rtype: None
        """
        logging.info("WoesApplication.__init__ entered. Version: %s", version)
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS | Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
                         **kwargs)
        self.version = version
        self.debug_enabled = False # Will be set by do_handle_local_options
        self.win = None # Main window reference

        # Initial basic logging. Reconfigured if --debug is passed.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

        # Define command-line options
        self.add_main_option(
            "debug", # Long name
            ord("d"), # Short name (single character)
            GLib.OptionFlags.NONE, # Flags
            GLib.OptionArg.NONE, # Argument type (no argument for a boolean flag)
            "Enable debug logging output", # Description
            None, # Placeholder for argument description if arg type was not NONE
        )

        # Define application actions
        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("about", self.on_about_action)
        self.create_action("preferences", self.on_preferences_action)

        # Actions for switching pages
        self.create_action("switch-to-http", self._action_switch_to_http, ["<primary>1"])
        self.create_action("switch-to-nmap", self._action_switch_to_nmap, ["<primary>2"])
        self.create_action("switch-to-dns", self._action_switch_to_dns, ["<primary>3"])
        self.create_action("switch-to-webscan", self._action_switch_to_webscan, ["<primary>4"])

        # Page-specific actions triggered globally (e.g., via shortcuts)
        self.create_action("page-action-http-fetch", self.on_page_action_http_fetch, ["<Alt>F"])
        self.create_action("page-action-nmap-scan", self.on_page_action_nmap_scan, ["<Alt>S"])
        self.create_action("page-action-dns-lookup", self.on_page_action_dns_lookup, ["<Alt>L"])
        self.create_action("page-action-webscan-scan", self.on_page_action_webscan_scan, ["<Alt>W"])

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        """Handles local command-line options parsed by GLib.

        Currently supports a ``--debug`` (or ``-d``) option to enable debug-level logging.
        This method is called automatically by GLib after command line parsing
        if `Gio.ApplicationFlags.HANDLES_COMMAND_LINE` is set.

        :param options: A :class:`GLib.VariantDict` containing the parsed command-line options.
                        Boolean options are present if the flag was given.
        :type options: GLib.VariantDict
        :return: -1 to indicate that command line processing is not finished by this handler,
                 allowing further processing (like :meth:`do_command_line`) to occur.
                 Returning 0 would indicate successful handling and stop further processing.
        :rtype: int
        """
        if options.contains("debug"):
            self.debug_enabled = True
            # Reconfigure logging for DEBUG level if --debug is present
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(levelname)s:%(name)s:%(lineno)d:%(funcName)s:%(message)s", # More detail for debug
                force=True, # Override previous basicConfig
            )
            logging.debug("Debug mode enabled via command line.")
        return super().do_handle_local_options(options) # Important to chain up for default handling

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """Handles the command line arguments for the application.

        This method is called when the application is run with command line arguments
        if the `Gio.ApplicationFlags.HANDLES_COMMAND_LINE` flag is set. It processes
        any local options (like ``--debug`` via :meth:`do_handle_local_options` which is
        called by `self.handle_local_options`) and then activates the application
        to show the main window.

        :param command_line: The :class:`Gio.ApplicationCommandLine` object representing
                             the command line invocation.
        :type command_line: Gio.ApplicationCommandLine
        :return: The exit status of the command line processing. Typically 0 for success,
                 or a non-zero value if there was an error that prevents activation.
        :rtype: int
        """
        # options = command_line.get_options_dict() # This is already handled by Gio.Application
        # self.handle_local_options(options) will call do_handle_local_options
        # No need to explicitly call handle_local_options if super().do_command_line or
        # the default Gio.Application machinery handles it before or during activate.
        # However, if specific argument processing beyond options is needed, it's done here.

        # According to Gio.Application docs, do_command_line is for handling arguments
        # *not* covered by GOptionContext. Options are typically handled by do_handle_local_options.
        # After options are processed, we activate the application.
        self.activate()
        return 0 # Indicate successful handling of the command line to exit cleanly

    def do_activate(self) -> None:
        """Creates and presents the main application window.

        This method is called when the application is activated (either as the
        primary instance or when launched without specific command-line actions
        that might be handled by `do_command_line`). It ensures the main
        application window (:class:`~.window.WoesWindow`) is created, configured,
        and presented to the user. If the window already exists, it is simply presented.

        :return: None
        :rtype: None
        """
        logging.info("WoesApplication.do_activate: Activating application.")
        # Use 'self.props.active_window' to get the current active window, or create one.
        # Adw.Application handles ensuring only one main window typically.
        if not self.props.active_window:
            logging.info("WoesApplication.do_activate: No active window, creating WoesWindow.")
            try:
                self.win = WoesWindow(application=self)
            except Exception:  # pylint: disable=broad-except
                logging.exception("WoesApplication.do_activate: Error creating WoesWindow instance.")
                sys.exit(1) # Critical failure if window can't be created
        else:
            self.win = self.props.active_window # type: ignore

        if self.win:
            logging.info("WoesApplication.do_activate: Presenting window.")
            try:
                self.win.present()
            except Exception:  # pylint: disable=broad-except
                logging.exception("WoesApplication.do_activate: Error during win.present().")
        else:
            # This case should ideally not be reached if window creation was successful
            # or if an existing window was correctly retrieved.
            logging.error("WoesApplication.do_activate: Window object is None after creation/retrieval attempt.")


    def _switch_to_page(self, page_name: str) -> None:
        """Switches the main window's view stack to the specified page.

        Logs a warning if the main window or its view stack is not available.

        :param page_name: The name of the page to switch to (e.g., "http", "nmap"),
                          corresponding to a child name in the :class:`Adw.ViewStack`.
        :type page_name: str
        :return: None
        :rtype: None
        """
        if self.win and hasattr(self.win, "stack") and self.win.stack:
            logging.debug("Switching to page: %s", page_name)
            self.win.stack.set_visible_child_name(page_name)
        else:
            logging.warning("Cannot switch to page '%s': Main window or view stack not available.", page_name)

    # --- Action Callbacks for Page Switching ---
    def _action_switch_to_http(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("http")
    def _action_switch_to_nmap(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("nmap")
    def _action_switch_to_dns(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("dns")
    def _action_switch_to_webscan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("webscan")

    def _trigger_page_action(
            self,
            expected_page_name: str,
            action_method_name: str,
            action_description: str
        ) -> None:
        """Triggers a specific action method on the currently visible page,
        if it matches the `expected_page_name`.

        This is a helper to route global actions (like keyboard shortcuts)
        to the appropriate active page's methods (e.g., a 'scan' or 'fetch' method).

        :param expected_page_name: The name of the page (from the :class:`Adw.ViewStack`)
                                   on which the action is expected to occur.
        :type expected_page_name: str
        :param action_method_name: The name of the method to call on the page object
                                   (e.g., "trigger_fetch", "trigger_scan").
        :type action_method_name: str
        :param action_description: A human-readable description of the action, used for logging.
        :type action_description: str
        :return: None
        :rtype: None
        """
        if not self.win or not hasattr(self.win, "stack") or not self.win.stack:
            logging.warning("Cannot trigger page action '%s': Window or stack not available.", action_description)
            return

        page_name = self.win.stack.get_visible_child_name()
        page_object = self.win.stack.get_visible_child()

        if page_name == expected_page_name and page_object:
            if hasattr(page_object, action_method_name):
                logging.debug("Triggering action '%s' on page '%s'.", action_method_name, page_name)
                getattr(page_object, action_method_name)() # Call the page's method
            else:
                logging.warning("Page '%s' object does not have method '%s' for action '%s'.",
                                page_name, action_method_name, action_description)
        elif page_name == expected_page_name and not page_object: # Page name matches but no object
            logging.warning("Page '%s' object is None, cannot trigger action '%s'.",
                            page_name, action_description)
        # No warning if it's not the expected_page_name, as the action is specific to that page.

    def on_page_action_http_fetch(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        """Handles the 'page-action-http-fetch' action.

        This action triggers the fetch operation on the currently visible HTTP page.

        :param _action: The :class:`Gio.SimpleAction` that triggered this handler (unused).
        :type _action: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        :return: None
        :rtype: None
        """
        self._trigger_page_action("http", "trigger_fetch", "HTTP fetch")

    def on_page_action_nmap_scan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        """Handles the 'page-action-nmap-scan' action.

        This action triggers the scan operation on the currently visible Nmap page.

        :param _action: The :class:`Gio.SimpleAction` that triggered this handler (unused).
        :type _action: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        :return: None
        :rtype: None
        """
        self._trigger_page_action("nmap", "trigger_scan", "Nmap scan")

    def on_page_action_dns_lookup(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        """Handles the 'page-action-dns-lookup' action.

        This action triggers the lookup operation on the currently visible DNS page.

        :param _action: The :class:`Gio.SimpleAction` that triggered this handler (unused).
        :type _action: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        :return: None
        :rtype: None
        """
        self._trigger_page_action("dns", "trigger_lookup", "DNS lookup")

    def on_page_action_webscan_scan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        """Handles the 'page-action-webscan-scan' action.

        This action triggers the scan operation on the currently visible WebScan page.

        :param _action: The :class:`Gio.SimpleAction` that triggered this handler (unused).
        :type _action: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        :return: None
        :rtype: None
        """
        self._trigger_page_action("webscan", "trigger_scan", "Webscan scan")

    def on_about_action(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        """Handles the 'about' action activation from the application menu.

        Creates and displays the application's :class:`Adw.AboutWindow`.

        :param _action: The :class:`Gio.SimpleAction` that was activated (unused).
        :type _action: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        :return: None
        :rtype: None
        """
        about_dialog = self._create_about_window()
        # Set transient_for to the active window if available
        if active_window := self.props.active_window: # Use assignment expression
            about_dialog.set_transient_for(active_window)
        about_dialog.present()

    def _create_about_window(self) -> Adw.AboutWindow:
        """Creates and configures the :class:`Adw.AboutWindow` for the application.

        The window is populated with metadata from :mod:`~.constants`.

        :return: The configured About Window.
        :rtype: Adw.AboutWindow
        """
        # Note: 'transient_for' is set by the caller (on_about_action)
        return Adw.AboutWindow(
            application_name="Woes", # User-visible name
            application_icon=APP_ID, # Application ID for the icon
            developer_name="Carey McLelland",
            version=self.version,
            developers=["Carey McLelland"], # List of developers
            copyright="© 2024-2025 Carey McLelland", # Copyright notice
            website=APP_WEBSITE_URL,
            license_type=APP_LICENSE_TYPE, # Gtk.License enum value
            comments=APP_DESCRIPTION,
            issue_url=APP_ISSUES_URL,
        )

    def on_preferences_action(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        """Handles the 'preferences' action activation from the application menu.

        Creates and displays the application's :class:`~.preferences.Preferences` dialog.

        :param _action: The :class:`Gio.SimpleAction` that was activated (unused).
        :type _action: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        :return: None
        :rtype: None
        """
        if not self.win: # Ensure main window exists to be transient for
            logging.error("Main window not available, cannot display preferences dialog.")
            return
        preferences_dialog = Preferences(main_window=self.win)
        preferences_dialog.present()

    def create_action(
        self,
        name: str,
        callback: Callable[..., Any],
        shortcuts: Optional[List[str]] = None
    ) -> None:
        """Creates a :class:`Gio.SimpleAction`, connects its 'activate' signal
        to the given callback, and adds it to the application.

        Optionally, keyboard shortcuts (accelerators) can be associated with
        the created action.

        :param name: The name of the action (e.g., "quit", "app.about").
                     The "app." prefix is conventional for application-wide actions
                     accessible via menus or shortcuts.
        :type name: str
        :param callback: The function to be called when the action is activated.
                         It typically accepts two arguments: the action object
                         (:class:`Gio.SimpleAction`) and an optional :class:`GLib.Variant`
                         parameter.
        :type callback: Callable[..., Any]
        :param shortcuts: An optional list of keyboard shortcut strings for the
                          action (e.g., ``["<primary>q"]``, ``["<Alt>F"]``).
        :type shortcuts: Optional[List[str]]
        :return: None
        :rtype: None
        """
        action = Gio.SimpleAction.new(name, None) # No parameter type for these actions
        action.connect("activate", callback)
        self.add_action(action) # Add to application-level actions
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(app_version: str = VERSION) -> int:
    """Main entry point for launching the Woes application.

    Initializes basic logging, creates an instance of :class:`WoesApplication`
    with the provided or default version, and runs the GTK application main loop.

    :param app_version: The version string of the application, defaults to the
                        version specified in :mod:`~.constants`.
    :type app_version: str
    :return: The exit status of the application, typically 0 for normal termination.
    :rtype: int
    """
    # Initial logging setup. This might be reconfigured by command-line options later.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    logging.info("Application main() function entered. Woes version: %s", app_version)

    app = WoesApplication(version=app_version)
    logging.debug("WoesApplication instance created.")

    # app.run() blocks until the application quits.
    # sys.argv is passed to allow Adw.Application to handle standard command-line arguments
    # like --gapplication-service, etc., and also custom options defined by add_main_option.
    logging.info("Calling app.run() with arguments: %s", sys.argv)
    exit_status = app.run(sys.argv)
    logging.info("app.run() finished. Exit status: %s.", exit_status)
    return exit_status


if __name__ == "__main__":
    # This makes the script executable and runs the main function.
    sys.exit(main())

```
