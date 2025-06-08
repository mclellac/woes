from .window import WoesWindow
from .preferences import Preferences
from .constants import APP_ID, VERSION, RESOURCE_PREFIX, PKGDATADIR  # Import PKGDATADIR
from gi.repository import Adw, Gio, GLib  # Added GLib for OptionArg/OptionFlags
import gi
import sys
import os
import logging

super_early_log_path = "/tmp/super_early_debug.log"
try:
    with open(super_early_log_path, "w") as f_super_early:
        f_super_early.write("src/main.py script execution started (top level).\n")
except Exception as e_super_early:
    print(f"CRITICAL: Failed to write to {super_early_log_path}: {e_super_early}", file=sys.stderr)

print("src/main.py: Script execution started (print)")
early_logger = logging.getLogger("EARLY_STARTUP")
early_logger.setLevel(logging.DEBUG)
if not logging.getLogger().hasHandlers():
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
    logging.getLogger().addHandler(console_handler)
early_logger.info("src/main.py: Script execution started (early_logger)")


gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

# Import GUI related modules here


def _load_gresources_early():
    """Loads GResources and logs detailed information."""
    resource_file_path = os.path.join(PKGDATADIR, "woes.gresource")

    temp_log_file_path_detail = "/tmp/gresource_debug_detail.log"
    with open(temp_log_file_path_detail, "w") as temp_log_detail:
        temp_log_detail.write("Attempting _load_gresources_early()\n")
        temp_log_detail.write(f"PKGDATADIR: {PKGDATADIR}\n")
        temp_log_detail.write(f"RESOURCE_PREFIX: {RESOURCE_PREFIX}\n")
        temp_log_detail.write(f"Resource file path: {resource_file_path}\n")
        temp_log_detail.write(f"Resource file exists: {os.path.exists(resource_file_path)}\n")

        logging.info(
            f"Attempting to load GResource file from: {resource_file_path} "
            f"(derived from PKGDATADIR: {PKGDATADIR})"
            )
        try:
            if os.path.exists(resource_file_path):
                resource = Gio.Resource.load(resource_file_path)
                logging.info(f"Type of 'resource' after load: {type(resource)}")
                temp_log_detail.write(f"Type of 'resource' after load: {type(resource)}\n")
                logging.info(f"Type of 'Gio.Resource': {type(Gio.Resource)}")
                temp_log_detail.write(f"Type of 'Gio.Resource': {type(Gio.Resource)}\n")
                logging.info(f"Attributes of 'Gio.Resource': {dir(Gio.Resource)}")
                temp_log_detail.write(f"Attributes of 'Gio.Resource': {dir(Gio.Resource)}\n")

                if resource:
                    logging.info(f"Attempting to register resource: {resource}")
                    temp_log_detail.write(f"Attempting to register resource: {resource}\n")
                    Gio.Resource._register(resource)
                    logging.info(f"Successfully loaded and registered GResource: {resource_file_path}")
                    temp_log_detail.write(f"Successfully loaded and registered GResource: {resource_file_path}\n")

                    available_resources = resource.enumerate_children(RESOURCE_PREFIX, Gio.ResourceLookupFlags.NONE)
                    logging.debug(f"Available resources under {RESOURCE_PREFIX}: {available_resources}")
                    temp_log_detail.write(f"Available resources under {RESOURCE_PREFIX}: {available_resources}\n")
                    if not available_resources:
                        logging.warning(
                            f"No resources found under prefix {RESOURCE_PREFIX} "
                            f"after loading {resource_file_path}."
                            )
                        temp_log_detail.write(
                            f"No resources found under prefix {RESOURCE_PREFIX} "
                            f"after loading {resource_file_path}.\n"
                            )
                else:
                    logging.error(
                        f"Gio.Resource.load() returned None for {resource_file_path}. Application will now exit.")
                    temp_log_detail.write(f"Gio.Resource.load() returned None for {resource_file_path}.\n")
                    sys.exit(1)
            else:
                logging.error(
                    f"GResource file not found at {resource_file_path} "
                    f"(derived from PKGDATADIR: {PKGDATADIR}). Application will now exit."
                    )
                temp_log_detail.write(f"GResource file not found at {resource_file_path}.\n")
                sys.exit(1)
        except GLib.Error as e:
            logging.error(
                f"Error loading or registering GResource {resource_file_path}: {e}. "
                "Check if the file is a valid GResource bundle. Application will now exit.", exc_info=True
                )
            temp_log_detail.write(f"GLib.Error during GResource loading: {e}\n")
            sys.exit(1)
        except Exception as e:
            logging.error(
                f"An unexpected error occurred during GResource loading: {e}. Application will now exit.",
                exc_info=True)
            temp_log_detail.write(f"Unexpected error during GResource loading: {e}\n")
            sys.exit(1)


_load_gresources_early()


class WoesApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version=VERSION, **kwargs):
        print("src/main.py: WoesApplication.__init__ entered (print)")
        logging.info("src/main.py: WoesApplication.__init__ entered (logging)")
        self.version = version
        self.debug_enabled = False

        logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

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
        logging.debug("WoesApplication.do_handle_local_options: Entered with options: %s", options)
        if options.contains('debug'):
            self.debug_enabled = True
            logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(name)s:%(message)s', force=True)
            logging.debug("Debug mode enabled via command line.")

        logging.debug("WoesApplication.do_handle_local_options: Returning -1")
        return -1

    def do_command_line(self, options):
        """Overrides the do_command_line virtual method."""
        logging.debug("WoesApplication.do_command_line: Entered with options: %s", options)
        self.do_handle_local_options(options.get_options_dict())
        self.activate()
        return 0

    def do_activate(self):
        """Called when the application is activated.
        We raise the application's main window, creating it if necessary.
        """
        print("src/main.py: WoesApplication.do_activate entered (print)")
        logging.info("src/main.py: WoesApplication.do_activate entered (logging)")
        win = self.props.active_window
        if not win:
            logging.debug("WoesApplication.do_activate: Creating WoesWindow...")
            try:
                win = WoesWindow(application=self)
                logging.debug("WoesApplication.do_activate: WoesWindow created.")
            except Exception:
                logging.exception("WoesApplication.do_activate: Error creating WoesWindow instance")
                sys.exit(1)

        if win:
            logging.debug("WoesApplication.do_activate: Calling win.present()...")
            try:
                win.present()
                logging.debug("WoesApplication.do_activate: win.present() called.")
            except Exception:
                logging.exception("WoesApplication.do_activate: Error during win.present()")
            self.win = win
        else:
            logging.error("WoesApplication.do_activate: Window object is None, cannot proceed.")

    def switch_to_http(self, *_args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("http")
        else:
            logging.warning("Cannot switch to http_page: window or stack not available.")

    def switch_to_nmap(self, *_args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("nmap")
        else:
            logging.warning("Cannot switch to nmap_page: window or stack not available.")

    def switch_to_dns(self, *_args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("dns")
        else:
            logging.warning("Cannot switch to dns_page: window or stack not available.")

    def switch_to_webscan(self, *_args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("webscan")
        else:
            logging.warning("Cannot switch to webscan_page: window or stack not available.")

    def on_about_action(self, _widget, _):
        """Callback for the app.about action."""
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

    def on_preferences_action(self, _widget, _):
        if not self.win:
            logging.error("Main window not available for preferences.")
            return
        preferences_dialog = Preferences(main_window=self.win)
        preferences_dialog.present()

    def create_action(self, name, callback, shortcuts=None):
        """Add an application action."""
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version=VERSION):
    """The application's entry point."""
    temp_log_file_path = "/tmp/gresource_debug_entry.log"
    try:
        with open(temp_log_file_path, "w") as temp_log:
            temp_log.write("main() function in src/main.py has been entered.\n")
    except Exception as e:
        print(f"CRITICAL: Failed to write to {temp_log_file_path}: {e}", file=sys.stderr)

    print("src/main.py: main() function entered (print)")
    logging.info("src/main.py: main() function entered (logging)")

    print("src/main.py: main() - Creating WoesApplication instance (print)")
    logging.info("src/main.py: main() - Creating WoesApplication instance (logging)")
    app = WoesApplication(version=version)
    print("src/main.py: main() - WoesApplication instance created (print)")
    logging.info("src/main.py: main() - WoesApplication instance created (logging)")

    print("src/main.py: main() - Calling app.run() (print)")
    logging.info("src/main.py: main() - Calling app.run() (logging)")
    exit_status = app.run(sys.argv)
    print(f"src/main.py: main() - app.run() finished with status {exit_status} (print)")
    logging.info("src/main.py: main() - app.run() finished with status %s (logging)", exit_status)
    return exit_status


if __name__ == '__main__':
    name_main_log_path = "/tmp/name_main_block.log"
    try:
        with open(name_main_log_path, "w") as f_name_main:
            f_name_main.write("__name__ == '__main__' block entered.\n")
    except Exception as e_name_main:
        print(f"CRITICAL: Failed to write to {name_main_log_path}: {e_name_main}", file=sys.stderr)

    sys.exit(main())
