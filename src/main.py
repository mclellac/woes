"""
Main application module for Woes.

Handles application initialization, GResource loading, command-line option
parsing, and launching the main application window and services.
"""

import sys
import os
import logging
from typing import Callable, Optional, Any
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .constants import (
    APP_ID,
    VERSION, # Used as default in main() and WoesApplication
    RESOURCE_PREFIX,
    PKGDATADIR, # Now sourced from constants, which is config-aware
    CONFIG_AVAILABLE, # To determine loading strategy
    APP_WEBSITE_URL,
    APP_LICENSE_TYPE,
    APP_DESCRIPTION,
    APP_ISSUES_URL,
)


# GResource loading must happen before any modules that use Gtk.Template are imported.
def _load_gresources_early():
    """
    Load GResources early in the application startup.

    This function is critical for ensuring that :class:`Gtk.Template`-based widgets
    can be loaded correctly. It attempts to load ``woes.gresource`` from
    either a development path or an installed path. Exits the application
    if the GResource file cannot be found or loaded.
    """
    logger = logging.getLogger(__name__)
    resource_file_path = None

    # Path for true installed versions (PKGDATADIR comes from src.config via constants.py)
    configured_install_path = os.path.join(PKGDATADIR, "woes.gresource") # PKGDATADIR is from constants

    # --- Development / Fallback Paths ---
    dev_path_from_env = None
    project_root_env = os.environ.get("WOES_PROJECT_ROOT")
    if project_root_env:
        dev_path_from_env = os.path.normpath(os.path.join(project_root_env, "build", "src", "woes.gresource"))
        logger.debug(f"WOES_PROJECT_ROOT is set to: {project_root_env}")
        logger.debug(f"Derived dev_path_from_env: {dev_path_from_env}")
    else:
        logger.debug("WOES_PROJECT_ROOT environment variable is not set.")

    script_dir = os.path.dirname(os.path.abspath(__file__)) # directory of main.py
    logger.debug(f"script_dir (__file__ directory): {script_dir}")
    project_root_from_file_heuristic = os.path.abspath(os.path.join(script_dir, ".."))
    dev_path_from_file_heuristic = os.path.normpath(os.path.join(project_root_from_file_heuristic, "build", "src", "woes.gresource"))
    logger.debug(f"dev_path_from_file_heuristic: {dev_path_from_file_heuristic}")

    # Standard installed path for gresource (from user's logs for their prefix)
    # This is used as a targeted fix if running from site-packages without src.config
    standard_install_gresource_path = "/usr/local/share/woes/woes.gresource"
    logger.debug(f"Standard installed gresource path (hardcoded fallback check): {standard_install_gresource_path}")


    if CONFIG_AVAILABLE: # True if src.config was imported - indicates an "installed" setup
        if os.path.exists(configured_install_path):
            logger.info("Using GResource from configured PKGDATADIR (src.config found): %s", configured_install_path)
            resource_file_path = configured_install_path
        # Optional: Fallback to WOES_PROJECT_ROOT if config exists but path is wrong (for weird setups)
        elif dev_path_from_env and os.path.exists(dev_path_from_env):
            logger.warning("src.config found, but GResource not in PKGDATADIR. Using WOES_PROJECT_ROOT dev path: %s", dev_path_from_env)
            resource_file_path = dev_path_from_env
        else:
            logger.critical("src.config found (installed mode), but GResource not found at configured path '%s'. If WOES_PROJECT_ROOT is set, its path ('%s') was also invalid. App will exit.",
                            configured_install_path, dev_path_from_env if dev_path_from_env else "N/A")
            sys.exit(1)
    else: # CONFIG_AVAILABLE is False (dev/uninstalled mode or installed but missing src.config)
        is_running_from_site_packages = "site-packages" in script_dir.lower() or \
                                        "dist-packages" in script_dir.lower()
        logger.debug(f"src.config not found. Is running from site-packages: {is_running_from_site_packages}")

        if dev_path_from_env and os.path.exists(dev_path_from_env):
            logger.info("Using development GResource from WOES_PROJECT_ROOT (src.config not found): %s", dev_path_from_env)
            resource_file_path = dev_path_from_env
        elif is_running_from_site_packages and os.path.exists(standard_install_gresource_path):
            # PATCH: If running from site-packages and src.config is missing,
            # and WOES_PROJECT_ROOT didn't work, explicitly check the known standard install path.
            logger.info("Using GResource from standard install path (running from site-packages, src.config not found, WOES_PROJECT_ROOT not used/invalid): %s", standard_install_gresource_path)
            resource_file_path = standard_install_gresource_path
        elif not is_running_from_site_packages and os.path.exists(dev_path_from_file_heuristic):
            # For true local dev (not in site-packages) if WOES_PROJECT_ROOT and standard install path didn't apply
            logger.info("Using development GResource from __file__ heuristic (not in site-packages, src.config not found, WOES_PROJECT_ROOT not used/invalid): %s", dev_path_from_file_heuristic)
            resource_file_path = dev_path_from_file_heuristic
        else: # All prioritized options failed
            err_msg_parts = ["Critical: Could not find woes.gresource (src.config not found)."]
            if project_root_env:
                err_msg_parts.append(f"Checked WOES_PROJECT_ROOT ('{project_root_env}') -> path: '{dev_path_from_env if dev_path_from_env else 'N/A'}'.")
            else:
                err_msg_parts.append("WOES_PROJECT_ROOT environment variable was not set.")

            if is_running_from_site_packages:
                err_msg_parts.append(f"As running from site-packages, also checked standard install path: '{standard_install_gresource_path}'.")

            err_msg_parts.append(f"Also checked __file__ heuristic path: '{dev_path_from_file_heuristic}'.")
            err_msg_parts.append("Ensure woes.gresource is at one of these locations. For development, setting WOES_PROJECT_ROOT is recommended. For installed versions, ensure 'src/config.py' is generated by Meson and 'woes.gresource' is in the correct share directory (e.g., /usr/local/share/woes/). App will exit.")
            logger.critical(" ".join(err_msg_parts))
            sys.exit(1)

    if not resource_file_path: # Should be caught by sys.exit above
        logger.critical("Failed to determine a valid GResource path. This state should not be reached. App will exit.")
        sys.exit(1)

    logging.info("Attempting to load GResource file from: %s", resource_file_path)
    try:
        resource = Gio.Resource.load(resource_file_path)
        if not resource:
            logging.critical(
                "Gio.Resource.load() returned None for %s. Application will now exit.",
                resource_file_path,
            )
            sys.exit(1)

        Gio.Resource._register(resource)
        logging.info("Successfully loaded and registered GResource: %s", resource_file_path)

        available_resources = resource.enumerate_children(RESOURCE_PREFIX, Gio.ResourceLookupFlags.NONE)
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
    except Exception as e:
        logging.critical(
            "An unexpected error occurred during GResource loading: %s. Application will now exit.",
            e,
            exc_info=True,
        )
        sys.exit(1)


_load_gresources_early()  # Call this as early as possible
# Ensure Gtk.Template custom widgets defined in these modules are registered

from .window import WoesWindow
from .preferences import Preferences


class WoesApplication(Adw.Application):
    """
    The main application singleton class for Woes.

        Manages the application lifecycle, actions, and the main window.
    """

    def __init__(self, version: str = VERSION, **kwargs: Any):
        """
        Initialize the WoesApplication.

        :param version: The application version. Defaults to :const:`.VERSION`.
        :type version: str
        :param kwargs: Additional keyword arguments for :class:`Adw.Application`.
        :type kwargs: Any
        """
        self.version: str = version
        self.debug_enabled: bool = False
        self.win: Optional[WoesWindow] = None

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
                 allowing further processing (like :meth:`do_command_line`) to occur.
        :rtype: int
        """
        if options.contains("debug"):
            self.debug_enabled = True
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(levelname)s:%(name)s:%(message)s",
                force=True,
            )
            logging.debug("Debug mode enabled via command line.")
        return -1  # Indicates that command line processing is not finished

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """
        Override the :meth:`Gio.Application.do_command_line` virtual method to handle command line arguments.

        This method processes options passed to the application and then
        activates the application.

        :param command_line: The :class:`Gio.ApplicationCommandLine` object.
        :type command_line: Gio.ApplicationCommandLine
        :return: The exit status of the command line processing. 0 for success.
        :rtype: int
        """
        options: GLib.VariantDict = command_line.get_options_dict()
        if self.handle_local_options(options):
            logging.debug("Local options handled.")

        self.activate()
        return 0

    def do_activate(self) -> None:
        """
        Create and present the main application window.

        This method is called when the application is activated.
        It ensures the main window (:class:`.window.WoesWindow`) is created and shown.
        If the window cannot be created or presented, the application may exit.
        """
        win: Optional[WoesWindow] = self.props.active_window
        if not win:
            try:
                win = WoesWindow(application=self)
            except Exception:
                logging.exception("WoesApplication.do_activate: Error creating WoesWindow instance")
                sys.exit(1)

        if win:
            try:
                win.present()
            except Exception:
                logging.exception("WoesApplication.do_activate: Error during win.present()")
            self.win = win
        else:
            logging.error("WoesApplication.do_activate: Window object is None after creation attempt, cannot proceed.")

    def _switch_to_page(self, page_name: str) -> None:
        """
        Switch the main window's view to the specified page.

        :param page_name: The name of the page to switch to (e.g., "http", "nmap").
        :type page_name: str
        """
        if self.win and hasattr(self.win, "stack"):
            self.win.stack.set_visible_child_name(page_name)
        else:
            logging.warning(f"Cannot switch to {page_name}_page: window or stack not available.")

    def switch_to_http(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("http")

    def switch_to_nmap(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("nmap")

    def switch_to_dns(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("dns")

    def switch_to_webscan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._switch_to_page("webscan")

    def _trigger_page_action(self, expected_page_name: str, action_method_name: str, action_description: str) -> None:
        if not self.win or not hasattr(self.win, "stack"):
            logging.warning(f"{action_description} action: Window or stack not available.")
            return
        current_page_name: str = self.win.stack.get_visible_child_name()
        visible_stack_page: Optional[Adw.ViewStackPage] = self.win.stack.get_visible_child()
        if current_page_name == expected_page_name and visible_stack_page:
            status_page: Optional[Adw.StatusPage] = visible_stack_page.get_child()
            if not status_page:
                logging.warning(f"{action_description} action: StatusPage not found for {current_page_name}.")
                return
            page_container_widget: Optional[Gtk.Widget] = status_page.get_child()
            if not page_container_widget:
                logging.warning(f"{action_description} action: Page container widget (child of StatusPage) not found for {current_page_name}.")
                return
            actual_page_object: Optional[Gtk.Widget] = None
            if hasattr(page_container_widget, "get_first_child") and callable(page_container_widget.get_first_child):
                candidate: Optional[Gtk.Widget] = page_container_widget.get_first_child()
                if hasattr(candidate, action_method_name):
                    actual_page_object = candidate
                elif hasattr(page_container_widget, action_method_name): # fallback if GtkBox itself is the page
                    actual_page_object = page_container_widget
            elif hasattr(page_container_widget, action_method_name): # if AdwStatusPage child is directly the page
                actual_page_object = page_container_widget

            if actual_page_object and hasattr(actual_page_object, action_method_name):
                getattr(actual_page_object, action_method_name)()
            else:
                logging.warning(f"Could not trigger page action '{action_method_name}' on page '{current_page_name}'.")

        elif current_page_name == expected_page_name:
            logging.warning(f"{action_description} action: AdwViewStackPage for {current_page_name} is None.")

    def on_page_action_http_fetch(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._trigger_page_action("http", "trigger_fetch", "HTTP fetch")

    def on_page_action_nmap_scan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._trigger_page_action("nmap", "trigger_scan", "Nmap scan")

    def on_page_action_dns_lookup(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._trigger_page_action("dns", "trigger_lookup", "DNS lookup")

    def on_page_action_webscan_scan(self, _action: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        self._trigger_page_action("webscan", "trigger_scan", "Webscan scan")

    def on_about_action(self, _widget: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        about = self._create_about_window()
        if self.props.active_window:
            about.set_transient_for(self.props.active_window)
        about.present()

    def _create_about_window(self) -> Adw.AboutWindow:
        return Adw.AboutWindow(
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

    def on_preferences_action(self, _widget: Gio.SimpleAction, _param: Optional[GLib.Variant]) -> None:
        if not self.win:
            logging.error("Main window not available for preferences.")
            return
        preferences_dialog = Preferences(main_window=self.win)
        preferences_dialog.present()

    def create_action(self, name: str, callback: Callable[..., Any], shortcuts: Optional[list[str]] = None) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version: str = VERSION) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    run_settings_test = os.environ.get("WOES_RUN_SETTINGS_TEST", "0") == "1"
    if run_settings_test:
        logging.info("WOES_RUN_SETTINGS_TEST is set. Running GSettings test in main().")
        print("Attempting to directly access GSettings for test...")
        try:
            settings = Gio.Settings(schema_id=APP_ID)
            test_setting_value = settings.get_string("default-user-agent-title")
            print(f"Successfully read 'default-user-agent-title': {test_setting_value}")
            test_theme_value = settings.get_string("theme-preference")
            print(f"Successfully read 'theme-preference': {test_theme_value}")
            print("Settings test passed.")
        except Exception as e:
            print(f"Error during settings test: {e}")
            sys.exit(1)
    else:
        logging.info("Skipping GSettings test in main() (WOES_RUN_SETTINGS_TEST not set to '1').")

    app = WoesApplication(version=version)
    exit_status: int = app.run(sys.argv)
    logging.info("Application exited with status %s.", exit_status)
    return exit_status
