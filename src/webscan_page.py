"""Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import subprocess
import re # Keep re for now, other parts of the file might use it, or remove if truly unused later.
import logging
logger = logging.getLogger(__name__)
from typing import Optional

import gi
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk, GtkSource

from .constants import RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_url # Added is_valid_url

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

    def __init__(self, **kwargs):
        """Initialize the WebScanPage."""
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
        target_url = self.url_entry.get_text().strip() # Added strip() here for consistency

        # Removed local url_pattern regex definition

        if not target_url:
            show_global_toast(self, "Target URL cannot be empty.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Target URL cannot be empty.")
            return

        # Use the new is_valid_url utility function
        # The original regex allowed http, https, ftp.
        # is_valid_url defaults to ['http', 'https'], so we provide the schemes.
        if not is_valid_url(target_url, schemes=['http', 'https', 'ftp']):
            show_global_toast(self, "Invalid URL format. Please enter a valid URL.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Invalid URL format. Please enter a valid URL.")
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
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done, None)
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
                                   source_object: GObject.Object,
                                   task_data: object,
                                   cancellable: Gio.Cancellable):
        """Execute the Nikto scan in a separate thread."""
        logger.debug("WebScanPage._run_scan_task_thread_func started")
        page_instance = source_object
        scan_data = page_instance._temp_scan_data

        target_url = scan_data["target_url"]
        force_ssl = scan_data["force_ssl"]
        cgi_vulns = scan_data["cgi_vulns"]
        interesting_content = scan_data["interesting_content"]
        evasion_active = scan_data.get("evasion", False)
        mutate_active = scan_data.get("mutate", False)
        maxtime_str = scan_data.get("maxtime", "")

        # Note: is_valid_url used in on_scan_button_clicked ensures scheme presence.
        # However, Nikto itself might prepend http:// if no scheme is given.
        # To be safe and explicit for Nikto's -h argument:
        if not target_url.startswith(("http://", "https://", "ftp://")): # Check against allowed schemes
            # Defaulting to http for nikto if scheme is missing after validation
            # This case should ideally not be hit if is_valid_url (with schemes) works as expected
            # and target_url is passed directly from input.
            # However, if is_valid_url allows URLs without explicit schemes (e.g. example.com)
            # then this logic is needed. The current is_valid_url requires a scheme.
            # Let's assume target_url from input might be schemeless and is_valid_url allows it.
            # The current is_valid_url in utils.py DOES require a scheme.
            # So this block might be less critical if target_url is always pre-schemed by user or validation.
            # For robustness with Nikto:
            logger.info("Prepending http:// to target URL for Nikto as scheme was missing or not http/https/ftp.")
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
        if cgi_vulns: tuning_options.append('2')
        if interesting_content: tuning_options.append('1')

        if tuning_options:
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
        except Exception as e:
            task.return_value((None, str(e), "Exception"))

    def _on_scan_task_done(self, source_object: GObject.Object, async_result_obj: Gio.AsyncResult, _user_data: object):
        """Handle completion of the Nikto scan task."""
        target_url = "Unknown URL"
        if hasattr(self, '_temp_scan_data') and self._temp_scan_data:
            target_url = self._temp_scan_data.get("target_url", target_url)

        active_task = self.current_web_scan_task
        if not active_task:
            logger.warning("_on_scan_task_done called but no active_task found.")
            if not self.scan_button.get_sensitive():
                self.scan_button.set_sensitive(True)
            return

        try:
            _intermediate_tuple_from_propagate = active_task.propagate_value()
            _status_or_bool, actual_payload_tuple = _intermediate_tuple_from_propagate
            stdout, stderr_or_error_msg, error_type = actual_payload_tuple

            if error_type == "FileNotFoundError":
                logger.exception("Nikto command not found. Ensure it's in PATH.")
                user_message = "Nikto command not found. Please ensure Nikto is installed and in your system's PATH."
                show_global_error(self, user_message)
                self._update_textview("", f"Error: {user_message}")
            elif error_type == "TimeoutExpired":
                logger.exception(f"Nikto scan for {target_url} timed out.")
                user_message = f"Scan for {target_url} timed out after 5 minutes."
                show_global_error(self, user_message)
                self._update_textview("", f"Error: {user_message}")
            elif error_type == "Exception":
                logger.exception(f"An unexpected error occurred during Nikto scan task for {target_url}:")
                user_message = "An unexpected error occurred during the scan. Please check the application logs for more details."
                show_global_error(self, user_message)
                self._update_textview("", user_message)
            else:
                self._update_textview(stdout, stderr_or_error_msg)

        except GLib.Error:
            logger.exception(f"GLib.Error during scan task finalization for {target_url}:")
            user_message = "A task finalization error occurred. Please check the application logs for more details."
            show_global_error(self, user_message)
            self._update_textview("", user_message)
        finally:
            self.scan_button.set_sensitive(True)
            if hasattr(self, '_temp_scan_data'):
                del self._temp_scan_data
            self.current_web_scan_task = None

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
            self.scan_button.clicked()
        else:
            logger.warning("Webscan scan button not available or not sensitive, cannot trigger scan.")
