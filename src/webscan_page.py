"""Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import subprocess
import logging
logger = logging.getLogger(__name__)
import re # Added for re.search
import ast # Added for ast.literal_eval
from typing import Optional, Dict, Any
import time # Added for polling loop
from enum import Enum

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
    webscan_status_action_row = Gtk.Template.Child() # Added
    webscan_status_spinner = Gtk.Template.Child("webscan_status_spinner")
    webscan_cancel_button = Gtk.Template.Child()

    # New UI elements for Nikto options
    nikto_format_combo_row = Gtk.Template.Child()
    no404_switch = Gtk.Template.Child()
    auth_bypass_switch = Gtk.Template.Child()

    def __init__(self, **kwargs):
        """Initialize the WebScanPage."""
        super().__init__(**kwargs)
        self.set_size_request(800, -1)
        self.current_web_scan_task: Optional[Gio.Task] = None
        self.current_web_scan_cancellable: Optional[Gio.Cancellable] = None
        self.current_nikto_process: Optional[subprocess.Popen] = None
        self._current_webscan_params: Optional[Dict[str, Any]] = None
        logger.debug("WebScanPage initialized")

        if self.webscan_status_action_row:
            self.webscan_status_action_row.set_subtitle("Idle") # type: ignore
        if self.webscan_status_spinner:
            self.webscan_status_spinner.set_spinning(False) # type: ignore
            self.webscan_status_spinner.set_visible(False) # type: ignore
        if self.webscan_cancel_button:
             self.webscan_cancel_button.set_visible(False) # type: ignore
             self.webscan_cancel_button.set_sensitive(False) # type: ignore


        if self.results_textview:
            buffer = self.results_textview.get_buffer()
            if buffer:
                lm = GtkSource.LanguageManager.get_default()
                language = lm.get_language("text")
                if language:
                    buffer.set_language(language)
                else:
                    logger.warning("GtkSource language '%s' not found. Syntax highlighting may not apply.", "text")

            self.results_textview.set_show_line_numbers(True)
            self.results_textview.set_monospace(True)
            self.results_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        self.url_entry.connect("entry-activated", self.on_scan_button_clicked)
        # Connect signal for the cancel button now that it's a template child
        if self.webscan_cancel_button:
            self.webscan_cancel_button.connect("clicked", self._on_cancel_scan_clicked)
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
                except Exception as e: # pylint: disable=broad-except # Clipboard operations can be unreliable
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

        self.scan_button.set_sensitive(False) # type: ignore

        if self.webscan_status_action_row:
            self.webscan_status_action_row.set_subtitle("Scanning...") # type: ignore
        if self.webscan_status_spinner:
            self.webscan_status_spinner.set_visible(True) # type: ignore
            self.webscan_status_spinner.start() # type: ignore
        if self.webscan_cancel_button:
            self.webscan_cancel_button.set_visible(True) # type: ignore
            self.webscan_cancel_button.set_sensitive(True) # type: ignore


        if self.current_web_scan_task and not self.current_web_scan_task.is_done():
            if self.current_web_scan_cancellable and not self.current_web_scan_cancellable.is_cancelled():
                logger.info("Cancelling previous web scan task before starting new one.")
                self.current_web_scan_cancellable.cancel()
                # The old task will clean itself up in its _on_scan_task_done.
                # We proceed to create and run a new task.

        self.current_web_scan_cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, self.current_web_scan_cancellable, self._on_scan_task_done, None) # type: ignore
        self.current_web_scan_task = task

        task_data_for_thread: Dict[str, Any] = {
            "target_url": target_url,
            "force_ssl": self.force_ssl_switch.get_active(), # type: ignore
            "cgi_vulns": self.cgi_vulns_switch.get_active(), # type: ignore
            "interesting_content": self.interesting_content_switch.get_active(), # type: ignore
            "evasion": self.evasion_switch.get_active(), # type: ignore
            "mutate": self.mutate_switch.get_active(), # type: ignore
            "maxtime": self.maxtime_entry_row.get_text().strip(), # type: ignore
            # New options for task_data_for_thread
            "nikto_format": self.nikto_format_combo_row.get_selected_item().get_string() if self.nikto_format_combo_row.get_selected_item() else "default (text)", # type: ignore
            "no404": self.no404_switch.get_active(), # type: ignore
            "auth_bypass": self.auth_bypass_switch.get_active() # type: ignore
        }
        self._current_webscan_params = task_data_for_thread # Store params in instance variable
        task.run_in_thread(self._run_scan_task_thread_func) # type: ignore

    def _run_scan_task_thread_func(self,
                                   task: Gio.Task, # Task object is still used for returning values/errors
                                   _source_object: GObject.Object,
                                   _task_data_unused: Any, # Parameter from Gio.Task.run_in_thread, not used for params
                                   cancellable: Gio.Cancellable):
        """Execute the Nikto scan in a separate thread, with cancellation support."""
        logger.debug("WebScanPage._run_scan_task_thread_func started")

        page_instance: WebScanPage = _source_object # type: ignore
        scan_params = page_instance._current_webscan_params
        # Optional: page_instance._current_webscan_params = None # To clear after reading

        if not scan_params:
            logger.error("WebScanPage: _run_scan_task_thread_func: _current_webscan_params is None.")
            # This indicates a programming error if scan_params is None.
            # The task object (if needed for error reporting) is 'task'.
            task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value, "Missing scan parameters in thread.") # type: ignore
            return

        target_url = scan_params["target_url"]
        force_ssl = scan_params.get("force_ssl", False)
        cgi_vulns = scan_params.get("cgi_vulns", False)
        interesting_content = scan_params.get("interesting_content", False)
        evasion_active = scan_params.get("evasion", False)
        mutate_active = scan_params.get("mutate", False)
        maxtime_str = scan_params.get("maxtime", "")
        # New params from scan_params
        nikto_format_str = scan_params.get("nikto_format", "default (text)")
        no404_active = scan_params.get("no404", False)
        auth_bypass_active = scan_params.get("auth_bypass", False)

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
        # Add new tuning options
        if auth_bypass_active:
            tuning_options.append('9') # '9' for Authentication Bypass

        # Nikto's -Tuning option takes a string of numbers (e.g., "12") to specify checks.
        # If no tuning switches are active, the -Tuning option is omitted.
        if tuning_options:
            # Using set avoids duplicates and sorted() ensures a consistent order.
            tuning_string = "".join(sorted(list(set(tuning_options))))
            if tuning_string:
                nikto_command.extend(['-Tuning', tuning_string])

        # Add format option
        if nikto_format_str and nikto_format_str not in ["default (text)", "txt (text)"]:
            format_cli_value = nikto_format_str
            if nikto_format_str == "html":
                format_cli_value = "htm" # Nikto uses 'htm' for HTML
            # 'csv', 'xml' are fine as is. 'txt' is default, so no need to add.
            # Other formats like 'nbe' could be added here if UI supports them.
            if format_cli_value not in ["txt"]: # Nikto's default is text, -Format txt is redundant
                 nikto_command.extend(['-Format', format_cli_value])

        # Add no404 option
        if no404_active:
            nikto_command.append('-no404')

        logger.debug(f"Constructed Nikto command: {nikto_command}")

        try:
            if cancellable.is_cancelled():
                task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.CANCELLED.value, "Scan cancelled before Nikto process start.") # type: ignore
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
                        except Exception as e_term: # pylint: disable=broad-except # Process termination can have diverse errors
                            logger.error(f"Error terminating Nikto process: {e_term}")
                    task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.CANCELLED.value, "Scan cancelled by user.") # type: ignore
                    self.current_nikto_process = None
                    return

                if process.poll() is not None:
                    break

                time.sleep(0.2) # Polling interval

            stdout_str, stderr_str = process.communicate()
            self.current_nikto_process = None # Clear process reference

            actual_nikto_output: Optional[str] = None
            parsed_output_from_stderr = False

            if stderr_str:
                cleaned_stderr_str = stderr_str.strip()
                if cleaned_stderr_str.startswith("('") and cleaned_stderr_str.endswith("', '')"):
                    try:
                        parsed_content = ast.literal_eval(cleaned_stderr_str)
                        if isinstance(parsed_content, tuple) and len(parsed_content) == 2 and \
                           isinstance(parsed_content[0], str) and parsed_content[1] == '':
                            actual_nikto_output = parsed_content[0]
                            parsed_output_from_stderr = True
                            logger.info("Successfully parsed Nikto's primary output from its stderr channel (which contained a stringified tuple).")
                            if stdout_str: # Log if stdout also had content, as it will be ignored
                                logger.info(f"Nikto stdout channel contained: '{stdout_str[:200]}...' (will be ignored as primary output was found in stderr).")
                        else:
                            logger.warning(f"Nikto stderr appeared to be a stringified tuple but did not match the expected structure: {cleaned_stderr_str}")
                    except (SyntaxError, ValueError) as e:
                        logger.warning(f"Could not parse Nikto stderr string as a Python literal: {e}. Stderr content: {cleaned_stderr_str}")

            if process.returncode not in [0, 1]:
                error_output_detail = actual_nikto_output if parsed_output_from_stderr else (stderr_str or stdout_str)
                logger.error(f"Nikto process finished with an unexpected error code {process.returncode}. Output/Stderr: {error_output_detail}")
                task.return_new_error_literal(
                    GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN),
                    WebScanErrorType.GENERIC.value,
                    f"Nikto execution error (code {process.returncode}). Output (if any):\n{error_output_detail}"
                )
                return

            if parsed_output_from_stderr:
                task.return_value((actual_nikto_output, None)) # Parsed output in stdout slot, original stderr (now None)
            else:
                task.return_value((stdout_str, stderr_str)) # Original behavior
        except FileNotFoundError:
            logger.error("Nikto command not found. Ensure it's in PATH.")
            task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.NIKTO_NOT_FOUND.value, "Nikto command not found. Please ensure it is installed and in your system's PATH.") # type: ignore
        # TimeoutExpired for process.communicate() is removed as we poll.
        # A global timeout for the entire scan could be implemented by checking time in the polling loop.
        except Exception as e: # pylint: disable=broad-except # Catch all for thread to report error via Gio.Task
            logger.exception(f"An unexpected error occurred during Nikto scan task: {e}")
            task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value, str(e)) # type: ignore
        finally:
            self.current_nikto_process = None # Ensure cleared

    def _on_scan_task_done(self, _source_object: GObject.Object, result: Gio.AsyncResult, _user_data: object): # type: ignore
        """Handle completion of the Nikto scan task."""
        target_url = "Unknown URL"
        # Retrieve target_url from instance variable
        if self._current_webscan_params:
            target_url = self._current_webscan_params.get("target_url", target_url)

        # It's good practice to clear the stored params now if they are no longer needed,
        # especially if the page can start a new scan before this callback fully completes
        # for a previous one (though current_web_scan_task should prevent that).
        # self._current_webscan_params = None # Consider clearing later or if issues arise.

        logger.info(f"Nikto scan task done for {target_url}.")
        stdout: Optional[str] = None
        stderr: Optional[str] = None

        try:
            # propagate_value() will return the (stdout, stderr) tuple on success
            # or raise GLib.Error if task.return_new_error_literal was called.
            # The 'result' parameter itself is the Gio.Task object here.
            returned_data = result.propagate_value() # type: ignore
            if isinstance(returned_data, tuple) and len(returned_data) == 2:
                s_out, s_err = returned_data
            elif hasattr(returned_data, 'value') and isinstance(returned_data.value, tuple) and len(returned_data.value) == 2: # Handle potential Gio.DBusCallFlags like wrapper
                s_out, s_err = returned_data.value
            else:
                s_out, s_err = None, None # Ensure they are defined for the error case below
                logger.error(f"Nikto scan for {target_url} returned unexpected result format: {type(returned_data)}")
                show_global_error(self, "Scan returned unexpected data format.") # type: ignore
                self._update_textview(None, "Error: Scan returned unexpected data format.", is_error_message=True)
                # Fall through to finally block for UI reset, status will be updated there

            # Ensure stdout and stderr are strings or None
            final_stdout: Optional[str] = None
            if s_out is not None:
                if not isinstance(s_out, str):
                    logger.warning(f"Nikto stdout was type {type(s_out)}, expected str. Converting. Value: {s_out}")
                    final_stdout = str(s_out)
                else:
                    final_stdout = s_out

            final_stderr: Optional[str] = None
            if s_err is not None:
                if not isinstance(s_err, str):
                    logger.warning(f"Nikto stderr was type {type(s_err)}, expected str. Converting. Value: {s_err}")
                    final_stderr = str(s_err)
                else:
                    final_stderr = s_err

            if final_stdout is not None or final_stderr is not None:
                self._update_textview(final_stdout, final_stderr, is_error_message=False)
                if self.webscan_status_action_row:
                    if final_stdout or final_stderr: # Check if there's actual content
                        self.webscan_status_action_row.set_subtitle("Scan complete. See results below.") # type: ignore
                    else: # Both are None or empty strings
                        self.webscan_status_action_row.set_subtitle("Scan complete. No output received.") # type: ignore
            elif s_out is None and s_err is None and not (isinstance(returned_data, tuple) and len(returned_data) == 2):
                # This case handles if returned_data was not the expected tuple, leading to s_out/s_err being None.
                # The error about "Scan returned unexpected data format" would have already been shown.
                # Here, we just ensure the status row reflects an issue if it's still "Scanning...".
                # The more specific error for textview is already handled by the logger.error and show_global_error above.
                if self.webscan_status_action_row and self.webscan_status_action_row.get_subtitle() == "Scanning...": # type: ignore
                     self.webscan_status_action_row.set_subtitle("Error: Unexpected scan result format.") # type: ignore
            else: # s_out and s_err were None from the start, and it was a valid tuple return
                if self.webscan_status_action_row:
                    self.webscan_status_action_row.set_subtitle("Scan complete. No output received.") # type: ignore

        except GLib.Error as e:
            logger.warning(f"Nikto scan task for {target_url} failed or was cancelled: {e.message} (Domain: {e.domain}, Code: {e.code})")

            brief_user_message = e.message # Default for status row and banner
            detailed_output_for_textview = f"Error: {e.message}" # Default for text view

            if e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.NIKTO_NOT_FOUND.value): # type: ignore
                brief_user_message = "Nikto command not found. Ensure Nikto is installed and in PATH."
                detailed_output_for_textview = f"Error: {brief_user_message}"
            elif e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.TIMEOUT.value): # type: ignore
                brief_user_message = f"Scan for {target_url} timed out."
                detailed_output_for_textview = f"Error: {brief_user_message}"
            elif e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.CANCELLED.value): # type: ignore
                brief_user_message = f"Scan for {target_url} was cancelled."
                detailed_output_for_textview = f"Error: {brief_user_message}"
            elif e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value): # type: ignore
                if "Missing scan parameters" in e.message:
                    brief_user_message = "Internal error: Missing scan parameters."
                    detailed_output_for_textview = f"Error: {brief_user_message}"
                elif "Nikto execution error" in e.message:
                    match = re.search(r"\(code (\d+)\)", e.message)
                    code_str = f" (code {match.group(1)})" if match else ""
                    brief_user_message = f"Nikto execution error{code_str}. See results for details."
                    # e.message already contains "Nikto execution error..." and the output
                    detailed_output_for_textview = f"Error: {e.message}"
                else: # Other generic errors not fitting the above patterns
                    brief_user_message = f"Scan failed: {e.message.splitlines()[0]}"
                    detailed_output_for_textview = f"Error: {e.message}"

            if self.webscan_status_action_row:
                self.webscan_status_action_row.set_subtitle(brief_user_message) # type: ignore
            show_global_error(self, brief_user_message) # type: ignore
            self._update_textview(None, detailed_output_for_textview, is_error_message=True)
        except Exception: # Catch any other Python exceptions from this callback itself
            logger.exception(f"Unexpected Python error in _on_scan_task_done for {target_url}:")
            user_message = "An unexpected error occurred."
            if self.webscan_status_action_row:
                self.webscan_status_action_row.set_subtitle(user_message) # type: ignore
            show_global_error(self, user_message + " Check logs for details.") # type: ignore
            self._update_textview(None, f"Error: {user_message}", is_error_message=True)
        finally:
            self.scan_button.set_sensitive(True) # type: ignore
            if self.webscan_cancel_button:
                self.webscan_cancel_button.set_sensitive(False) # type: ignore
                self.webscan_cancel_button.set_visible(False) # type: ignore
            if self.webscan_status_spinner:
                self.webscan_status_spinner.stop() # type: ignore
                self.webscan_status_spinner.set_visible(False) # type: ignore

            # Set status to Idle or Finished if it was still "Scanning..."
            if self.webscan_status_action_row :
                current_subtitle = self.webscan_status_action_row.get_subtitle() # type: ignore
                if current_subtitle == "Scanning...":
                     self.webscan_status_action_row.set_subtitle("Scan finished.") # type: ignore

            self.current_web_scan_task = None
            self.current_web_scan_cancellable = None
            self.current_nikto_process = None # Ensure cleared

    def _update_textview(self, stdout_content: Optional[str], stderr_content: Optional[str], is_error_message: bool = False):
        """Update the results TextView with Nikto's stdout and error messages/stderr."""
        buffer = self.results_textview.get_buffer()
        if stdout_content:
            buffer.insert(buffer.get_end_iter(), stdout_content)

        if stderr_content:
            if is_error_message: # This is a pre-formatted error message for the text view
                buffer.insert(buffer.get_end_iter(), "\n" + stderr_content) # Add newline before error block
            else: # This is raw stderr from Nikto on a successful run
                buffer.insert(buffer.get_end_iter(), "\n--- Nikto Standard Error Output ---\n" + stderr_content)

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
