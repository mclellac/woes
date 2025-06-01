import logging
import subprocess
from typing import Optional  # Dict removed

import gi
# requests, re, urllib.parse removed

# GTK version requirements must be called before importing from gi.repository
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')

# Now import GTK libraries and other dependencies
# pylint: disable=wrong-import-position
from gi.repository import Adw, Gio, GObject, Gtk, GLib
# pylint: disable=wrong-import-position
from .constants import RESOURCE_PREFIX

# Configure logger for the module - AFTER all imports
logger = logging.getLogger(__name__)

WEB_SCAN_ERROR_DOMAIN = "webscan-error-domain"


class WebScanError:
    NIKTO_NOT_FOUND = 0
    TIMEOUT = 1
    CALLED_PROCESS = 2  # If nikto returns non-zero
    UNKNOWN = 3


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    __gtype_name__ = 'WebScanPage'

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()
    error_banner_webscan = Gtk.Template.Child()

    def on_scan_button_clicked(self, _widget):
        target_url = self.url_entry.get_text()
        if not target_url:
            self.show_error_toast("Target URL cannot be empty.")
            return

        buffer = self.results_textview.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n")  # f-string for UI is fine

        self.scan_button.set_sensitive(False)

        cancellable = Gio.Cancellable.new()  # Allow cancellation
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done, None)
        task.set_task_data(target_url)
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(
        self,
        gio_task: Gio.Task,
        _source_object,
        task_data: str,
        cancellable: Gio.Cancellable
    ):
        """Worker function for Gio.Task that runs in a separate thread."""
        target_url = task_data
        stdout_str = ""
        stderr_str = ""

        try:
            if not target_url.startswith(('http://', 'https://')):
                target_url = 'http://' + target_url

            if cancellable.is_cancelled():
                gio_task.return_error(
                    GLib.Error("Scan cancelled before start.", WEB_SCAN_ERROR_DOMAIN, Gio.IOErrorEnum.CANCELLED)
                )
                return

            # Nikto command arguments
            command = [
                'nikto', '-h', target_url, '-Format', 'txt', '-ask', 'no',
                '-Tuning', 'xCGIVulnerable', '-Display', 'V', '-nointeractive',
                '-timeout', '300'
            ]
            logger.info("Running Nikto command: %s", " ".join(command))

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout_str, stderr_str = process.communicate()  # No timeout here, rely on Nikto's timeout

            if cancellable.is_cancelled():
                if process.poll() is None:  # Check if process is still running
                    logger.info("Scan cancelled, attempting to terminate Nikto process.")
                    process.terminate()
                    try:
                        process.wait(timeout=10)  # Wait a bit for termination
                    except subprocess.TimeoutExpired:
                        logger.warning("Nikto process did not terminate gracefully, killing.")
                        process.kill()
                gio_task.return_error(
                    GLib.Error("Scan cancelled by user.", WEB_SCAN_ERROR_DOMAIN, Gio.IOErrorEnum.CANCELLED)
                )
                return

            if process.returncode:  # Use implicit booleaness for non-zero check
                log_stderr = stderr_str[:200]  # Log truncated stderr for brevity
                logger.error("Nikto for %s exited with code %d. stderr: %s", target_url, process.returncode, log_stderr)
                error_message = f"Nikto scan failed (code {process.returncode})."
                if "Can't find host" in stderr_str or "ERROR: Cannot resolve hostname" in stderr_str:
                    error_message = "Cannot resolve hostname or invalid target."
                elif "ERROR: No HTTP response" in stderr_str:
                    error_message = "No HTTP response from target."
                gio_task.return_error(GLib.Error(error_message, WEB_SCAN_ERROR_DOMAIN, WebScanError.CALLED_PROCESS))
                return

            gio_task.return_value(GLib.Variant('(ss)', (stdout_str, stderr_str)))

        except FileNotFoundError:
            logger.error("Nikto command not found.", exc_info=True)
            gio_task.return_error(
                GLib.Error("Nikto not found. Ensure installed.", WEB_SCAN_ERROR_DOMAIN, WebScanError.NIKTO_NOT_FOUND)
            )
        except subprocess.TimeoutExpired:  # This would be for communicate() timeout itself
            logger.error("Nikto process communicate() timed out for %s.", target_url, exc_info=True)
            gio_task.return_error(GLib.Error("Scan process timed out.", WEB_SCAN_ERROR_DOMAIN, WebScanError.TIMEOUT))
        except Exception as e:
            logger.exception("Unexpected error during Nikto scan for %s.", target_url)
            err_name = type(e).__name__
            gio_task.return_error(GLib.Error(f"Unexpected: {err_name}", WEB_SCAN_ERROR_DOMAIN, WebScanError.UNKNOWN))

    def _on_scan_task_done(self, _source_object, task: Gio.Task, _user_data):
        """Callback for when the Gio.Task is complete. Runs in the main thread."""
        try:
            stdout, stderr = task.propagate_value().unpack()  # unpack the (ss) GLib.Variant
            self._update_textview(stdout, stderr)
            if not stdout and not stderr:  # If Nikto produced no output but no error code
                self.show_error_toast(
                    "Scan completed with no output. Target might not be a web server or scan options too restrictive."
                )
            elif stderr:  # If there's stderr, show it as a toast as well for visibility
                self.show_error_toast("Scan completed with errors/warnings (see details).")

        except GObject.GError as e:  # Catches errors set by gio_task.return_error()
            # Shorten log message
            logger.error("Web scan task error: %s (Code: %d)", e.message, e.code)
            self.show_error_toast(e.message)  # Display the error message from GLib.Error
            self._update_textview("", f"Error: {e.message}")
        finally:
            self.scan_button.set_sensitive(True)

    def _update_textview(self, stdout: Optional[str], stderr: Optional[str]):
        buffer = self.results_textview.get_buffer()
        full_text = ""
        if stdout:
            full_text += stdout
        if stderr:
            full_text += "\n--- Standard Error ---\n" + stderr

        if not full_text:  # If both are empty or None
            buffer.set_text("Scan completed. No specific output to display.")
        else:
            buffer.set_text(full_text)

        # Scroll to the end
        scroll_adj = self.results_textview.get_parent().get_vadjustment()
        if scroll_adj:
            scroll_adj.set_value(scroll_adj.get_upper() - scroll_adj.get_page_size())

    def show_error_toast(self, message: str):
        logger.info("Displaying WebScan error/info: %s", message)  # Changed to info as it's also for warnings
        self.error_banner_webscan.set_title(message)
        self.error_banner_webscan.set_revealed(True)

    def on_error_banner_dismiss_clicked(self, _widget, *_args):
        self.error_banner_webscan.set_revealed(False)
