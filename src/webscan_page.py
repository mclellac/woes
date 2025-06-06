import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

import logging
import subprocess

from .constants import RESOURCE_PREFIX


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    __gtype_name__ = 'WebScanPage'

    # Renamed to match UI IDs from webscan_page.ui
    webscan_url_entry = Gtk.Template.Child()
    webscan_start_button = Gtk.Template.Child()
    webscan_results_textview = Gtk.Template.Child()
    webscan_error_banner = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # super().__init__() must be called before accessing Gtk.Template.Child widgets

        initial_message = "Enter a target URL above and click 'Start Scan'. Scan output will be displayed here."
        if self.webscan_results_textview and self.webscan_results_textview.get_buffer():
            self.webscan_results_textview.get_buffer().set_text(initial_message)
        else:
            # This case should ideally not happen if UI is loaded correctly
            logging.warning("WebScanPage: webscan_results_textview or its buffer is not available in __init__.")

    @Gtk.Template.Callback()
    def webscan_start_button_clicked_cb(self, _widget):
        target_url = self.webscan_url_entry.get_text()
        if not target_url:
            self.show_error_banner("Target URL cannot be empty.")
            return

        buffer = self.webscan_results_textview.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n") # Clear previous results and show status

        self.webscan_start_button.set_sensitive(False)
        self.webscan_error_banner.set_revealed(False) # Hide previous errors

        cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done_cb)
        task.set_task_data(target_url) # Pass target_url as task data
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(self, gio_task, source_object, task_data, cancellable):
        """Worker function for Gio.Task that runs Nikto scan in a separate thread."""
        target_url = task_data # Retrieve target_url from task_data

        try:
            if not target_url.startswith(('http://', 'https://')):
                target_url = 'http://' + target_url

            nikto_path = GLib.find_program_in_path('nikto')
            if not nikto_path:
                gio_task.return_value(("Nikto command not found. Please ensure it is installed and in your PATH.", None, "NiktoNotFound"))
                return

            command = [nikto_path, '-h', target_url, '-Tuning', 'xCGIVulnerable', '-Format', 'txt']

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors='replace'
            )
            stdout, stderr = process.communicate(timeout=300) # 5 minutes timeout

            if process.returncode != 0 and not stdout:
                gio_task.return_value((None, stderr if stderr else "Nikto scan failed with no specific output.", "NiktoError"))
                return

            gio_task.return_value((stdout, stderr, None)) # Success

        except FileNotFoundError:
            gio_task.return_value((None, "Nikto (or other subprocess component) not found.", "FileNotFoundError"))
        except subprocess.TimeoutExpired:
            gio_task.return_value((None, f"Scan for {target_url} timed out after 5 minutes.", "TimeoutExpired"))
        except Exception as e:
            logging.error(f"Exception in Nikto scan thread: {e}")
            gio_task.return_value((None, str(e), "Exception"))

    def _on_scan_task_done_cb(self, task, source_object, user_data):
        """Callback for when the Gio.Task is complete. Runs in the main thread."""
        try:
            stdout, stderr_or_error_msg, error_type = task.run_in_thread_finish()

            if error_type == "NiktoNotFound":
                self.show_error_banner(stdout)
                self._update_textview("", stdout)
            elif error_type == "FileNotFoundError":
                self.show_error_banner(stderr_or_error_msg)
                self._update_textview("", stderr_or_error_msg)
            elif error_type == "TimeoutExpired":
                self.show_error_banner(stderr_or_error_msg)
                self._update_textview("", stderr_or_error_msg)
            elif error_type == "NiktoError":
                self.show_error_banner(stderr_or_error_msg if stderr_or_error_msg else "Nikto scan failed.")
                self._update_textview(stdout if stdout else "", stderr_or_error_msg)
            elif error_type == "Exception":
                self.show_error_banner(f"An unexpected error occurred: {stderr_or_error_msg}")
                self._update_textview("", f"An unexpected error occurred: {stderr_or_error_msg}")
            else:
                self.webscan_error_banner.set_revealed(False)
                self._update_textview(stdout, stderr_or_error_msg if stderr_or_error_msg else "")

        except GLib.Error as e:
            logging.error("Error in scan task result processing: %s", e.message)
            self.show_error_banner(f"A task error occurred: {e.message}")
            if task.is_cancelled():
                 self._update_textview("", "Scan was cancelled.")
            else:
                self._update_textview("", f"A task error occurred: {e.message}")
        finally:
            self.webscan_start_button.set_sensitive(True)

    def _update_textview(self, stdout_text, stderr_text):
        buffer = self.webscan_results_textview.get_buffer()
        full_text = ""
        if stdout_text:
            full_text += stdout_text
        if stderr_text:
            full_text += "\n--- Standard Error / Info ---\n" + stderr_text

        buffer.set_text(full_text)

        scrolled_window = self.webscan_results_textview.get_parent()
        if isinstance(scrolled_window, Gtk.ScrolledWindow):
            v_adjustment = scrolled_window.get_vadjustment()
            if v_adjustment:
                v_adjustment.set_value(0)
        else:
            logging.warning("Could not get GtkScrolledWindow to adjust scroll for results_textview.")

    def show_error_banner(self, message):
        logging.info("Displaying error banner: %s", message)
        self.webscan_error_banner.set_title(message)
        self.webscan_error_banner.set_revealed(True)

    @Gtk.Template.Callback()
    def webscan_error_banner_button_clicked_cb(self, _widget, *_args):
        self.webscan_error_banner.set_revealed(False)
