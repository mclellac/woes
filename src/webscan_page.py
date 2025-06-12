"""Manages the Web Application Scanning user interface for the Woes application.

This module defines the :class:`WebScanPage` class, an :class:`Adw.PreferencesPage`
subclass. It provides the UI for configuring and running web vulnerability scans,
primarily using the Nikto scanner. Users can specify a target URL and various
Nikto options. Scan output is displayed in a :class:`GtkSource.View`.
Asynchronous scan execution is handled using :class:`Gio.Task`.
"""
from .style_utils import apply_source_style_scheme
from .utils import show_global_error, show_global_toast, is_valid_url
from .constants import RESOURCE_PREFIX, APP_ID
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk, GtkSource
import gi
from enum import Enum
import time
from typing import Optional, Dict, Any, Tuple # Added Tuple

import re
import subprocess
import logging
logger = logging.getLogger(__name__)


gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")


# Define error domain and types for web scans specifically
WEB_SCAN_ERROR_DOMAIN = "woes-web-scan-error-domain"

class WebScanErrorType(int, Enum):
    """Enumeration of Web Scan error types for :class:`Gio.Task` error reporting.

    These values are used as error codes within the `WEB_SCAN_ERROR_DOMAIN`
    when reporting errors via :meth:`Gio.Task.return_new_error_literal`.
    """
    NIKTO_NOT_FOUND = 0
    TIMEOUT = 1         # If a global scan timeout is implemented by the caller
    CANCELLED = 2
    GENERIC = 3         # For other Nikto/subprocess errors or general exceptions


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/webscan_page.ui")
class WebScanPage(Adw.PreferencesPage):
    """UI component for configuring and running web vulnerability scans with Nikto.

    This class provides the user interface for the Nikto web scanning tool.
    It allows users to:
    - Input a target URL.
    - Configure various Nikto scan options (e.g., Force SSL, CGI vulnerabilities,
      evasion techniques, output format).
    - Initiate Nikto scans, which are run asynchronously via :class:`Gio.Task`
      by calling the `nikto` command-line tool.
    - View real-time scan output in a :class:`GtkSource.View`.
    - Cancel ongoing scans.
    - Clear or copy the scan results.
    - Manages UI states (loading indicators, error messages, results display).
    """

    __gtype_name__ = "WebScanPage"

    # --- Template Children ---
    url_entry: Adw.EntryRow = Gtk.Template.Child() # type: ignore
    scan_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    results_scrolled_window: Gtk.ScrolledWindow = Gtk.Template.Child() # type: ignore
    force_ssl_switch: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    cgi_vulns_switch: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    interesting_content_switch: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    evasion_switch: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    mutate_switch: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    maxtime_entry_row: Adw.EntryRow = Gtk.Template.Child() # type: ignore
    clear_results_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    copy_results_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    webscan_status_action_row: Adw.ActionRow = Gtk.Template.Child() # type: ignore
    webscan_status_spinner: Gtk.Spinner = Gtk.Template.Child() # type: ignore
    webscan_cancel_button: Gtk.Button = Gtk.Template.Child() # type: ignore

    nikto_format_combo_row: Adw.ComboRow = Gtk.Template.Child() # type: ignore
    no404_switch: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    auth_bypass_switch: Adw.SwitchRow = Gtk.Template.Child() # type: ignore

    source_view: GtkSource.View # Declare instance variable for GtkSource.View

    def __init__(self, **kwargs: Any) -> None:
        """Initializes the WebScanPage.

        Sets up UI elements from the Gtk.Template, including the
        :class:`GtkSource.View` for displaying scan results. Connects signal
        handlers for user interactions and initializes GSettings for style preferences.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage`
                       constructor.
        :type kwargs: Any
        :return: None
        :rtype: None
        """
        super().__init__(**kwargs)
        self.init_template() # Initialize Gtk.Template children

        # Create and configure GtkSource.View and Buffer
        self.source_view = GtkSource.View()
        source_buffer = GtkSource.Buffer()
        self.source_view.set_buffer(source_buffer)
        self.source_view.set_hexpand(True)
        self.source_view.set_vexpand(True)
        self.source_view.set_monospace(True)
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.source_view.set_editable(False) # Results are read-only

        if self.results_scrolled_window:
            self.results_scrolled_window.set_child(self.source_view)
        else:
            logger.error("WebScanPage: 'results_scrolled_window' template child not found.")

        self.current_web_scan_task: Optional[Gio.Task] = None
        self.current_web_scan_cancellable: Optional[Gio.Cancellable] = None
        self.current_nikto_process: Optional[subprocess.Popen[str]] = None # type: ignore
        self._current_webscan_params: Optional[Dict[str, Any]] = None

        self.settings = Gio.Settings.new(APP_ID)
        self.style_manager = Adw.StyleManager.get_default()
        logger.debug("WebScanPage initialized")

        # Set initial language and style for the source view
        if self.source_view and self.source_view.get_buffer():
            buffer = self.source_view.get_buffer()
            lm = GtkSource.LanguageManager.get_default()
            language = lm.get_language("text") # Default to plain text for Nikto output
            if language:
                buffer.set_language(language)
            else:
                logger.warning("GtkSource language 'text' not found. Syntax highlighting may not apply.")
        self._apply_webscan_source_view_style()

        self._connect_signals()

    def _connect_signals(self) -> None:
        """Connects Gtk signals for various UI elements to their respective handlers.

        This includes signals for button clicks, entry activation, and
        changes in GSettings that affect styling.

        :return: None
        :rtype: None
        """
        self.style_manager.connect("notify::dark", self._on_webscan_source_style_settings_changed)
        self.settings.connect("changed::source-style-scheme", self._on_webscan_source_style_settings_changed)
        self.url_entry.connect("activate", self.on_scan_button_clicked)
        self.scan_button.connect("clicked", self.on_scan_button_clicked)

        if self.webscan_cancel_button:
            self.webscan_cancel_button.connect("clicked", self._on_cancel_scan_clicked)
        if self.clear_results_button:
            self.clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.copy_results_button:
            self.copy_results_button.connect("clicked", self._on_copy_results_clicked)

    def _on_webscan_source_style_settings_changed(
            self,
            _manager_or_settings: GObject.Object, # Adw.StyleManager or Gio.Settings
            _param_spec_or_key: Any # GObject.ParamSpec or str (key name)
        ) -> None:
        """Handles theme (dark/light) or GtkSourceView style scheme changes.

        This callback is triggered when the system theme changes or when the user
        selects a different source style scheme in the application preferences.
        It re-applies the determined style to the GtkSourceView buffer.

        :param _manager_or_settings: The object that emitted the signal.
        :type _manager_or_settings: GObject.Object
        :param _param_spec_or_key: The changed property or GSettings key (unused).
        :type _param_spec_or_key: Any
        :return: None
        :rtype: None
        """
        logger.info("WebScanPage: System theme or source style scheme changed. Applying new style to GtkSourceView.")
        self._apply_webscan_source_view_style()

    def _apply_webscan_source_view_style(self) -> None:
        """Applies the appropriate GtkSourceView style scheme to the source view buffer.

        Determines the correct style scheme based on the current system theme
        (dark/light) and the user's preferred GtkSourceView style scheme from
        GSettings. Falls back to "Adwaita" or "Adwaita-dark" if schemes are
        not found or if the preferred scheme is not suitable for the current theme.

        :return: None
        :rtype: None
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            logger.debug("WebScanPage._apply_webscan_source_view_style: self.source_view not ready.")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            logger.debug("WebScanPage._apply_webscan_source_view_style: buffer not ready.")
            return

        is_dark = self.style_manager.get_dark()
        user_scheme_name = self.settings.get_string("source-style-scheme")
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        final_scheme_name = "Adwaita" if not is_dark else "Adwaita-dark" # Base default

        # Determine the best scheme to apply
        if is_dark:
            # If user explicitly chose a known light theme, but system is dark, stick to Adwaita-dark
            if user_scheme_name.lower() in ["adwaita", "default", "classic", "light", "kate"]: # Common light theme names
                pass # final_scheme_name is already Adwaita-dark
            # Otherwise, try user's preferred scheme or fallback to Adwaita-dark
            elif scheme_manager.get_scheme(user_scheme_name):
                final_scheme_name = user_scheme_name
            else:
                logger.warning("User scheme '%s' not found for dark theme, falling back to '%s'.",
                               user_scheme_name, final_scheme_name)
        else: # Light system theme
            # If user explicitly chose a known dark theme, but system is light, stick to Adwaita
            if user_scheme_name.lower() in ["adwaita-dark"]: # Common dark theme names
                pass # final_scheme_name is already Adwaita
            elif scheme_manager.get_scheme(user_scheme_name):
                final_scheme_name = user_scheme_name
            else:
                logger.warning("User scheme '%s' not found for light theme, falling back to '%s'.",
                               user_scheme_name, final_scheme_name)

        # Apply the chosen scheme
        if scheme_to_apply := scheme_manager.get_scheme(final_scheme_name):
            buffer.set_style_scheme(scheme_to_apply)
            logger.debug("Applying GtkSourceView style scheme: %s (Dark Mode: %s, User Pref: %s)",
                         final_scheme_name, is_dark, user_scheme_name)
        else:
            logger.error("Could not load GtkSourceView style scheme: '%s'. Styling may be incorrect.",
                         final_scheme_name)


    def __del__(self) -> None:
        """Cleans up resources when the WebScanPage instance is destroyed.

        Specifically, it attempts to cancel any ongoing Nikto scan.

        :return: None
        :rtype: None
        """
        if self.current_web_scan_cancellable and \
           not self.current_web_scan_cancellable.is_cancelled():
            logger.info("WebScanPage being destroyed, cancelling ongoing Nikto scan.")
            self.current_web_scan_cancellable.cancel()
        # Note: self.current_nikto_process is managed by the Gio.Task thread.
        # If the task is cancelled, it attempts to terminate/kill the process.

    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Cancel Scan' button.

        If a Nikto scan is currently in progress and cancellable, this method
        requests its cancellation. UI updates (disabling cancel button, etc.)
        are primarily handled in the :meth:`_on_scan_task_done` callback.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
        :rtype: None
        """
        logger.info("WebScanPage: Cancel scan button clicked.")
        if self.current_web_scan_cancellable and \
           not self.current_web_scan_cancellable.is_cancelled():
            self.current_web_scan_cancellable.cancel()
            logger.info("WebScanPage: Nikto scan cancellation requested via button.")
            if self.webscan_cancel_button: # Disable button immediately for responsiveness
                self.webscan_cancel_button.set_sensitive(False)
        else:
            logger.warning("WebScanPage: No active scan or cancellable object found to cancel.")

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Clear Results' button.

        Clears the text content of the :class:`GtkSource.View` used for displaying results.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
        :rtype: None
        """
        logger.info("WebScanPage: Results cleared by user action.")
        if hasattr(self, 'source_view') and self.source_view: # Ensure source_view exists
            buffer = self.source_view.get_buffer()
            if buffer:
                buffer.set_text("") # Clear all text
        else:
            logger.warning("WebScanPage: source_view not available, cannot clear results.")


    def _on_copy_results_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Copy Results' button.

        Retrieves all text content from the :class:`GtkSource.View` buffer and
        copies it to the system clipboard.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
        :rtype: None
        """
        logger.info("WebScanPage: Copying web scan results to clipboard.")
        if hasattr(self, 'source_view') and self.source_view: # Ensure source_view exists
            buffer = self.source_view.get_buffer()
            if buffer:
                start_iter = buffer.get_start_iter()
                end_iter = buffer.get_end_iter()
                text_content = buffer.get_text(start_iter, end_iter, False) # Get all text

                if text_content:
                    try:
                        clipboard = Gdk.Display.get_default().get_clipboard()
                        if clipboard:
                            clipboard.set(text_content) # Gdk.Clipboard.set takes string directly
                            logger.info("WebScanPage: Results copied to clipboard successfully.")
                            show_global_toast(self, "Results copied to clipboard.")
                        else:
                            logger.warning("WebScanPage: Failed to get default clipboard.")
                            show_global_toast(self, "Failed to access clipboard.")
                    except Exception as e: # pylint: disable=broad-except
                        logger.error("WebScanPage: Error copying results to clipboard: %s", e, exc_info=True)
                        show_global_toast(self, f"Error copying to clipboard: {e}")
                else:
                    logger.info("WebScanPage: No results to copy.")
                    show_global_toast(self, "No results to copy.")
            else:
                logger.warning("WebScanPage: source_view buffer not available, cannot copy results.")
        else:
            logger.warning("WebScanPage: source_view not available, cannot copy results.")


    def on_scan_button_clicked(self, _widget: Gtk.Widget) -> None: # Can be Gtk.Button or Adw.EntryRow
        """Handles the 'clicked' signal for the 'Scan' button or 'activate'
        signal from the URL entry row.

        Validates the target URL. If valid, it gathers all selected Nikto scan
        parameters from the UI, cancels any ongoing scan, displays an initial
        "Scanning..." message, and initiates a new asynchronous scan task
        using :meth:`_run_scan_task_thread_func`. Updates UI to reflect the
        scanning state (e.g., disables scan button, shows spinner).

        :param _widget: The :class:`Gtk.Button` or :class:`Adw.EntryRow`
                        that triggered the action (unused).
        :type _widget: Gtk.Widget
        :return: None
        :rtype: None
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            logger.error("WebScanPage: source_view not found, cannot proceed with scan.")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            logger.error("WebScanPage: source_view buffer not found, cannot proceed with scan.")
            return

        logger.debug("WebScanPage: Scan button clicked. URL from entry: '%s'", self.url_entry.get_text())
        target_url = self.url_entry.get_text().strip()

        if not target_url:
            show_global_toast(self, "Target URL cannot be empty.")
            return

        # Validate URL (defaulting to http/https schemes if none provided by is_valid_url)
        if not is_valid_url(target_url, schemes=['http', 'https', 'ftp']): # Nikto might handle ftp by http to host
            show_global_toast(self, "Invalid URL format. Please enter a valid URL (e.g., http://example.com).")
            return

        buffer.set_text(f"Starting Nikto scan for {target_url}...\n\n") # Initial UI feedback

        # Update UI to scanning state
        self.scan_button.set_sensitive(False)
        if self.webscan_status_action_row:
            self.webscan_status_action_row.set_subtitle("Scanning...")
        if self.webscan_status_spinner:
            self.webscan_status_spinner.set_visible(True)
            self.webscan_status_spinner.start()
        if self.webscan_cancel_button:
            self.webscan_cancel_button.set_visible(True)
            self.webscan_cancel_button.set_sensitive(True)

        # Cancel any previous task
        if self.current_web_scan_task and not self.current_web_scan_task.is_done():
            if self.current_web_scan_cancellable and not self.current_web_scan_cancellable.is_cancelled():
                logger.info("WebScanPage: Cancelling previous scan task before starting new one.")
                self.current_web_scan_cancellable.cancel()

        self.current_web_scan_cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, self.current_web_scan_cancellable, self._on_scan_task_done, None)
        self.current_web_scan_task = task

        # Gather Nikto parameters from UI
        nikto_format_item = self.nikto_format_combo_row.get_selected_item()
        nikto_format_str = nikto_format_item.get_string() if isinstance(nikto_format_item, Gtk.StringObject) else "default (text)"

        self._current_webscan_params = {
            "target_url": target_url, # Already scheme-checked by is_valid_url
            "force_ssl": self.force_ssl_switch.get_active(),
            "cgi_vulns": self.cgi_vulns_switch.get_active(), # Corresponds to Nikto -Tuning 2
            "interesting_content": self.interesting_content_switch.get_active(), # Corresponds to Nikto -Tuning 1
            "evasion": self.evasion_switch.get_active(), # Corresponds to Nikto -evasion 1
            "mutate": self.mutate_switch.get_active(), # Corresponds to Nikto -mutate 1
            "maxtime": self.maxtime_entry_row.get_text().strip(),
            "nikto_format": nikto_format_str,
            "no404": self.no404_switch.get_active(),
            "auth_bypass": self.auth_bypass_switch.get_active() # Corresponds to Nikto -Tuning 9
        }
        logger.debug("WebScanPage: Starting Nikto scan task with parameters: %s", self._current_webscan_params)
        task.run_in_thread(self._run_scan_task_thread_func)


    def _run_scan_task_thread_func(
        self,
        task: Gio.Task,
        _source_object: GObject.Object,
        _task_data_unused: Any, # Parameter from Gio.Task.run_in_thread
        cancellable: Gio.Cancellable # Expected to be self.current_web_scan_cancellable
    ) -> None:
        """Executes the Nikto scan in a background thread.

        This function is run by :meth:`Gio.Task.run_in_thread`. It retrieves scan
        parameters stored in `self._current_webscan_params`, constructs the `nikto`
        command-line arguments, and runs Nikto as a subprocess using
        :class:`subprocess.Popen`. Stdout and stderr from Nikto are captured.
        The method monitors the `cancellable` object to terminate the Nikto
        process if a cancellation is requested.
        Results (stdout, stderr tuple) or errors are reported back to the main
        thread via the `task` object.

        :param task: The :class:`Gio.Task` associated with this background operation.
        :type task: Gio.Task
        :param _source_object: The source object that initiated the task (the WebScanPage instance).
        :type _source_object: GObject.Object
        :param _task_data_unused: Task-specific data (unused here, params are on `self`).
        :type _task_data_unused: Any
        :param cancellable: A :class:`Gio.Cancellable` to monitor for cancellation requests.
        :type cancellable: Gio.Cancellable
        :return: None. Results or errors are set on the `task` object.
        :rtype: None
        """
        logger.debug("WebScanPage: _run_scan_task_thread_func started.")
        page_instance: WebScanPage = _source_object # type: ignore
        scan_params = page_instance._current_webscan_params

        if not scan_params:
            logger.error("WebScanPage: Scan parameters not found in thread function.")
            task.return_new_error_literal(
                GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN),
                WebScanErrorType.GENERIC.value, "Internal error: Missing scan parameters in thread.")
            return

        target_url = scan_params["target_url"]
        # Ensure URL has a scheme for Nikto; is_valid_url should have handled basic cases.
        # Nikto prepends 'http://' if no scheme; this makes it explicit.
        if not target_url.startswith(("http://", "https://")):
            # This case should ideally be caught by initial validation using is_valid_url
            # but as a safeguard for direct calls or changes.
            logger.info("WebScanPage: URL '%s' missing scheme, prepending 'http://' for Nikto.", target_url)
            target_url = "http://" + target_url

        # Construct Nikto command
        nikto_command = ['nikto', '-h', target_url]
        if scan_params.get("force_ssl"): nikto_command.append('-ssl')
        if scan_params.get("evasion"): nikto_command.extend(['-evasion', '1']) # Basic evasion
        if scan_params.get("mutate"): nikto_command.extend(['-mutate', '1'])   # Basic mutation

        maxtime_str = scan_params.get("maxtime", "")
        if maxtime_str:
            try:
                maxtime_val = int(maxtime_str)
                if maxtime_val > 0: nikto_command.extend(['-maxtime', f"{maxtime_val}s"])
                else: logger.warning("Invalid maxtime '%s', must be positive. Ignoring.", maxtime_str)
            except ValueError: logger.warning("Invalid maxtime '%s', not an integer. Ignoring.", maxtime_str)

        tuning_options: List[str] = []
        if scan_params.get("cgi_vulns"): tuning_options.append('2') # Misconfig/Default File
        if scan_params.get("interesting_content"): tuning_options.append('1') # Interesting File
        if scan_params.get("auth_bypass"): tuning_options.append('9') # Auth Bypass

        if tuning_options:
            tuning_string = "".join(sorted(list(set(tuning_options)))) # Ensure unique and ordered
            if tuning_string: nikto_command.extend(['-Tuning', tuning_string])

        nikto_format_str = scan_params.get("nikto_format", "default (text)")
        if nikto_format_str and nikto_format_str.lower() not in ["default (text)", "txt (text)", "txt"]:
            format_cli = nikto_format_str.split(" ")[0].lower() # e.g. "html" from "html (*.htm)"
            if format_cli == "html": format_cli = "htm" # Nikto uses 'htm'
            if format_cli not in ["txt"]: nikto_command.extend(['-Format', format_cli])

        if scan_params.get("no404"): nikto_command.append('-no404')

        logger.debug("WebScanPage: Constructed Nikto command: %s", nikto_command)

        try:
            if cancellable.is_cancelled():
                task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN),
                                              WebScanErrorType.CANCELLED.value, "Scan cancelled before Nikto process start.")
                return

            # Start the Nikto process
            process = subprocess.Popen(
                nikto_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace") # errors='replace' for robustness
            self.current_nikto_process = process # Store for potential cancellation from __del__

            # Polling loop for cancellation and process completion
            while process.poll() is None: # While process is running
                if cancellable.is_cancelled():
                    logger.info("WebScanPage: Nikto scan cancellation detected in polling loop. Terminating process.")
                    if process.poll() is None: # Check again before terminate
                        try:
                            process.terminate()
                            process.wait(timeout=2) # Wait for graceful termination
                        except subprocess.TimeoutExpired:
                            logger.warning("WebScanPage: Nikto process did not terminate gracefully, killing.")
                            process.kill()
                        except Exception as e_term: # pylint: disable=broad-except
                            logger.error("WebScanPage: Error during Nikto process termination: %s", e_term)
                    task.return_new_error_literal(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN),
                                                  WebScanErrorType.CANCELLED.value, "Scan cancelled by user request.")
                    self.current_nikto_process = None
                    return
                time.sleep(0.2) # Polling interval

            # Process has finished, communicate to get final output
            stdout_str, stderr_str = process.communicate()
            self.current_nikto_process = None # Clear process reference

            if process.returncode not in [0, 1]: # Nikto often exits 0 for success, 1 for some "errors" like host not found
                logger.error("WebScanPage: Nikto process for '%s' finished with unexpected error code %d. Stderr: '%s'",
                             target_url, process.returncode, stderr_str[:500])
                # Combine stdout and stderr for error context if stderr is primary
                error_detail = stderr_str if stderr_str else stdout_str
                task.return_new_error_literal(
                    GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value,
                    f"Nikto execution error (code {process.returncode}). Output:\n{error_detail}")
                return

            # Nikto's primary output is typically to stdout. Stderr might contain warnings or errors.
            task.return_value((stdout_str, stderr_str)) # Return tuple of (stdout, stderr)

        except FileNotFoundError:
            logger.error("WebScanPage: Nikto command not found. Ensure Nikto is installed and in system PATH.")
            task.return_new_error_literal(
                GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.NIKTO_NOT_FOUND.value,
                "Nikto command not found. Please ensure it is installed and in your system's PATH.")
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("WebScanPage: An unexpected error occurred during Nikto scan task for '%s':", target_url)
            task.return_new_error_literal(
                GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.GENERIC.value,
                f"An unexpected error occurred: {str(e)}")
        finally:
            self.current_nikto_process = None # Ensure cleared on any exit path


    def _on_scan_task_done(
        self,
        _source_object: GObject.Object,
        result: Gio.AsyncResult,
        _user_data: Any # type: ignore # GObject callback signature
    ) -> None:
        """Callback executed in the main thread when the Nikto scan :class:`Gio.Task` completes.

        Processes the results from the `task` (stdout and stderr strings from Nikto,
        or an exception) and updates the UI. This includes populating the
        :class:`GtkSource.View` with the scan output or displaying error messages.
        Resets UI elements (e.g., scan button, spinner) to their idle state.

        :param _source_object: The source object that initiated the task (the WebScanPage instance, unused).
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: Any
        :return: None
        :rtype: None
        """
        target_url = "Unknown URL"
        if self._current_webscan_params: # Retrieve target URL for context
            target_url = self._current_webscan_params.get("target_url", target_url)

        logger.info("WebScanPage: Nikto scan task completion callback started for target: %s.", target_url)
        # Ensure we are handling the correct task, though current_web_scan_task should be it
        task_being_processed = self.current_web_scan_task
        self.current_web_scan_task = None # Clear task reference

        final_stdout: Optional[str] = None
        final_stderr: Optional[str] = None

        try:
            if task_being_processed is None: # Should ideally not happen
                raise GLib.Error(domain=GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN),
                                 code=WebScanErrorType.GENERIC.value,
                                 message="Task object was None in callback.")

            returned_data = task_being_processed.propagate_value() # Raises GLib.Error on failure
            if isinstance(returned_data, tuple) and len(returned_data) == 2:
                s_out, s_err = returned_data
                final_stdout = str(s_out) if s_out is not None else None
                final_stderr = str(s_err) if s_err is not None else None
            elif hasattr(returned_data, 'value') and isinstance(returned_data.value, tuple) and len(returned_data.value) == 2: # type: ignore
                s_out, s_err = returned_data.value # type: ignore
                final_stdout = str(s_out) if s_out is not None else None
                final_stderr = str(s_err) if s_err is not None else None
            else: # Unexpected successful return type
                logger.error("Nikto scan for %s returned unexpected result format: %s", target_url, type(returned_data))
                show_global_error(self, "Scan returned an unexpected data format.")
                self._update_textview(None, "Error: Scan returned unexpected data format.", is_error_message=True)

            # Display results if any
            if final_stdout is not None or final_stderr is not None:
                self._update_textview(final_stdout, final_stderr, is_error_message=False)
                status_msg = "Scan complete. See results." if (final_stdout or final_stderr) else "Scan complete. No output."
                if self.webscan_status_action_row: self.webscan_status_action_row.set_subtitle(status_msg)
            else: # Both None, but no GLib.Error means task returned (None, None)
                if self.webscan_status_action_row: self.webscan_status_action_row.set_subtitle("Scan complete. No output received.")


        except GLib.Error as e: # Handles errors set by task.return_new_error_literal()
            logger.warning("Nikto scan task for %s failed or was cancelled: %s (Domain: %s, Code: %d)",
                           target_url, e.message, e.domain, e.code)
            user_msg = e.message # Default user message
            if e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.NIKTO_NOT_FOUND.value):
                user_msg = "Nikto command not found. Ensure Nikto is installed and in system PATH."
            elif e.matches(GLib.quark_from_string(WEB_SCAN_ERROR_DOMAIN), WebScanErrorType.CANCELLED.value):
                user_msg = f"Scan for {target_url} was cancelled."
            # Other specific errors can be handled here based on WebScanErrorType

            if self.webscan_status_action_row: self.webscan_status_action_row.set_subtitle(user_msg.splitlines()[0])
            show_global_error(self, user_msg)
            self._update_textview(None, f"Error: {user_msg}", is_error_message=True)
        except Exception as e: # Catch any other Python exceptions from this callback
            logger.exception("Unexpected Python error in _on_scan_task_done for %s:", target_url)
            user_msg = "An unexpected error occurred while processing scan results."
            if self.webscan_status_action_row: self.webscan_status_action_row.set_subtitle(user_msg)
            show_global_error(self, user_msg + " Check logs for details.")
            self._update_textview(None, f"Error: {user_msg} - {type(e).__name__}", is_error_message=True)
        finally:
            # Reset UI to idle state
            self.scan_button.set_sensitive(True)
            if self.webscan_cancel_button:
                self.webscan_cancel_button.set_sensitive(False)
                self.webscan_cancel_button.set_visible(False)
            if self.webscan_status_spinner:
                self.webscan_status_spinner.stop()
                self.webscan_status_spinner.set_visible(False)
            # Update status row if it's still showing "Scanning..."
            if self.webscan_status_action_row and self.webscan_status_action_row.get_subtitle() == "Scanning...":
                self.webscan_status_action_row.set_subtitle("Scan finished or error occurred.")
            self.current_web_scan_cancellable = None # Clear cancellable for the completed/failed task
            self.current_nikto_process = None


    def _update_textview(
            self,
            stdout_content: Optional[str],
            stderr_content: Optional[str],
            is_error_message: bool = False
        ) -> None:
        """Updates the :class:`GtkSource.View` with content from the Nikto scan.

        Appends stdout and, if present, stderr to the source view buffer.
        If `is_error_message` is True, `stderr_content` is treated as a
        pre-formatted error message. Otherwise, stderr is appended with a separator.
        Scrolls the view to the end to show the latest output.

        :param stdout_content: The content from Nikto's standard output.
        :type stdout_content: Optional[str]
        :param stderr_content: The content from Nikto's standard error, or a
                               custom error message.
        :type stderr_content: Optional[str]
        :param is_error_message: If ``True``, `stderr_content` is treated as a
                                 formatted error message. Defaults to ``False``.
        :type is_error_message: bool
        :return: None
        :rtype: None
        """
        if not hasattr(self, 'source_view') or not self.source_view:
            logger.error("WebScanPage: source_view not found, cannot update text view.")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            logger.error("WebScanPage: source_view buffer not found, cannot update text view.")
            return

        # Insert stdout content
        if stdout_content:
            buffer.insert(buffer.get_end_iter(), stdout_content)

        # Insert stderr content or formatted error message
        if stderr_content:
            if is_error_message:
                buffer.insert(buffer.get_end_iter(), "\n--- ERROR ---\n" + stderr_content + "\n")
            else: # Raw stderr from Nikto (e.g., warnings, progress if not to stdout)
                buffer.insert(buffer.get_end_iter(), "\n--- Nikto Standard Error Output ---\n" + stderr_content)

        # Auto-scroll to the end of the text view to show new content
        if self.results_scrolled_window:
            scroll_adj = self.results_scrolled_window.get_vadjustment()
            if scroll_adj: # Ensure adjustment exists
                # GLib.idle_add is often good for scrolling after text insert
                GLib.idle_add(scroll_adj.set_value, scroll_adj.get_upper() - scroll_adj.get_page_size())


    def trigger_scan(self) -> None:
        """Programmatically triggers the WebScan 'Scan' action.

        Useful for invoking a scan via a shortcut or another UI event.
        Checks if the scan button is available and sensitive before simulating a click.

        :return: None
        :rtype: None
        """
        logger.debug("WebScanPage: Webscan scan triggered by shortcut or external action.")
        if self.scan_button and self.scan_button.get_sensitive():
            self.scan_button.clicked()
        elif self.current_web_scan_task and not self.current_web_scan_task.is_done():
            show_global_toast(self, "A web scan is already in progress. Please wait or cancel it.")
        else:
            logger.warning("WebScanPage: Scan button is not available or not sensitive; cannot trigger scan.")

```
