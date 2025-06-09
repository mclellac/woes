"""Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import subprocess
import re
"""Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import logging
from typing import Optional # Added for type hinting

import gi
from gi.repository import Gtk, Adw, Gio, GLib, GObject # Added GObject

from .constants import RESOURCE_PREFIX

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.Bin):
    """Container for web scanning, managing an AdwToastOverlay and AdwPreferencesPage."""

    __gtype_name__ = "WebScanPage"

    toast_overlay_internal = Gtk.Template.Child()
    preferences_page_content = Gtk.Template.Child() # The AdwPreferencesPage
    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def on_scan_button_clicked(self, _widget: Gtk.Button):
        """Handle the 'Scan' button click event.

        Validates the URL entered by the user, then initiates an asynchronous
        Nikto scan task if the URL is valid. Disables the scan button during
        the scan.

        Args:
        ----
            _widget: The Gtk.Button that was clicked.

        """
        target_url = self.url_entry.get_text()

        # URL validation
        url_pattern = re.compile(
            r"^(?:(?:https?|ftp)://)?(?:\S+(?::\S*)?@)?"  # Scheme and optional user:pass
            r"(?:(?:[1-9]\d?|1\d\d|2[01]\d|22[0-3])"  # IPv4 part 1
            r"(?:\.(?:1?\d{1,2}|2[0-4]\d|25[0-5])){2}"  # IPv4 part 2 & 3
            r"(?:\.(?:[1-9]\d?|1\d\d|2[0-4]\d|25[0-4]))|"  # IPv4 part 4
            r"(?:(?:[a-z¡-￿0-9]-*)*[a-z¡-￿0-9]+)"  # Domain name parts
            r"(?:\.(?:[a-z¡-￿0-9]-*)*[a-z¡-￿0-9]+)*"  # Domain name parts
            r"(?:\.(?:[a-z¡-￿]{2,}))\.?)"  # TLD
            r"(?::\d{2,5})?"  # Optional port
            r"(?:[/?#]\S*)?$",  # Optional path, query, fragment
            re.IGNORECASE,
        )
        if not target_url:
            toast = Adw.Toast.new("Target URL cannot be empty.")
            self.toast_overlay_internal.add_toast(toast)
            return

        if not url_pattern.match(target_url):
            toast = Adw.Toast.new("Invalid URL format. Please enter a valid URL.")
            self.toast_overlay_internal.add_toast(toast)
            return

        buffer = self.results_textview.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n")

        self.scan_button.set_sensitive(False)

        cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done)
        task.set_task_data(target_url)
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(self,
                                   gio_task: Gio.Task,
                                   _source_object: GObject.Object,
                                   task_data: str,
                                   _cancellable: Gio.Cancellable):
        """Execute the Nikto scan in a separate thread.

        This method is run by `Gio.Task.run_in_thread`. It takes the target URL,
        prepends 'http://' if no scheme is present, and runs the Nikto command
        using `subprocess.Popen`. It captures stdout and stderr.

        Returns results or error information via `gio_task.return_value` or
        `gio_task.return_error` (implicitly, by raising GLib.Error for task failures,
        though here it returns specific error types as strings in a tuple).

        Args:
        ----
            gio_task: The `Gio.Task` associated with this asynchronous operation.
            _source_object: The source GObject that initiated the task (unused).
            task_data: The target URL string passed via `task.set_task_data()`.
            _cancellable: A `Gio.Cancellable` (unused in this implementation).

        """
        target_url = task_data

        try:
            if not target_url.startswith(("http://", "https://")):
                target_url = "http://" + target_url

            with subprocess.Popen(
                ["nikto", "-h", target_url, "-Tuning", "xCGIVulnerable"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as process:
                stdout, stderr = process.communicate(timeout=300)
            gio_task.return_value((stdout, stderr, None))

        except FileNotFoundError:
            gio_task.return_value((None, None, "FileNotFoundError"))
        except subprocess.TimeoutExpired:
            gio_task.return_value((None, None, "TimeoutExpired"))
        except Exception as e:  # pylint: disable=broad-except
            gio_task.return_value((None, str(e), "Exception"))

    def _on_scan_task_done(self, task: Gio.Task, result: Gio.AsyncResult, _user_data: object):
        """Handle completion of the Nikto scan task.

        This callback is executed in the main thread. It retrieves the results
        (or error information) from the completed `Gio.Task` and updates the UI
        (TextView for results, error banner for errors). Re-enables the scan button.

        Args:
        ----
            task: The `Gio.Task` that has completed.
            result: The `Gio.AsyncResult` associated with the task's completion.
            _user_data: User data passed to the callback (unused).

        """
        target_url = task.get_task_data()

        try:
            stdout, stderr_or_error_msg, error_type = task.run_in_thread_finish(result)

            if error_type == "FileNotFoundError":
                self.show_error_toast(
                    "Nikto command not found. Please ensure it is installed and in your PATH."
                )
                self._update_textview("", "Error: Nikto not found.")
            elif error_type == "TimeoutExpired":
                self.show_error_toast(f"Scan for {target_url} timed out.")
                self._update_textview(
                    "", f"Error: Scan for {target_url} timed out after 5 minutes."
                )
            elif error_type == "Exception":
                self.show_error_toast(f"An error occurred: {stderr_or_error_msg}")
                self._update_textview("", f"An error occurred: {stderr_or_error_msg}")
            else:
                self._update_textview(stdout, stderr_or_error_msg)

        except GLib.Error as e:
            logging.error("Error in scan task: %s", e.message)
            self.show_error_toast(f"An error occurred: {e.message}")
            self._update_textview("", f"An error occurred: {e.message}")
        finally:
            self.scan_button.set_sensitive(True)

    def _update_textview(self, stdout: Optional[str], stderr: Optional[str]):
        """Update the results TextView with Nikto's stdout and stderr.

        Appends stdout first, then stderr if present. Scrolls the TextView
        to the end to show the latest results.

        Args:
        ----
            stdout: The standard output from the Nikto command, or None.
            stderr: The standard error from the Nikto command, or None.

        """
        buffer = self.results_textview.get_buffer()
        if stdout:
            buffer.insert(buffer.get_end_iter(), stdout)
        if stderr:
            buffer.insert(buffer.get_end_iter(), "\n--- Errors ---\n" + stderr)

        scroll_adj = self.results_textview.get_parent().get_vadjustment()
        if scroll_adj:
            scroll_adj.set_value(scroll_adj.get_upper() - scroll_adj.get_page_size())

    def show_error_toast(self, message: str):
        """Display an error message in the page's Adw.Banner.

        Args:
        ----
            message: The error message to display.

        """
        logging.error("Displaying error: %s", message)
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'show_error'):
            main_window.show_error(message)
        else:
            logging.warning("Could not find main window or show_error method to display: %s", message)

    # Removed on_error_banner_dismiss_clicked method
    # def on_error_banner_dismiss_clicked(self, _widget: Adw.Banner, *_args):
    #     """Handle the dismissal of the error banner.
    #
    #     Args:
    #     ----
    #         _widget: The Adw.Banner or its dismiss button.
    #         *_args: Additional arguments (unused).
    #
    #     """
    #     self.error_banner_webscan.set_revealed(False)
