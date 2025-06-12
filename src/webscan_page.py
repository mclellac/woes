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

from .constants import RESOURCE_PREFIX, APP_ID
from .utils import show_global_error, show_global_toast, is_valid_url
from .style_utils import apply_source_style_scheme

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    """Page for conducting web scans using Nikto, displaying results and errors."""

    __gtype_name__ = "WebScanPage"

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_scrolled_window = Gtk.Template.Child() # Parent of GtkSourceView
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
        """Initialize the WebScanPage.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage` constructor.
        :type kwargs: Any
        """

        super().__init__(**kwargs)
        self.source_view = GtkSource.View()
        # Every GtkSource.View needs a GtkSource.Buffer
        source_buffer = GtkSource.Buffer()
        self.source_view.set_buffer(source_buffer)

        self.source_view.set_hexpand(True)
        self.source_view.set_vexpand(True)
        self.source_view.set_monospace(True)
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        if self.results_scrolled_window:
            self.results_scrolled_window.set_child(self.source_view)
        else:
            # This case should ideally log an error if logger is available
            # For subtask, direct print might be visible if it runs in a context that shows stdout.
            print("ERROR: results_scrolled_window is None in __init__, cannot add GtkSource.View.")

        # The existing call to self._apply_webscan_source_view_style() in __init__ will handle
        # applying the language and style scheme after this setup.

        self.current_web_scan_task: Optional[Gio.Task] = None
        self.current_web_scan_cancellable: Optional[Gio.Cancellable] = None
        self.current_nikto_process: Optional[subprocess.Popen] = None
        self._current_webscan_params: Optional[Dict[str, Any]] = None
        self.settings = Gio.Settings.new(APP_ID)
        self.style_manager = Adw.StyleManager.get_default()
        logger.debug("WebScanPage initialized")

        if self.source_view:
            buffer = self.source_view.get_buffer()
            if buffer:
                lm = GtkSource.LanguageManager.get_default()
                language = lm.get_language("text")
                if language:
                    buffer.set_language(language)
                else:
                    logger.warning("GtkSource language '%s' not found. Syntax highlighting may not apply.", "text")

        self._apply_webscan_source_view_style() # Initial application

        self.style_manager.connect("notify::dark", self._on_webscan_source_style_settings_changed)
        self.settings.connect("changed::source-style-scheme", self._on_webscan_source_style_settings_changed)
        self.url_entry.connect("entry-activated", self.on_scan_button_clicked)
        # Connect signal for the cancel button now that it's a template child
        if self.webscan_cancel_button:
            self.webscan_cancel_button.connect("clicked", self._on_cancel_scan_clicked)
        if self.clear_results_button:
            self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)

    def _on_webscan_source_style_settings_changed(self, _manager_or_settings, _param_spec_or_key):
        """Handle theme or style scheme changes for WebScan's GtkSourceView.

        :param _manager_or_settings: The Adw.StyleManager or Gio.Settings object that emitted the signal.
        :type _manager_or_settings: Adw.StyleManager or Gio.Settings
        :param _param_spec_or_key: The GObject.ParamSpec or GSettings key that changed.
        :type _param_spec_or_key: GObject.ParamSpec or str
        """
        logger.info("WebScanPage: Dark theme or source style scheme changed. Applying new style.")
        self._apply_webscan_source_view_style()

    def _apply_webscan_source_view_style(self):
        if not hasattr(self, 'source_view') or not self.source_view:
            # print("DEBUG: _apply_webscan_source_view_style: self.source_view not ready")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print("DEBUG: _apply_webscan_source_view_style: buffer not ready")
            return

        is_dark = self.style_manager.get_dark()
        user_scheme_name = self.settings.get_string("source-style-scheme")
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        final_scheme_name = "Adwaita" # Default fallback

        if is_dark:
            if user_scheme_name.lower() in ["adwaita", "default", "classic", "light"]:
                final_scheme_name = "Adwaita-dark"
            else:
                if scheme_manager.get_scheme(user_scheme_name):
                    final_scheme_name = user_scheme_name
                else:
                    # print(f"DEBUG: User scheme '{user_scheme_name}' not found for dark theme, falling back to Adwaita-dark.")
                    final_scheme_name = "Adwaita-dark"
        else: # Light theme
            if user_scheme_name.lower() in ["adwaita-dark", "dark"]:
                final_scheme_name = "Adwaita"
            else:
                if scheme_manager.get_scheme(user_scheme_name):
                    final_scheme_name = user_scheme_name
                else:
                    # print(f"DEBUG: User scheme '{user_scheme_name}' not found for light theme, falling back to Adwaita.")
                    final_scheme_name = "Adwaita"

        if not scheme_manager.get_scheme(final_scheme_name):
            # print(f"DEBUG: Scheme '{final_scheme_name}' could not be loaded. Defaulting to basic Adwaita (light/dark).")
            final_scheme_name = "Adwaita-dark" if is_dark else "Adwaita"

        # print(f"DEBUG: Applying source style scheme: {final_scheme_name} (Dark: {is_dark}, User: {user_scheme_name})")
        apply_source_style_scheme(scheme_manager, buffer, final_scheme_name)

    def __del__(self):
        """Clean up when the WebScanPage is destroyed."""
        if self.current_web_scan_cancellable and \
           not self.current_web_scan_cancellable.is_cancelled():
            logger.info("WebScanPage being destroyed, cancelling ongoing Nikto scan.")
            self.current_web_scan_cancellable.cancel()

    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        """Handles click on the 'Cancel Scan' button.

        :param _button: The Gtk.Button that was clicked (unused).
        :type _button: Gtk.Button
        """
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
        """Handle click of the 'Clear Results' button.

        :param _button: The Gtk.Button that was clicked (unused).
        :type _button: Gtk.Button
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            # print(f"DEBUG: _on_clear_results_clicked - source_view not found")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print(f"DEBUG: _on_clear_results_clicked - buffer not found")
            return
        logger.info("Webscan results cleared by user action.")
        if self.source_view:
            buffer = self.source_view.get_buffer()
            buffer.set_text("")

    def _on_copy_results_clicked(self, _button: Gtk.Button):
        """Handle click of the 'Copy Results' button.

        :param _button: The Gtk.Button that was clicked (unused).
        :type _button: Gtk.Button
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            # print(f"DEBUG: _on_copy_results_clicked - source_view not found")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print(f"DEBUG: _on_copy_results_clicked - buffer not found")
            return
        logger.info("Copying webscan results to clipboard.")
        if self.source_view:
            buffer = self.source_view.get_buffer()
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
        """Handle the 'Scan' button click event.

        :param _widget: The Gtk.Button that was clicked (unused).
        :type _widget: Gtk.Button
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            # print(f"DEBUG: on_scan_button_clicked - source_view not found")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print(f"DEBUG: on_scan_button_clicked - buffer not found")
            return
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

        buffer = self.source_view.get_buffer()
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
        """Execute the Nikto scan in a separate thread, with cancellation support.

        :param task: The Gio.Task associated with this operation.
        :type task: Gio.Task
        :param _source_object: The GObject source of the task.
        :type _source_object: GObject.Object
        :param _task_data_unused: Additional data passed to the task (unused).
        :type _task_data_unused: Any
        :param cancellable: A Gio.Cancellable object to monitor for cancellation.
        :type cancellable: Gio.Cancellable
        """
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
                logger.debug(f"Attempting to parse Nikto output from stderr. Cleaned stderr string (first 500 chars): {cleaned_stderr_str[:500]}")

                # Regex to find a string like "('nikto output', '')" in stderr.
                # Note: \x5B is '[', \x27 is ''', \x22 is '"', \x5D is ']'.
                # These hex escapes were used to resolve a severe parser issue in the environment.
                regex_part1 = r"^\(\s*(\x5B\x27\x22\x5D)(.*?)"
                regex_part2 = r"\1\s*,\s*(\x5B\x27\x22\x5D)"
                regex_part3 = r"\3\s*\)$"
                regex_pattern_str = regex_part1 + regex_part2 + regex_part3
                try:
                    pattern = re.compile(regex_pattern_str, re.DOTALL)
                    match = pattern.search(cleaned_stderr_str)
                except re.error as e_regex: # Should not happen with this fixed pattern
                    logger.error(f"Regex compilation failed: {e_regex}")
                    match = None # Ensure match is defined

                if match:
                    nikto_output_raw_string = match.group(2)
                    quote_char_for_literal_eval = match.group(1)

                    logger.info(f"Regex matched tuple-like string in stderr. Raw content (first 200 chars): {nikto_output_raw_string[:200]}")
                    try:
                        # Reconstruct a valid Python string literal for ast.literal_eval to unescape.
                        string_to_evaluate = quote_char_for_literal_eval + nikto_output_raw_string + quote_char_for_literal_eval
                        actual_nikto_output = ast.literal_eval(string_to_evaluate)
                        parsed_output_from_stderr = True
                        logger.info("Successfully parsed Nikto's primary output from stderr using regex and ast.literal_eval.")
                        if stdout_str:
                            logger.info(f"Nikto stdout channel contained (will be ignored): '{stdout_str[:200]}...'")
                    except Exception as e:
                        logger.warning(f"ast.literal_eval failed for extracted string: {e}. String (first 200 chars): {nikto_output_raw_string[:200]}", exc_info=True)
                else:
                    logger.info("Nikto stderr did not match tuple-like string pattern via regex. Trying original startswith/endswith check.")
                    # Fallback to original logic if regex doesn't match.
                    if cleaned_stderr_str.startswith("('") and cleaned_stderr_str.endswith("', '')"):
                        try:
                            parsed_content_tuple = ast.literal_eval(cleaned_stderr_str)
                            if isinstance(parsed_content_tuple, tuple) and len(parsed_content_tuple) == 2 and \
                               isinstance(parsed_content_tuple[0], str) and parsed_content_tuple[1] == '':
                                actual_nikto_output = parsed_content_tuple[0]
                                parsed_output_from_stderr = True
                                logger.info("Successfully parsed Nikto's primary output from stderr using original startswith/endswith method.")
                                if stdout_str:
                                    logger.info(f"Nikto stdout channel contained (original method, will be ignored): '{stdout_str[:200]}...'")
                            else:
                                logger.warning(f"Original method: Nikto stderr matched start/end but internal structure was not as expected. Data: {cleaned_stderr_str[:200]}...")
                        except (SyntaxError, ValueError) as e:
                            logger.warning(f"Original method: ast.literal_eval failed for cleaned_stderr_str: {e}. Data (first 200 chars): {cleaned_stderr_str[:200]}", exc_info=True)
                    else:
                        logger.info("Nikto stderr did not match any known tuple-like string pattern. It will be treated as plain text.")

                # If parsed_output_from_stderr is True, actual_nikto_output contains the desired string.
                # If False, the original stdout_str and stderr_str will be used later.

            if process.returncode not in [0, 1]:
                error_output_detail = actual_nikto_output if parsed_output_from_stderr else (stderr_str or stdout_str) # Ensure this line is not duplicated if it's outside the 'if stderr_str:' block
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
        """Handle completion of the Nikto scan task.

        :param _source_object: The GObject source of the task.
        :type _source_object: GObject.Object
        :param result: The Gio.AsyncResult from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: object
        """
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
        """Update the results TextView with Nikto's stdout and error messages/stderr.

        :param stdout_content: The standard output content from Nikto.
        :type stdout_content: Optional[str]
        :param stderr_content: The standard error content from Nikto or a pre-formatted error message.
        :type stderr_content: Optional[str]
        :param is_error_message: If True, stderr_content is treated as a pre-formatted error for display.
                                 Otherwise, it's treated as raw stderr output from Nikto.
        :type is_error_message: bool
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            # print("DEBUG: _update_textview - source_view not found")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print("DEBUG: _update_textview - buffer not found")
            return
        buffer = self.source_view.get_buffer()
        if stdout_content:
            buffer.insert(buffer.get_end_iter(), stdout_content)

        if stderr_content:
            if is_error_message: # This is a pre-formatted error message for the text view
                buffer.insert(buffer.get_end_iter(), "\n" + stderr_content) # Add newline before error block
            else: # This is raw stderr from Nikto on a successful run
                buffer.insert(buffer.get_end_iter(), "\n--- Nikto Standard Error Output ---\n" + stderr_content)

        scroll_adj = self.results_scrolled_window.get_vadjustment() if self.results_scrolled_window else None
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
