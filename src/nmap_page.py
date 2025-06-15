"""
"""Defines the Nmap scanning page for the Woes application.

This page allows users to configure and run Nmap scans against specified targets.
Results are displayed in a structured way, with hosts listed and detailed
information (ports, OS, etc.) shown in expandable sections.
"""

import logging

logger = logging.getLogger(__name__)
import re
from enum import Enum
from typing import Optional, List, Dict, Any # Use list, dict
import collections.abc # For Sequence if needed, though not directly used here
import yaml

import gi
from gi.repository import Adw, Gio, GLib, GObject, Gtk

import nmap

from .constants import APP_ID, RESOURCE_PREFIX
from .nmap_scanner import NmapScanner, ScanStatus, ScanCancelledError
from .utils import show_global_error, show_global_toast


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


class NmapItem(GObject.Object):
    """A simple :class:`GObject.Object` to hold key-value pairs for Nmap results display."""

    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        """
        Initialize an NmapItem.

        :param key: The key string.
        :type key: str
        :param value: The value string.
        :type value: str
        """
        super().__init__()
        self.key = key
        self.value = value


class NmapTargetRow(Gtk.ListBoxRow):
    """A :class:`Gtk.ListBoxRow` customized to display an NmapItem's key."""

    nmap_item = GObject.Property(type=NmapItem)

    def __init__(self, nmap_item: NmapItem, **kwargs: Any):
        """
        Initialize an NmapTargetRow.

        :param nmap_item: The :class:`.NmapItem` to display.
        :type nmap_item: NmapItem
        :param kwargs: Additional keyword arguments for :class:`Gtk.ListBoxRow`.
        :type kwargs: Any
        """
        super().__init__(**kwargs)
        self.nmap_item = nmap_item
        label = Gtk.Label(label=nmap_item.key, halign=Gtk.Align.START, margin_start=6, margin_end=6)
        self.set_child(label)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/nmap_page.ui")
class NmapPage(Adw.PreferencesPage):
    """Activity page for performing Nmap scans and viewing results."""

    __gtype_name__ = "NmapPage"

    nmap_target_entryrow = Gtk.Template.Child("nmap_target_entryrow") # type: Adw.EntryRow
    nmap_apply_button = Gtk.Template.Child("nmap_apply_button") # type: Gtk.Button
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow") # type: Adw.SwitchRow
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow") # type: Adw.SwitchRow
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown") # type: Gtk.DropDown
    nmap_service_version_switchrow = Gtk.Template.Child("nmap_service_version_switchrow") # type: Adw.SwitchRow
    nmap_no_ping_switchrow = Gtk.Template.Child("nmap_no_ping_switchrow") # type: Adw.SwitchRow
    nmap_timing_template_comborow = Gtk.Template.Child("nmap_timing_template_comborow") # type: Adw.ComboRow
    status_row = Gtk.Template.Child("status_row") # type: Adw.ActionRow
    scan_spinner = Gtk.Template.Child("scan_spinner") # type: Gtk.Spinner
    nmap_cancel_scan_button = Gtk.Template.Child() # type: Gtk.Button

    nmap_host_listbox = Gtk.Template.Child("nmap_host_listbox") # type: Gtk.ListBox
    nmap_detail_box = Gtk.Template.Child("nmap_detail_box") # type: Gtk.Box
    nmap_detail_placeholder = Gtk.Template.Child("nmap_detail_placeholder") # type: Adw.StatusPage

    def __init__(self, **kwargs: Any):
        """
        Initialize the NmapPage.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage` constructor.
        :type kwargs: Any
        """
        super().__init__(**kwargs)
        logger.info("Initializing NmapPage...")
        self.results_by_host: dict[str, str] = {}
        self.nmap_target_listbox_store: Gio.ListStore = Gio.ListStore(item_type=NmapItem)
        self.scanner: NmapScanner = NmapScanner()
        self.settings: Gio.Settings = Gio.Settings.new(APP_ID)

        self._current_selected_host_key: Optional[str] = None
        self._current_selected_host_data_dict: Optional[dict[str, Any]] = None

        if self.nmap_apply_button:
            self.nmap_apply_button.get_style_context().add_class("suggested-action")
        if self.nmap_cancel_scan_button: # type: ignore[truthy-bool]
            self.nmap_cancel_scan_button.get_style_context().add_class("destructive-action")

        self.current_nmap_task: Optional[Gio.Task] = None
        self.current_nmap_cancellable: Optional[Gio.Cancellable] = None
        self._current_nmap_scan_params: Optional[dict[str, Any]] = None

        self._init_page_ui()
        self._connect_signals()
        logger.info("NmapPage initialized.")

    def __del__(self):
        """Clean up resources, specifically the NmapScanner's thread pool and cancel any ongoing scan."""
        if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel()
            logger.info("NmapPage finalized, ongoing scan cancelled.")
        if hasattr(self, "scanner") and self.scanner:
            del self.scanner
        super().__del__()

    def _init_page_ui(self):
        """Initialize NmapPage UI components."""
        self.nmap_host_listbox.bind_model(self.nmap_target_listbox_store, self._create_target_listbox_row)
        child = self.nmap_detail_box.get_first_child()
        while child and child != self.nmap_detail_placeholder:
            self.nmap_detail_box.remove(child)
            child = self.nmap_detail_box.get_first_child()

    def _clear_dynamic_details(self):
        """Clear dynamically added details from the detail box."""
        child = self.nmap_detail_box.get_first_child()
        while child:
            if child == self.nmap_detail_placeholder:
                child = child.get_next_sibling()
                continue
            current_child_to_remove = child
            child = child.get_next_sibling()
            self.nmap_detail_box.remove(current_child_to_remove)

    def _connect_signals(self):
        """Connect NmapPage signals."""
        self.nmap_target_entryrow.connect("entry-activated", self._on_target_activate)
        self.nmap_apply_button.connect("clicked", self._on_target_activate)
        self.nmap_host_listbox.connect("row-selected", self._on_target_selected)
        if self.nmap_cancel_scan_button:
            self.nmap_cancel_scan_button.connect("clicked", self._on_cancel_scan_clicked)

    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle the 'Cancel Scan' button click.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        """
        logger.info("Cancel scan button clicked.")
        if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel() # type: ignore[union-attr]
            logger.info("Scan cancellation requested.")
        else:
            logger.warning("No active scan or cancellable to cancel.")

    def _on_target_activate(self, _widget: Adw.EntryRow) -> None: # pylint: disable=unused-argument # Standard GTK signal handler signature
        target = self.nmap_target_entryrow.get_text().strip() # type: ignore[attr-defined]
        self._clear_error()

        if not self.scanner.validate_target_input(target):
            message = "Invalid target format. Please enter a valid IP, CIDR, or hostname."
            show_global_toast(self, message) # type: ignore[arg-type]
            return
        self.nmap_target_entryrow.remove_css_class("error") # type: ignore[attr-defined]

        if not target:
            self._clear_results()
            return

        if self.current_nmap_task and not self.current_nmap_task.is_done():
            if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
                logger.info("Requesting cancellation of previous Nmap scan task.")
                self.current_nmap_cancellable.cancel()
            if not self.current_nmap_task.is_done(): # Re-check after cancel attempt
                 logger.warning("Previous scan task still running. Please cancel it explicitly or wait.")
                 # Optionally, prevent starting a new scan here if the old one couldn't be cancelled quickly
                 # show_global_toast(self, "Previous scan is still finalizing. Please wait.")
                 # return

        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Starting scan for {target}...")

        self.current_nmap_cancellable = Gio.Cancellable()

        scan_params: dict[str, Any] = {
            "target": target,
            "os_fingerprinting": self.nmap_fingerprint_switchrow.get_active(), # type: ignore[attr-defined]
            "scan_all_ports": self.nmap_all_ports_switchrow.get_active(), # type: ignore[attr-defined]
            "selected_script": (
                item.get_string() # type: ignore[union-attr]
                if isinstance(item := self.nmap_scripts_dropdown.get_selected_item(), Gtk.StringObject) and item.get_string() != "None" # type: ignore[attr-defined]
                else None
            ),
            "service_version": self.nmap_service_version_switchrow.get_active(), # type: ignore[attr-defined]
            "no_ping": self.nmap_no_ping_switchrow.get_active(), # type: ignore[attr-defined]
            "timing_template": (
                f"T{m.group(1)}"
                if (m := re.search(r"\(T([0-5])\)", self.nmap_timing_template_comborow.get_selected_item().get_string())) # type: ignore[attr-defined]
                else "T3"
            ),
            "custom_dns_server": self.settings.get_string("custom-dns-server")
        }
        logger.info(f"NmapPage: Starting Nmap scan task with params: {scan_params}")

        self.current_nmap_task = Gio.Task.new(
            self, self.current_nmap_cancellable, self._nmap_scan_task_done_cb, None
        )
        self._current_nmap_scan_params = scan_params
        self.current_nmap_task.run_in_thread(self._run_nmap_scan_thread_func) # type: ignore[arg-type]


    def _run_nmap_scan_thread_func(
        self,
        task: Gio.Task,
        _source_object: GObject.Object,
        _task_data: Optional[dict[str, Any]], # pylint: disable=unused-argument # This arg from run_in_thread is not used
        cancellable: Optional[Gio.Cancellable]
    ) -> None:
        """
        Execute the Nmap scan in a separate thread via :class:`.nmap_scanner.NmapScanner`, for :class:`Gio.Task`.

        :param task: The :class:`Gio.Task` associated with this operation.
        :type task: Gio.Task
        :param _source_object: The :class:`GObject.Object` source of the task.
        :type _source_object: GObject.Object
        :param _task_data: Additional data passed to the task (unused).
        :type _task_data: Optional[dict[str, Any]]
        :param cancellable: A :class:`Gio.Cancellable` object to monitor for cancellation.
        :type cancellable: Optional[Gio.Cancellable]
        """
        page_instance: NmapPage = _source_object # type: ignore[assignment]
        params = page_instance._current_nmap_scan_params

        if not params:
            logger.error("NmapPage: _run_nmap_scan_thread_func: _current_nmap_scan_params is None. This indicates a programming error.")
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.UNEXPECTED.value, "Internal error: Scan parameters not found.") # type: ignore[union-attr]
            return

        target = params["target"]

        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value, "Scan cancelled before start.") # type: ignore[union-attr]
            return

        try:
            nm = self.scanner.run_nmap_scan(params, cancellable=cancellable)

            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value, "Scan cancelled during operation.") # type: ignore[union-attr]
            else:
                task.return_value(nm) # type: ignore[union-attr]
        except ScanCancelledError as e:
            logger.info("Nmap scan for %s was cancelled: %s", target, e)
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value, str(e)) # type: ignore[union-attr]
        except nmap.PortScannerError as e:
            logger.exception("Nmap PortScannerError for %s:", target)
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.SCAN_FAILED.value, f"Nmap scan error: {e}") # type: ignore[union-attr]
        except Exception as e: # pylint: disable=broad-except
            logger.exception("Unexpected exception in Nmap scan task for %s (%s):", target, type(e).__name__)
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.UNEXPECTED.value, f"Scan failed unexpectedly: {e}") # type: ignore[union-attr]
        finally:
            logger.info("Nmap scan thread finished for %s.", target)

    def _nmap_scan_task_done_cb(self, _source_object: GObject.Object, result: Gio.AsyncResult, _user_data: Optional[Any]) -> None: # pylint: disable=unused-argument
        """
        Handle completion of the Nmap scan :class:`Gio.Task`.

        :param _source_object: The :class:`GObject.Object` source of the task.
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: Optional[Any]
        """
        original_target = self._current_nmap_scan_params["target"] if self._current_nmap_scan_params else "unknown target" # type: ignore[index]

        logger.info(f"Nmap scan task done for {original_target}.")

        nm_results_final: Optional[nmap.PortScanner] = None
        try:
            propagated_value = result.propagate_value()

            if isinstance(propagated_value, nmap.PortScanner):
                nm_results_final = propagated_value
            elif hasattr(propagated_value, 'value') and isinstance(getattr(propagated_value, 'value'), nmap.PortScanner): # type: ignore[attr-defined]
                logger.debug(f"NmapPage: Received wrapped object {type(propagated_value)} with .value attribute containing nmap.PortScanner. Unwrapping.")
                nm_results_final = getattr(propagated_value, 'value') # type: ignore[attr-defined]
            elif hasattr(propagated_value, 'value'): # type: ignore[attr-defined]
                logger.error(f"NmapPage: Received wrapped object {type(propagated_value)} with .value of type {type(getattr(propagated_value, 'value'))}. Expected nmap.PortScanner.") # type: ignore[attr-defined]
                self._handle_scan_error(original_target, "Scan returned unexpectedly wrapped data of the wrong type.")
            else:
                logger.error(f"Nmap scan for {original_target} returned unexpected result type: {type(propagated_value)}")
                self._handle_scan_error(original_target, "Scan returned an unexpected data type.")

            if nm_results_final:
                self._process_scan_results(nm_results_final, original_target)

        except GLib.Error as e:
            logger.warning(f"Nmap scan for {original_target} failed or was cancelled. Domain: {e.domain}, Code: {e.code}, Message: {e.message}")
            if e.matches(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value):
                self._set_scan_status(ScanStatus.IDLE, f"Scan for {original_target} cancelled.")
                self._clear_results()
            elif e.matches(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.SCAN_FAILED.value):
                self._handle_scan_error(original_target, e.message)
            else: # UNEXPECTED or other GLib.Error
                self._handle_scan_error(original_target, f"Scan error: {e.message}")
        except Exception as e: # pylint: disable=broad-except
            logger.exception(f"NmapPage: Unexpected Python error in _nmap_scan_task_done_cb for {original_target}:")
            self._handle_scan_error(original_target, f"Unexpected error processing scan results: {e}")
        finally:
            self.current_nmap_task = None
            self.current_nmap_cancellable = None
            self._set_scan_status(ScanStatus.IDLE, "Idle") # Default status after task completion
            self.nmap_target_entryrow.set_sensitive(True) # type: ignore[attr-defined]
            if self.nmap_apply_button:
                self.nmap_apply_button.set_sensitive(True)
            if hasattr(self, 'nmap_cancel_scan_button') and self.nmap_cancel_scan_button: # type: ignore[truthy-bool]
                 self.nmap_cancel_scan_button.set_sensitive(False)
                 self.nmap_cancel_scan_button.set_visible(False)


    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str) -> None:
        """
        Process the Nmap scan results received from the scanner task.

        :param nm: The :class:`nmap.PortScanner` object containing the scan results.
        :type nm: nmap.PortScanner
        :param original_target: The original target string for the scan.
        :type original_target: str
        """
        hosts_found = nm.all_hosts()
        if not hosts_found:
            logger.warning("No hosts found in Nmap results for target %s.", original_target)
            self._set_scan_status(
                ScanStatus.COMPLETE,
                f"Scan complete for {original_target}. No hosts found or responsive.",
            )
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Responsive Hosts") # type: ignore[attr-defined]
            self.nmap_detail_placeholder.set_description( # type: ignore[attr-defined]
                (f"The Nmap scan for '{original_target}' did not find any responsive hosts.")
            )
            self.nmap_detail_placeholder.set_visible(True) # type: ignore[attr-defined]
            return

        results_yaml_map: dict[str, str] = self.scanner.convert_results_to_yaml(nm)
        if results_yaml_map:
            logger.debug("results_yaml_map keys: %s", list(results_yaml_map.keys()))
        else:
            logger.debug("results_yaml_map is empty.")
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        self._set_scan_status(
            ScanStatus.COMPLETE,
            f"Scan complete for {original_target}. {len(hosts_found)} host(s) found.",
        )

    def _handle_scan_error(self, target: str, error_message: str) -> None:
        """
        Handle errors reported from the Nmap scan task.

        :param target: The target string for which the scan failed.
        :type target: str
        :param error_message: The error message to display.
        :type error_message: str
        """
        show_global_error(self, f"Error scanning {target}: {error_message}") # type: ignore[arg-type]
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        """
        Handle selection of a host in the Nmap results :class:`Gtk.ListBox`.

        :param _listbox: The :class:`Gtk.ListBox` that emitted the signal.
        :type _listbox: Gtk.ListBox
        :param row: The selected :class:`Gtk.ListBoxRow`, or ``None`` if deselected.
        :type row: Optional[Gtk.ListBoxRow]
        """
        self._clear_dynamic_details()
        self._current_selected_host_key = None
        self._current_selected_host_data_dict = None

        if row is None:
            self.nmap_detail_placeholder.set_title("No Host Selected") # type: ignore[attr-defined]
            self.nmap_detail_placeholder.set_description("Select a host from the list to view details.") # type: ignore[attr-defined]
            if not self.nmap_detail_placeholder.get_parent(): # type: ignore[attr-defined]
                self.nmap_detail_box.append(self.nmap_detail_placeholder) # type: ignore[attr-defined]
            self.nmap_detail_placeholder.set_visible(True) # type: ignore[attr-defined]
            return
        self.nmap_detail_placeholder.set_visible(False) # type: ignore[attr-defined]
        item_obj = row.nmap_item if isinstance(row, NmapTargetRow) else None # type: ignore[attr-defined]
        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            try:
                host_data_dict: dict[str, Any] = yaml.safe_load(item_obj.value) # type: ignore[assignment]
                if not isinstance(host_data_dict, dict):
                    logger.warning(
                        "Parsed YAML for host %s is not a dictionary. Value: %s",
                        selected_target_key,
                        item_obj.value[:100],
                    )
                    host_data_dict = {}

                self._current_selected_host_key = selected_target_key
                self._current_selected_host_data_dict = host_data_dict

            except yaml.YAMLError as e:
                logger.error("Error parsing YAML for host %s: %s", selected_target_key, e)
                error_label = Gtk.Label(label=f"Error: Could not parse scan results for {selected_target_key}.\n{e}")
                error_label.set_wrap(True)
                error_label.set_halign(Gtk.Align.START)
                self.nmap_detail_box.append(error_label) # type: ignore[attr-defined]
                self._current_selected_host_key = None
                self._current_selected_host_data_dict = None
                return

            self._add_host_details_expander(host_data_dict, selected_target_key)
            self._add_ports_expander(host_data_dict, selected_target_key)
            self._add_os_expander(host_data_dict, selected_target_key)
            self._add_raw_output_expander(host_data_dict, selected_target_key)
        elif item_obj is None and row is not None:
            logger.warning("Selected row is not a valid NmapTargetRow or has no NmapItem.")
            self.nmap_detail_placeholder.set_title("Selection Error") # type: ignore[attr-defined]
            self.nmap_detail_placeholder.set_description("Could not process selected item.") # type: ignore[attr-defined]
            if not self.nmap_detail_placeholder.get_parent(): # type: ignore[attr-defined]
                self.nmap_detail_box.append(self.nmap_detail_placeholder) # type: ignore[attr-defined]
            self.nmap_detail_placeholder.set_visible(True) # type: ignore[attr-defined]
        else:
            logger.warning("Could not retrieve NmapItem from selected row or item_obj is None.")
            self.nmap_detail_placeholder.set_title("Error") # type: ignore[attr-defined]
            self.nmap_detail_placeholder.set_description("Could not load details for the selected host.") # type: ignore[attr-defined]
            if not self.nmap_detail_placeholder.get_parent(): # type: ignore[attr-defined]
                self.nmap_detail_box.append(self.nmap_detail_placeholder) # type: ignore[attr-defined]
            self.nmap_detail_placeholder.set_visible(True) # type: ignore[attr-defined]

    def _add_raw_output_expander(self, host_data_dict: Dict[str, Any], host_key: str):
        """
        Add an Adw.ExpanderRow to display the human-readable text summary for a host.

        :param host_data_dict: The dictionary containing data for the host.
        :param host_key: The identifier for the host (e.g., IP address).
        """
        expander = Adw.ExpanderRow(title=f"Text Scan Summary - {host_key}")
        expander.set_expanded(True)
        human_readable_summary = self._generate_human_readable_host_summary(host_data_dict)

        source_view = Gtk.TextView()
        source_view.set_name("nmap-raw-output-textview")
        source_buffer = Gtk.TextBuffer()
        source_view.set_buffer(source_buffer)

        source_view.set_monospace(True)
        source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        source_view.set_editable(False)

        source_view.set_hexpand(True)
        source_view.set_vexpand(True)

        source_buffer.set_text(human_readable_summary, -1)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_child(source_view)
        scrolled_window.set_min_content_height(300)
        scrolled_window.set_max_content_height(600)
        scrolled_window.set_vexpand(True)
        expander.add_row(scrolled_window)

        copy_summary_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_summary_button.set_tooltip_text("Copy Host Summary")
        copy_summary_button.get_style_context().add_class("flat")
        copy_summary_button.connect("clicked", lambda _btn, text=human_readable_summary: self._on_copy_host_summary_clicked(text))
        expander.add_suffix(copy_summary_button)

        self.nmap_detail_box.append(expander)

    def _on_copy_host_summary_clicked(self, summary_text: str):
        """
        Handle the click of the 'Copy Host Summary' button.

        :param summary_text: The summary text to copy to the clipboard.
        """
        if not summary_text:
            show_global_toast(self, "No summary text available to copy.") # type: ignore
            return

        try:
            clipboard = self.get_clipboard() # type: ignore
            if clipboard:
                clipboard.set(summary_text) # type: ignore
                show_global_toast(self, "Host summary copied to clipboard.") # type: ignore
                logger.info("Copied Nmap host summary to clipboard.")
            else:
                logger.warning("Could not get clipboard for NmapPage.")
                show_global_toast(self, "Failed to access clipboard.") # type: ignore
        except Exception as e: # pylint: disable=broad-except
            logger.exception("Error copying Nmap host summary to clipboard:")
            show_global_toast(self, f"Error copying: {e}") # type: ignore


    def _add_host_details_expander(self, host_data: Dict[str, Any], host_key: str):  # pylint: disable=too-many-locals # UI construction method with many data points
        """
        Add an Adw.ExpanderRow to display general host information.

        :param host_data: The dictionary containing data for the host.
        :param host_key: The identifier for the host (e.g., IP address).
        """
        expander = Adw.ExpanderRow(title=f"Host Information - {host_key}")
        expander.set_expanded(True)
        status_info = host_data.get("status", {})
        status_subtitle = f"{status_info.get('state', 'N/A')} (Reason: {status_info.get('reason', 'N/A')})"
        status_row = Adw.ActionRow(title="Status", subtitle=status_subtitle)
        expander.add_row(status_row)
        addresses_info = host_data.get("addresses", {})
        if addresses_info.get("ipv4"):
            expander.add_row(Adw.ActionRow(title="IPv4 Address", subtitle=addresses_info["ipv4"]))
        if addresses_info.get("ipv6"):
            expander.add_row(Adw.ActionRow(title="IPv6 Address", subtitle=addresses_info["ipv6"]))
        if addresses_info.get("mac"):
            expander.add_row(Adw.ActionRow(title="MAC Address", subtitle=addresses_info["mac"]))
        hostnames_list = host_data.get("hostnames", [])
        if hostnames_list:
            for hn_entry in hostnames_list:
                hn_title = f"Hostname ({hn_entry.get('type', 'N/A')})"
                hn_subtitle = hn_entry.get("name", "N/A")
                expander.add_row(Adw.ActionRow(title=hn_title, subtitle=hn_subtitle))
        else:
            expander.add_row(Adw.ActionRow(title="Hostnames", subtitle="No hostnames reported"))
        self.nmap_detail_box.append(expander)

    def _add_ports_expander(self, host_data: Dict[str, Any], host_key: str):  # pylint: disable=too-many-locals # UI construction method with many data points
        """
        Add an Adw.ExpanderRow to display detected network ports and their details.

        :param host_data: The dictionary containing data for the host.
        :param host_key: The identifier for the host (e.g., IP address).
        """
        expander = Adw.ExpanderRow(title=f"Network Ports - {host_key}")
        expander.set_expanded(True)
        ports_found = False
        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data.get(proto):
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        ports_found = True
                        state = port_info.get("state", "N/A")
                        name = port_info.get("name", "")
                        product = port_info.get("product", "")
                        version = port_info.get("version", "")
                        reason = port_info.get("reason", "")
                        title = f"Port {port_id}/{proto.upper()} ({state})"
                        subtitle_parts = [name, product, version]
                        subtitle = " ".join(filter(None, subtitle_parts))
                        subtitle = f"{subtitle} (Reason: {reason})" if subtitle else f"Reason: {reason}"
                        expander.add_row(Adw.ActionRow(title=title, subtitle=subtitle))
        if not ports_found:
            expander.add_row(Adw.ActionRow(title="Ports", subtitle="No open ports reported or port data available."))
        self.nmap_detail_box.append(expander)

    def _add_os_expander(self, host_data: Dict[str, Any], host_key: str):  # pylint: disable=too-many-locals # UI construction method with many data points
        """
        Add an Adw.ExpanderRow to display OS detection results.

        :param host_data: The dictionary containing data for the host.
        :param host_key: The identifier for the host (e.g., IP address).
        """
        osmatch_data = host_data.get("osmatch", [])
        if not osmatch_data:
            return
        expander = Adw.ExpanderRow(title=f"Operating System Detection - {host_key}")
        expander.set_expanded(True)
        os_details_added = False
        for match in osmatch_data:
            name = match.get("name", "N/A")
            accuracy = match.get("accuracy", "N/A")
            title = f"{name} (Accuracy: {accuracy}%)"
            osclass_details = []
            osclass_data = match.get("osclass", [])
            osclasses_to_process = (
                osclass_data
                if isinstance(osclass_data, list)
                else [osclass_data]
                if isinstance(osclass_data, dict)
                else []
            )
            for os_class in osclasses_to_process:
                if isinstance(os_class, dict):
                    vendor = os_class.get("vendor", "N/A")
                    osfamily = os_class.get("osfamily", "N/A")
                    osgen = os_class.get("osgen", "N/A")
                    osclass_details.append(
                        f"Type: {os_class.get('type', 'N/A')}, Vendor: {vendor}, Family: {osfamily}, Gen: {osgen}"
                    )
            subtitle = "\n".join(osclass_details) if osclass_details else "No OS class details."
            row = Adw.ActionRow(title=title, subtitle=subtitle if subtitle != "No OS class details." else "")
            expander.add_row(row)
            os_details_added = True
        if not os_details_added:
            expander.add_row(Adw.ActionRow(title="OS Detection", subtitle="No specific OS matches found."))
        self.nmap_detail_box.append(expander)

    def _update_results_view(self, hosts: List[str], results_map: Dict[str, str]):
        """
        Update the host ListBox with new scan results.

        :param hosts: A list of host identifiers (e.g., IP addresses).
        :param results_map: A dictionary mapping host identifiers to their YAML scan data.
        """
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()
        if not hosts:
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Found")
            self.nmap_detail_placeholder.set_description("The scan did not find any responsive hosts.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return
        for host_key in hosts:
            yaml_data = results_map.get(host_key, f"# No YAML data for {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data
        if self.nmap_target_listbox_store.get_n_items() > 0:
            self.nmap_host_listbox.select_row(self.nmap_host_listbox.get_row_at_index(0))
        else:
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Available")
            self.nmap_detail_placeholder.set_description("No host data to display.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)

    def _set_scan_status(self, status_type: ScanStatus, message: str):
        """
        Set the scan status and update the UI via GLib.idle_add.

        :param status_type: The ScanStatus enum member representing the current status.
        :param message: The message to display in the status row.
        """
        logger.info("Setting Nmap scan status: %s - %s", status_type.name, message)
        GLib.idle_add(self._update_status_ui, status_type, message)

    def _update_status_ui(self, status_type: ScanStatus, message: str):
        """
        Update the status row and spinner in the UI.

        :param status_type: The ScanStatus enum member.
        :param message: The message to display.
        """
        self.status_row.set_subtitle(message)
        status_css_classes = ["success-color", "warning-color", "error-color", "accent-color"]
        style_context = self.status_row.get_style_context()
        for css_class in status_css_classes:
            style_context.remove_class(css_class)
        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True)
            self.scan_spinner.start() # type: ignore
            self.status_row.set_title("Scanning...") # type: ignore
            style_context.add_class("accent-color")
            if self.nmap_apply_button:
                self.nmap_apply_button.set_sensitive(False) # type: ignore
            if hasattr(self, 'nmap_cancel_scan_button') and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(True)
                self.nmap_cancel_scan_button.set_sensitive(True)
        else:
            self.scan_spinner.stop() # type: ignore
            self.scan_spinner.set_visible(False) # type: ignore
            if self.nmap_apply_button:
                self.nmap_apply_button.set_sensitive(True) # type: ignore
            if hasattr(self, 'nmap_cancel_scan_button') and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(False)
                self.nmap_cancel_scan_button.set_sensitive(False)

            if status_type == ScanStatus.COMPLETE:
                self.status_row.set_title("Scan Complete") # type: ignore
                style_context.add_class("success-color")
            elif status_type == ScanStatus.FAILED:
                self.status_row.set_title("Scan Failed") # type: ignore
                style_context.add_class("error-color")
            elif status_type == ScanStatus.IDLE:
                self.status_row.set_title("Idle") # type: ignore
            else:
                self.status_row.set_title("Scan Status") # type: ignore

    def _clear_results(self):
        """Clear all Nmap scan results from the UI."""
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()
        self._clear_dynamic_details()
        self.nmap_detail_placeholder.set_title("No Host Selected")
        self.nmap_detail_placeholder.set_description(
            "Select a host from the list to view details, or start a new scan."
        )
        if not self.nmap_detail_placeholder.get_parent():
            self.nmap_detail_box.append(self.nmap_detail_placeholder)
        self.nmap_detail_placeholder.set_visible(True)
        self.nmap_target_entryrow.remove_css_class("error") # type: ignore
        self.nmap_target_entryrow.set_sensitive(True) # type: ignore
        if self.nmap_apply_button:
            self.nmap_apply_button.set_sensitive(True) # type: ignore
        if hasattr(self, 'nmap_cancel_scan_button') and self.nmap_cancel_scan_button:
            self.nmap_cancel_scan_button.set_sensitive(False)
            self.nmap_cancel_scan_button.set_visible(False)
        self._set_scan_status(ScanStatus.IDLE, "Idle")


    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        """Handle dismissal of the error banner by clearing the error state."""
        self._clear_error()

    def _clear_error(self):
        """Clear any displayed error message using the main window's banner."""
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error()
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        """
        Create an NmapTargetRow for the host ListBox.

        :param item: The NmapItem to create a row for.
        :return: A new NmapTargetRow.
        """
        return NmapTargetRow(nmap_item=item)

    def _generate_human_readable_host_summary(self, host_data_dict: Dict[str, Any]) -> str:
        """
        Generate a human-readable summary of host scan data.

        :param host_data_dict: A dictionary containing the scan data for a host.
        :return: A string containing the human-readable summary.
        """
        summary_lines = []
        status_info = host_data_dict.get("status", {})
        state = status_info.get("state", "N/A") # type: ignore[attr-defined]
        reason = status_info.get("reason", "N/A") # type: ignore[attr-defined]
        summary_lines.append(f"Host is {state} (reason: {reason}).") # type: ignore[attr-defined]
        addresses_info = host_data_dict.get("addresses", {}) # type: ignore[attr-defined]
        if ipv4 := addresses_info.get("ipv4"): # type: ignore[attr-defined]
            summary_lines.append(f"IPv4 Address: {ipv4}")
        if ipv6 := addresses_info.get("ipv6"): # type: ignore[attr-defined]
            summary_lines.append(f"IPv6 Address: {ipv6}")
        if mac := addresses_info.get("mac"): # type: ignore[attr-defined]
            summary_lines.append(f"MAC Address: {mac}")

        prescan_results = host_data_dict.get("prescript_results") # type: ignore[attr-defined]
        if prescan_results and isinstance(prescan_results, list):
            summary_lines.append("\nPre-scan script results:")
            for script_item in prescan_results:
                if isinstance(script_item, dict):
                    script_id, script_output = script_item.get("id", "N/A"), script_item.get("output", "N/A")
                    if script_output and isinstance(script_output, str):
                        formatted_output = "\n".join(
                            [
                                f"|_ {script_id}: {line.strip()}" if i == 0 else f"|  {line.strip()}" # type: ignore[str-bytes-safe]
                                for i, line in enumerate(script_output.strip().split("\n"))
                            ]
                        )
                        summary_lines.append(formatted_output)
                    else:
                        summary_lines.append(f"|_ {script_id}: (no output)") # type: ignore[str-bytes-safe]

        hostnames_list = host_data_dict.get("hostnames", []) # type: ignore[attr-defined]
        if hostnames_list:
            summary_lines.append("\nHostnames:")
            for hn_entry in hostnames_list:
                summary_lines.append(f"  {hn_entry.get('name', 'N/A')} ({hn_entry.get('type', 'N/A')})") # type: ignore[str-bytes-safe]

        host_scripts = host_data_dict.get("hostscript") # type: ignore[attr-defined]
        if host_scripts and isinstance(host_scripts, list):
            summary_lines.append("\nHost script output:")
            for script_item in host_scripts:
                if isinstance(script_item, dict):
                    script_id, script_output = script_item.get("id", "N/A"), script_item.get("output", "N/A")
                    formatted_output = "\n".join([f"    {line.strip()}" for line in script_output.strip().split("\n")]) # type: ignore[union-attr, str-bytes-safe]
                    summary_lines.append(f"  Script: {script_id}\n{formatted_output}") # type: ignore[str-bytes-safe]

        ports_data = []
        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data_dict.get(proto): # type: ignore[attr-defined]
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        p_state = port_info.get("state", "N/A")
                        if p_state not in ["closed", "filtered out"]: # type: ignore[comparison-overlap]
                            p_name, p_product, p_version, p_reason = (
                                port_info.get(k, "") for k in ["name", "product", "version", "reason"]
                            )
                            port_str = f"{port_id}/{proto.upper():<4} {p_state:<10} {p_name}" # type: ignore[str-bytes-safe]
                            if p_product:
                                port_str += f" {p_product}" # type: ignore[str-bytes-safe]
                            if p_version:
                                port_str += f" {p_version}" # type: ignore[str-bytes-safe]
                            if p_reason:
                                port_str += f" (reason: {p_reason})" # type: ignore[str-bytes-safe]
                            ports_data.append(port_str)
                        if port_scripts := port_info.get("script"):
                            if isinstance(port_scripts, dict):
                                for script_id, script_output in port_scripts.items():
                                    if script_output and isinstance(script_output, str):
                                        formatted_script_output = "\n".join(
                                            [
                                                f"  |_{script_id}: {line.strip()}" if i == 0 else f"  | {line.strip()}" # type: ignore[str-bytes-safe]
                                                for i, line in enumerate(script_output.strip().split("\n"))
                                            ]
                                        )
                                        ports_data.append(formatted_script_output)
        if ports_data:
            summary_lines.append("\nPORT      STATE SERVICE      VERSION")
            summary_lines.extend(ports_data)
        else:
            summary_lines.append("No open ports reported or port data available.")

        osmatch_data = host_data_dict.get("osmatch", []) # type: ignore[attr-defined]
        if osmatch_data:
            summary_lines.append("\nOS details:")
            for match in osmatch_data:
                summary_lines.append(f"  Name: {match.get('name', 'N/A')}") # type: ignore[str-bytes-safe]
                summary_lines.append(f"  Accuracy: {match.get('accuracy', 'N/A')}%") # type: ignore[str-bytes-safe]
                if "osclass" in match: # type: ignore[operator]
                    osclasses = match["osclass"] if isinstance(match["osclass"], list) else [match["osclass"]] # type: ignore[index]
                    for os_class in osclasses:
                        if isinstance(os_class, dict):
                            summary_lines.append("  OS Class:")
                            summary_lines.append( # type: ignore[str-bytes-safe]
                                f"    Type: {os_class.get('type', 'N/A')}, Vendor: {os_class.get('vendor', 'N/A')}, Family: {os_class.get('osfamily', 'N/A')}, Gen: {os_class.get('osgen', 'N/A')}"
                            )
        else:
            summary_lines.append("No OS data available.")
        return "\n".join(summary_lines)

    def trigger_scan(self) -> None:
        """Programmatically trigger the Nmap 'Scan' action."""
        if self.nmap_apply_button and self.nmap_apply_button.get_sensitive():
            self.nmap_apply_button.clicked()
        elif self.current_nmap_task and not self.current_nmap_task.is_done():
             show_global_toast(self, "A scan is already in progress. Cancel it or wait.") # type: ignore[arg-type]
        else:
            logger.warning("Nmap scan button not available or not sensitive, cannot trigger scan.")

NMAP_SCAN_ERROR_DOMAIN = "nmap-scan-error-domain"

class NmapScanErrorType(int, Enum):
    """Enumeration of Nmap scan error types for :class:`Gio.Task` error reporting."""

    SCAN_FAILED = 0
    UNEXPECTED = 1
    CANCELLED = 2
