import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

import subprocess

@Gtk.Template(filename='/com/github/mclellac/WebOpsEvaluationSuite/gtk/webscan_page.ui')
class WebScanPage(Adw.PreferencesPage):
    __gtype_name__ = 'WebScanPage'

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @Gtk.Template.Callback()
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
        thread = Gio.Thread.new(self._run_scan_thread, target_url)
        thread.start()

    def _run_scan_thread(self, target_url):
        try:
            # Ensure URL has a scheme
            if not target_url.startswith(('http://', 'https://')):
                target_url = 'http://' + target_url # Default to http

            process = subprocess.Popen(
                ['nikto', '-h', target_url, '-Tuning', 'xCGIVulnerable'], # Basic tuning to find common issues
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate(timeout=300) # 5 minutes timeout

            GLib.idle_add(self._update_textview, stdout, stderr)

        except FileNotFoundError:
            GLib.idle_add(self.show_error_toast, "Nikto command not found. Please ensure it is installed and in your PATH.")
            GLib.idle_add(self._update_textview, "", "Error: Nikto not found.")
        except subprocess.TimeoutExpired:
            GLib.idle_add(self.show_error_toast, f"Scan for {target_url} timed out.")
            GLib.idle_add(self._update_textview, "", f"Error: Scan for {target_url} timed out after 5 minutes.")
        except Exception as e:
            GLib.idle_add(self.show_error_toast, f"An error occurred: {str(e)}")
            GLib.idle_add(self._update_textview, "", f"An error occurred: {str(e)}")
        finally:
            GLib.idle_add(self.scan_button.set_sensitive, True)
        return None # Required for Gio.Thread

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
        # For simplicity, printing to console. In a real app, you'd get the ToastOverlay.
        print(f"Error Toast: {message}")
        # Example of how you might get a toast overlay if it's part of the window:
        # top_level = self.get_ancestor(Adw.ApplicationWindow)
        # if top_level and hasattr(top_level, 'add_toast'):
        #     top_level.add_toast(Adw.Toast.new(message))
        # else:
        #     # Fallback if no toast overlay is found
        #     print(f"Error (no toast mechanism found): {message}")

        # Since we don't have direct access to the window's toast overlay here,
        # we can display the error in the text view itself as a fallback.
        buffer = self.results_textview.get_buffer()
        buffer.insert(buffer.get_end_iter(), f"\n\nERROR: {message}\n")
