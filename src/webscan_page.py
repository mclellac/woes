"""Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import subprocess
# import re # No longer used in this file
import logging
logger = logging.getLogger(__name__)
from typing import Optional, Dict, Any, Tuple
import time # Added for polling loop
from enum import Enum # Added for WebScanErrorType

import gi
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk, GtkSource

from .constants import RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_url

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")


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
    webscan_status_spinner = Gtk.Template.Child("webscan_status_spinner")

    def __init__(self, **kwargs):
        """Initialize the WebScanPage."""
        super().__init__(**kwargs)
        self.current_web_scan_task: Optional[Gio.Task] = None
        self.current_web_scan_cancellable: Optional[Gio.Cancellable] = None
        self.current_nikto_process: Optional[subprocess.Popen] = None
        # self._temp_scan_data was used to pass data to thread, now using task.set_task_data()
        logger.debug("WebScanPage initialized")

        # Programmatically create and add the Cancel Scan button
        self.webscan_cancel_button = Gtk.Button(label="Cancel Scan", icon_name="process-stop-symbolic")
        self.webscan_cancel_button.set_sensitive(False)
        self.webscan_cancel_button.set_visible(False)
        self.webscan_cancel_button.add_css_class("destructive-action")
        self.webscan_cancel_button.connect("clicked", self._on_cancel_scan_clicked)

        # Add cancel button next to scan_button. Assuming scan_button is in a Gtk.Box.
        if self.scan_button and isinstance(self.scan_button.get_parent(), Gtk.Box):
            parent_box = self.scan_button.get_parent()
            parent_box.append(self.webscan_cancel_button)
        else:
            # Fallback: if scan_button's parent is not a Box, or scan_button not found,
            # try adding to url_entry row if it's an ActionRow. This might need UI file adjustment.
            if isinstance(self.url_entry, Adw.ActionRow):
                 logger.info("Adding cancel button to url_entry row as a suffix.")
                 self.url_entry.add_suffix(self.webscan_cancel_button)
            else:
                 logger.warning("Could not programmatically add Cancel Scan button to a suitable container.")

        if self.webscan_status_spinner:
            self.webscan_status_spinner.set_spinning(False) # type: ignore
            self.webscan_status_spinner.set_visible(False) # type: ignore


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

    def __del__(self):
        """Clean up when the WebScanPage is destroyed."""
        if self.current_web_scan_cancellable and \
           not self.current_web_scan_cancellable.is_cancelled():
            logger.info("WebScanPage being destroyed, cancelling ongoing Nikto scan.")
            self.current_web_scan_cancellable.cancel()

        # If self.current_nikto_process was managed directly by the page (it's not in current plan)
        # if self.current_nikto_process and self.current_nikto_process.poll() is None:
        #    logger.info("Terminating Nikto process from WebScanPage.__del__")
        #    self.current_nikto_process.terminate()
        #    try:
        #        self.current_nikto_process.wait(timeout=1)
        #    except subprocess.TimeoutExpired:
        #        self.current_nikto_process.kill()
        # self.current_nikto_process = None
        # super().__del__() # If inheriting from GObject.Object directly and needing its __del__

    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        """Handles click on the 'Cancel Scan' button."""
        logger.info("Cancel scan button clicked.")
        if self.current_web_scan_cancellable and \
           not self.current_web_scan_cancellable.is_cancelled():
            self.current_web_scan_cancellable.cancel()
            logger.info("Nikto scan cancellation requested via button.")
            # UI updates (disabling cancel, enabling scan) will happen in _on_scan_task_done
            # Or potentially set a flag here and update UI immediately if preferred.
            if hasattr(self, 'webscan_cancel_button'): self.webscan_cancel_button.set_sensitive(False)
        else:
            logger.warning("No active scan or cancellable to cancel.")

    def _on_clear_results_clicked(self, _button: Gtk.Button):
        """Handle click of the 'Clear Results' button."""
        logger.info("Webscan results cleared by user action.")
        if self.results_textview:
            buffer = self.results_textview.get_buffer()
            buffer.set_text("")

    def _on_copy_results_clicked(self, _button: Gtk.Button):
        """Handle click of the 'Copy Results' button."""
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
        """Handle the 'Scan' button click event."""
        logger.debug(f"WebScanPage scan button clicked. URL: '{self.url_entry.get_text()}'")
        target_url = self.url_entry.get_text().strip()

        if not target_url:
            show_global_toast(self, "Target URL cannot be empty.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Target URL cannot be empty.")
            return

        # The original regex allowed http, https, ftp.
        # utils.is_valid_url defaults to ['http', 'https'], so we provide the schemes.
        if not is_valid_url(target_url, schemes=['http', 'https', 'ftp']):
            show_global_toast(self, "Invalid URL format. Please enter a valid URL.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Invalid URL format. Please enter a valid URL.")
            return

        buffer = self.results_textview.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n")

        self.scan_button.set_sensitive(False)

        self.scan_button.set_sensitive(False) # type: ignore
        if hasattr(self, 'webscan_cancel_button'):
            self.webscan_cancel_button.set_visible(True)
            self.webscan_cancel_button.set_sensitive(True)
        if self.webscan_status_spinner: self.webscan_status_spinner.start() # type: ignore


        if self.current_web_scan_task and not self.current_web_scan_task.is_done():
            if self.current_web_scan_cancellable and not self.current_web_scan_cancellable.is_cancelled():
                logger.info("Cancelling previous web scan task before starting new one.")
                self.current_web_scan_cancellable.cancel()
                # The old task will clean itself up in its _on_scan_task_done.
                # We proceed to create and run a new task.

        self.current_web_scan_cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, self.current_web_scan_cancellable, self._on_scan_task_done, None) # type: ignore
        self.current_web_scan_task = task

        # Store scan parameters for the thread function to access via task_data
        task_data_for_thread: Dict[str, Any] = {
            "target_url": target_url,
            "force_ssl": self.force_ssl_switch.get_active(), # type: ignore
            "cgi_vulns": self.cgi_vulns_switch.get_active(), # type: ignore
            "interesting_content": self.interesting_content_switch.get_active(), # type: ignore
            "evasion": self.evasion_switch.get_active(), # type: ignore
            "mutate": self.mutate_switch.get_active(), # type: ignore
            "maxtime": self.maxtime_entry_row.get_text().strip() # type: ignore
        }
        task.set_task_data(task_data_for_thread) # type: ignore
        task.run_in_thread(self._run_scan_task_thread_func) # type: ignore

    def _run_scan_task_thread_func(self,
                                   task: Gio.Task,
                                   _source_object: GObject.Object,
                                   _task_data_unused: Any, # Parameter from Gio.Task.run_in_thread, but we use task.get_task_data()
                                   cancellable: Gio.Cancellable):
        """Execute the Nikto scan in a separate thread, with cancellation support."""
        logger.debug("WebScanPage._run_scan_task_thread_func started")

        scan_params = task.get_task_data()
        if not scan_params: # Should not happen if set correctly
            logger.error("No scan parameters found in task data.")
            task.return_new_error_literal(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.GENERIC.value, "Missing scan parameters.") # type: ignore
            return

        target_url = scan_params["target_url"]

        # is_valid_url (used in on_scan_button_clicked) ensures target_url has a scheme from
        # ['http', 'https', 'ftp']. Nikto prepends 'http://' if no scheme is given,
        # but since our validation requires a scheme, this explicit check and prepend
        # ensures Nikto gets a URL it can work with, especially if the URL somehow
        # lost its scheme or if ftp was validated but nikto needs http/s for -h.
        # For robustness with Nikto, ensure it has an http/https scheme if ftp isn't directly supported by -h.
        if target_url.startswith("ftp://"):
            logger.info("FTP URL provided; Nikto might not directly support it. Attempting with http:// for the host part.")
            # Attempt to convert ftp://host/path to http://host/path for Nikto
            # This is a simple conversion; complex FTP URLs might not translate well.
            target_url = target_url.replace("ftp://", "http://", 1)
        elif not target_url.startswith(("http://", "https://")):
            logger.info("Prepending http:// to target URL for Nikto as scheme was missing or not http/https.")
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
                    nikto_command.extend(['-maxtime', str(maxtime_val) + 's'])
                else:
                    logger.warning(f"Invalid maxtime value '{maxtime_str}', must be positive. Ignoring.")
            except ValueError:
                logger.warning(f"Invalid maxtime value '{maxtime_str}', not an integer. Ignoring.")

        tuning_options = []
        if cgi_vulns: tuning_options.append('2') # Corresponds to Nikto's "Misconfiguration / Default File"
        if interesting_content: tuning_options.append('1') # Corresponds to Nikto's "Interesting File / Seen in logs"

        # Nikto's -Tuning option takes a string of numbers (e.g., "12") to specify checks.
        # If no tuning switches are active, the -Tuning option is omitted.
        if tuning_options:
            # Using set avoids duplicates and sorted() ensures a consistent order.
            tuning_string = "".join(sorted(list(set(tuning_options))))
            if tuning_string:
                nikto_command.extend(['-Tuning', tuning_string])

        logger.debug(f"Constructed Nikto command: {nikto_command}")

        try:
            if cancellable.is_cancelled():
                task.return_new_error_literal(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.CANCELLED.value, "Scan cancelled before Nikto process start.") # type: ignore
                return

            process = subprocess.Popen(nikto_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            self.current_nikto_process = process # Store for potential external cancellation (e.g. __del__) - though __del__ might not run in main thread

            stdout_str, stderr_str = "", ""
            # Polling loop for cancellation
            while True:
                if cancellable.is_cancelled():
                    logger.info("Nikto scan cancellation polling: Scan cancelled.")
                    if process.poll() is None: # Check if process is still running
                        try:
                            logger.info("Terminating Nikto process.")
                            process.terminate()
                            process.wait(timeout=2) # Short wait for graceful termination
                        except subprocess.TimeoutExpired:
                            logger.warning("Nikto process did not terminate gracefully, killing.")
                            process.kill()
                        except Exception as e_term: # pylint: disable=broad-except
                            logger.error(f"Error terminating Nikto process: {e_term}")
                    task.return_new_error_literal(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.CANCELLED.value, "Scan cancelled by user.") # type: ignore
                    self.current_nikto_process = None
                    return

                if process.poll() is not None:
                    break

                time.sleep(0.2) # Polling interval

            stdout_str, stderr_str = process.communicate()
            self.current_nikto_process = None # Clear process reference

            if process.returncode != 0:
                 logger.error(f"Nikto process finished with error code {process.returncode}. Stderr: {stderr_str}")
                 task.return_new_error_literal(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.GENERIC.value, f"Nikto scan failed. Output:\n{stderr_str or stdout_str}") # type: ignore
                 return

            task.return_value((stdout_str, stderr_str)) # type: ignore
        except FileNotFoundError:
            logger.error("Nikto command not found. Ensure it's in PATH.")
            task.return_new_error_literal(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.NIKTO_NOT_FOUND.value, "Nikto command not found. Please ensure it is installed and in your system's PATH.") # type: ignore
        # TimeoutExpired for process.communicate() is removed as we poll.
        # A global timeout for the entire scan could be implemented by checking time in the polling loop.
        except Exception as e: # pylint: disable=broad-except
            logger.exception(f"An unexpected error occurred during Nikto scan task: {e}")
            task.return_new_error_literal(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.GENERIC.value, str(e)) # type: ignore
        finally:
            self.current_nikto_process = None # Ensure cleared

    def _on_scan_task_done(self, _source_object: GObject.Object, result: Gio.AsyncResult, _user_data: object): # type: ignore
        """Handle completion of the Nikto scan task."""
        target_url = "Unknown URL"
        if self.current_web_scan_task and self.current_web_scan_task.get_task_data():
            task_data_retrieved = self.current_web_scan_task.get_task_data()
            if isinstance(task_data_retrieved, dict): # Check if it's the dict we set
                 target_url = task_data_retrieved.get("target_url", target_url)

        logger.info(f"Nikto scan task done for {target_url}.")
        stdout: Optional[str] = None
        stderr: Optional[str] = None

        try:
            # propagate_value() will return the (stdout, stderr) tuple on success
            # or raise GLib.Error if task.return_new_error_literal was called.
            returned_data = self.current_web_scan_task.propagate_value() # type: ignore
            if isinstance(returned_data, tuple) and len(returned_data) == 2:
                stdout, stderr = returned_data
            elif hasattr(returned_data, 'value') and isinstance(returned_data.value, tuple) and len(returned_data.value) == 2: # Handle potential Gio.DBusCallFlags like wrapper
                stdout, stderr = returned_data.value
            else:
                logger.error(f"Nikto scan for {target_url} returned unexpected result format: {type(returned_data)}")
                show_global_error(self, "Scan returned unexpected data format.") # type: ignore
                self._update_textview(None, "Error: Scan returned unexpected data format.")
                # Fall through to finally block for UI reset

            if stdout is not None or stderr is not None: # If successful (even with stderr info)
                self._update_textview(stdout, stderr)

        except GLib.Error as e:
            logger.warning(f"Nikto scan task for {target_url} failed or was cancelled: {e.message} (Domain: {e.domain}, Code: {e.code})")
            user_message = e.message # Default to the error message from the task
            if e.matches(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.NIKTO_NOT_FOUND.value): # type: ignore
                user_message = "Nikto command not found. Please ensure Nikto is installed and in your system's PATH."
            elif e.matches(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.TIMEOUT.value): # type: ignore
                user_message = f"Scan for {target_url} timed out." # This error type might not be used if timeout is only for process.communicate
            elif e.matches(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.CANCELLED.value): # type: ignore
                user_message = f"Scan for {target_url} was cancelled."
            elif e.matches(WEB_SCAN_ERROR_DOMAIN, WebScanErrorType.GENERIC.value): # type: ignore
                user_message = f"Scan failed for {target_url}: {e.message}" # e.message already contains details

            show_global_error(self, user_message) # type: ignore
            self._update_textview(None, f"Error: {user_message}") # Display error in textview as well
        except Exception as e: # Catch any other Python exceptions from this callback itself
            logger.exception(f"Unexpected Python error in _on_scan_task_done for {target_url}:")
            user_message = "An unexpected error occurred while processing scan results."
            show_global_error(self, user_message) # type: ignore
            self._update_textview(None, f"Error: {user_message}")
        finally:
            self.scan_button.set_sensitive(True) # type: ignore
            if hasattr(self, 'webscan_cancel_button'):
                self.webscan_cancel_button.set_sensitive(False)
                self.webscan_cancel_button.set_visible(False)
            if self.webscan_status_spinner: self.webscan_status_spinner.stop() # type: ignore

            self.current_web_scan_task = None
            self.current_web_scan_cancellable = None
            self.current_nikto_process = None # Ensure cleared

    def _update_textview(self, stdout: Optional[str], stderr: Optional[str]):
        """Update the results TextView with Nikto's stdout and stderr."""
        buffer = self.results_textview.get_buffer()
        if stdout:
            buffer.insert(buffer.get_end_iter(), stdout)
        if stderr:
            buffer.insert(buffer.get_end_iter(), "\n--- Errors ---\n" + stderr)

        scroll_adj = self.results_textview.get_parent().get_vadjustment()
        if scroll_adj:
            scroll_adj.set_value(scroll_adj.get_upper() - scroll_adj.get_page_size())

    def trigger_scan(self):
        """Programmatically triggers the WebScan 'Scan' action."""
        logger.debug("Webscan scan triggered by shortcut.")
        if self.scan_button and self.scan_button.get_sensitive():
            self.scan_button.clicked() # type: ignore
        elif self.current_web_scan_task and not self.current_web_scan_task.is_done():
            show_global_toast(self, "A scan is already in progress. Cancel it or wait.") # type: ignore
        else:
            logger.warning("Webscan scan button not available or not sensitive, cannot trigger scan.")

# --- Gio.Task Error Handling ---
WEB_SCAN_ERROR_DOMAIN = "web-scan-error-domain"

class WebScanErrorType(int, Enum):
    """Enumeration of Web Scan error types for Gio.Task error reporting."""
    NIKTO_NOT_FOUND = 0
    TIMEOUT = 1         # If a global scan timeout is implemented
    CANCELLED = 2
    GENERIC = 3         # For other Nikto/subprocess errors or general exceptions
