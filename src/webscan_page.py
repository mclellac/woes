"""Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import subprocess
import re
import logging
logger = logging.getLogger(__name__)
from typing import Optional # Added for type hinting

import gi
from gi.repository import Gtk, Adw, Gio, GLib, GObject # Added GObject

from .constants import RESOURCE_PREFIX

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    """Page for conducting web scans using Nikto, displaying results and errors."""

    __gtype_name__ = "WebScanPage"

    # toast_overlay_internal and preferences_page_content removed
    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()
    force_ssl_switch = Gtk.Template.Child()
    cgi_vulns_switch = Gtk.Template.Child()
    interesting_content_switch = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.debug("WebScanPage initialized")
        self.url_entry.connect("entry-activated", self.on_scan_button_clicked)

    def on_scan_button_clicked(self, _widget: Gtk.Button):
        """Handle the 'Scan' button click event.

        Validates the URL entered by the user, then initiates an asynchronous
        Nikto scan task if the URL is valid. Disables the scan button during
        the scan.

        Args:
        ----
            _widget: The Gtk.Button that was clicked.

        """
        logger.debug(f"WebScanPage scan button clicked. URL: '{self.url_entry.get_text()}'")
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
            self._show_main_banner_error("Target URL cannot be empty.")
            return

        if not url_pattern.match(target_url):
            self._show_main_banner_error("Invalid URL format. Please enter a valid URL.")
            return

        buffer = self.results_textview.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n")

        self.scan_button.set_sensitive(False)

        cancellable = Gio.Cancellable()
        # Pass self (WebScanPage instance) as source_object, task_data will be None
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done, None)

        self._temp_scan_data = {
            "target_url": target_url,
            "force_ssl": self.force_ssl_switch.get_active(),
            "cgi_vulns": self.cgi_vulns_switch.get_active(),
            "interesting_content": self.interesting_content_switch.get_active()
        }
        # task_data is not set on the task directly anymore.
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(self,
                                   task: Gio.Task, # Correct first parameter
                                   source_object: GObject.Object, # This is 'self' (WebScanPage instance)
                                   task_data: object, # This will be None (as passed to Gio.Task.new)
                                   cancellable: Gio.Cancellable): # Corrected name
        """Execute the Nikto scan in a separate thread.

        This method is run by `Gio.Task.run_in_thread`. It retrieves scan parameters
        from the `source_object` (WebScanPage instance), constructs, and runs the Nikto command.

        Args:
        ----
            task: The `Gio.Task` associated with this asynchronous operation.
            source_object: The source GObject that initiated the task (the WebScanPage instance).
            task_data: Task-specific data (None in this implementation, parameters are on source_object).
            cancellable: A `Gio.Cancellable` to monitor for cancellation requests.

        """
        logger.debug("WebScanPage._run_scan_task_thread_func started")
        page_instance = source_object # source_object is the WebScanPage instance
        scan_data = page_instance._temp_scan_data

        target_url = scan_data["target_url"]
        force_ssl = scan_data["force_ssl"]
        cgi_vulns = scan_data["cgi_vulns"]
        interesting_content = scan_data["interesting_content"]

        if not target_url.startswith(("http://", "https://")):
            target_url = "http://" + target_url

        # Construct Nikto command
        nikto_command = ['nikto', '-h', target_url, '-Format', 'txt']

        # Check SSL option
        if force_ssl:
            nikto_command.append('-ssl')

        # Check Tuning options
        tuning_options = []
        if cgi_vulns:
            tuning_options.append('2') # Misconfiguration / Default File
        if interesting_content:
            tuning_options.append('1') # Interesting File / Seen in logs

        # Nikto's -Tuning option takes a string of numbers, e.g., "12"
        # It's important not to pass 'x' if we are specifying numbers,
        # as 'x' is for excluding options, while numbers are for including specific checks.
        # The old command used "xCGIVulnerable" which is equivalent to "x2" (excluding CGI checks).
        # The new logic is to include specific checks if their switches are on.
        # If both cgi_vulns_switch (2) and interesting_content_switch (1) are on,
        # and no other default tuning is desired beyond these, the tuning string should be "12".
        # If no tuning switches are active, we should not add the -Tuning option,
        # allowing Nikto to use its default tuning.

        if tuning_options:
            # Sort to ensure consistent order if needed, e.g., "12" not "21"
            # Though for Nikto's -Tuning, order usually doesn't matter for inclusion.
            tuning_string = "".join(sorted(list(set(tuning_options)))) # Use set to avoid duplicates like "11"
            if tuning_string: # Ensure not empty if logic changes
                nikto_command.extend(['-Tuning', tuning_string])

        logger.debug(f"Constructed Nikto command: {nikto_command}")

        try:
            with subprocess.Popen(
                nikto_command,
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
        # Retrieve target_url from the instance attribute for messages
        target_url = "Unknown URL" # Default if _temp_scan_data is somehow missing
        if hasattr(self, '_temp_scan_data') and self._temp_scan_data:
            target_url = self._temp_scan_data.get("target_url", target_url)

        try:
            stdout, stderr_or_error_msg, error_type = task.run_in_thread_finish(result)

            if error_type == "FileNotFoundError":
                logger.exception("Nikto command not found. Ensure it's in PATH.")
                user_message = "Nikto command not found. Please ensure Nikto is installed and in your system's PATH."
                self._show_main_banner_error(user_message)
                self._update_textview("", f"Error: {user_message}")
            elif error_type == "TimeoutExpired":
                logger.exception(f"Nikto scan for {target_url} timed out.")
                user_message = f"Scan for {target_url} timed out after 5 minutes."
                self._show_main_banner_error(user_message)
                self._update_textview("", f"Error: {user_message}")
            elif error_type == "Exception":
                logger.exception(f"An unexpected error occurred during Nikto scan task for {target_url}:")
                user_message = "An unexpected error occurred during the scan. Please check the application logs for more details."
                self._show_main_banner_error(user_message)
                self._update_textview("", user_message)
            else:
                self._update_textview(stdout, stderr_or_error_msg)

        except GLib.Error as e:
            logger.exception(f"GLib.Error during scan task finalization for {target_url}:")
            user_message = "A task finalization error occurred. Please check the application logs for more details."
            self._show_main_banner_error(user_message)
            self._update_textview("", user_message)
        finally:
            self.scan_button.set_sensitive(True)
            if hasattr(self, '_temp_scan_data'):
                del self._temp_scan_data

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

    def _show_main_banner_error(self, message: str):
        """Display an error message in the main window's error banner.

        Args:
        ----
            message: The error message to display.

        """
        logger.error("Displaying error: %s", message)
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'show_error'):
            main_window.show_error(message)
        else:
            logger.warning("Could not find main window or show_error method to display: %s", message)

    # Removed on_error_banner_dismiss_clicked method
