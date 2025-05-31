import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

import subprocess
import logging

from .constants import RESOURCE_PREFIX # Import RESOURCE_PREFIX

@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui") # Use resource_path and constant
class WebScanPage(Adw.PreferencesPage):
    __gtype_name__ = 'WebScanPage'

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()
    error_banner_webscan = Gtk.Template.Child() # New banner

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def on_scan_button_clicked(self, widget):
        target_url = self.url_entry.get_text()
        if not target_url:
            self.show_error_toast("Target URL cannot be empty.")
            return

        # Clear previous results
        buffer = self.results_textview.get_buffer()
        buffer.set_text("Scanning {}...\n\n".format(target_url))

        # Disable button during scan
        self.scan_button.set_sensitive(False)

        # Run Nikto scan in a separate thread to avoid blocking UI
        # Using Gio.Task for modern asynchronous programming
        cancellable = Gio.Cancellable() # Optional: can be used to cancel the task
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done)
        task.set_task_data(target_url) # Pass target_url to the task
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(self, task, source_object, task_data, cancellable):
        """Worker function for Gio.Task that runs in a separate thread."""
        target_url = task_data # Retrieve target_url

        try:
            if not target_url.startswith(('http://', 'https://')):
                target_url = 'http://' + target_url

            process = subprocess.Popen(
                ['nikto', '-h', target_url, '-Tuning', 'xCGIVulnerable'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate(timeout=300)
            task.return_value((stdout, stderr, None)) # Success: (stdout, stderr, None for error_type)

        except FileNotFoundError:
            task.return_value((None, None, "FileNotFoundError"))
        except subprocess.TimeoutExpired:
            task.return_value((None, None, "TimeoutExpired"))
        except Exception as e:
            # For other exceptions, we might want to use task.return_error
            # but for simplicity in matching existing error handling,
            # we'll pass error string via return_value.
            # A more robust way would be:
            # error = GLib.Error(str(e), domain="WebScanPageErrorDomain", code=0)
            # task.return_error(error)
            task.return_value((None, str(e), "Exception"))


    def _on_scan_task_done(self, source_object, result, user_data):
        """Callback for when the Gio.Task is complete. Runs in the main thread."""
        task = source_object # In this case, source_object is the task itself
        target_url = task.get_task_data() # Retrieve target_url if needed for messages

        try:
            # This will re-raise an error if task.return_error() was called
            # or return the value from task.return_value()
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
            else: # Success
                self._update_textview(stdout, stderr_or_error_msg)

        except GLib.Error as e: # Catches errors set by task.return_error()
            logging.error(f"Error in scan task: {e.message}")
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

        # Scroll to the end
        scroll_adj = self.results_textview.get_parent().get_vadjustment()
        if scroll_adj:
            scroll_adj.set_value(scroll_adj.get_upper() - scroll_adj.get_page_size())


    def show_error_toast(self, message):
        # A helper function to show toasts, assuming this page is within a context that can display them
        # (e.g., Adw.ApplicationWindow or a view that has access to Adw.ToastOverlay)
        logging.error(f"Displaying error: {message}")
        self.error_banner_webscan.set_title(message)
        self.error_banner_webscan.set_revealed(True)

    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def on_error_banner_dismiss_clicked(self, widget, *args):
        self.error_banner_webscan.set_revealed(False)
