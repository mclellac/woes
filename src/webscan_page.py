"""Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import subprocess
import re
import logging
logger = logging.getLogger(__name__)
from typing import Optional

import gi
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk, GtkSource # Added Gdk and GtkSource

from .constants import RESOURCE_PREFIX

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5") # Match version from UI file


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    """Page for conducting web scans using Nikto, displaying results and errors."""

    __gtype_name__ = "WebScanPage"

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()
    force_ssl_switch = Gtk.Template.Child()
    cgi_vulns_switch = Gtk.Template.Child()
    interesting_content_switch = Gtk.Template.Child()
    evasion_switch = Gtk.Template.Child()
    mutate_switch = Gtk.Template.Child()
    maxtime_entry_row = Gtk.Template.Child()
    clear_results_button = Gtk.Template.Child()
    copy_results_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        """Initialize the WebScanPage.

        Sets up signal handlers for UI elements.
        """
        super().__init__(**kwargs)
        self.current_web_scan_task = None
        logger.debug("WebScanPage initialized")

        if self.results_textview:
            buffer = self.results_textview.get_buffer()
            if buffer:
                lm = GtkSource.LanguageManager.get_default()
                language = lm.get_language("text")
                if language:
                    buffer.set_language(language)
                else:
                    logger.warning("GtkSource language 'text' not found. Syntax highlighting may not apply.")

            self.results_textview.set_show_line_numbers(True)
            self.results_textview.set_monospace(True)
            self.results_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        self.url_entry.connect("entry-activated", self.on_scan_button_clicked)
        if self.clear_results_button:
            self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)

    def _on_clear_results_clicked(self, _button: Gtk.Button):
        """Handle click of the 'Clear Results' button.

        Clears the content of the results TextView.

        Args:
        ----
            _button: The Gtk.Button that was clicked (unused).

        """
        logger.info("Webscan results cleared by user action.")
        if self.results_textview:
            buffer = self.results_textview.get_buffer()
            buffer.set_text("")
        # Optionally, clear any related error banners if desired
        # main_window = self.get_native()
        # if main_window and hasattr(main_window, 'hide_error'):
        # main_window.hide_error()

    def _on_copy_results_clicked(self, _button: Gtk.Button):
        """Handle click of the 'Copy Results' button.

        Copies the entire content of the results TextView to the clipboard.

        Args:
        ----
            _button: The Gtk.Button that was clicked (unused).

        """
        logger.info("Copying webscan results to clipboard.")
        if self.results_textview:
            buffer = self.results_textview.get_buffer()
            start_iter = buffer.get_start_iter()
            end_iter = buffer.get_end_iter()
            text_content = buffer.get_text(start_iter, end_iter, False)

            if text_content:
                try:
                    clipboard = Gdk.Display.get_default().get_clipboard()
                    if clipboard:
                        clipboard.set(text_content)
                        logger.info("Webscan results copied to clipboard successfully.")
                    else:
                        logger.warning("Failed to get default clipboard for copying webscan results.")
                except Exception as e: # pylint: disable=broad-except
                    logger.error(f"Error copying webscan results to clipboard: {e}", exc_info=True)
            else:
                logger.info("No webscan results to copy.")

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

        url_pattern = re.compile(
            r"^(?:(?:https?|ftp)://)?(?:\S+(?::\S*)?@)?"  # Scheme and optional user:pass
            r"(?:(?:[1-9]\d?|1\d\d|2[01]\d|22[0-3])"  # IPv4 part 1
            r"(?:\.(?:1?\d{1,2}|2[0-4]\d|25[0-5])){2}"  # IPv4 part 2 & 3
            r"(?:\.(?:[1-9]\d?|1\d\d|2[0-4]\d|25[0-4]))|"  # IPv4 part 4
            r"(?:(?:[a-z¡-￿0-9]-*)*[a-z¡-￿0-9]+)"  # Domain name
            r"(?:\.(?:[a-z¡-￿0-9]-*)*[a-z¡-￿0-9]+)*"  # Optional subdomains
            r"(?:\.(?:[a-z¡-￿]{2,}))\.?)"  # Top Level Domain
            r"(?::\d{2,5})?"  # Optional port
            r"(?:[/?#]\S*)?$",  # Optional path, query, or fragment
            re.IGNORECASE,
        )
        if not target_url:
            main_window = self.get_native()
            if main_window and hasattr(main_window, 'show_toast'):
                main_window.show_toast("Target URL cannot be empty.")
            else:
                logger.warning("Could not find main window or show_toast method for empty Webscan URL toast.")
                self._show_main_banner_error("Target URL cannot be empty.")
            return

        if not url_pattern.match(target_url):
            main_window = self.get_native()
            if main_window and hasattr(main_window, 'show_toast'):
                main_window.show_toast("Invalid URL format. Please enter a valid URL.")
            else:
                logger.warning("Could not find main window or show_toast method for invalid Webscan URL toast.")
                self._show_main_banner_error("Invalid URL format. Please enter a valid URL.")
            return

        buffer = self.results_textview.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n")

        self.scan_button.set_sensitive(False)

        if self.current_web_scan_task and not self.current_web_scan_task.get_completed():
            try:
                logger.info("Attempting to cancel previous web scan task.")
                self.current_web_scan_task.get_cancellable().cancel()
            except Exception as e_cancel: # pylint: disable=broad-except
                logger.warning(f"Error trying to cancel previous web scan task: {e_cancel}")

        cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done, None) # task_data is None
        self.current_web_scan_task = task

        self._temp_scan_data = {
            "target_url": target_url,
            "force_ssl": self.force_ssl_switch.get_active(),
            "cgi_vulns": self.cgi_vulns_switch.get_active(),
            "interesting_content": self.interesting_content_switch.get_active(),
            "evasion": self.evasion_switch.get_active(),
            "mutate": self.mutate_switch.get_active(),
            "maxtime": self.maxtime_entry_row.get_text().strip()
        }
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(self,
                                   task: Gio.Task,
                                   source_object: GObject.Object, # This is 'self' (WebScanPage instance)
                                   task_data: object, # This will be None as no task_data is passed to Gio.Task.new
                                   cancellable: Gio.Cancellable):
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
        page_instance = source_object # page_instance is 'self' (WebScanPage)
        scan_data = page_instance._temp_scan_data

        target_url = scan_data["target_url"]
        force_ssl = scan_data["force_ssl"]
        cgi_vulns = scan_data["cgi_vulns"]
        interesting_content = scan_data["interesting_content"]
        evasion_active = scan_data.get("evasion", False)
        mutate_active = scan_data.get("mutate", False)
        maxtime_str = scan_data.get("maxtime", "")

        if not target_url.startswith(("http://", "https://")):
            target_url = "http://" + target_url

        nikto_command = ['nikto', '-h', target_url]

        if force_ssl:
            nikto_command.append('-ssl')
        if evasion_active:
            nikto_command.extend(['-evasion', '1'])
        if mutate_active:
            nikto_command.extend(['-mutate', '1'])
        if maxtime_str:
            try:
                maxtime_val = int(maxtime_str)
                if maxtime_val > 0:
                    nikto_command.extend(['-maxtime', str(maxtime_val) + 's']) # Nikto expects time with 's' suffix
                else:
                    logger.warning(f"Invalid maxtime value '{maxtime_str}', must be positive. Ignoring.")
            except ValueError:
                logger.warning(f"Invalid maxtime value '{maxtime_str}', not an integer. Ignoring.")

        tuning_options = []
        if cgi_vulns:
            tuning_options.append('2') # Corresponds to Nikto's "Misconfiguration / Default File"
        if interesting_content:
            tuning_options.append('1') # Corresponds to Nikto's "Interesting File / Seen in logs"

        # Nikto's -Tuning option takes a string of numbers (e.g., "12") to specify checks.
        # If no tuning switches are active, the -Tuning option is omitted,
        # allowing Nikto to use its default set of checks.
        if tuning_options:
            # Using set avoids duplicates (e.g. if '1' was added twice)
            # and sorted() ensures a consistent order (e.g., "12" not "21"),
            # though order usually doesn't matter for Nikto's -Tuning inclusion.
            tuning_string = "".join(sorted(list(set(tuning_options))))
            if tuning_string:
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
            task.return_value((stdout, stderr, None))

        except FileNotFoundError:
            task.return_value((None, None, "FileNotFoundError"))
        except subprocess.TimeoutExpired:
            task.return_value((None, None, "TimeoutExpired"))
        except Exception as e:  # pylint: disable=broad-except
            task.return_value((None, str(e), "Exception"))

    def _on_scan_task_done(self, source_object: GObject.Object, async_result_obj: Gio.AsyncResult, _user_data: object):
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
        target_url = "Unknown URL"
        if hasattr(self, '_temp_scan_data') and self._temp_scan_data: # Check if scan data exists
            target_url = self._temp_scan_data.get("target_url", target_url)

        active_task = self.current_web_scan_task
        if not active_task:
            logger.warning("_on_scan_task_done called but no active_task found.")
            if not self.scan_button.get_sensitive(): # Re-enable button if it got stuck
                self.scan_button.set_sensitive(True)
            return

        try:
            # Gio.Task.propagate_value() can raise a GLib.Error if the task itself
            # encountered an unhandled exception.
            # On success, it returns a 2-tuple. The first element's role is secondary
            # here as critical errors are caught by GLib.Error.
            # The second element is the actual payload (our 3-element tuple)
            # passed to task.return_value() in the thread.
            _intermediate_tuple_from_propagate = active_task.propagate_value()
            _status_or_bool, actual_payload_tuple = _intermediate_tuple_from_propagate
            stdout, stderr_or_error_msg, error_type = actual_payload_tuple

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

        except GLib.Error:
            logger.exception(f"GLib.Error during scan task finalization for {target_url}:")
            user_message = "A task finalization error occurred. Please check the application logs for more details."
            self._show_main_banner_error(user_message)
            self._update_textview("", user_message)
        finally:
            self.scan_button.set_sensitive(True)
            if hasattr(self, '_temp_scan_data'):
                del self._temp_scan_data
            self.current_web_scan_task = None

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


    def trigger_scan(self):
        """Programmatically triggers the WebScan 'Scan' action.

        This method is typically called by a global action/shortcut.
        It simulates a click on the scan button.
        """
        logger.debug("Webscan scan triggered by shortcut.")
        if self.scan_button and self.scan_button.get_sensitive():
            self.scan_button.clicked()
        else:
            logger.warning("Webscan scan button not available or not sensitive, cannot trigger scan.")
