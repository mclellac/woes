"""
Main application module for Woes.

Handles application initialization, GResource loading, command-line option
parsing, and launching the main application window and services.
"""
import sys
import os
import logging
from typing import Callable, List, Optional
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib

from .constants import (
    APP_ID,
    VERSION,
    RESOURCE_PREFIX,
    PKGDATADIR,
    APP_WEBSITE_URL,
    APP_LICENSE_TYPE,
    APP_DESCRIPTION,
    APP_ISSUES_URL,
)

# GResource loading must happen before any modules that use Gtk.Template are imported.
def _load_gresources_early():
    """
    Load GResources early in the application startup.

    This function is critical for ensuring that Gtk.Template-based widgets
    can be loaded correctly. It attempts to load ``woes.gresource`` from
    either a development path or an installed path. Exits the application
    if the GResource file cannot be found or loaded.
    """
    # Path for installed version
    installed_resource_path = os.path.join(PKGDATADIR, "woes.gresource")

    # Path for running from source tree (e.g., relative to src/main.py)
    # Assumes main.py is in src/ and resources are in ../build/src/ relative to main.py's directory.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Example: resolves to /app/build/src/woes.gresource if script_dir is /app/src
    dev_resource_path = os.path.normpath(os.path.join(script_dir, "..", "build", "src", "woes.gresource"))

    resource_file_path = None
    if os.path.exists(dev_resource_path):
        logging.info("Development GResource path found: %s", dev_resource_path)
        resource_file_path = dev_resource_path
    elif os.path.exists(installed_resource_path):
        logging.info("Installed GResource path found: %s", installed_resource_path)
        resource_file_path = installed_resource_path
    else:
        logging.critical(
            "GResource file not found at development path (%s) or installed path (%s). Application will now exit.",
            dev_resource_path,
            installed_resource_path,
        )
        sys.exit(1)

    logging.info(
        "Attempting to load GResource file from: %s",
        resource_file_path
    )

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


_load_gresources_early() # Call this as early as possible

# Now import other application modules
from .window import WoesWindow
from .preferences import Preferences


class WoesApplication(Adw.Application):
    """
    The main application singleton class for Woes.

    Manages the application lifecycle, actions, and the main window.
    """

    def __init__(self, version: str = VERSION, **kwargs):
        """
        Initialize the WoesApplication.

        :param version: The application version.
        :type version: str
        :param kwargs: Additional keyword arguments for :class:`Adw.Application`.
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

        # Page-specific actions
        self.create_action("page-action-http-fetch", self.on_page_action_http_fetch, ["<Alt>F"])
        self.create_action("page-action-nmap-scan", self.on_page_action_nmap_scan, ["<Alt>S"])
        self.create_action("page-action-dns-lookup", self.on_page_action_dns_lookup, ["<Alt>L"])
        self.create_action("page-action-webscan-scan", self.on_page_action_webscan_scan, ["<Alt>W"])

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        """
        Handle local command-line options.

        Currently supports a ``--debug`` option to enable debug logging.

        :param options: A :class:`GLib.VariantDict` containing the command-line options.
        :type options: GLib.VariantDict
        :return: -1 to indicate that command line processing is not finished,
                 allowing further processing (like ``do_command_line``) to occur.
        :rtype: int
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
        """
        Override the do_command_line virtual method to handle command line arguments.

        This method processes options passed to the application and then
        activates the application.

        :param command_line: The :class:`Gio.ApplicationCommandLine` object.
        :type command_line: Gio.ApplicationCommandLine
        :return: The exit status of the command line processing. 0 for success.
        :rtype: int
        """
        options = command_line.get_options_dict()
        if self.handle_local_options(options): # do_handle_local_options is called by this
            logging.debug("Local options handled.")

        self.activate()
        return 0

    def do_activate(self):
        """
        Create and present the main application window.

        This method is called when the application is activated.
        It ensures the main window (:class:`.window.WoesWindow`) is created and shown.
        If the window cannot be created or presented, the application may exit.
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

    def _switch_to_page(self, page_name: str):
        """
        Switch the main window's view to the specified page.

        :param page_name: The name of the page to switch to (e.g., "http", "nmap").
        :type page_name: str
        """
        if self.win and hasattr(self.win, "stack"):
            self.win.stack.set_visible_child_name(page_name)
        else:
            logging.warning(f"Cannot switch to {page_name}_page: window or stack not available.")

    def switch_to_http(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Switch the main window's view to the HTTP page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._switch_to_page("http")

    def switch_to_nmap(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Switch the main window's view to the Nmap page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._switch_to_page("nmap")

    def switch_to_dns(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Switch the main window's view to the DNS page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._switch_to_page("dns")

    def switch_to_webscan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Switch the main window's view to the WebScan page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._switch_to_page("webscan")

    def _trigger_page_action(self, expected_page_name: str, action_method_name: str, action_description: str):
        """
        Trigger an action on the currently visible page if it matches the expected page.

        :param expected_page_name: The name of the page on which the action is expected to occur.
        :type expected_page_name: str
        :param action_method_name: The name of the method to call on the page object.
        :type action_method_name: str
        :param action_description: A human-readable description of the action for logging.
        :type action_description: str
        """
        if not self.win or not hasattr(self.win, "stack"):
            logging.warning(f"{action_description} action: Window or stack not available.")
            return

        current_page_name = self.win.stack.get_visible_child_name()
        visible_stack_page = self.win.stack.get_visible_child() # This is AdwViewStackPage

        if current_page_name == expected_page_name and visible_stack_page:
            status_page = visible_stack_page.get_child() # This is AdwStatusPage
            if not status_page:
                logging.warning(f"{action_description} action: StatusPage not found for {current_page_name}.")
                return

            # Child of AdwStatusPage. Based on the previous error, this is likely the GtkBox
            # (e.g., the one with id="HttpPage" in the UI file) that directly contains
            # the actual page class instance (e.g., an instance of your HttpPage class).
            page_container_widget = status_page.get_child()
            if not page_container_widget:
                logging.warning(f"{action_description} action: Page container widget (child of StatusPage) not found for {current_page_name}.")
                return

            actual_page_object = None
            # Scenario 1: The page_container_widget is the GtkBox, and its first child is the page instance.
            if hasattr(page_container_widget, "get_first_child") and callable(getattr(page_container_widget, "get_first_child")):
                # This assumes the GtkBox (like <object class="GtkBox" id="HttpPage">) is page_container_widget
                # and its child (<object class="HttpPage">) is the actual page.
                candidate = page_container_widget.get_first_child()
                if hasattr(candidate, action_method_name): # Check if this candidate has the method
                    actual_page_object = candidate
                elif hasattr(page_container_widget, action_method_name): # Check if page_container_widget itself has the method
                    # This could happen if AdwClampScrollable was the child of AdwStatusPage,
                    # and page_container_widget is that AdwClampScrollable, and *its* child is the actual page object.
                    # However, AdwClampScrollable itself does not have get_first_child.
                    # This path is less likely given the error, but as a fallback.
                    # More likely, if the first child didn't have the method, the structure is different or the page isn't the first child.
                    # A more robust check here would be to verify type, e.g. isinstance(page_container_widget, Gtk.Box)
                    # For now, we trust get_first_child if available on page_container_widget.
                    # If that first child isn't our page, then we might be wrong about the structure.
                    # Let's refine: if page_container_widget.get_first_child() exists and has the method, use it.
                    # Else, check if page_container_widget itself has the method (e.g. if it IS the actual page object)
                    actual_page_object = candidate # Keep candidate from above for now.
                    if not actual_page_object or not hasattr(actual_page_object, action_method_name):
                         # If the first child doesn't have the method, check if page_container_widget itself is the page
                        if hasattr(page_container_widget, action_method_name):
                            actual_page_object = page_container_widget
                            logging.debug(f"Child of StatusPage (type: {type(page_container_widget)}) appears to be the actual page object itself.")
                        else:
                             logging.warning(f"Neither page_container_widget (type: {type(page_container_widget)}) nor its first_child has method '{action_method_name}'.")
                             return


            # Scenario 2: The page_container_widget IS the actual page instance.
            # This could happen if AdwStatusPage's child is set directly to the HttpPage/NmapPage instance.
            elif hasattr(page_container_widget, action_method_name):
                actual_page_object = page_container_widget
                logging.debug(f"Child of StatusPage (type: {type(page_container_widget)}) appears to be the actual page object itself (no get_first_child).")
            else:
                logging.warning(f"Page container (type: {type(page_container_widget)}) does not have get_first_child and is not the page object. Structure: AdwViewStackPage -> AdwStatusPage -> ?")
                return

            if actual_page_object:
                if hasattr(actual_page_object, action_method_name):
                    logging.debug(f"Attempting to call {action_method_name} on {type(actual_page_object)} for page {current_page_name}")
                    getattr(actual_page_object, action_method_name)()
                else:
                    # This should ideally not be reached if the above logic is correct
                    logging.warning(f"Retrieved actual page object for {current_page_name} (type: {type(actual_page_object)}), but it does not have method '{action_method_name}'. This indicates an issue in widget retrieval or page class structure.")
            else:
                logging.warning(f"Could not retrieve actual page object for {current_page_name} after checks.")

        elif current_page_name == expected_page_name: # visible_stack_page is None
            logging.warning(f"{action_description} action: AdwViewStackPage for {current_page_name} is None.")
        # No warning if it's not the expected_page_name page, as the action is specific to that page.

    def on_page_action_http_fetch(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Handle the 'page-action-http-fetch' action.

        This action triggers the fetch operation on the currently visible HTTP page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._trigger_page_action("http", "trigger_fetch", "HTTP fetch")

    def on_page_action_nmap_scan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Handle the 'page-action-nmap-scan' action.

        This action triggers the scan operation on the currently visible Nmap page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._trigger_page_action("nmap", "trigger_scan", "Nmap scan")

    def on_page_action_dns_lookup(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Handle the 'page-action-dns-lookup' action.

        This action triggers the lookup operation on the currently visible DNS page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._trigger_page_action("dns", "trigger_lookup", "DNS lookup")

    def on_page_action_webscan_scan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Handle the 'page-action-webscan-scan' action.

        This action triggers the scan operation on the currently visible WebScan page.

        :param _action: The action that triggered this handler.
        :type _action: Gio.SimpleAction
        :param _param: Optional parameter for the action (unused).
        :type _param: Optional[GLib.Variant]
        """
        self._trigger_page_action("webscan", "trigger_scan", "Webscan scan")

    def on_about_action(self, _widget: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Handle the 'about' action activation.

        Displays the application's About dialog.

        :param _widget: The :class:`Gio.SimpleAction` that was activated.
        :type _widget: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter (unused).
        :type _param: Optional[GLib.Variant]
        """
        about = self._create_about_window()
        # Ensure there's an active window before making it transient for
        # In a typical application flow, active_window would be the main WoesWindow.
        # For testing, it might be None if do_activate wasn't fully run,
        # or if a dummy window needs to be set on the application.
        if self.props.active_window:
            about.set_transient_for(self.props.active_window)
        about.present()

    def _create_about_window(self) -> Adw.AboutWindow:
        """
        Create and configure the :class:`Adw.AboutWindow`.

        :return: The configured About Window.
        :rtype: Adw.AboutWindow
        """
        # Note: 'transient_for' is typically set by the caller (on_about_action)
        # if an active window exists. If direct testing _create_about_window,
        # transient_for might be None unless a window is explicitly managed for the test.
        return Adw.AboutWindow( # type: ignore
            application_name="woes",
            application_icon=APP_ID,
            developer_name="Carey McLelland",
            version=self.version,
            developers=["Carey McLelland"],
            copyright="© 2025 Carey McLelland",
            website=APP_WEBSITE_URL,
            license_type=APP_LICENSE_TYPE,
            comments=APP_DESCRIPTION,
            issue_url=APP_ISSUES_URL,
        )

    def on_preferences_action(self, _widget: Gio.SimpleAction, _param: Optional[GLib.Variant]):
        """
        Handle the 'preferences' action activation.

        Displays the application's Preferences dialog.

        :param _widget: The :class:`Gio.SimpleAction` that was activated.
        :type _widget: Gio.SimpleAction
        :param _param: Optional :class:`GLib.Variant` parameter (unused).
        :type _param: Optional[GLib.Variant]
        """
        if not self.win:
            logging.error("Main window not available for preferences.")
            return
        preferences_dialog = Preferences(main_window=self.win)
        preferences_dialog.present()

    def create_action(self, name: str, callback: Callable, shortcuts: Optional[List[str]] = None):
        """
        Create and add a Gio.SimpleAction to the application.

        :param name: The name of the action (e.g., "quit", "about").
        :type name: str
        :param callback: The function to call when the action is activated.
        :type callback: Callable
        :param shortcuts: An optional list of keyboard shortcuts for the action
                          (e.g., ``["<primary>q"]``).
        :type shortcuts: Optional[List[str]]
        """
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version: str = VERSION) -> int:
    """
    Run the Woes application.

    This is the main entry point for the application. It initializes
    logging, creates the :class:`WoesApplication` instance, and runs it.

    :param version: The version string of the application.
    :type version: str
    :return: The exit status of the application.
    :rtype: int
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
