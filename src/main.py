# main.py
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from .constants import APP_ID, DEFAULT_VERSION
from .preferences import Preferences
from .window import WoesWindow


class WoesApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version=DEFAULT_VERSION):
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.version = version
        self.win = None  # Store a reference to the main window

        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("about", self.on_about_action)
        self.create_action("preferences", self.on_preferences_action)

    def do_activate(self):
        """Called when the application is activated.

        We raise the application's main window, creating it if
        necessary.
        """
        win = self.props.active_window
        if not win:
            win = WoesWindow(application=self)
        win.present()
        self.win = win

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
        """Callback for the app.preferences action."""
        preferences = Preferences(main_window=self.win)
        preferences.set_transient_for(self.win)
        preferences.present()

    def create_action(self, name, callback, shortcuts=None):
        """Add an application action."""
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version=DEFAULT_VERSION):
    """The application's entry point."""
    app = WoesApplication(version)
    return app.run(sys.argv)
