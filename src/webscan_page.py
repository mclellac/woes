import logging
import subprocess
from typing import Optional

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GObject, Gtk, GLib

from .constants import RESOURCE_PREFIX

logger = logging.getLogger(__name__)

WEB_SCAN_ERROR_DOMAIN = "webscan-error-domain"


class WebScanError:
    NIKTO_NOT_FOUND = 0
    TIMEOUT = 1
    CALLED_PROCESS = 2
    UNKNOWN = 3


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.blp")
class WebScanPage(Adw.PreferencesPage):
    __gtype_name__ = 'WebScanPage'

    url_entry = Gtk.Template.Child()
    scan_button = Gtk.Template.Child()
    results_textview = Gtk.Template.Child()
    error_banner_webscan = Gtk.Template.Child()

    def __init__(self, **kwargs):
        logger.debug("WebScanPage.__init__: Starting.")
        super().__init__(**kwargs)
        logger.debug(f"WebScanPage.__init__: self.url_entry: {self.url_entry}")
        logger.debug(f"WebScanPage.__init__: self.scan_button: {self.scan_button}")
        logger.debug(f"WebScanPage.__init__: self.results_textview: {self.results_textview}")
        if self.results_textview:
            buffer = self.results_textview.get_buffer()
            logger.debug(f"WebScanPage.__init__: self.results_textview buffer: {buffer}")
        logger.debug(f"WebScanPage.__init__: self.error_banner_webscan: {self.error_banner_webscan}")
        logger.debug("WebScanPage.__init__: Finished.")

    def on_scan_button_clicked(self, _widget):
        logger.debug(f"WebScanPage.on_scan_button_clicked: Triggered by widget: {_widget}")
        target_url = self.url_entry.get_text()
        logger.debug(f"WebScanPage.on_scan_button_clicked: Target URL from entry: '{target_url}'")
        if not target_url:
            logger.debug("WebScanPage.on_scan_button_clicked: Target URL is empty.")
            self.show_error_toast("Target URL cannot be empty.")
            logger.debug("WebScanPage.on_scan_button_clicked: Finished due to empty target URL.")
            return

        buffer = self.results_textview.get_buffer()
        logger.debug(f"WebScanPage.on_scan_button_clicked: Got results_textview buffer: {buffer}")
        buffer.set_text(f"Scanning {target_url}...\n\n")
        logger.debug(f"WebScanPage.on_scan_button_clicked: Set initial text in buffer: 'Scanning {target_url}...'")

        self.scan_button.set_sensitive(False)
        logger.debug("WebScanPage.on_scan_button_clicked: scan_button sensitivity set to False.")

        cancellable = Gio.Cancellable.new()
        logger.debug(f"WebScanPage.on_scan_button_clicked: Created Gio.Cancellable: {cancellable}")
        task = Gio.Task.new(self, cancellable, self._on_scan_task_done, None)
        logger.debug(f"WebScanPage.on_scan_button_clicked: Created Gio.Task: {task}")
        task.set_task_data(target_url)
        logger.debug(f"WebScanPage.on_scan_button_clicked: Set task_data for Gio.Task: '{target_url}'")
        logger.debug(f"WebScanPage.on_scan_button_clicked: Starting async task nikto-scan by calling task.run_in_thread()")
        task.run_in_thread(self._run_scan_task_thread_func)
        logger.debug("WebScanPage.on_scan_button_clicked: After task.run_in_thread()")
        logger.debug("WebScanPage.on_scan_button_clicked: Finished.")

    def _run_scan_task_thread_func(
        self,
        gio_task: Gio.Task,
        _source_object,
        task_data: str,
        cancellable: Gio.Cancellable
    ):
        logger.debug(
            f"WebScanPage._run_scan_task_thread_func: Starting for Gio.Task: {gio_task}, "
            f"source_object: {_source_object}, task_data (target_url): '{task_data}', cancellable: {cancellable}"
        )
        target_url = task_data
        stdout_str = ""
        stderr_str = ""

        try:
            original_target_url_for_log = target_url
            if not target_url.startswith(('http://', 'https://')):
                target_url = 'http://' + target_url
                logger.debug(
                    f"WebScanPage._run_scan_task_thread_func: Prepended 'http://' to target_url. "
                    f"Now: '{target_url}' (Original: '{original_target_url_for_log}')"
                )

            if cancellable.is_cancelled():
                logger.debug("WebScanPage._run_scan_task_thread_func: Scan cancelled before start.")
                err = GLib.Error("Scan cancelled before start.", WEB_SCAN_ERROR_DOMAIN, Gio.IOErrorEnum.CANCELLED)
                gio_task.return_error(err)
                logger.debug(f"WebScanPage._run_scan_task_thread_func: Returned error for pre-cancellation: {err.message}")
                return

            command = [
                'nikto', '-h', target_url, '-Format', 'txt', '-ask', 'no',
                '-Tuning', 'xCGIVulnerable', '-Display', 'V', '-nointeractive',
                '-timeout', '300'
            ]
            logger.info(f"WebScanPage._run_scan_task_thread_func: Running Nikto command: {' '.join(command)}")

            logger.debug(f"WebScanPage._run_scan_task_thread_func: Before subprocess.Popen({command})")
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            logger.debug(f"WebScanPage._run_scan_task_thread_func: After subprocess.Popen(), process: {process}")
            logger.debug("WebScanPage._run_scan_task_thread_func: Before process.communicate()")
            stdout_str, stderr_str = process.communicate()
            logger.debug(
                f"WebScanPage._run_scan_task_thread_func: After process.communicate(). "
                f"stdout (len {len(stdout_str)}), stderr (len {len(stderr_str)})"
            )
            logger.debug(f"WebScanPage._run_scan_task_thread_func: stdout (first 200 chars): '{stdout_str[:200]}'")
            logger.debug(f"WebScanPage._run_scan_task_thread_func: stderr (first 200 chars): '{stderr_str[:200]}'")

            if cancellable.is_cancelled():
                logger.debug("WebScanPage._run_scan_task_thread_func: Scan cancelled during/after Nikto execution, before processing result.")
                if process.poll() is None:
                    logger.info("WebScanPage._run_scan_task_thread_func: Scan cancelled, attempting to terminate Nikto process.")
                    process.terminate()
                    logger.debug("WebScanPage._run_scan_task_thread_func: process.terminate() called.")
                    try:
                        logger.debug("WebScanPage._run_scan_task_thread_func: Before process.wait(timeout=10)")
                        process.wait(timeout=10)
                        logger.debug("WebScanPage._run_scan_task_thread_func: process.wait() completed.")
                    except subprocess.TimeoutExpired:
                        logger.warning("WebScanPage._run_scan_task_thread_func: Nikto process did not terminate gracefully, killing.")
                        process.kill()
                        logger.debug("WebScanPage._run_scan_task_thread_func: process.kill() called.")
                err = GLib.Error("Scan cancelled by user.", WEB_SCAN_ERROR_DOMAIN, Gio.IOErrorEnum.CANCELLED)
                gio_task.return_error(err)
                logger.debug(f"WebScanPage._run_scan_task_thread_func: Returned error for cancellation during/after execution: {err.message}")
                return

            logger.debug(f"WebScanPage._run_scan_task_thread_func: Nikto process return code: {process.returncode}")
            if process.returncode:
                log_stderr = stderr_str[:200]
                logger.error(
                    f"WebScanPage._run_scan_task_thread_func: Nikto for {target_url} exited with code {process.returncode}. "
                    f"stderr: {log_stderr}"
                )
                error_message = f"Nikto scan failed (code {process.returncode})."
                if "Can't find host" in stderr_str or "ERROR: Cannot resolve hostname" in stderr_str:
                    error_message = "Cannot resolve hostname or invalid target."
                elif "ERROR: No HTTP response" in stderr_str:
                    error_message = "No HTTP response from target."
                err = GLib.Error(error_message, WEB_SCAN_ERROR_DOMAIN, WebScanError.CALLED_PROCESS)
                gio_task.return_error(err)
                logger.debug(f"WebScanPage._run_scan_task_thread_func: Returned error for Nikto non-zero exit: {err.message}")
                return

            result_variant = GLib.Variant('(ss)', (stdout_str, stderr_str))
            gio_task.return_value(result_variant)
            logger.debug(f"WebScanPage._run_scan_task_thread_func: Returned value with Gio.Task: {result_variant}")

        except FileNotFoundError:
            logger.error("WebScanPage._run_scan_task_thread_func: Nikto command not found.", exc_info=True)
            err = GLib.Error("Nikto not found. Ensure installed.", WEB_SCAN_ERROR_DOMAIN, WebScanError.NIKTO_NOT_FOUND)
            gio_task.return_error(err)
            logger.debug(f"WebScanPage._run_scan_task_thread_func: Returned error for FileNotFoundError: {err.message}")
        except subprocess.TimeoutExpired:
            logger.error(f"WebScanPage._run_scan_task_thread_func: Nikto process communicate() timed out for {target_url}.", exc_info=True)
            err = GLib.Error("Scan process timed out.", WEB_SCAN_ERROR_DOMAIN, WebScanError.TIMEOUT)
            gio_task.return_error(err)
            logger.debug(f"WebScanPage._run_scan_task_thread_func: Returned error for TimeoutExpired: {err.message}")
        except Exception as e:
            logger.exception(f"WebScanPage._run_scan_task_thread_func: Unexpected error during Nikto scan for {target_url}.")
            err_name = type(e).__name__
            err = GLib.Error(f"Unexpected: {err_name}", WEB_SCAN_ERROR_DOMAIN, WebScanError.UNKNOWN)
            gio_task.return_error(err)
            logger.debug(f"WebScanPage._run_scan_task_thread_func: Returned error for Unexpected Exception ({err_name}): {err.message}")
        logger.debug(f"WebScanPage._run_scan_task_thread_func: Finished for target_url '{target_url}'.")

    def _on_scan_task_done(self, _source_object, task: Gio.Task, _user_data):
        logger.debug(
            f"WebScanPage._on_scan_task_done: Starting for Gio.Task: {task}, "
            f"source_object: {_source_object}, user_data: {_user_data}"
        )
        try:
            logger.debug(f"WebScanPage._on_scan_task_done: Before task.propagate_value() for task {task}")
            result_variant = task.propagate_value()
            logger.debug(f"WebScanPage._on_scan_task_done: task.propagate_value() returned: {result_variant}")
            stdout, stderr = result_variant.unpack()
            logger.debug(f"WebScanPage._on_scan_task_done: Unpacked stdout (len {len(stdout)}) and stderr (len {len(stderr)})")
            self._update_textview(stdout, stderr)
            if not stdout and not stderr:
                logger.debug("WebScanPage._on_scan_task_done: Both stdout and stderr are empty.")
                self.show_error_toast(
                    "Scan completed with no output. Target might not be a web server or scan options too restrictive."
                )
            elif stderr:
                logger.debug("WebScanPage._on_scan_task_done: stderr is present, showing toast.")
                self.show_error_toast("Scan completed with errors/warnings (see details).")

        except GObject.GError as e:
            logger.error(
                f"WebScanPage._on_scan_task_done: Error from task propagation: {e.message} "
                f"(Code: {e.code}, Domain: {GLib.quark_to_string(e.domain)})"
            )
            self.show_error_toast(e.message)
            self._update_textview("", f"Error: {e.message}")
        finally:
            logger.debug("WebScanPage._on_scan_task_done: In finally block.")
            self.scan_button.set_sensitive(True)
            logger.debug("WebScanPage._on_scan_task_done: scan_button sensitivity set to True.")
        logger.debug(f"WebScanPage._on_scan_task_done: Finished for Gio.Task: {task}")

    def _update_textview(self, stdout: Optional[str], stderr: Optional[str]):
        logger.debug(
            f"WebScanPage._update_textview: Starting with stdout (len {len(stdout) if stdout else 0}), "
            f"stderr (len {len(stderr) if stderr else 0})"
        )
        buffer = self.results_textview.get_buffer()
        logger.debug(f"WebScanPage._update_textview: results_textview buffer: {buffer}")
        full_text = ""
        if stdout:
            full_text += stdout
            logger.debug("WebScanPage._update_textview: stdout added to full_text.")
        if stderr:
            full_text += "\n--- Standard Error ---\n" + stderr
            logger.debug("WebScanPage._update_textview: stderr added to full_text.")

        if not full_text:
            logger.debug("WebScanPage._update_textview: full_text is empty, setting placeholder.")
            buffer.set_text("Scan completed. No specific output to display.")
        else:
            logger.debug(f"WebScanPage._update_textview: Setting buffer text to full_text (len {len(full_text)})")
            buffer.set_text(full_text)
        logger.debug("WebScanPage._update_textview: Buffer text set.")

        parent_scrolled_window = self.results_textview.get_parent()
        if parent_scrolled_window and isinstance(parent_scrolled_window, Gtk.ScrolledWindow):
            scroll_adj = parent_scrolled_window.get_vadjustment()
            if scroll_adj:
                logger.debug(
                    f"WebScanPage._update_textview: Scroll adjustment value before: {scroll_adj.get_value()}, "
                    f"upper: {scroll_adj.get_upper()}, page_size: {scroll_adj.get_page_size()}"
                )
                scroll_adj.set_value(scroll_adj.get_upper() - scroll_adj.get_page_size())
                logger.debug(f"WebScanPage._update_textview: Scroll adjustment value after: {scroll_adj.get_value()}")
            else:
                logger.debug("WebScanPage._update_textview: No scroll adjustment found for results_textview parent.")
        else:
            logger.debug("WebScanPage._update_textview: Parent of results_textview is not Gtk.ScrolledWindow or not found.")
        logger.debug("WebScanPage._update_textview: Finished.")

    def show_error_toast(self, message: str):
        logger.debug(f"WebScanPage.show_error_toast: Displaying WebScan error/info: '{message}'")
        self.error_banner_webscan.set_title(message)
        logger.debug("WebScanPage.show_error_toast: error_banner_webscan title set.")
        self.error_banner_webscan.set_revealed(True)
        logger.debug("WebScanPage.show_error_toast: error_banner_webscan revealed set to True.")
        logger.debug("WebScanPage.show_error_toast: Finished.")

    def on_error_banner_dismiss_clicked(self, _widget, *_args):
        logger.debug(f"WebScanPage.on_error_banner_dismiss_clicked: Triggered by widget: {_widget}")
        self.error_banner_webscan.set_revealed(False)
        logger.debug("WebScanPage.on_error_banner_dismiss_clicked: error_banner_webscan revealed set to False.")
        logger.debug("WebScanPage.on_error_banner_dismiss_clicked: Finished.")
