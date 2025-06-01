import sys
import logging

# Import gi and set versions first - this needs to be at the top for the class def.
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# gi.require_version("GtkSource", "5") # Only if GtkSource is used by WoesApplication directly

# Import GUI related modules here
from gi.repository import Adw, Gio, GLib  # Added GLib for OptionArg/OptionFlags

from .constants import APP_ID, VERSION
from .preferences import Preferences  # Ensure Preferences is imported
from .window import WoesWindow  # Changed from MinimalWoesWindow


class WoesApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version=VERSION, **kwargs):
        self.version = version  # Simple assignment first
        self.debug_enabled = False  # Initialize debug status early

        # Initial logging setup - will be overridden if --debug is passed
        logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

        # Call super().__init__ before adding main options as per subtask instruction
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.DEFAULT_FLAGS,
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

        return 0  # Indicate success

    def do_activate(self):
        """Called when the application is activated.
        We raise the application's main window, creating it if necessary.
        """
        win = self.props.active_window
        if not win:
            win = WoesWindow(application=self)  # Changed to WoesWindow
        win.present()
        self.win = win

    def switch_to_http(self, *args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("http_page")
        else:
            logging.warning("Cannot switch to http_page: window or stack not available.")

    def switch_to_nmap(self, *args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("nmap_page")
        else:
            logging.warning("Cannot switch to nmap_page: window or stack not available.")

    def switch_to_dns(self, *args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("dns_page")
        else:
            logging.warning("Cannot switch to dns_page: window or stack not available.")

    def switch_to_webscan(self, *args):
        if self.win and hasattr(self.win, 'stack'):
            self.win.stack.set_visible_child_name("webscan_page")
        else:
            logging.warning("Cannot switch to webscan_page: window or stack not available.")

    def on_about_action(self, widget, _):
        """Callback for the app.about action."""
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

    def on_preferences_action(self, widget, _):
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
    # argparse is no longer used here for --debug
    # GLib.Application handles it.

    # Initial log message, will be INFO unless --debug promotes it later
    # Or, it might be DEBUG if basicConfig in __init__ ran and then was overridden by do_handle_local_options
    # To be safe, application startup messages should follow do_handle_local_options if they depend on its logging level
    # However, WoesApplication __init__ runs before do_handle_local_options.

    app = WoesApplication(version=version)
    # sys.argv is passed to app.run(), which handles parsing based on add_main_option
    exit_status = app.run(sys.argv)
    return exit_status
