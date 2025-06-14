"""
Defines the WebScan page for the Woes application.

This page provides a simple interface to run Nikto scans against a target URL
and display the results.
"""
import subprocess
import logging
logger = logging.getLogger(__name__)
import re
import ast
from typing import Optional, Dict, Any
import time
from enum import Enum

import gi
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk

from .constants import RESOURCE_PREFIX, APP_ID
from .utils import show_global_error, show_global_toast, is_valid_url

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    """Page for conducting web scans using Nikto, displaying results and errors."""

    __gtype_name__ = "WebScanPage"

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_scrolled_window = Gtk.Template.Child()
    force_ssl_switch = Gtk.Template.Child()
    cgi_vulns_switch = Gtk.Template.Child()
    interesting_content_switch = Gtk.Template.Child()
    evasion_switch = Gtk.Template.Child()
    mutate_switch = Gtk.Template.Child()
    maxtime_entry_row = Gtk.Template.Child()
    clear_results_button = Gtk.Template.Child()
    copy_results_button = Gtk.Template.Child()
    webscan_status_action_row = Gtk.Template.Child()
    webscan_status_spinner = Gtk.Template.Child("webscan_status_spinner")
    webscan_cancel_button = Gtk.Template.Child()

    # New UI elements for Nikto options
    nikto_format_combo_row = Gtk.Template.Child()
    nikto_output_file_row = Gtk.Template.Child()
    nikto_output_file_button = Gtk.Template.Child()
    no404_switch = Gtk.Template.Child()
    auth_bypass_switch = Gtk.Template.Child()

    def __init__(self, **kwargs):
        """
        Initialize the WebScanPage.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage` constructor.
        """
        super().__init__(**kwargs)
        self.source_view = Gtk.TextView()
        self.source_view.set_name("webscan-output-textview")
        source_buffer = Gtk.TextBuffer()
        self.source_view.set_buffer(source_buffer)

        self.source_view.set_hexpand(True)
        self.source_view.set_vexpand(True)
        self.source_view.set_monospace(True)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.source_view.set_editable(False)

        if self.results_scrolled_window:
            self.results_scrolled_window.set_child(self.source_view)
        else:
            logger.error("results_scrolled_window is None in __init__, cannot add Gtk.TextView.")

        self.current_web_scan_task: Optional[Gio.Task] = None
        self.current_web_scan_cancellable: Optional[Gio.Cancellable] = None
        self.current_nikto_process: Optional[subprocess.Popen] = None
        self._current_webscan_params: Optional[Dict[str, Any]] = None
        self.settings = Gio.Settings.new(APP_ID)
        self.style_manager = Adw.StyleManager.get_default()

        if self.scan_button:
            self.scan_button.get_style_context().add_class("suggested-action")
        if self.webscan_cancel_button:
            self.webscan_cancel_button.get_style_context().add_class("destructive-action")
        if self.clear_results_button:
            self.clear_results_button.get_style_context().add_class("destructive-action")
        if self.copy_results_button:
            self.copy_results_button.get_style_context().add_class("flat")
        if self.nikto_output_file_button:
            self.nikto_output_file_button.get_style_context().add_class("flat")

        if self.clear_results_button:
            self.clear_results_button.set_sensitive(False)
        if self.copy_results_button:
            self.copy_results_button.set_sensitive(False)

        self.url_entry.connect("entry-activated", self.on_scan_button_clicked)

        if self.scan_button:
            self.scan_button.connect("clicked", self.on_scan_button_clicked)
        else:
            logger.error("scan_button is None in __init__, cannot connect its clicked signal.")

        if self.webscan_cancel_button:
            self.webscan_cancel_button.connect("clicked", self._on_cancel_scan_clicked)
        if self.clear_results_button:
            self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)

        if self.nikto_format_combo_row:
            self.nikto_format_combo_row.connect("notify::selected-item", self._on_nikto_format_changed)
        if self.nikto_output_file_button:
            self.nikto_output_file_button.connect("clicked", self._on_nikto_output_file_button_clicked)

        self._update_results_actions_sensitivity()

    def __del__(self):
        """Clean up when the WebScanPage is destroyed."""
        if self.current_web_scan_cancellable and \
           not self.current_web_scan_cancellable.is_cancelled():
            logger.info("WebScanPage being destroyed, cancelling ongoing Nikto scan.")
            self.current_web_scan_cancellable.cancel()

    def _on_nikto_format_changed(self, combo_row: Adw.ComboRow, _param_spec: GObject.ParamSpec):
        """
        Handle changes in the Nikto output format selection.

        Updates the sensitivity of the output file row based on whether
        the selected format requires an output file.

        :param combo_row: The Adw.ComboRow for Nikto format selection.
        :param _param_spec: The GObject.ParamSpec of the property that changed (unused).
        """
        selected_item = combo_row.get_selected_item()
        if not selected_item:
            return

        selected_format = selected_item.get_string()
        # These formats require an output file (display strings from combo box)
        formats_requiring_file = ["csv", "xml", "html"]

        is_file_required = selected_format in formats_requiring_file

        if self.nikto_output_file_row:
            self.nikto_output_file_row.set_sensitive(is_file_required)
            if not is_file_required:
                self.nikto_output_file_row.set_text("")
        logger.debug(f"Nikto format changed to: {selected_format}. File required: {is_file_required}")

    def _on_nikto_output_file_button_clicked(self, _button: Gtk.Button):
        """
        Handle click of the 'Choose Output File' button for Nikto.

        Opens a Gtk.FileChooserNative dialog to allow the user to select
        a save location and filename for Nikto's output.

        :param _button: The Gtk.Button that was clicked (unused).
        """
        dialog = Gtk.FileChooserNative.new(
            "Save Nikto Output",
            self.get_native(),
            Gtk.FileChooserAction.SAVE,
            "_Save",
            "_Cancel"
        )
        dialog.set_modal(True)

        selected_format_item = self.nikto_format_combo_row.get_selected_item()
        if selected_format_item:
            selected_format = selected_format_item.get_string()
            extension = selected_format
            if selected_format == "html":
                extension = "htm"
            elif selected_format in ["default (text)", "txt (text)"]:
                extension = "txt"

            if extension not in ["csv", "xml", "htm", "txt"]:
                extension = "txt"

            dialog.set_current_name(f"nikto_report.{extension}")

        def on_dialog_response(_source_object, response_id, _user_data):
            if response_id == Gtk.ResponseType.ACCEPT:
                file_path = dialog.get_file().get_path() # type: ignore
                if self.nikto_output_file_row:
                    self.nikto_output_file_row.set_text(file_path if file_path else "")
                    logger.info(f"Nikto output file set to: {file_path}")
            elif response_id == Gtk.ResponseType.CANCEL:
                logger.info("Nikto output file selection cancelled.")
            dialog.destroy()

        dialog.connect("response", on_dialog_response, None)
        dialog.show()

    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle click on the 'Cancel Scan' button.

        :param _button: The Gtk.Button that was clicked (unused).
        """
        logger.info("Cancel scan button clicked.")
        if self.current_web_scan_cancellable and \
           not self.current_web_scan_cancellable.is_cancelled():
            self.current_web_scan_cancellable.cancel()
            logger.info("Nikto scan cancellation requested via button.")
            if hasattr(self, 'webscan_cancel_button'):
                self.webscan_cancel_button.set_sensitive(False)
        else:
            logger.warning("No active scan or cancellable to cancel.")

    def _on_clear_results_clicked(self, _button: Gtk.Button):
        """
        Handle click of the 'Clear Results' button.

        :param _button: The Gtk.Button that was clicked (unused).
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            return

        buffer.set_text("")
        logger.info("Webscan results cleared by user action.")
        self._update_results_actions_sensitivity()

    def _on_copy_results_clicked(self, _button: Gtk.Button):
        """
        Handle click of the 'Copy Results' button.

        :param _button: The Gtk.Button that was clicked (unused).
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
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
        """
        Handle the 'Scan' button click event.

        :param _widget: The Gtk.Button that was clicked (unused).
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            return
        target_url = self.url_entry.get_text().strip()
        if target_url and not (target_url.startswith("http://") or target_url.startswith("https://") or target_url.startswith("ftp://")):
            logger.info(f"No scheme found in URL '{target_url}'. Prepending 'https://'.")
            target_url = "https://" + target_url

        if not target_url:
            show_global_toast(self, "Target URL cannot be empty.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Target URL cannot be empty.")
            self.scan_button.set_sensitive(True)
            return

        if not is_valid_url(target_url, schemes=['http', 'https', 'ftp']):
            show_global_toast(self, "Invalid URL format. Please enter a valid URL.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Invalid URL format. Please enter a valid URL.")
            self.scan_button.set_sensitive(True)
            return

        buffer = self.source_view.get_buffer()
        buffer.set_text(f"Scanning {target_url}...\n\n")
        self._update_results_actions_sensitivity()

        self.scan_button.set_sensitive(False)

        if self.webscan_status_action_row:
            self.webscan_status_action_row.set_subtitle("Scanning...")
        if self.webscan_status_spinner:
            self.webscan_status_spinner.set_visible(True)
            self.webscan_status_spinner.start()
        if self.webscan_cancel_button:
            self.webscan_cancel_button.set_visible(True)
            self.webscan_cancel_button.set_sensitive(True)


        if self.current_web_scan_task and not self.current_web_scan_task.is_done():
            if self.current_web_scan_cancellable and not self.current_web_scan_cancellable.is_cancelled():
                logger.info("Cancelling previous web scan task before starting new one.")
                self.current_web_scan_cancellable.cancel()

        self.current_web_scan_cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, self.current_web_scan_cancellable, self._on_scan_task_done, None)
        self.current_web_scan_task = task

        task_data_for_thread: Dict[str, Any] = {
            "target_url": target_url,
            "force_ssl": self.force_ssl_switch.get_active(),
            "cgi_vulns": self.cgi_vulns_switch.get_active(),
            "interesting_content": self.interesting_content_switch.get_active(),
            "evasion": self.evasion_switch.get_active(),
            "mutate": self.mutate_switch.get_active(),
            "maxtime": self.maxtime_entry_row.get_text().strip(),
            "nikto_format": self.nikto_format_combo_row.get_selected_item().get_string() if self.nikto_format_combo_row.get_selected_item() else "default (text)",
            "no404": self.no404_switch.get_active(),
            "auth_bypass": self.auth_bypass_switch.get_active()
        }
        task_data_for_thread["nikto_output_filename"] = self.nikto_output_file_row.get_text().strip() if self.nikto_output_file_row else ""
        self._current_webscan_params = task_data_for_thread
        task.run_in_thread(self._run_scan_task_thread_func)

    def _run_scan_task_thread_func(self,
                                   task: Gio.Task,
                                   _source_object: GObject.Object,
                                   _task_data_unused: Any,
                                   cancellable: Gio.Cancellable):
        """
        Execute the Nikto scan in a separate thread, with cancellation support.

        :param task: The Gio.Task associated with this operation.
        :param _source_object: The GObject source of the task.
        :param _task_data_unused: Additional data passed to the task (unused).
        :param cancellable: A Gio.Cancellable object to monitor for cancellation.
        """
        page_instance: WebScanPage = _source_object # type: ignore
        scan_params = page_instance._current_webscan_params

        if not scan_params:
            logger.error("WebScanPage: _run_scan_task_thread_func: _current_webscan_params is None.")
            task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value, "Missing scan parameters in thread.")
            return

        target_url = scan_params["target_url"]
        force_ssl = scan_params.get("force_ssl", False)
        cgi_vulns = scan_params.get("cgi_vulns", False)
        interesting_content = scan_params.get("interesting_content", False)
        evasion_active = scan_params.get("evasion", False)
        mutate_active = scan_params.get("mutate", False)
        maxtime_str = scan_params.get("maxtime", "")
        nikto_format_str = scan_params.get("nikto_format", "default (text)")
        no404_active = scan_params.get("no404", False)
        auth_bypass_active = scan_params.get("auth_bypass", False)

        if force_ssl:
            if target_url.startswith("http://"):
                target_url = target_url.replace("http://", "https://", 1)
                logger.info(f"Force SSL is ON. Changed URL to: {target_url}")
            elif not target_url.startswith("https://"):
                if "://" in target_url and not target_url.startswith("ftp://"):
                    target_url = "https://" + target_url.split("://", 1)[-1]
                elif "://" not in target_url:
                    target_url = "https://" + target_url
                logger.info(f"Force SSL is ON and original scheme was not http/https. Ensured URL is: {target_url}")

        if target_url.startswith("ftp://"):
            logger.warning("FTP URL provided for Nikto's -h option. Nikto primarily scans HTTP/S. Attempting to use the host part with http://. This may not yield meaningful web scan results.")
            target_url = target_url.replace("ftp://", "http://", 1)

        if "://" not in target_url:
            logger.warning(f"URL '{target_url}' still schemeless in scan thread. Defaulting to http://.")
            target_url = "http://" + target_url

        nikto_command = ['nikto', '-h', target_url]

        output_filename = scan_params.get("nikto_output_filename", "").strip()
        formats_requiring_file = ["csv", "xml", "html"]
        is_file_required = nikto_format_str in formats_requiring_file

        if is_file_required:
            if not output_filename:
                logger.error("A Nikto output format requiring a filename was selected, but no filename was provided.")
                task.return_new_error_literal(
                    GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN),
                    WebScanErrorType.GENERIC.value,
                    "Output format requires a filename, but none was provided."
                )
                return
            nikto_command.extend(['-o', output_filename])

        if force_ssl:
            nikto_command.append('-ssl')
        if evasion_active:
            nikto_command.extend(['-evasion', '1'])

        if mutate_active:
            plugin_argument = "@@DEFAULT;tests(,all)"
            nikto_command.extend(['-Plugin', plugin_argument])
            logger.info(f"Using -Plugin {plugin_argument} instead of deprecated -mutate.")

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
        if cgi_vulns:
            tuning_options.append('2')
        if interesting_content:
            tuning_options.append('1')
        if auth_bypass_active:
            tuning_options.append('9')

        if tuning_options:
            tuning_string = "".join(sorted(list(set(tuning_options))))
            if tuning_string:
                nikto_command.extend(['-Tuning', tuning_string])

        if nikto_format_str and nikto_format_str not in ["default (text)", "txt (text)"]:
            format_cli_value = nikto_format_str
            if nikto_format_str == "html":
                format_cli_value = "htm"
            if format_cli_value not in ["txt"]:
                 nikto_command.extend(['-Format', format_cli_value])

        if no404_active:
            nikto_command.append('-no404')

        logger.debug(f"Constructed Nikto command: {nikto_command}")

        try:
            if cancellable.is_cancelled():
                task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.CANCELLED.value, "Scan cancelled before Nikto process start.")
                return

            process = subprocess.Popen(nikto_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            self.current_nikto_process = process

            stdout_str, stderr_str = "", ""
            while True:
                if cancellable.is_cancelled():
                    logger.info("Nikto scan cancellation polling: Scan cancelled.")
                    if process.poll() is None:
                        try:
                            logger.info("Terminating Nikto process.")
                            process.terminate()
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            logger.warning("Nikto process did not terminate gracefully, killing.")
                            process.kill()
                        except Exception as e_term:
                            logger.error(f"Error terminating Nikto process: {e_term}")
                    task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.CANCELLED.value, "Scan cancelled by user.")
                    self.current_nikto_process = None
                    return

                if process.poll() is not None:
                    break

                time.sleep(0.2)

            stdout_str, stderr_str = process.communicate()
            self.current_nikto_process = None

            raw_original_stderr = stderr_str if isinstance(stderr_str, str) else ""
            raw_original_stdout = stdout_str if isinstance(stdout_str, str) else ""
            rfi_warning_detected_in_raw = False
            rfi_warning_signature = "- ***** RFIURL is not defined in nikto.conf--no RFI tests will run *****"

            if rfi_warning_signature in raw_original_stderr or rfi_warning_signature in raw_original_stdout:
                rfi_warning_detected_in_raw = True
                logger.info("RFIURL warning detected in original Nikto output.")

            main_report_content: Optional[str] = None
            aux_output: Optional[str] = None
            report_extracted_from_tuple = False

            tuple_end_marker = "', None))"
            tuple_end_marker_alt = "', '')"

            if isinstance(stderr_str, str):
                stripped_stderr = stderr_str.strip()
                if stripped_stderr.startswith("('") and \
                   (stripped_stderr.endswith(tuple_end_marker) or stripped_stderr.endswith(tuple_end_marker_alt)):
                    logger.debug("Attempting to parse tuple string from stderr_str.")
                    try:
                        parsed_tuple = ast.literal_eval(stripped_stderr)
                        if isinstance(parsed_tuple, tuple) and len(parsed_tuple) >= 1 and isinstance(parsed_tuple[0], str):
                            main_report_content = parsed_tuple[0]
                            aux_output = stdout_str
                            report_extracted_from_tuple = True
                            logger.info("Report extracted from string representation of tuple in stderr.")
                        else:
                            logger.warning("stderr_str looked like a tuple string but ast.literal_eval returned unexpected structure.")
                    except Exception as e_eval_stderr:
                        logger.warning(f"ast.literal_eval failed for stderr_str: {e_eval_stderr}. String (first 200): {stripped_stderr[:200]}")

            if not report_extracted_from_tuple and isinstance(stdout_str, str):
                stripped_stdout = stdout_str.strip()
                if stripped_stdout.startswith("('") and \
                   (stripped_stdout.endswith(tuple_end_marker) or stripped_stdout.endswith(tuple_end_marker_alt)):
                    logger.debug("Attempting to parse tuple string from stdout_str.")
                    try:
                        parsed_tuple = ast.literal_eval(stripped_stdout)
                        if isinstance(parsed_tuple, tuple) and len(parsed_tuple) >= 1 and isinstance(parsed_tuple[0], str):
                            main_report_content = parsed_tuple[0]
                            aux_output = stderr_str
                            report_extracted_from_tuple = True
                            logger.info("Report extracted from string representation of tuple in stdout.")
                        else:
                            logger.warning("stdout_str looked like a tuple string but ast.literal_eval returned unexpected structure.")
                    except Exception as e_eval_stdout:
                        logger.warning(f"ast.literal_eval failed for stdout_str: {e_eval_stdout}. String (first 200): {stripped_stdout[:200]}")

            if not report_extracted_from_tuple:
                logger.info("No tuple-like string processed. Using direct stdout/stderr.")
                if stdout_str:
                    main_report_content = stdout_str
                    aux_output = stderr_str
                elif stderr_str:
                    main_report_content = stderr_str
                    aux_output = None
                else:
                    main_report_content = ""
                    aux_output = None

            if main_report_content is None:
                main_report_content = ""
            if not isinstance(main_report_content, str):
                logger.warning(f"main_report_content was type {type(main_report_content)}, converting to string. Value (first 100 chars): {str(main_report_content)[:100]}")
                main_report_content = str(main_report_content)

            main_report_content = main_report_content.replace('\\n', '\n')
            logger.debug("Applied newline unescaping to main_report_content.")


            if aux_output is not None:
                if not isinstance(aux_output, str):
                    logger.warning(f"aux_output was type {type(aux_output)}, converting to string. Value (first 100 chars): {str(aux_output)[:100]}")
                    aux_output = str(aux_output)

                if aux_output.strip() == "" or aux_output.strip().lower() == "none":
                    aux_output = None
                    logger.debug("aux_output was empty or 'None', set to actual None.")
                elif aux_output:
                     aux_output = aux_output.replace('\\n', '\n')
                     logger.debug("Applied newline unescaping to aux_output.")

            if aux_output and rfi_warning_signature in aux_output:
                aux_output = aux_output.replace(rfi_warning_signature, '').strip()
                if not aux_output.strip():
                    aux_output = None
                logger.info("Filtered RFIURL warning from Nikto's aux_output.")


            if process.returncode not in [0, 1]:
                error_output_detail = main_report_content if main_report_content else (aux_output or "")
                logger.error(f"Nikto process finished with an unexpected error code {process.returncode}. Output/Stderr: {error_output_detail[:500]}...")
                task.return_new_error_literal(
                    GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN),
                    WebScanErrorType.GENERIC.value,
                    f"Nikto execution error (code {process.returncode}). Output (if any):\n{error_output_detail}"
                )
                return

            logger.debug(f"PRE-RETURN: main_report_content TYPE: {type(main_report_content)}, VALUE: {str(main_report_content)[:200]}")
            logger.debug(f"PRE-RETURN: aux_output TYPE: {type(aux_output)}, VALUE: {str(aux_output)[:200]}")


            if rfi_warning_detected_in_raw:
                instructional_message = (
                    "\n\n[FYI on Nikto RFIURL Configuration]\n"
                    "Nikto's Remote File Inclusion (RFI) tests did not run because 'RFIURL' "
                    "is not defined in your nikto.conf file. Nikto reported: "
                    f"\n'{rfi_warning_signature}'\n"
                    "To enable these specific tests, you may need to configure RFIURL in your Nikto "
                    "installation's configuration file (usually nikto.conf).\n\n"
                    "How to configure RFIURL in nikto.conf:\n"
                    "1. Locate nikto.conf (e.g., /etc/nikto.conf, or in Nikto's installation directory).\n"
                    "2. Open it and find the '#RFIURL=' line.\n"
                    "3. Uncomment and set it, e.g., RFIURL=http://your.accessible.server/rfi_probe.txt\n"
                    "   (The URL should point to a harmless file you control for testing.)\n"
                    "4. Save nikto.conf and rerun the scan if RFI tests are desired."
                )

                if isinstance(main_report_content, str):
                    main_report_content = main_report_content.replace(rfi_warning_signature, "")
                if isinstance(aux_output, str):
                    aux_output = aux_output.replace(rfi_warning_signature, "")

                if aux_output and aux_output.strip():
                    aux_output = aux_output.strip() + instructional_message
                else:
                    aux_output = instructional_message.strip()
                logger.info("Appended RFIURL instructional message to aux_output.")

            task.return_value((
                str(main_report_content) if main_report_content is not None else "",
                str(aux_output) if aux_output is not None else None
            ))
        except FileNotFoundError:
            logger.error("Nikto command not found. Ensure it's in PATH.")
            task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.NIKTO_NOT_FOUND.value, "Nikto command not found. Please ensure it is installed and in your system's PATH.")
        except Exception as e:
            logger.exception(f"An unexpected error occurred during Nikto scan task: {e}")
            task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value, str(e))
        finally:
            self.current_nikto_process = None

    def _on_scan_task_done(self, _source_object: GObject.Object, result: Gio.AsyncResult, _user_data: object):
        """
        Handle completion of the Nikto scan task.

        :param _source_object: The GObject source of the task.
        :param result: The Gio.AsyncResult from the completed task.
        :param _user_data: User data passed with the callback (unused).
        """
        target_url = "Unknown URL"
        if self._current_webscan_params:
            target_url = self._current_webscan_params.get("target_url", target_url)

            # self._current_webscan_params = None

        logger.info(f"Nikto scan task done for {target_url}.")

        try:
            returned_data = result.propagate_value()


            if isinstance(returned_data, tuple) and len(returned_data) == 2:
                s_out, s_err = returned_data
            elif hasattr(returned_data, 'value') and isinstance(returned_data.value, tuple) and len(returned_data.value) == 2:
                s_out, s_err = returned_data.value
            else:
                s_out, s_err = None, None
                logger.error(f"Nikto scan for {target_url} returned unexpected result format: {type(returned_data)}")
                show_global_error(self, "Scan returned unexpected data format.")
                self._update_textview(None, "Error: Scan returned unexpected data format.", is_error_message=True)

            final_stdout: Optional[str]
            if s_out is None:
                logger.info("_on_scan_task_done: s_out (main report) is None, defaulting to empty string.")
                final_stdout = ""
            elif not isinstance(s_out, str):
                logger.warning(f"_on_scan_task_done: s_out (main report) was type {type(s_out)}, expected str. Converting. Value (first 100 chars): {str(s_out)[:100]}")
                final_stdout = str(s_out)
            else:
                final_stdout = s_out

            if final_stdout:
                final_stdout = final_stdout.replace('\\n', '\n')
                logger.debug("Applied .replace('\\\\n', '\\n') to final_stdout in _on_scan_task_done.")

            final_stderr: Optional[str] = None
            if s_err is not None:
                if not isinstance(s_err, str):
                    logger.warning(f"_on_scan_task_done: s_err (auxiliary output) was type {type(s_err)}, expected str. Converting. Value (first 100 chars): {str(s_err)[:100]}")
                    final_stderr = str(s_err)
                else:
                    final_stderr = s_err

                if final_stderr:
                    final_stderr = final_stderr.replace('\\n', '\n')
                    logger.debug("Applied .replace('\\\\n', '\\n') to final_stderr in _on_scan_task_done.")

            self._update_textview(final_stdout, final_stderr, is_error_message=False)

            if self.webscan_status_action_row:
                    if final_stdout or final_stderr:
                        self.webscan_status_action_row.set_subtitle("Scan complete. See results below.")
                    else:
                        self.webscan_status_action_row.set_subtitle("Scan complete. No output received.")
            elif s_out is None and s_err is None and not (isinstance(returned_data, tuple) and len(returned_data) == 2):
                if self.webscan_status_action_row and self.webscan_status_action_row.get_subtitle() == "Scanning...":
                     self.webscan_status_action_row.set_subtitle("Error: Unexpected scan result format.")
            else:
                if self.webscan_status_action_row:
                    self.webscan_status_action_row.set_subtitle("Scan complete. No output received.")

        except GLib.Error as e:
            logger.warning(f"Nikto scan task for {target_url} failed or was cancelled: {e.message} (Domain: {e.domain}, Code: {e.code})")

            brief_user_message = e.message
            detailed_output_for_textview = f"Error: {e.message}"

            if e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.NIKTO_NOT_FOUND.value):
                brief_user_message = "Nikto command not found. Ensure Nikto is installed and in PATH."
                detailed_output_for_textview = f"Error: {brief_user_message}"
            elif e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.TIMEOUT.value):
                brief_user_message = f"Scan for {target_url} timed out."
                detailed_output_for_textview = f"Error: {brief_user_message}"
            elif e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.CANCELLED.value):
                brief_user_message = f"Scan for {target_url} was cancelled."
                detailed_output_for_textview = f"Error: {brief_user_message}"
            elif e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value):
                if "Missing scan parameters" in e.message:
                    brief_user_message = "Internal error: Missing scan parameters."
                    detailed_output_for_textview = f"Error: {brief_user_message}"
                elif "Nikto execution error" in e.message:
                    match = re.search(r"\(code (\d+)\)", e.message)
                    code_str = f" (code {match.group(1)})" if match else ""
                    brief_user_message = f"Nikto execution error{code_str}. See results for details."
                    detailed_output_for_textview = f"Error: {e.message}"
                else:
                    brief_user_message = f"Scan failed: {e.message.splitlines()[0]}"
                    detailed_output_for_textview = f"Error: {e.message}"

            if self.webscan_status_action_row:
                self.webscan_status_action_row.set_subtitle(brief_user_message)
            show_global_error(self, brief_user_message)

            if detailed_output_for_textview and isinstance(detailed_output_for_textview, str) and '\\n' in detailed_output_for_textview:
                detailed_output_for_textview = detailed_output_for_textview.replace('\\n', '\n')
                logger.debug("Applied .replace('\\\\n', '\\n') to GLib.Error message for textview.")
            self._update_textview(None, detailed_output_for_textview, is_error_message=True)

        except Exception as e_generic:
            logger.exception(f"Unexpected Python error in _on_scan_task_done for {target_url}:")
            user_message = "An unexpected error occurred."
            detailed_error_msg_for_textview = f"Error: {user_message} ({str(e_generic)})"
            if detailed_error_msg_for_textview and isinstance(detailed_error_msg_for_textview, str) and '\\n' in detailed_error_msg_for_textview:
                 detailed_error_msg_for_textview = detailed_error_msg_for_textview.replace('\\n', '\n')
                 logger.debug("Applied .replace('\\\\n', '\\n') to generic Exception message for textview.")

            if self.webscan_status_action_row:
                self.webscan_status_action_row.set_subtitle(user_message)
            show_global_error(self, user_message + " Check logs for details.")
            self._update_textview(None, detailed_error_msg_for_textview, is_error_message=True)
        finally:
            self.scan_button.set_sensitive(True)
            if self.webscan_cancel_button:
                self.webscan_cancel_button.set_sensitive(False)
                self.webscan_cancel_button.set_visible(False)
            if self.webscan_status_spinner:
                self.webscan_status_spinner.stop()
                self.webscan_status_spinner.set_visible(False)

            if self.webscan_status_action_row :
                current_subtitle = self.webscan_status_action_row.get_subtitle()
                if current_subtitle == "Scanning...":
                     self.webscan_status_action_row.set_subtitle("Scan finished.")

            self.current_web_scan_task = None
            self.current_web_scan_cancellable = None
            self.current_nikto_process = None
            self._update_results_actions_sensitivity()

    def _update_textview(self, stdout_content: Optional[str], stderr_content: Optional[str], is_error_message: bool = False):
        """
        Update the results TextView with Nikto's stdout and error messages/stderr.

        :param stdout_content: The standard output content from Nikto.
        :param stderr_content: The standard error content from Nikto or a pre-formatted error message.
        :param is_error_message: If True, stderr_content is treated as a pre-formatted error for display.
                                 Otherwise, it's treated as raw stderr output from Nikto.
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            return
        buffer = self.source_view.get_buffer()
        if stdout_content:
            buffer.insert(buffer.get_end_iter(), stdout_content)

        if stderr_content:
            if is_error_message:
                buffer.insert(buffer.get_end_iter(), "\n" + stderr_content)
            else:
                buffer.insert(buffer.get_end_iter(), "\n--- Nikto Standard Error Output ---\n" + stderr_content)

        scroll_adj = self.results_scrolled_window.get_vadjustment() if self.results_scrolled_window else None
        if scroll_adj:
            scroll_adj.set_value(scroll_adj.get_upper() - scroll_adj.get_page_size())

    def _update_results_actions_sensitivity(self):
        if not hasattr(self, 'source_view') or not self.source_view:
            if self.clear_results_button:
                self.clear_results_button.set_sensitive(False)
            if self.copy_results_button:
                self.copy_results_button.set_sensitive(False)
            return

        buffer = self.source_view.get_buffer()
        if not buffer:
            if self.clear_results_button:
                self.clear_results_button.set_sensitive(False)
            if self.copy_results_button:
                self.copy_results_button.set_sensitive(False)
            return

        has_content = buffer.get_char_count() > 0
        if self.clear_results_button:
            self.clear_results_button.set_sensitive(has_content)
        if self.copy_results_button:
            self.copy_results_button.set_sensitive(has_content)

    def trigger_scan(self):
        """Programmatically trigger the WebScan 'Scan' action."""
        logger.debug("Webscan scan triggered by shortcut.")
        if self.scan_button and self.scan_button.get_sensitive():
            self.scan_button.clicked()
        elif self.current_web_scan_task and not self.current_web_scan_task.is_done():
            show_global_toast(self, "A scan is already in progress. Cancel it or wait.")
        else:
            logger.warning("Webscan scan button not available or not sensitive, cannot trigger scan.")

WEB_SCAN_ERROR_DOMAIN = "web-scan-error-domain"

class WebScanErrorType(int, Enum):
    """Enumeration of Web Scan error types for Gio.Task error reporting."""
