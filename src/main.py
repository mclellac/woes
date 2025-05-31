import sys
import argparse
import logging

# No gi.repository imports at the top level for CLI testability

from .constants import APP_ID, VERSION

# This function is independent of GUI and can be tested easily
def parse_arguments_and_setup_logging(argv):
    """Parses command line arguments and sets up logging."""
    parser = argparse.ArgumentParser(description="Woes application.")
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging."
    )
    args = parser.parse_args(argv[1:]) # Pass only arguments, not script name

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.debug("Debug mode enabled via command line.")
    else:
        logging.basicConfig(level=logging.INFO)
    return args

# --- GUI Application Part ---
_WoesApplication_class_ref = None # Placeholder for the lazily defined class

def _define_and_init_gui_app_class():
    """Initializes GUI toolkit, imports, and defines the Application class."""
    global _WoesApplication_class_ref
    if _WoesApplication_class_ref is not None:
        return

    # Import gi and set versions first
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("GtkSource", "5") # If GtkSource is used by any GUI component

    # Import GUI related modules here
    from gi.repository import Adw, Gio
    from .preferences import Preferences
    from .window import WoesWindow

    class WoesApplication(Adw.Application):
        """The main application singleton class."""

        def __init__(self, version=VERSION, **kwargs):
            super().__init__(
                application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS, **kwargs
            )
            self.version = version
            self.win = None  # Store a reference to the main window

            # Create actions and set accelerators
            self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
            self.create_action("about", self.on_about_action)
            self.create_action("preferences", self.on_preferences_action)
            self.create_action("switch-to-http", self.switch_to_http, ["<primary>1"])
            self.create_action("switch-to-nmap", self.switch_to_nmap, ["<primary>2"])

        def do_activate(self):
            """Called when the application is activated.
            We raise the application's main window, creating it if necessary.
            """
            win = self.props.active_window
            if not win:
                win = WoesWindow(application=self) # WoesWindow is from the outer scope
            win.present()
            self.win = win

        def switch_to_http(self, *args):
            if self.win:
                self.win.stack.set_visible_child(self.win.http_page)

        def switch_to_nmap(self, *args):
            if self.win:
                self.win.stack.set_visible_child(self.win.nmap_page)

        def on_about_action(self, widget, _):
            """Callback for the app.about action."""
            # Adw.AboutWindow is from the outer scope
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
            """Callback for the app.preferences action."""
            # Preferences is from the outer scope
            preferences = Preferences(main_window=self.win)
            preferences.set_transient_for(self.win)
            preferences.present()

        def create_action(self, name, callback, shortcuts=None):
            """Add an application action."""
            # Gio.SimpleAction is from the outer scope
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if shortcuts:
                self.set_accels_for_action(f"app.{name}", shortcuts)

    _WoesApplication_class_ref = WoesApplication


def main(version=VERSION):
    """The application's entry point."""
    args = parse_arguments_and_setup_logging(sys.argv) # CLI part, no gi needed yet

    # GUI part starts here
    # logging.info("Application starting (GUI mode).") # Already logged by parse_...

    _define_and_init_gui_app_class() # This will import gi and define WoesApplication

    app = _WoesApplication_class_ref(version=version) # Use the globally set class reference
    # app.run expects the full sys.argv, including script name
    return app.run(sys.argv)
