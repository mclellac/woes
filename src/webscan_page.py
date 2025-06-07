import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

import logging
import re # Added for URL validation
import subprocess

from .constants import RESOURCE_PREFIX


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    __gtype_name__ = 'WebScanPage'

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()
    error_banner_webscan = Gtk.Template.Child()

    def on_scan_button_clicked(self, _widget):
        target_url = self.url_entry.get_text()

        # URL validation
        url_pattern = re.compile(
            r"^(?:(?:https?|ftp)://)?(?:\S+(?::\S*)?@)?"
            r"(?:(?:[1-9]\d?|1\d\d|2[01]\d|22[0-3])(?:\.(?:1?\d{1,2}|2[0-4]\d|25[0-5])){2}(?:\.(?:[1-9]\d?|1\d\d|2[0-4]\d|25[0-4]))|"
            r"(?:(?:[a-z¡-￿0-9]-*)*[a-z¡-￿0-9]+)(?:\.(?:[a-z¡-￿0-9]-*)*[a-z¡-￿0-9]+)*"
            r"(?:\.(?:[a-z¡-￿]{2,}))\.?)(?::\d{2,5})?(?:[/?#]\S*)?$",
            re.IGNORECASE
        )
        if not url_pattern.match(target_url):
            self.show_error_toast("Invalid URL format. Please enter a valid URL.")
            return

        if not target_url: # This check can remain for the empty case, though regex might catch some empty-like strings too.
            self.show_error_toast("Target URL cannot be empty.")
            return

        buffer = self.results_textview.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n")

        self.scan_button.set_sensitive(False)

        cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done)
        task.set_task_data(target_url)
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(self, gio_task, _source_object, task_data, _cancellable):
        """Worker function for Gio.Task that runs Nikto scan in a separate thread."""
        target_url = task_data

        try:
            if not target_url.startswith(('http://', 'https://')):
                target_url = 'http://' + target_url

            with subprocess.Popen(
                ['nikto', '-h', target_url, '-Tuning', 'xCGIVulnerable'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            ) as process:
                stdout, stderr = process.communicate(timeout=300)
            gio_task.return_value((stdout, stderr, None))

        except FileNotFoundError:
            gio_task.return_value((None, None, "FileNotFoundError"))
        except subprocess.TimeoutExpired:
            gio_task.return_value((None, None, "TimeoutExpired"))
        except Exception as e:
            gio_task.return_value((None, str(e), "Exception"))

    def _on_scan_task_done(self, task, result, _user_data):
        """Callback for when the Gio.Task is complete. Runs in the main thread."""
        target_url = task.get_task_data()

        try:
            stdout, stderr_or_error_msg, error_type = task.run_in_thread_finish(result)

            if error_type == "FileNotFoundError":
                self.show_error_toast("Nikto command not found. Please ensure it is installed and in your PATH.")
                self._update_textview("", "Error: Nikto not found.")
            elif error_type == "TimeoutExpired":
                self.show_error_toast(f"Scan for {target_url} timed out.")
                self._update_textview("", f"Error: Scan for {target_url} timed out after 5 minutes.")
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

    def _update_textview(self, stdout, stderr):
        buffer = self.results_textview.get_buffer()
        if stdout:
            buffer.insert(buffer.get_end_iter(), stdout)
        if stderr:
            buffer.insert(buffer.get_end_iter(), "\n--- Errors ---\n" + stderr)

        scroll_adj = self.results_textview.get_parent().get_vadjustment()
        if scroll_adj:
            scroll_adj.set_value(scroll_adj.get_upper() - scroll_adj.get_page_size())

    def show_error_toast(self, message):
        logging.error("Displaying error: %s", message)
        self.error_banner_webscan.set_title(message)
        self.error_banner_webscan.set_revealed(True)

    def on_error_banner_dismiss_clicked(self, _widget, *_args):
        self.error_banner_webscan.set_revealed(False)
