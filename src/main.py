import sys
import os  # Added for path manipulation
import logging

# Ultra-early logging setup
# Super early file log test
super_early_log_path = "/tmp/super_early_debug.log"
try:
    with open(super_early_log_path, "w") as f_super_early:
        f_super_early.write("src/main.py script execution started (top level).\n")
except Exception as e_super_early:
    print(f"CRITICAL: Failed to write to {super_early_log_path}: {e_super_early}", file=sys.stderr)

print("src/main.py: Script execution started (print)")
# Attempt to get a logger and ensure it can output,
# without interfering too much with subsequent app-level basicConfig.
early_logger = logging.getLogger("EARLY_STARTUP")
early_logger.setLevel(logging.DEBUG)
# Add a handler if no handlers are configured for the root logger yet
if not logging.getLogger().hasHandlers():
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
    logging.getLogger().addHandler(console_handler)  # Add to root to catch all
    # Or add specifically to early_logger if preferred: early_logger.addHandler(console_handler)
early_logger.info("src/main.py: Script execution started (early_logger)")


import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# gi.require_version("GtkSource", "5") # Only if GtkSource is used by WoesApplication directly

# Import GUI related modules here
from gi.repository import Adw, Gio, GLib  # Added GLib for OptionArg/OptionFlags
from .constants import APP_ID, VERSION, RESOURCE_PREFIX, PKGDATADIR  # Import PKGDATADIR


def _load_gresources_early():
    """Loads GResources and logs detailed information."""
    # Construct the full path to woes.gresource using PKGDATADIR
    resource_file_path = os.path.join(PKGDATADIR, "woes.gresource")

    # Create a temporary log file for GResource loading details
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
                    logging.error(f"Gio.Resource.load() returned None for {resource_file_path}.")
                    temp_log_detail.write(f"Gio.Resource.load() returned None for {resource_file_path}.\n")
            else:
                logging.error(
                    f"GResource file not found at {resource_file_path} "
                    f"(derived from PKGDATADIR: {PKGDATADIR})."
                )
                temp_log_detail.write(f"GResource file not found at {resource_file_path}.\n")
        except GLib.Error as e:
            logging.error(
                f"Error loading or registering GResource {resource_file_path}: {e}. "
                "Check if the file is a valid GResource bundle.", exc_info=True
            )
            temp_log_detail.write(f"GLib.Error during GResource loading: {e}\n")
        except Exception as e:
            logging.error(f"An unexpected error occurred during GResource loading: {e}", exc_info=True)
            temp_log_detail.write(f"Unexpected error during GResource loading: {e}\n")


# Call GResource loading very early
_load_gresources_early()


# Now import other local modules that might use Gtk.Template
from .preferences import Preferences
from .window import WoesWindow


class WoesApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version=VERSION, **kwargs):
        print("src/main.py: WoesApplication.__init__ entered (print)")
        # Use the already configured logger or default from basicConfig if this is hit first
        logging.info("src/main.py: WoesApplication.__init__ entered (logging)")
        self.version = version  # Simple assignment first
        self.debug_enabled = False  # Initialize debug status early

        # Initial logging setup - will be overridden if --debug is passed
        logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

        # Call super().__init__ before adding main options as per subtask instruction
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            **kwargs
        )

        # Add command line options after super().__init__
        self.add_main_option(
            "debug",
            ord("d"),  # Using 'd' as a short option for debug
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Enable debug logging",
            None
        )

        self.win = None  # Store a reference to the main window

        # Create actions and set accelerators
        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("about", self.on_about_action)
        self.create_action("preferences", self.on_preferences_action)  # Uncommented
        self.create_action("switch-to-http", self.switch_to_http, ["<primary>1"])  # Uncommented
        self.create_action("switch-to-nmap", self.switch_to_nmap, ["<primary>2"])  # Uncommented
        self.create_action("switch-to-dns", self.switch_to_dns, ["<primary>3"])  # Added
        self.create_action("switch-to-webscan", self.switch_to_webscan, ["<primary>4"])  # Added


    def do_handle_local_options(self, options):
        logging.debug("WoesApplication.do_handle_local_options: Entered with options: %s", options)
        # This method is called after options are parsed
        if options.contains('debug'):
            # The presence of 'debug' key means --debug or -d was passed
            # No need to check its boolean value as GLib.OptionArg.NONE implies a flag
            self.debug_enabled = True
            # Reconfigure logging for DEBUG level
            # force=True (Python 3.8+) ensures this overrides previous basicConfig
            logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(name)s:%(message)s', force=True)
            logging.debug("Debug mode enabled via command line.")

        # If not debug, the INFO level set in __init__ remains.
        # No need to explicitly set logging.INFO here unless changing format or other settings.

        logging.debug("WoesApplication.do_handle_local_options: Returning -1")
        return -1  # Indicate normal activation should proceed

    def do_command_line(self, options):
        """Overrides the do_command_line virtual method."""
        logging.debug("WoesApplication.do_command_line: Entered with options: %s", options)
        self.do_handle_local_options(options.get_options_dict())  # Ensure options are passed correctly
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
                win = WoesWindow(application=self)  # Changed to WoesWindow
                logging.debug("WoesApplication.do_activate: WoesWindow created.")
            except Exception:  # F841: local variable 'e' is assigned to but never used
                logging.exception("WoesApplication.do_activate: Error creating WoesWindow instance")
                # Exit or handle critical failure, as the app cannot run without a window
                sys.exit(1)

        if win:  # Ensure win is not None if creation failed
            logging.debug("WoesApplication.do_activate: Calling win.present()...")
            try:
                win.present()
                logging.debug("WoesApplication.do_activate: win.present() called.")
            except Exception:  # F841: local variable 'e' is assigned to but never used
                logging.exception("WoesApplication.do_activate: Error during win.present()")
            self.win = win
        else:
            logging.error("WoesApplication.do_activate: Window object is None, cannot proceed.")
            # Optionally, exit here too if window creation failed and wasn't handled above
            # sys.exit(1)


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
        # preferences_dialog.set_transient_for(self.win)  # Already set in Preferences.__init__

    def create_action(self, name, callback, shortcuts=None):
        """Add an application action."""
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version=VERSION):
    """The application's entry point."""
    # Simplified temp log write, as one of the very first actions in main()
    temp_log_file_path = "/tmp/gresource_debug_entry.log"  # This specific log is less critical now
    try:
        with open(temp_log_file_path, "w") as temp_log:
            temp_log.write("main() function in src/main.py has been entered.\n")
    except Exception as e:
        print(f"CRITICAL: Failed to write to {temp_log_file_path}: {e}", file=sys.stderr)

    print("src/main.py: main() function entered (print)")
    logging.info("src/main.py: main() function entered (logging)")

    # GResource loading is now done by _load_gresources_early() before this point.
    # argparse is no longer used here for --debug
    # GLib.Application handles it.

    print("src/main.py: main() - Creating WoesApplication instance (print)")
    logging.info("src/main.py: main() - Creating WoesApplication instance (logging)")
    app = WoesApplication(version=version)
    print("src/main.py: main() - WoesApplication instance created (print)")
    logging.info("src/main.py: main() - WoesApplication instance created (logging)")

    # sys.argv is passed to app.run(), which handles parsing based on add_main_option
    print("src/main.py: main() - Calling app.run() (print)")
    logging.info("src/main.py: main() - Calling app.run() (logging)")
    exit_status = app.run(sys.argv)
    print(f"src/main.py: main() - app.run() finished with status {exit_status} (print)")
    logging.info("src/main.py: main() - app.run() finished with status %s (logging)", exit_status)
    return exit_status

if __name__ == '__main__':
    # This ensures main() is called when running the script directly
    # e.g., python -m src.main
    # Another temp log before calling main()
    name_main_log_path = "/tmp/name_main_block.log"
    try:
        with open(name_main_log_path, "w") as f_name_main:
            f_name_main.write("__name__ == '__main__' block entered.\n")
    except Exception as e_name_main:
        print(f"CRITICAL: Failed to write to {name_main_log_path}: {e_name_main}", file=sys.stderr)

    sys.exit(main())
