"""Manages the Nmap Scanning user interface within the Woes application.

This module defines the :class:`NmapPage` class, an :class:`Adw.Bin` subclass,
which provides the UI for configuring Nmap scan parameters, initiating scans,
and displaying the results. It interacts with :class:`~.nmap_scanner.NmapScanner`
for backend scan operations and uses :class:`Gio.Task` for asynchronous execution.
Scan results are presented in a list of hosts, with detailed information for each
host shown in expandable sections.
"""

from .utils import create_source_view, show_global_error, show_global_toast
from .style_utils import apply_source_style_scheme
from .nmap_scanner import NmapScanner, ScanStatus, ScanCancelledError, NmapScanParameters
from .constants import APP_ID, RESOURCE_PREFIX
import nmap # For nmap.PortScanner type hint
from gi.repository import Adw, Gio, GLib, GObject, Gtk, GtkSource
import gi
import yaml
from typing import Optional, List, Dict, Any
from enum import Enum # For NmapScanErrorType
import re
import logging

logger = logging.getLogger(__name__)


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")


class NmapItem(GObject.Object):
    """A GObject representing a host item in the Nmap results list.

    This object holds the key (e.g., host IP or name) and the full YAML
    value of the scan results for that host, for display in a :class:`Gtk.ListBox`
    bound to a :class:`Gio.ListStore`.

    :ivar key: The primary identifier for the Nmap target (e.g., IP address or hostname).
    :vartype key: str
    :ivar value: The YAML string containing the full Nmap scan results for this host.
    :vartype value: str
    """

    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        """Initializes an NmapItem.

        :param key: The header key or special row title.
        :type key: str
        :param value: The header value or special row description.
        :type value: str
        """
        super().__init__()
        self.key = key
        self.value = value


class NmapTargetRow(Gtk.ListBoxRow):
    """A :class:`Gtk.ListBoxRow` customized to display the key of an :class:`NmapItem`.

    This row is used in the list of scanned hosts, showing the target's key
    (IP or hostname).

    :ivar nmap_item: The :class:`NmapItem` instance associated with this row.
    :vartype nmap_item: NmapItem
    """

    nmap_item = GObject.Property(type=NmapItem)

    def __init__(self, nmap_item: NmapItem, **kwargs: Any):
        """Initializes an NmapTargetRow.

        :param nmap_item: The NmapItem data for this row.
        :type nmap_item: NmapItem
        :param kwargs: Additional keyword arguments for the Gtk.ListBoxRow constructor.
        :type kwargs: Any
        """
        super().__init__(**kwargs)
        self.nmap_item = nmap_item
        label = Gtk.Label(label=nmap_item.key,
                          halign=Gtk.Align.START, margin_start=6, margin_end=6)
        self.set_child(label)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/nmap_page.ui")
class NmapPage(Adw.Bin):
    """UI component for configuring, running, and viewing Nmap scans.

    This class provides the main user interface for the Nmap scanning feature.
    It allows users to:
    - Specify target hosts (IPs, hostnames, CIDR ranges).
    - Select various Nmap scan options (OS detection, all ports, scripts, etc.).
    - Initiate scans, which are run asynchronously using :class:`~.nmap_scanner.NmapScanner`
      and :class:`Gio.Task`.
    - View scan progress and status.
    - Cancel ongoing scans.
    - Browse scan results, with each host displayed in a list. Detailed information
      (ports, OS, services, script output) for a selected host is shown in
      expandable sections using :class:`Adw.ExpanderRow`.
    - Copy scan results to the clipboard.

    It manages UI state changes based on scan progress and handles errors
    gracefully, displaying them to the user. It also interacts with
    :class:`Gio.Settings` for retrieving configurations like custom DNS servers.
    """

    __gtype_name__ = "NmapPage"

    # --- Template Children ---
    nmap_target_entryrow: Adw.EntryRow = Gtk.Template.Child() # type: ignore
    nmap_apply_button: Gtk.Button = Gtk.Template.Child() # type: ignore
    nmap_fingerprint_switchrow: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    nmap_all_ports_switchrow: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    nmap_scripts_dropdown: Gtk.DropDown = Gtk.Template.Child() # type: ignore
    nmap_service_version_switchrow: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    nmap_no_ping_switchrow: Adw.SwitchRow = Gtk.Template.Child() # type: ignore
    nmap_timing_template_comborow: Adw.ComboRow = Gtk.Template.Child() # type: ignore
    status_row: Adw.ActionRow = Gtk.Template.Child() # type: ignore
    scan_spinner: Gtk.Spinner = Gtk.Template.Child() # type: ignore
    nmap_cancel_scan_button: Gtk.Button = Gtk.Template.Child() # type: ignore

    nmap_host_listbox: Gtk.ListBox = Gtk.Template.Child() # type: ignore
    nmap_detail_box: Gtk.Box = Gtk.Template.Child() # type: ignore
    nmap_detail_placeholder: Adw.StatusPage = Gtk.Template.Child() # type: ignore

    def __init__(self, **kwargs: Any) -> None:
        """Initializes the NmapPage.

        Sets up UI elements from the Gtk.Template, connects signal handlers,
        initializes the :class:`~.nmap_scanner.NmapScanner`, GSettings,
        style manager, and prepares the list store for scan results.

        :param kwargs: Keyword arguments passed to the :class:`Adw.Bin` constructor.
        :type kwargs: Any
        :return: None
        :rtype: None
        """
        super().__init__(**kwargs)
        logger.info("Initializing NmapPage...")
        self.results_by_host: Dict[str, str] = {} # Stores YAML results by host key
        self.nmap_target_listbox_store = Gio.ListStore.new(NmapItem)
        self.scanner = NmapScanner()
        self.settings = Gio.Settings.new(APP_ID)
        self.style_manager = Adw.StyleManager.get_default()

        # Connect to theme/style changes for GtkSourceView updates
        self.style_manager.connect("notify::dark", self._on_nmap_source_style_settings_changed)
        self.settings.connect(f"changed::source-style-scheme", self._on_nmap_source_style_settings_changed)

        self.current_nmap_task: Optional[Gio.Task] = None
        self.current_nmap_cancellable: Optional[Gio.Cancellable] = None
        self._current_nmap_scan_params: Optional[NmapScanParameters] = None # Stores params for the bg thread

        self._init_page_ui()
        self._connect_signals()
        logger.info("NmapPage initialized.")
        logger.debug("NmapPage __init__ completed.")

    def __del__(self) -> None:
        """Cleans up resources when the NmapPage instance is destroyed.

        Specifically, it attempts to cancel any ongoing Nmap scan and ensures
        the :class:`~.nmap_scanner.NmapScanner` (which might hold a thread pool)
        is deleted.

        :return: None
        :rtype: None
        """
        if self.current_nmap_cancellable and \
           not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel()
            logger.info("NmapPage finalized, ongoing scan cancellation requested.")
        if hasattr(self, "scanner") and self.scanner:
            del self.scanner # Explicitly delete to trigger NmapScanner.__del__
        # The super().__del__() is important if GObject has its own __del__ in the future,
        # though typically not needed for Adw.Bin unless it's holding specific resources.
        # For Python's garbage collection, direct __del__ on GObject subclasses is tricky.
        # Explicit cleanup of resources like threads or external processes is better done
        # in a dedicated `destroy` or `cleanup` method tied to widget lifecycle.

    def _on_nmap_source_style_settings_changed(self, _source: GObject.Object, _pspec: GObject.ParamSpec) -> None:
        """Handles theme (dark/light) or GtkSourceView style scheme changes.

        This callback is triggered when the system theme changes or when the user
        selects a different source style scheme in the application preferences.
        It logs the change; actual style re-application to existing GtkSourceView
        widgets (if any were dynamically created and visible) would need to iterate
        them and call `_apply_source_view_style_to_buffer`. Currently, new views
        get the style upon creation.

        :param _source: The :class:`GObject.Object` that emitted the signal (StyleManager or Settings).
        :type _source: GObject.Object
        :param _pspec: The :class:`GObject.ParamSpec` describing the changed property (unused).
        :type _pspec: GObject.ParamSpec
        :return: None
        :rtype: None
        """
        logger.info(
            "NmapPage: System theme or source style scheme changed. "
            "Existing GtkSourceView instances in scan results will reflect this.")
        # If results are visible, re-apply styles to existing GtkSourceView instances.
        # This requires iterating through visible expanders and their source views.
        if self.nmap_detail_box.get_first_child() != self.nmap_detail_placeholder:
            selected_row = self.nmap_host_listbox.get_selected_row()
            if selected_row:
                # Re-triggering selection re-builds and re-styles the detail view
                self._on_target_selected(self.nmap_host_listbox, selected_row)


    def _apply_source_view_style_to_buffer(self, buffer: GtkSource.Buffer) -> None:
        """Applies the appropriate GtkSourceView style scheme to a given buffer.

        Determines the correct style scheme based on the current system theme
        (dark/light) and the user's preferred GtkSourceView style scheme from
        GSettings. Falls back to "Adwaita" or "Adwaita-dark" if schemes are
        not found.

        :param buffer: The :class:`GtkSource.Buffer` to apply the style to.
        :type buffer: GtkSource.Buffer
        :return: None
        :rtype: None
        """
        is_dark = self.style_manager.get_dark()
        user_scheme_name = self.settings.get_string("source-style-scheme")
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        final_scheme_name = "Adwaita"  # Default fallback for light theme

        if is_dark:
            final_scheme_name = "Adwaita-dark" # Default fallback for dark theme
            # If user selected a light theme but system is dark, prefer Adwaita-dark
            if user_scheme_name.lower() in ["adwaita", "default", "classic", "light"]:
                pass # final_scheme_name is already Adwaita-dark
            # If user selected a specific scheme, try to use it
            elif scheme_manager.get_scheme(user_scheme_name):
                final_scheme_name = user_scheme_name
            else: # User's scheme not found, stick to Adwaita-dark
                logger.warning(
                    f"NmapPage: User scheme '{user_scheme_name}' not found for dark theme, "
                    f"falling back to '{final_scheme_name}'.")
        else: # Light theme
            # If user selected a dark theme but system is light, prefer Adwaita
            if user_scheme_name.lower() in ["adwaita-dark", "dark"]:
                pass # final_scheme_name is already Adwaita
            elif scheme_manager.get_scheme(user_scheme_name):
                final_scheme_name = user_scheme_name
            else: # User's scheme not found, stick to Adwaita
                logger.warning(
                    f"NmapPage: User scheme '{user_scheme_name}' not found for light theme, "
                    f"falling back to '{final_scheme_name}'.")

        # Final check: if the chosen scheme (even default) doesn't exist, log error.
        # GtkSource.StyleSchemeManager.get_scheme() returns None if not found.
        if not scheme_manager.get_scheme(final_scheme_name):
            logger.error(
                f"NmapPage: Determined style scheme '{final_scheme_name}' could not be loaded. "
                "SourceView styling might be incorrect.")
            # At this point, buffer.set_style_scheme() might fail or do nothing.
            # No further fallback here as Adwaita itself would be missing.
        else:
            logger.debug(
                "NmapPage: Applying source style scheme: %s (Dark Mode: %s, User Preference: %s)",
                final_scheme_name, is_dark, user_scheme_name)
            apply_source_style_scheme(scheme_manager, buffer, final_scheme_name)


    def _init_page_ui(self) -> None:
        """Initializes static UI components of the Nmap page.

        Binds the model for the host listbox and ensures the detail view
        placeholder is shown initially.

        :return: None
        :rtype: None
        """
        logger.debug("Initializing NmapPage UI components.")
        self.nmap_host_listbox.bind_model(
            self.nmap_target_listbox_store, self._create_target_listbox_row)
        # Ensure only placeholder is visible initially in detail area
        self._clear_dynamic_details()
        self.nmap_detail_placeholder.set_visible(True)
        logger.debug("NmapPage _init_page_ui completed.")

    def _clear_dynamic_details(self) -> None:
        """Removes all dynamically added widgets (scan result details) from the
        `nmap_detail_box`, leaving only the `nmap_detail_placeholder` if it's
        supposed to be there.

        :return: None
        :rtype: None
        """
        child = self.nmap_detail_box.get_first_child()
        while child:
            # Do not remove the placeholder itself if it's the only child or part of initial setup
            if child == self.nmap_detail_placeholder:
                child = child.get_next_sibling()
                continue
            current_child_to_remove = child
            child = child.get_next_sibling()
            self.nmap_detail_box.remove(current_child_to_remove)

    def _connect_signals(self) -> None:
        """Connects Gtk signals for various UI elements to their respective handlers.

        This includes interactions like button clicks, entry activation, and
        list row selection.

        :return: None
        :rtype: None
        """
        logger.debug("Connecting NmapPage signals.")
        self.nmap_target_entryrow.connect("activate", self._on_target_activate)
        self.nmap_apply_button.connect("clicked", self._on_target_activate)
        self.nmap_host_listbox.connect("row-selected", self._on_target_selected)
        if self.nmap_cancel_scan_button: # This button might not exist in all UI versions
            self.nmap_cancel_scan_button.connect("clicked", self._on_cancel_scan_clicked)

    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Cancel Scan' button.

        If an Nmap scan is currently in progress and cancellable, this method
        requests its cancellation.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :return: None
        :rtype: None
        """
        logger.info("Cancel scan button clicked.")
        if self.current_nmap_cancellable and \
           not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel()
            logger.info("Nmap scan cancellation requested via button.")
            # UI updates (spinner, button sensitivity) are handled by _nmap_scan_task_done_cb
            # when the task acknowledges the cancellation.
        else:
            logger.warning("No active scan or cancellable object found to cancel.")

    def _on_target_activate(self, _widget: Gtk.Widget) -> None: # Adw.EntryRow or Gtk.Button
        """Handles activation of the target entry row (e.g., Enter key) or
        click of the 'Scan' (Apply) button.

        Validates the target input. If valid, it gathers all selected Nmap scan
        parameters from the UI, cancels any ongoing scan, and initiates a new
        asynchronous scan task using :meth:`_run_nmap_scan_thread_func`.
        Updates UI to reflect the scanning state.

        :param _widget: The widget that triggered the action (Adw.EntryRow or Gtk.Button, unused).
        :type _widget: Gtk.Widget
        :return: None
        :rtype: None
        """
        logger.debug("_on_target_activate called by widget: %s.", type(_widget).__name__)
        target = self.nmap_target_entryrow.get_text().strip()
        self._clear_error() # Clear previous error states

        if not self.scanner.validate_target_input(target):
            message = ("Invalid target format. Please enter a valid IP address, "
                       "CIDR range, or hostname (e.g., example.com, 192.168.1.0/24).")
            show_global_toast(self, message)
            self.nmap_target_entryrow.add_css_class("error")
            return
        self.nmap_target_entryrow.remove_css_class("error")

        if not target: # Should be caught by validate_target_input if truly empty after strip
            self._clear_results() # Clear UI if target is empty
            return

        # Cancel any existing scan task before starting a new one
        if self.current_nmap_task and not self.current_nmap_task.is_done():
            if self.current_nmap_cancellable and \
               not self.current_nmap_cancellable.is_cancelled():
                logger.info("Requesting cancellation of previous Nmap scan task before starting new one.")
                self.current_nmap_cancellable.cancel()
                # Note: The new scan will start immediately. The old task's cancellation
                # and cleanup will happen in its _nmap_scan_task_done_cb.
            # If task is running but cancellable is already cancelled or None, log it.
            elif not self.current_nmap_cancellable or self.current_nmap_cancellable.is_cancelled():
                 logger.warning("Previous scan task is running but cannot be cancelled (no active cancellable). "
                                "Proceeding with new scan may lead to overlapping operations if not careful.")


        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Starting scan for {target}...")
        self.current_nmap_cancellable = Gio.Cancellable()

        # Gather scan parameters from UI elements
        scan_params: NmapScanParameters = { # type: ignore # pyright complains due to total=False
            "target": target,
            "os_fingerprinting": self.nmap_fingerprint_switchrow.get_active(),
            "scan_all_ports": self.nmap_all_ports_switchrow.get_active(),
            "selected_script": None, # Default to None
            "service_version": self.nmap_service_version_switchrow.get_active(),
            "no_ping": self.nmap_no_ping_switchrow.get_active(),
            "timing_template": "T3", # Default timing
            "custom_dns_server": self.settings.get_string("custom-dns-server") or None
        }
        # Selected script from dropdown
        selected_script_item = self.nmap_scripts_dropdown.get_selected_item()
        if isinstance(selected_script_item, Gtk.StringObject):
            script_str = selected_script_item.get_string()
            if script_str and script_str.lower() != "none":
                scan_params["selected_script"] = script_str
        # Timing template from combobox
        timing_item = self.nmap_timing_template_comborow.get_selected_item()
        if timing_item and isinstance(timing_item, Gtk.StringObject):
            timing_str = timing_item.get_string() # e.g., "Normal (T3)"
            match = re.search(r"\(T([0-5])\)", timing_str)
            if match:
                scan_params["timing_template"] = f"T{match.group(1)}"

        logger.info("NmapPage: Starting Nmap scan task with parameters: %s", scan_params)
        self._current_nmap_scan_params = scan_params # Store for the thread

        # Create and run the Gio.Task for background processing
        self.current_nmap_task = Gio.Task.new(
            source_object=self, # Source of the task
            cancellable=self.current_nmap_cancellable,
            callback=self._nmap_scan_task_done_cb, # Callback when done
            user_data=None # Optional user data
        )
        self.current_nmap_task.run_in_thread(self._run_nmap_scan_thread_func)

    def _run_nmap_scan_thread_func(
        self,
        task: Gio.Task,
        _source_object: GObject.Object, # The NmapPage instance
        _task_data: Optional[Dict[str, Any]], # User data from task creation (None here)
        cancellable: Optional[Gio.Cancellable] # The task's cancellable
    ) -> None:
        """Executes the Nmap scan in a background thread via :class:`NmapScanner`.

        This function is intended to be run by :meth:`Gio.Task.run_in_thread`.
        It retrieves scan parameters stored in `self._current_nmap_scan_params`,
        calls :meth:`NmapScanner.run_nmap_scan`, and returns the results or
        reports errors through the `task` object.

        :param task: The :class:`Gio.Task` associated with this background operation.
        :type task: Gio.Task
        :param _source_object: The source object that initiated the task (the NmapPage instance).
        :type _source_object: GObject.Object
        :param _task_data: Task-specific data passed during task creation (unused here).
        :type _task_data: Optional[Dict[str, Any]]
        :param cancellable: A :class:`Gio.Cancellable` to monitor for cancellation requests.
        :type cancellable: Optional[Gio.Cancellable]
        :return: None. Results or errors are set on the `task` object.
        :rtype: None
        """
        # Retrieve the parameters that were stored just before starting the task
        page_instance: NmapPage = _source_object # type: ignore
        params_for_scan = page_instance._current_nmap_scan_params

        if not params_for_scan: # Should not happen if _on_target_activate set it
            logger.error("NmapPage: _run_nmap_scan_thread_func called but _current_nmap_scan_params is None.")
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.UNEXPECTED.value,
                "Internal error: Scan parameters were not found for the background task.")
            return

        target = params_for_scan.get("target", "Unknown Target") # Default for logging
        logger.debug("_run_nmap_scan_thread_func started for target: %s", target)

        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.CANCELLED.value,
                f"Scan for {target} cancelled before Nmap process start.")
            return

        try:
            # Perform the potentially long-running Nmap scan
            nm_results: nmap.PortScanner = self.scanner.run_nmap_scan(params_for_scan, cancellable=cancellable)

            # Check for cancellation again after the blocking call returns
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(
                    GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                    NmapScanErrorType.CANCELLED.value,
                    f"Scan for {target} cancelled during or after Nmap execution.")
            else:
                task.return_value(nm_results) # Return the nmap.PortScanner object on success
        except ScanCancelledError as e:
            logger.info("Nmap scan for %s was explicitly cancelled by NmapScanner: %s", target, e)
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.CANCELLED.value, str(e))
        except PortScannerError as e: # Catches errors from NmapScanner.run_nmap_scan
            logger.error("Nmap scan error for %s: %s", target, e, exc_info=True)
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.SCAN_FAILED.value,
                f"Nmap scan error for {target}: {e}")
        except Exception as e:  # Catch-all for any other unexpected errors
            logger.exception("Unexpected exception in Nmap scan task for %s (%s):", target, type(e).__name__)
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.UNEXPECTED.value,
                f"Scan for {target} failed unexpectedly: {e}")
        finally:
            logger.info("Nmap scan thread finished for target: %s.", target)


    def _nmap_scan_task_done_cb(
            self, _source_object: GObject.Object, result: Gio.AsyncResult,
            _user_data: Optional[Any]) -> None:
        """Callback executed in the main thread when the Nmap scan :class:`Gio.Task` completes.

        Processes the results (an :class:`nmap.PortScanner` object on success) or
        handles errors reported by the background task. Updates the UI to display
        results or error messages and resets UI elements to their idle state.

        :param _source_object: The source object that initiated the task (the NmapPage instance, unused).
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: Optional[Any]
        :return: None
        :rtype: None
        """
        original_target = (self._current_nmap_scan_params.get("target", "unknown target") # type: ignore
                           if self._current_nmap_scan_params else "unknown target")
        logger.info("Nmap scan task completion callback started for target: %s.", original_target)

        nm_results_final: Optional[nmap.PortScanner] = None
        try:
            # propagate_value() will return the value set by task.return_value()
            # or raise GLib.Error if task.return_new_error_literal() was called.
            propagated_value = result.propagate_value()

            if isinstance(propagated_value, nmap.PortScanner):
                nm_results_final = propagated_value
            # Handle cases where Gio might wrap results (e.g., Gio.DBusCallFlags)
            elif hasattr(propagated_value, 'value') and \
                 isinstance(getattr(propagated_value, 'value'), nmap.PortScanner):
                logger.debug("NmapPage: Received wrapped GObject with .value attribute "
                             "containing nmap.PortScanner. Unwrapping.")
                nm_results_final = getattr(propagated_value, 'value')
            elif propagated_value is not None: # Unexpected type if not None and not PortScanner
                logger.error("Nmap scan for %s returned an unexpected result type: %s",
                             original_target, type(propagated_value))
                self._handle_scan_error(original_target, "Scan returned unexpected data type.")
            # If propagated_value is None and no error was raised, it's an issue.
            # However, Gio.Task usually raises if return_value(None) was called by mistake
            # when an error should have been set.

            if nm_results_final: # Successfully retrieved PortScanner object
                self._process_scan_results(nm_results_final, original_target)

        except GLib.Error as e: # Errors set by task.return_new_error_literal()
            logger.warning("Nmap scan for %s failed or was cancelled. Error Domain: '%s', Code: %d, Message: '%s'",
                           original_target, e.domain, e.code, e.message)
            if e.matches(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value):
                self._set_scan_status(ScanStatus.IDLE, f"Scan for {original_target} cancelled.")
                self._clear_results() # Optionally clear results on cancel
            elif e.matches(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.SCAN_FAILED.value):
                self._handle_scan_error(original_target, f"Scan Failed: {e.message}")
            else: # UNEXPECTED or other GLib.Error
                self._handle_scan_error(original_target, f"Unexpected Scan Error: {e.message}")
        except Exception as e: # Catch any other Python exceptions from this callback itself
            logger.exception("NmapPage: Unexpected Python error in _nmap_scan_task_done_cb for %s:", original_target)
            self._handle_scan_error(original_target, f"Unexpected error processing scan results: {e}")
        finally:
            # Reset task references and UI state
            self.current_nmap_task = None
            self.current_nmap_cancellable = None
            # Set status to IDLE unless a more specific status was set by error/success handlers
            if self.status_row.get_subtitle() not in [ # type: ignore
                f"Scan for {original_target} cancelled.",
                f"Scan failed for {original_target}", # Assuming _handle_scan_error sets this
                f"Scan complete for {original_target}. No hosts found or responsive.", # From _process_scan_results
                # Add other terminal messages from _process_scan_results if needed
            ] and not (self.status_row.get_subtitle() and "host(s) found" in self.status_row.get_subtitle()): # type: ignore
                self._set_scan_status(ScanStatus.IDLE, "Idle")

            # Re-enable UI elements
            self.nmap_target_entryrow.set_sensitive(True)
            if self.nmap_apply_button:
                self.nmap_apply_button.set_sensitive(True)
            if self.nmap_cancel_scan_button: # Ensure this button exists
                self.nmap_cancel_scan_button.set_sensitive(False)
                self.nmap_cancel_scan_button.set_visible(False)


    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str) -> None:
        """Processes successful Nmap scan results from the :class:`nmap.PortScanner` object.

        Converts results to YAML, updates the UI list view, and sets the status message.
        If no hosts are found, it updates the UI placeholder accordingly.

        :param nm: The :class:`nmap.PortScanner` object containing the scan results.
        :type nm: nmap.PortScanner
        :param original_target: The target string originally scanned.
        :type original_target: str
        :return: None
        :rtype: None
        """
        logger.debug("Processing Nmap scan results for target: %s", original_target)
        hosts_found = nm.all_hosts()
        logger.debug("Hosts found by Nmap scan: %s", hosts_found)

        if not hosts_found:
            logger.warning("No hosts found in Nmap scan results for target %s.", original_target)
            self._set_scan_status(ScanStatus.COMPLETE,
                                  f"Scan complete for {original_target}. No hosts found or responsive.")
            self._clear_dynamic_details() # Clear previous details
            self.nmap_detail_placeholder.set_title("No Responsive Hosts Found")
            self.nmap_detail_placeholder.set_description(
                f"The Nmap scan for '{original_target}' did not find any responsive hosts "
                "or all specified hosts were down.")
            self.nmap_detail_placeholder.set_visible(True)
            return

        # Convert results to YAML for display and storage
        results_yaml_map = self.scanner.convert_results_to_yaml(nm)
        if results_yaml_map:
            logger.debug("Converted Nmap results to YAML. Host keys: %s", list(results_yaml_map.keys()))
        else: # Should not happen if hosts_found is not empty
            logger.warning("Nmap results YAML map is empty despite hosts being found for %s.", original_target)

        # Update UI in the main thread
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        self._set_scan_status(ScanStatus.COMPLETE,
                              f"Scan complete for {original_target}. {len(hosts_found)} host(s) found.")


    def _handle_scan_error(self, target: str, error_message: str) -> None:
        """Handles errors reported from the Nmap scan task or its processing.

        Displays a global error message and sets the scan status UI to 'Failed'.

        :param target: The target string for which the scan failed.
        :type target: str
        :param error_message: The error message to display.
        :type error_message: str
        :return: None
        :rtype: None
        """
        logger.debug("Handling Nmap scan error for target '%s': %s", target, error_message)
        show_global_error(self, f"Error scanning {target}: {error_message.splitlines()[0]}")
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")
        self._clear_results() # Clear any partial results from view

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        """Handles selection of a host in the Nmap results :class:`Gtk.ListBox`.

        When a host row is selected, this method clears any previous details shown
        and then parses the YAML data for the selected host to populate the detail
        view with various expandable sections (Host Info, Ports, OS, Raw Summary).

        :param _listbox: The :class:`Gtk.ListBox` from which a row was selected (unused).
        :type _listbox: Gtk.ListBox
        :param row: The selected :class:`Gtk.ListBoxRow`, or ``None`` if selection is cleared.
        :type row: Optional[Gtk.ListBoxRow]
        :return: None
        :rtype: None
        """
        self._clear_dynamic_details() # Clear previous host's details

        if row is None: # Selection cleared
            self.nmap_detail_placeholder.set_title("No Host Selected")
            self.nmap_detail_placeholder.set_description("Select a host from the list to view its details.")
            if not self.nmap_detail_placeholder.get_parent(): # Ensure placeholder is in the box
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return

        self.nmap_detail_placeholder.set_visible(False) # Hide placeholder when a row is selected
        item_obj = getattr(row, 'nmap_item', None) if isinstance(row, NmapTargetRow) else None

        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            logger.debug("Host selected in ListBox: %s", selected_target_key)
            try:
                # The 'value' of NmapItem is the YAML string
                host_data_dict = yaml.safe_load(item_obj.value)
                if not isinstance(host_data_dict, dict): # Basic check after parsing
                    logger.warning("Parsed YAML for host %s is not a dictionary. Content: %s",
                                   selected_target_key, item_obj.value[:100] + "...")
                    host_data_dict = {} # Use empty dict to avoid errors in expander methods
                logger.debug("Successfully parsed YAML for host %s. Data keys: %s",
                             selected_target_key, list(host_data_dict.keys()) if host_data_dict else "None")
            except yaml.YAMLError as e:
                logger.error("Error parsing YAML for host %s: %s", selected_target_key, e)
                error_label = Gtk.Label(
                    label=f"Error: Could not parse scan results for {selected_target_key}.\nDetails: {e}",
                    wrap=True, halign=Gtk.Align.START)
                self.nmap_detail_box.append(error_label)
                return

            # Populate detail area with expanders for different sections of Nmap data
            self._add_host_details_expander(host_data_dict, selected_target_key)
            self._add_ports_expander(host_data_dict, selected_target_key)
            self._add_os_expander(host_data_dict, selected_target_key)
            self._add_raw_output_expander(host_data_dict, selected_target_key) # For text summary
        else: # Row selected is not an NmapTargetRow or item_obj is somehow None
            logger.warning("Selected row is not a valid NmapTargetRow or has no NmapItem. Row: %s", row)
            self.nmap_detail_placeholder.set_title("Selection Error")
            self.nmap_detail_placeholder.set_description("Could not process the selected item.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)


    def _add_raw_output_expander(self, host_data_dict: Dict[str, Any], host_key: str) -> None:
        """Adds an :class:`Adw.ExpanderRow` to display a human-readable text summary of the scan for a host.

        The summary is generated by :meth:`_generate_human_readable_host_summary` and displayed
        in a non-editable :class:`GtkSource.View`. A button to copy this summary is also added.

        :param host_data_dict: The parsed YAML data for the host.
        :type host_data_dict: Dict[str, Any]
        :param host_key: The identifier of the host (IP or name).
        :type host_key: str
        :return: None
        :rtype: None
        """
        logger.debug("Adding text scan summary expander for host: %s", host_key)
        expander = Adw.ExpanderRow(title=f"Text Scan Summary - {host_key}")
        expander.set_expanded(False) # Collapsed by default

        human_readable_summary = self._generate_human_readable_host_summary(host_data_dict)
        source_view, source_buffer = create_source_view(language_name="text") # Use utility
        source_buffer.set_text(human_readable_summary, -1)
        self._apply_source_view_style_to_buffer(source_buffer) # Apply current theme/style
        source_view.set_editable(False)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_child(source_view)
        scrolled_window.set_min_content_height(200) # Suggested height
        scrolled_window.set_max_content_height(400) # Limit max height
        scrolled_window.set_vexpand(True) # Allow vertical expansion
        expander.add_row(scrolled_window)

        copy_summary_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_summary_button.set_tooltip_text("Copy Full Host Summary to Clipboard")
        copy_summary_button.get_style_context().add_class("flat")
        copy_summary_button.connect(
            "clicked", lambda _btn, text=human_readable_summary: self._on_copy_host_summary_clicked(text))
        expander.add_suffix(copy_summary_button)

        self.nmap_detail_box.append(expander)

    def _on_copy_host_summary_clicked(self, summary_text: str) -> None:
        """Handles the 'clicked' signal for the 'Copy Host Summary' button within an expander.

        Copies the provided summary text to the clipboard and shows a confirmation toast.

        :param summary_text: The text summary to copy.
        :type summary_text: str
        :return: None
        :rtype: None
        """
        logger.info("Copying Nmap host summary to clipboard.")
        if not summary_text:
            show_global_toast(self, "No summary text available to copy.")
            return

        try:
            clipboard = self.get_clipboard()
            if clipboard:
                clipboard.set(summary_text)
                show_global_toast(self, "Host summary copied to clipboard.")
                logger.debug("Successfully copied Nmap host summary to clipboard.")
            else:
                logger.warning("Could not get clipboard for NmapPage to copy summary.")
                show_global_toast(self, "Failed to access clipboard.")
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("Error copying Nmap host summary to clipboard:")
            show_global_toast(self, f"Error copying summary: {e}")


    def _add_host_details_expander(self, host_data: Dict[str, Any], host_key: str) -> None: # pylint: disable=too-many-locals
        """Adds an :class:`Adw.ExpanderRow` to display general host information.

        This includes status, IP/MAC addresses, and reported hostnames.

        :param host_data: The parsed YAML data for the host.
        :type host_data: Dict[str, Any]
        :param host_key: The identifier of the host (IP or name).
        :type host_key: str
        :return: None
        :rtype: None
        """
        logger.debug("Adding host details expander for host: %s", host_key)
        expander = Adw.ExpanderRow(title=f"Host Information - {host_key}")
        expander.set_expanded(True) # Expanded by default

        # Host Status (Up/Down)
        status_info = host_data.get("status", {})
        status_subtitle = f"{status_info.get('state', 'N/A')} (Reason: {status_info.get('reason', 'N/A')})"
        status_row = Adw.ActionRow(title="Status", subtitle=status_subtitle)
        expander.add_row(status_row)

        # Addresses (IPv4, IPv6, MAC)
        addresses_info = host_data.get("addresses", {})
        if ipv4_addr := addresses_info.get("ipv4"):
            expander.add_row(Adw.ActionRow(title="IPv4 Address", subtitle=ipv4_addr))
        if ipv6_addr := addresses_info.get("ipv6"):
            expander.add_row(Adw.ActionRow(title="IPv6 Address", subtitle=ipv6_addr))
        if mac_addr := addresses_info.get("mac"):
            expander.add_row(Adw.ActionRow(title="MAC Address", subtitle=mac_addr))

        # Hostnames
        hostnames_list = host_data.get("hostnames", [])
        if hostnames_list and isinstance(hostnames_list, list):
            for hn_entry in hostnames_list:
                if isinstance(hn_entry, dict):
                    hn_title = f"Hostname ({hn_entry.get('type', 'N/A')})"
                    hn_subtitle = hn_entry.get("name", "N/A")
                    expander.add_row(Adw.ActionRow(title=hn_title, subtitle=hn_subtitle))
        else: # No hostnames or not in expected list format
            expander.add_row(Adw.ActionRow(title="Hostnames", subtitle="No hostnames reported"))

        self.nmap_detail_box.append(expander)


    def _add_ports_expander(self, host_data: Dict[str, Any], host_key: str) -> None:
        """Adds an :class:`Adw.ExpanderRow` to display detected network ports and their details.

        Iterates through protocols (TCP, UDP, etc.) and lists each port with its state,
        service name, product, version, and reason for its state.

        :param host_data: The parsed YAML data for the host.
        :type host_data: Dict[str, Any]
        :param host_key: The identifier of the host (IP or name).
        :type host_key: str
        :return: None
        :rtype: None
        """
        logger.debug("Adding ports expander for host: %s", host_key)
        expander = Adw.ExpanderRow(title=f"Network Ports - {host_key}")
        expander.set_expanded(True) # Usually important, expand by default
        ports_found_for_display = False

        for proto in ["tcp", "udp", "sctp", "ip"]: # Common Nmap protocols
            proto_data = host_data.get(proto)
            if isinstance(proto_data, dict): # Ensure protocol data is a dictionary of ports
                for port_id_str, port_info in proto_data.items():
                    if isinstance(port_info, dict): # Ensure port_info itself is a dict
                        ports_found_for_display = True
                        state = port_info.get("state", "N/A")
                        name = port_info.get("name", "") # Service name
                        product = port_info.get("product", "")
                        version = port_info.get("version", "")
                        reason = port_info.get("reason", "")

                        title = f"Port {port_id_str}/{proto.upper()} ({state})"
                        subtitle_parts = [name, product, version]
                        subtitle = " ".join(filter(None, subtitle_parts)) # Join non-empty parts
                        subtitle = (f"{subtitle} (Reason: {reason})" if subtitle else f"Reason: {reason}")
                        expander.add_row(Adw.ActionRow(title=title, subtitle=subtitle))

        if not ports_found_for_display:
            expander.add_row(Adw.ActionRow(title="Ports",
                                           subtitle="No open/interesting ports reported or port data available."))
        self.nmap_detail_box.append(expander)


    def _add_os_expander(self, host_data: Dict[str, Any], host_key: str) -> None:
        """Adds an :class:`Adw.ExpanderRow` to display OS detection results from Nmap.

        Lists OS matches with their accuracy and OS class details (type, vendor, family, generation).

        :param host_data: The parsed YAML data for the host.
        :type host_data: Dict[str, Any]
        :param host_key: The identifier of the host (IP or name).
        :type host_key: str
        :return: None
        :rtype: None
        """
        osmatch_data = host_data.get("osmatch", [])
        if not osmatch_data or not isinstance(osmatch_data, list): # OS data might be missing or not a list
            logger.debug("No OS detection data or invalid format for host %s, skipping OS expander.", host_key)
            return # Do not add expander if no OS data

        logger.debug("Adding OS detection expander for host %s. OS match data count: %d", host_key, len(osmatch_data))
        expander = Adw.ExpanderRow(title=f"Operating System Detection - {host_key}")
        expander.set_expanded(True) # OS info is often primary, expand by default
        os_details_added_to_expander = False

        for match in osmatch_data:
            if isinstance(match, dict): # Each match should be a dictionary
                name = match.get("name", "N/A")
                accuracy = match.get("accuracy", "N/A")
                title = f"{name} (Accuracy: {accuracy}%)"
                osclass_details_parts: List[str] = []

                osclass_data_list = match.get("osclass", [])
                # osclass can be a single dict or a list of dicts
                osclasses_to_process = (osclass_data_list if isinstance(osclass_data_list, list)
                                        else [osclass_data_list] if isinstance(osclass_data_list, dict)
                                        else [])

                for os_class in osclasses_to_process:
                    if isinstance(os_class, dict):
                        vendor = os_class.get("vendor", "N/A")
                        os_family = os_class.get("osfamily", "N/A")
                        os_gen = os_class.get("osgen", "N/A")
                        osclass_type = os_class.get("type", "N/A")
                        osclass_details_parts.append(
                            f"Type: {osclass_type}, Vendor: {vendor}, "
                            f"Family: {os_family}, Gen: {os_gen}")

                subtitle = ("\n".join(osclass_details_parts) if osclass_details_parts
                            else "No specific OS class details available for this match.")
                row = Adw.ActionRow(title=title, subtitle=subtitle)
                expander.add_row(row)
                os_details_added_to_expander = True

        if not os_details_added_to_expander: # If loop didn't add any valid rows
            expander.add_row(Adw.ActionRow(title="OS Detection",
                                           subtitle="No specific OS matches found or data was malformed."))
        self.nmap_detail_box.append(expander)


    def _update_results_view(self, hosts: List[str], results_map: Dict[str, str]) -> None:
        """Updates the host :class:`Gtk.ListBox` with new Nmap scan results.

        Clears previous results and populates the list with :class:`NmapItem` objects
        representing each scanned host. If hosts are present, selects the first host
        by default to show its details.

        :param hosts: A list of host identifiers (IPs or names) found in the scan.
        :type hosts: List[str]
        :param results_map: A dictionary mapping host identifiers to their scan results
                            in YAML string format.
        :type results_map: Dict[str, str]
        :return: None
        :rtype: None
        """
        logger.info("Updating Nmap results view for %d hosts: %s", len(hosts), hosts)
        self.nmap_target_listbox_store.remove_all() # Clear previous items
        self.results_by_host.clear() # Clear cached YAML data

        if not hosts: # No hosts found or reported
            self._clear_dynamic_details() # Clear any old details
            self.nmap_detail_placeholder.set_title("No Hosts Found or Scanned")
            self.nmap_detail_placeholder.set_description(
                "The Nmap scan did not find any responsive hosts, or no targets were specified.")
            if not self.nmap_detail_placeholder.get_parent(): # Ensure placeholder is in the box
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return

        # Populate the list store with new results
        for host_key in hosts:
            yaml_data = results_map.get(host_key, f"# Error: No YAML data found for host {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data # Cache for potential reuse

        # Select the first host in the list by default, if any
        if self.nmap_target_listbox_store.get_n_items() > 0:
            first_row = self.nmap_host_listbox.get_row_at_index(0)
            if first_row:
                self.nmap_host_listbox.select_row(first_row)
            # _on_target_selected will be triggered by select_row to show details
        else: # Should not happen if hosts list was not empty, but as a safeguard
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts to Display")
            self.nmap_detail_placeholder.set_description("Scan results are empty or could not be processed.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)


    def _set_scan_status(self, status_type: ScanStatus, message: str) -> None:
        """Sets the scan status message and updates UI elements via :meth:`GLib.idle_add`.

        This ensures UI updates are performed safely in the main GTK thread.

        :param status_type: The :class:`ScanStatus` enum member representing the current state.
        :type status_type: ScanStatus
        :param message: The status message string to display.
        :type message: str
        :return: None
        :rtype: None
        """
        logger.info("Setting Nmap scan status: %s - %s", status_type.name, message)
        # Schedule UI update on the main GTK thread
        GLib.idle_add(self._update_status_ui, status_type, message)


    def _update_status_ui(self, status_type: ScanStatus, message: str) -> bool:
        """Updates the status row, spinner, and button sensitivity in the UI.
        This method is intended to be called by :meth:`GLib.idle_add`.

        :param status_type: The :class:`ScanStatus` indicating the current scan state.
        :type status_type: ScanStatus
        :param message: The message to display in the status row.
        :type message: str
        :return: Returns :attr:`GLib.SOURCE_REMOVE` if called via `GLib.idle_add`
                 to prevent it from being called again, though typically `idle_add`
                 runs it once by default unless True is returned. For safety,
                 explicitly returning False or GLib.SOURCE_REMOVE is good.
        :rtype: bool
        """
        self.status_row.set_subtitle(message)
        # Manage CSS classes for status row styling
        status_css_classes = ["success-color", "warning-color", "error-color", "accent-color"]
        style_context = self.status_row.get_style_context()
        for css_class in status_css_classes:
            style_context.remove_class(css_class)

        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True)
            self.scan_spinner.start()
            self.status_row.set_title("Scanning...")
            style_context.add_class("accent-color") # Visual cue for ongoing activity
            if self.nmap_apply_button: self.nmap_apply_button.set_sensitive(False)
            if self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(True)
                self.nmap_cancel_scan_button.set_sensitive(True)
        else: # COMPLETE, FAILED, IDLE, or CANCELLED (if handled by IDLE state)
            self.scan_spinner.stop()
            self.scan_spinner.set_visible(False)
            if self.nmap_apply_button: self.nmap_apply_button.set_sensitive(True)
            if self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(False)
                self.nmap_cancel_scan_button.set_sensitive(False)

            if status_type == ScanStatus.COMPLETE:
                self.status_row.set_title("Scan Complete")
                style_context.add_class("success-color")
            elif status_type == ScanStatus.FAILED:
                self.status_row.set_title("Scan Failed")
                style_context.add_class("error-color")
            elif status_type == ScanStatus.IDLE: # Also covers CANCELLED if it reverts to IDLE
                self.status_row.set_title("Idle")
                # No specific color class for idle, or remove all accent/error/success
            else: # Should not happen given ScanStatus enum
                self.status_row.set_title("Scan Status") # Generic title

        return GLib.SOURCE_REMOVE # Ensures this runs only once per idle_add call


    def _clear_results(self) -> None:
        """Clears all Nmap scan results from the UI, including the host list and detail view.

        Resets the UI to its initial state before any scan is run or after results are cleared.
        Sets the scan status to Idle.

        :return: None
        :rtype: None
        """
        logger.info("Clearing Nmap results display and detail view.")
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()
        self._clear_dynamic_details() # Remove expanders from detail box

        # Reset placeholder
        self.nmap_detail_placeholder.set_title("No Host Selected")
        self.nmap_detail_placeholder.set_description(
            "Select a host from the list to view details, or start a new scan.")
        if not self.nmap_detail_placeholder.get_parent(): # Ensure placeholder is in the box
            self.nmap_detail_box.append(self.nmap_detail_placeholder)
        self.nmap_detail_placeholder.set_visible(True)

        # Reset entry row and buttons
        self.nmap_target_entryrow.remove_css_class("error")
        self.nmap_target_entryrow.set_sensitive(True)
        if self.nmap_apply_button:
            self.nmap_apply_button.set_sensitive(True)
        if self.nmap_cancel_scan_button: # Ensure button exists
            self.nmap_cancel_scan_button.set_sensitive(False)
            self.nmap_cancel_scan_button.set_visible(False)

        self._set_scan_status(ScanStatus.IDLE, "Idle")


    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args: Any) -> None:
        """Handles dismissal of the main window's error banner by clearing the error state.

        :param _banner: The :class:`Adw.Banner` that was dismissed (unused).
        :type _banner: Adw.Banner
        :param _args: Additional arguments from the signal (unused).
        :type _args: Any
        :return: None
        :rtype: None
        """
        self._clear_error()

    def _clear_error(self) -> None:
        """Clears any displayed error message from the main window's banner and
        removes error styling from the target entry row.

        :return: None
        :rtype: None
        """
        main_window = self.get_native() # Adw.PreferencesPage.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error() # type: ignore
        else:
            # This can happen if the page is not yet fully part of a window hierarchy
            logger.debug("NmapPage: Could not find main_window or hide_error method to clear error banner.")
        if self.nmap_target_entryrow:
            self.nmap_target_entryrow.remove_css_class("error")


    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        """Factory function to create a :class:`NmapTargetRow` for the host :class:`Gtk.ListBox`.

        This method is used by `Gtk.ListBox.bind_model`.

        :param item: The :class:`NmapItem` data for which to create a row.
        :type item: NmapItem
        :return: A new :class:`NmapTargetRow` instance displaying the item's key.
        :rtype: Gtk.ListBoxRow
        """
        return NmapTargetRow(nmap_item=item)

    def _generate_human_readable_host_summary(self, host_data_dict: Dict[str, Any]) -> str:
        """Generates a human-readable text summary of scan data for a single host.

        This summary includes host status, addresses, hostnames, script outputs (pre-scan and host),
        open ports with service information, and OS detection details.

        :param host_data_dict: The parsed YAML data (as a dictionary) for the host.
        :type host_data_dict: Dict[str, Any]
        :return: A formatted string containing the human-readable summary.
        :rtype: str
        """
        summary_lines: List[str] = []

        # Host Status and Addresses
        status_info = host_data_dict.get("status", {})
        state = status_info.get("state", "N/A")
        reason = status_info.get("reason", "N/A")
        summary_lines.append(f"Host is {state} (reason: {reason}).")
        addresses_info = host_data_dict.get("addresses", {})
        if ipv4 := addresses_info.get("ipv4"): summary_lines.append(f"IPv4 Address: {ipv4}")
        if ipv6 := addresses_info.get("ipv6"): summary_lines.append(f"IPv6 Address: {ipv6}")
        if mac := addresses_info.get("mac"): summary_lines.append(f"MAC Address: {mac}")

        # Pre-scan Script Results (if any, added during YAML conversion)
        prescan_results = host_data_dict.get("prescript_results")
        if prescan_results and isinstance(prescan_results, list):
            summary_lines.append("\nPre-scan script results:")
            for script_item in prescan_results:
                if isinstance(script_item, dict):
                    script_id = script_item.get("id", "N/A")
                    script_output = script_item.get("output", "N/A")
                    if script_output and isinstance(script_output, str):
                        # Format multi-line script output nicely
                        formatted_output = "\n".join(
                            [f"|_ {script_id}: {line.strip()}" if i == 0 else f"|  {line.strip()}"
                             for i, line in enumerate(script_output.strip().split("\n"))])
                        summary_lines.append(formatted_output)
                    else:
                        summary_lines.append(f"|_ {script_id}: (no output or invalid format)")

        # Hostnames
        hostnames_list = host_data_dict.get("hostnames", [])
        if hostnames_list and isinstance(hostnames_list, list):
            summary_lines.append("\nHostnames:")
            for hn_entry in hostnames_list:
                if isinstance(hn_entry, dict):
                    summary_lines.append(f"  {hn_entry.get('name', 'N/A')} ({hn_entry.get('type', 'N/A')})")

        # Host Script Output
        host_scripts = host_data_dict.get("hostscript")
        if host_scripts and isinstance(host_scripts, list):
            summary_lines.append("\nHost script output:")
            for script_item in host_scripts:
                if isinstance(script_item, dict):
                    script_id = script_item.get("id", "N/A")
                    script_output = script_item.get("output", "N/A")
                    if isinstance(script_output, str):
                        formatted_output = "\n".join([f"    {line.strip()}" for line in script_output.strip().split("\n")])
                        summary_lines.append(f"  Script: {script_id}\n{formatted_output}")
                    else:
                         summary_lines.append(f"  Script: {script_id} (no output or invalid format)")


        # Ports Information
        ports_data_lines: List[str] = []
        for proto in ["tcp", "udp", "sctp", "ip"]: # Iterate common protocols
            proto_data = host_data_dict.get(proto)
            if isinstance(proto_data, dict): # Protocol data should be a dict of ports
                for port_id_str, port_info in proto_data.items():
                    if isinstance(port_info, dict): # Port info should be a dict
                        p_state = port_info.get("state", "N/A")
                        # Only include ports that are not definitively closed or filtered out for brevity
                        if p_state not in ["closed", "filtered out", "closed|filtered"]: # Nmap can use combined states
                            p_name = port_info.get("name", "")
                            p_product = port_info.get("product", "")
                            p_version = port_info.get("version", "")
                            p_reason = port_info.get("reason", "")
                            port_str = (f"{port_id_str}/{proto.upper():<4} "
                                        f"{p_state:<10} {p_name}")
                            if p_product: port_str += f" {p_product}"
                            if p_version: port_str += f" {p_version}"
                            if p_reason: port_str += f" (reason: {p_reason})"
                            ports_data_lines.append(port_str)

                            # Port-specific script output
                            port_scripts = port_info.get("script")
                            if isinstance(port_scripts, dict): # Scripts are a dict under port
                                for script_id, script_output in port_scripts.items():
                                    if script_output and isinstance(script_output, str):
                                        formatted_script_output = "\n".join(
                                            [f"  |_{script_id}: {line.strip()}" if i == 0 else f"  |  {line.strip()}"
                                             for i, line in enumerate(script_output.strip().split("\n"))])
                                        ports_data_lines.append(formatted_script_output)
        if ports_data_lines:
            summary_lines.append("\nPORT      STATE SERVICE      VERSION")
            summary_lines.extend(ports_data_lines)
        else:
            summary_lines.append("\nNo open or otherwise interesting ports reported.")

        # OS Detection Details
        osmatch_data = host_data_dict.get("osmatch", [])
        if osmatch_data and isinstance(osmatch_data, list):
            summary_lines.append("\nOS details:")
            for match in osmatch_data:
                if isinstance(match, dict):
                    summary_lines.append(f"  Name: {match.get('name', 'N/A')} "
                                         f"(Accuracy: {match.get('accuracy', 'N/A')}%)")
                    osclass_data_list = match.get("osclass", [])
                    osclasses_to_process = (osclass_data_list if isinstance(osclass_data_list, list)
                                            else [osclass_data_list] if isinstance(osclass_data_list, dict)
                                            else [])
                    for os_class in osclasses_to_process:
                        if isinstance(os_class, dict):
                            summary_lines.append(
                                f"    Class: Type: {os_class.get('type', 'N/A')}, "
                                f"Vendor: {os_class.get('vendor', 'N/A')}, "
                                f"Family: {os_class.get('osfamily', 'N/A')}, "
                                f"Gen: {os_class.get('osgen', 'N/A')}")
        elif "osmatch" in host_data_dict: # Key exists but no data or wrong format
             summary_lines.append("\nOS details: No specific OS matches found or data malformed.")
        else: # No OS detection attempted or no results
            summary_lines.append("\nOS details: No OS detection data available.")

        return "\n".join(summary_lines)


    def trigger_scan(self) -> None:
        """Programmatically triggers the Nmap 'Scan' (Apply) action.

        Useful for invoking an Nmap scan via a shortcut or another UI event.
        Checks if the scan button is available and sensitive before simulating a click.

        :return: None
        :rtype: None
        """
        logger.debug("Nmap scan triggered by shortcut or external action.")
        if self.nmap_apply_button and self.nmap_apply_button.get_sensitive():
            self.nmap_apply_button.clicked()
        elif self.current_nmap_task and not self.current_nmap_task.is_done():
            show_global_toast(self, "An Nmap scan is already in progress. Please wait or cancel.")
        else:
            logger.warning("Nmap scan button is not available or not sensitive; cannot trigger scan.")


# --- Gio.Task Error Handling ---
NMAP_SCAN_ERROR_DOMAIN = "nmap-scan-error-domain" # Used for GError domain

class NmapScanErrorType(int, Enum):
    """Enumeration of Nmap scan error types for :class:`Gio.Task` error reporting.

    These values are used as error codes within the `NMAP_SCAN_ERROR_DOMAIN`
    when reporting errors via :meth:`Gio.Task.return_new_error_literal`.
    """
    SCAN_FAILED = 0     # Corresponds to nmap.PortScannerError or other Nmap execution issues
    UNEXPECTED = 1      # For other unexpected Python exceptions during scan task
    CANCELLED = 2       # If the scan was cancelled via Gio.Cancellable

```
