"""
Defines the Nmap scanning page for the Woes application.

This page allows users to configure and run Nmap scans against specified targets.
Results are displayed in a structured way, with hosts listed and detailed
information (ports, OS, etc.) shown in expandable sections.
"""

import logging

logger = logging.getLogger(__name__)
import re
from enum import Enum
from typing import Optional, Any # list, dict used directly
import yaml

import gi
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

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
class NmapPage(Gtk.Box):
    """Activity page for performing Nmap scans and viewing results."""

    __gtype_name__ = "NmapPage"

    nmap_target_entryrow = Gtk.Template.Child("nmap_target_entryrow")
    nmap_apply_button = Gtk.Template.Child("nmap_apply_button")
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow")
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow")
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown")
    nmap_service_version_switchrow = Gtk.Template.Child("nmap_service_version_switchrow")
    nmap_no_ping_switchrow = Gtk.Template.Child("nmap_no_ping_switchrow")
    nmap_timing_template_comborow = Gtk.Template.Child("nmap_timing_template_comborow")
    status_row = Gtk.Template.Child("status_row")
    scan_spinner = Gtk.Template.Child("scan_spinner")
    nmap_cancel_scan_button = Gtk.Template.Child()

    nmap_host_listbox = Gtk.Template.Child("nmap_host_listbox")
    nmap_detail_box = Gtk.Template.Child("nmap_detail_box")
    nmap_detail_placeholder = Gtk.Template.Child("nmap_detail_placeholder")

    def __init__(self, **kwargs: Any):
        """
        Initialize the NmapPage.

        :param kwargs: Keyword arguments passed to the :class:`Gtk.Box` constructor.
        :type kwargs: Any
        :rtype: None
        """
        super().__init__(**kwargs)
        logger.info("Initializing NmapPage...")
        self.results_by_host: dict[str, str] = {}
        self.nmap_target_listbox_store: Gio.ListStore = Gio.ListStore.new(NmapItem) # type: ignore[no-any-return, var-annotated]
        self.scanner: NmapScanner = NmapScanner()
        self.settings: Gio.Settings = Gio.Settings.new(APP_ID)

        self._output_font_gsettings_key: str = "output-font"
        output_font_str: str = self.settings.get_string(self._output_font_gsettings_key)
        self._output_font_desc: Pango.FontDescription = Pango.FontDescription.from_string(
            output_font_str if output_font_str else "Monospace 10"
        )

        self.nmap_output_textview = Gtk.TextView()
        self.nmap_output_textview.set_name("nmap-raw-output-textview")
        self.nmap_output_textview.set_monospace(True)
        self.nmap_output_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.nmap_output_textview.set_editable(False)
        self.nmap_output_textview.set_hexpand(True)
        self.nmap_output_textview.set_vexpand(True)

        self.font_css_provider = Gtk.CssProvider()
        self.nmap_output_textview.get_style_context().add_provider(
            self.font_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        self._update_font_css()

        self.nmap_output_scrolled_window = Gtk.ScrolledWindow()
        self.nmap_output_scrolled_window.set_child(self.nmap_output_textview)
        self.nmap_output_scrolled_window.set_min_content_height(300)
        self.nmap_output_scrolled_window.set_max_content_height(600)
        self.nmap_output_scrolled_window.set_vexpand(True)

        self._current_selected_host_key: Optional[str] = None
        self._current_selected_host_data_dict: Optional[dict[str, Any]] = None

        if self.nmap_apply_button:
            self.nmap_apply_button.get_style_context().add_class("suggested-action")
        if self.nmap_cancel_scan_button:
            self.nmap_cancel_scan_button.get_style_context().add_class("destructive-action")

        self.current_nmap_task: Optional[Gio.Task] = None
        self.current_nmap_cancellable: Optional[Gio.Cancellable] = None
        self._current_nmap_scan_params: Optional[dict[str, Any]] = None

        self._init_page_ui()
        self._connect_signals()
        logger.info("NmapPage initialized.")

    def __del__(self):
        """
        Clean up resources, specifically the NmapScanner's thread pool and cancel any ongoing scan.
        :rtype: None
        """
        if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel()
            logger.info("NmapPage finalized, ongoing scan cancelled.")
        if hasattr(self, "scanner") and self.scanner:
            del self.scanner
        super().__del__()

    def _init_page_ui(self):
        """
        Initialize NmapPage UI components.
        :rtype: None
        """
        self.nmap_host_listbox.bind_model(
            self.nmap_target_listbox_store, self._create_target_listbox_row
        )
        child = self.nmap_detail_box.get_first_child()
        while child and child != self.nmap_detail_placeholder:
            self.nmap_detail_box.remove(child)
            child = self.nmap_detail_box.get_first_child()

    def _clear_dynamic_details(self):
        """
        Clear dynamically added details from the detail box.
        :rtype: None
        """
        child = self.nmap_detail_box.get_first_child()
        while child:
            if child == self.nmap_detail_placeholder:
                child = child.get_next_sibling()
                continue
            current_child_to_remove = child
            child = child.get_next_sibling()
            self.nmap_detail_box.remove(current_child_to_remove)

    def _connect_signals(self):
        """
        Connect NmapPage signals.
        :rtype: None
        """
        self.nmap_target_entryrow.connect("entry-activated", self._on_target_activate)
        self.nmap_apply_button.connect("clicked", self._on_target_activate)
        self.nmap_host_listbox.connect("row-selected", self._on_target_selected)
        if self.nmap_cancel_scan_button:
            self.nmap_cancel_scan_button.connect("clicked", self._on_cancel_scan_clicked)
        self.settings.connect(f"changed::{self._output_font_gsettings_key}", self._on_global_output_font_changed)

    def _on_global_output_font_changed(self, settings: Gio.Settings, key: str) -> None:
        """
        Handle changes to the global output font GSettings key.

        :param settings: The :class:`Gio.Settings` object that changed.
        :type settings: Gio.Settings
        :param key: The GSettings key that changed.
        :type key: str
        :rtype: None
        """
        logger.debug("NmapPage: Global output font setting changed for key: %s", key)
        if key == self._output_font_gsettings_key:
            output_font_str = settings.get_string(key)
            self._output_font_desc = Pango.FontDescription.from_string(
                output_font_str if output_font_str else "Monospace 10"
            )
            self._update_font_css()

    def _update_font_css(self) -> None:
        """
        Update the CSS provider with the current font settings.

        This method generates a CSS string to set the ``font-family`` and
        ``font-size`` for the ``textview#nmap-raw-output-textview`` widget.
        The font size is converted from Pango units (obtained from
        ``self._output_font_desc``) to points.
        :rtype: None
        """
        if not hasattr(self, "font_css_provider") or not self.font_css_provider:
            logger.warning("NmapPage: font_css_provider is not available to update CSS.")
            return
        if not hasattr(self, "_output_font_desc") or not self._output_font_desc:
            logger.warning("NmapPage: _output_font_desc is not available to update CSS.")
            return

        font_family = self._output_font_desc.get_family()
        size_in_pango_units = self._output_font_desc.get_size()

        size_in_points = 0.0
        if size_in_pango_units > 0 : # Pango.SCALE can be 0, avoid division by zero
            size_in_points = size_in_pango_units / Pango.SCALE
        else: # Default to a reasonable size if Pango size is 0 or invalid
            size_in_points = 10.0
            logger.warning(f"NmapPage: Pango font size was {size_in_pango_units}, defaulting to {size_in_points}pt.")

        effective_font_family = font_family if font_family else "Monospace"

        css = f"textview#nmap-raw-output-textview {{ font-family: '{effective_font_family}'; font-size: {size_in_points:.1f}pt; }}"
        try:
            self.font_css_provider.load_from_string(css)
        except GLib.Error as e: # Catch potential errors from load_from_string
            logger.error(f"NmapPage: Error loading CSS string '{css}': {e}")


    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle the 'Cancel Scan' button click.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        :rtype: None
        """
        logger.info("Cancel scan button clicked.")
        if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel()
            logger.info("Scan cancellation requested.")
        else:
            logger.warning("No active scan or cancellable to cancel.")

    def _on_target_activate(self, _widget: Adw.EntryRow) -> None:
        """
        Handle activation of the Nmap target entry row or click of the 'Scan' button.

        Validates target, sets up parameters, and starts the Nmap scan task.

        :param _widget: The :class:`Adw.EntryRow` that was activated.
        :type _widget: Adw.EntryRow
        :rtype: None
        """
        target = self.nmap_target_entryrow.get_text().strip()
        self._clear_error()

        if not self.scanner.validate_target_input(target):
            message = "Invalid target format. Please enter a valid IP, CIDR, or hostname."
            show_global_toast(self, message)
            return
        self.nmap_target_entryrow.remove_css_class("error")

        if not target:
            self._clear_results()
            return

        if self.current_nmap_task and not self.current_nmap_task.is_done():
            if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
                logger.info("Requesting cancellation of previous Nmap scan task.")
                self.current_nmap_cancellable.cancel()
            if not self.current_nmap_task.is_done():  # Re-check after cancel attempt
                logger.warning(
                    "Previous scan task still running. Please cancel it explicitly or wait."
                )
                # Optionally, prevent starting a new scan here if the old one couldn't be cancelled quickly
                # show_global_toast(self, "Previous scan is still finalizing. Please wait.")
                # return

        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Starting scan for {target}...")

        self.current_nmap_cancellable = Gio.Cancellable()

        scan_params: dict[str, Any] = {
            "target": target,
            "os_fingerprinting": self.nmap_fingerprint_switchrow.get_active(),
            "scan_all_ports": self.nmap_all_ports_switchrow.get_active(),
            "selected_script": (
                item.get_string()
                if isinstance(
                    item := self.nmap_scripts_dropdown.get_selected_item(), Gtk.StringObject
                )
                and item.get_string() != "None"
                else None
            ),
            "service_version": self.nmap_service_version_switchrow.get_active(),
            "no_ping": self.nmap_no_ping_switchrow.get_active(),
            "timing_template": (
                f"T{m.group(1)}"
                if (
                    m := re.search(
                        r"\(T([0-5])\)",
                        self.nmap_timing_template_comborow.get_selected_item().get_string(),
                    )
                )
                else "T3"
            ),
            "custom_dns_server": self.settings.get_string("custom-dns-server"),
        }
        logger.info(f"NmapPage: Starting Nmap scan task with params: {scan_params}")

        self.current_nmap_task = Gio.Task.new(
            self, self.current_nmap_cancellable, self._nmap_scan_task_done_cb, None
        )
        self._current_nmap_scan_params = scan_params
        self.current_nmap_task.run_in_thread(self._run_nmap_scan_thread_func)

    def _run_nmap_scan_thread_func( # type: ignore[type-arg]
        self,
        task: Gio.Task, # type: ignore[type-arg]
        _source_object: "NmapPage", # More specific type
        _task_data: Optional[dict[str, Any]],
        cancellable: Optional[Gio.Cancellable],
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
        :rtype: None
        """
        # _source_object is self, so we can directly use self.
        params = self._current_nmap_scan_params

        if not params:
            logger.error(
                "NmapPage: _run_nmap_scan_thread_func: _current_nmap_scan_params is None. This indicates a programming error."
            )
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.UNEXPECTED.value,
                "Internal error: Scan parameters not found.",
            )
            return

        target = params["target"]

        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.CANCELLED.value,
                "Scan cancelled before start.",
            )
            return

        try:
            nm = self.scanner.run_nmap_scan(params, cancellable=cancellable)

            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(
                    GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                    NmapScanErrorType.CANCELLED.value,
                    "Scan cancelled during operation.",
                )
            else:
                task.return_value(nm)
        except ScanCancelledError as e:
            logger.info("Nmap scan for %s was cancelled: %s", target, e)
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.CANCELLED.value,
                str(e),
            )
        except nmap.PortScannerError as e:
            logger.exception("Nmap PortScannerError for %s:", target)
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.SCAN_FAILED.value,
                f"Nmap scan error: {e}",
            )
        except Exception as e:
            logger.exception(
                "Unexpected exception in Nmap scan task for %s (%s):", target, type(e).__name__
            )
            task.return_new_error_literal(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN),
                NmapScanErrorType.UNEXPECTED.value,
                f"Scan failed unexpectedly: {e}",
            )
        finally:
            logger.info("Nmap scan thread finished for %s.", target)

    def _nmap_scan_task_done_cb( # type: ignore[type-arg]
        self, _source_object: "NmapPage", result: Gio.AsyncResult, _user_data: Optional[Any] # More specific type
    ) -> None:
        """
        Handle completion of the Nmap scan :class:`Gio.Task`.

        :param _source_object: The :class:`GObject.Object` source of the task.
        :type _source_object: GObject.Object
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :type result: Gio.AsyncResult
        :param _user_data: User data passed with the callback (unused).
        :type _user_data: Optional[Any]
        :rtype: None
        """
        original_target = (
            self._current_nmap_scan_params["target"]
            if self._current_nmap_scan_params
            else "unknown target"
        )

        logger.info(f"Nmap scan task done for {original_target}.")

        nm_results_final: Optional[nmap.PortScanner] = None
        try:
            propagated_value = result.propagate_value()

            if isinstance(propagated_value, nmap.PortScanner):
                nm_results_final = propagated_value
            elif hasattr(propagated_value, "value") and isinstance(
                propagated_value.value, nmap.PortScanner
            ):
                logger.debug(
                    f"NmapPage: Received wrapped object {type(propagated_value)} with .value attribute containing nmap.PortScanner. Unwrapping."
                )
                nm_results_final = propagated_value.value
            elif hasattr(propagated_value, "value"):
                logger.error(
                    f"NmapPage: Received wrapped object {type(propagated_value)} with .value of type {type(propagated_value.value)}. Expected nmap.PortScanner."
                )
                self._handle_scan_error(
                    original_target, "Scan returned unexpectedly wrapped data of the wrong type."
                )
            else:
                logger.error(
                    f"Nmap scan for {original_target} returned unexpected result type: {type(propagated_value)}"
                )
                self._handle_scan_error(original_target, "Scan returned an unexpected data type.")

            if nm_results_final:
                self._process_scan_results(nm_results_final, original_target)

        except GLib.Error as e:
            logger.warning(
                f"Nmap scan for {original_target} failed or was cancelled. Domain: {e.domain}, Code: {e.code}, Message: {e.message}"
            )
            if e.matches(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value
            ):
                self._set_scan_status(ScanStatus.IDLE, f"Scan for {original_target} cancelled.")
                self._clear_results()
            elif e.matches(
                GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.SCAN_FAILED.value
            ):
                self._handle_scan_error(original_target, e.message)
            else:  # UNEXPECTED or other GLib.Error
                self._handle_scan_error(original_target, f"Scan error: {e.message}")
        except Exception as e:
            logger.exception(
                f"NmapPage: Unexpected Python error in _nmap_scan_task_done_cb for {original_target}:"
            )
            self._handle_scan_error(
                original_target, f"Unexpected error processing scan results: {e}"
            )
        finally:
            self.current_nmap_task = None
            self.current_nmap_cancellable = None
            # Ensure status is appropriately set if not already handled by specific error/success paths
            current_status_subtitle = self.status_row.get_subtitle()
            current_status_title = self.status_row.get_title()
            is_still_scanning = current_status_title == "Scanning..." or "Starting scan for" in current_status_subtitle

            if is_still_scanning or not any(s_type.value[1].startswith(str(current_status_subtitle).split('.')[0]) for s_type in ScanStatus if s_type != ScanStatus.IN_PROGRESS):
                 self._set_scan_status(ScanStatus.IDLE, "Idle - operation ended.")

            self.nmap_target_entryrow.set_sensitive(True)
            if self.nmap_apply_button:
                self.nmap_apply_button.set_sensitive(True)
            if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_sensitive(False)
                self.nmap_cancel_scan_button.set_visible(False)

    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str) -> None:
        """
        Process the Nmap scan results received from the scanner task.

        :param nm: The :class:`nmap.PortScanner` object containing the scan results.
        :type nm: nmap.PortScanner
        :param original_target: The original target string for the scan.
        :type original_target: str
        :rtype: None
        """
        hosts_found = nm.all_hosts()
        if not hosts_found:
            logger.warning("No hosts found in Nmap results for target %s.", original_target)
            self._set_scan_status(
                ScanStatus.COMPLETE,
                f"Scan complete for {original_target}. No hosts found or responsive.",
            )
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Responsive Hosts")
            self.nmap_detail_placeholder.set_description(
                (f"The Nmap scan for '{original_target}' did not find any responsive hosts.")
            )
            self.nmap_detail_placeholder.set_visible(True)
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
        :rtype: None
        """
        show_global_error(self, f"Error scanning {target}: {error_message}")
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        """
        Handle selection of a host in the Nmap results :class:`Gtk.ListBox`.

        :param _listbox: The :class:`Gtk.ListBox` that emitted the signal.
        :type _listbox: Gtk.ListBox
        :param row: The selected :class:`Gtk.ListBoxRow`, or ``None`` if deselected.
        :type row: Optional[Gtk.ListBoxRow]
        :rtype: None
        """
        self._clear_dynamic_details()
        self._current_selected_host_key = None
        self._current_selected_host_data_dict = None

        if row is None:
            self.nmap_detail_placeholder.set_title("No Host Selected")
            self.nmap_detail_placeholder.set_description(
                "Select a host from the list to view details."
            )
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return
        self.nmap_detail_placeholder.set_visible(False)
        item_obj = row.nmap_item if isinstance(row, NmapTargetRow) else None
        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            try:
                host_data_dict: dict[str, Any] = yaml.safe_load(item_obj.value)
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
                error_label = Gtk.Label(
                    label=f"Error: Could not parse scan results for {selected_target_key}.\n{e}"
                )
                error_label.set_wrap(True)
                error_label.set_halign(Gtk.Align.START)
                self.nmap_detail_box.append(error_label)
                self._current_selected_host_key = None
                self._current_selected_host_data_dict = None
                return

            self._add_host_details_expander(host_data_dict, selected_target_key)
            self._add_ports_expander(host_data_dict, selected_target_key)
            self._add_os_expander(host_data_dict, selected_target_key)
            self._add_raw_output_expander(host_data_dict, selected_target_key)
        elif item_obj is None and row is not None:
            logger.warning("Selected row is not a valid NmapTargetRow or has no NmapItem.")
            self.nmap_detail_placeholder.set_title("Selection Error")
            self.nmap_detail_placeholder.set_description("Could not process selected item.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
        else:
            logger.warning("Could not retrieve NmapItem from selected row or item_obj is None.")
            self.nmap_detail_placeholder.set_title("Error")
            self.nmap_detail_placeholder.set_description(
                "Could not load details for the selected host."
            )
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)

    def _add_raw_output_expander(self, host_data_dict: dict[str, Any], host_key: str):
        """
        Add an Adw.ExpanderRow to display the human-readable text summary for a host.

        :param host_data_dict: The dictionary containing data for the host.
        :type host_data_dict: dict[str, Any]
        :param host_key: The identifier for the host (e.g., IP address).
        :type host_key: str
        :rtype: None
        """
        expander = Adw.ExpanderRow(title=f"Text Scan Summary - {host_key}")
        expander.set_expanded(True)
        human_readable_summary = self._generate_human_readable_host_summary(host_data_dict)

        # Ensure the textview's buffer is cleared and new text is set
        source_buffer = self.nmap_output_textview.get_buffer()
        if source_buffer:
            source_buffer.set_text(human_readable_summary, -1)
        else:
            # Fallback if buffer is somehow None, though Gtk.TextView usually ensures one
            new_buffer = Gtk.TextBuffer()
            new_buffer.set_text(human_readable_summary, -1)
            self.nmap_output_textview.set_buffer(new_buffer)

        # Remove self.nmap_output_scrolled_window from its parent if it has one,
        # before adding it to the expander row.
        current_parent = self.nmap_output_scrolled_window.get_parent()
        if current_parent:
            if isinstance(current_parent, Adw.ExpanderRow): # Check if parent is ExpanderRow
                 # Adw.ExpanderRow does not have a direct 'remove' method for its rows.
                 # Rows are added with add_row. If it's already in an expander,
                 # it might be okay if it's the *same* expander and row.
                 # However, to be safe, if it's a different expander or if we need to ensure
                 # it's freshly added, we might need to manage expanders differently.
                 # For now, assume we are adding to a new expander or re-adding is fine.
                 # If issues arise, the logic for expander re-use or creation needs adjustment.
                 pass # Potentially do nothing if parent is already the right expander
            elif hasattr(current_parent, "remove"): # Generic remove for Gtk.Container
                 current_parent.remove(self.nmap_output_scrolled_window)
            elif hasattr(current_parent, "set_child") and hasattr(current_parent, "get_child") and current_parent.get_child() == self.nmap_output_scrolled_window:
                 current_parent.set_child(None)
            else:
                 logger.warning("NmapPage: nmap_output_scrolled_window parent is of unhandled type or not the direct child.")


        expander.add_row(self.nmap_output_scrolled_window)

        copy_summary_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_summary_button.set_tooltip_text("Copy Host Summary")
        copy_summary_button.get_style_context().add_class("flat")
        copy_summary_button.connect(
            "clicked",
            lambda _btn, text=human_readable_summary: self._on_copy_host_summary_clicked(text),
        )
        expander.add_suffix(copy_summary_button)

        self.nmap_detail_box.append(expander)

    def _on_copy_host_summary_clicked(self, summary_text: str):
        """
        Handle the click of the 'Copy Host Summary' button.

        :param summary_text: The summary text to copy to the clipboard.
        :type summary_text: str
        :rtype: None
        """
        if not summary_text:
            show_global_toast(self, "No summary text available to copy.")
            return

        try:
            clipboard = self.get_clipboard()
            if clipboard:
                clipboard.set(summary_text)
                show_global_toast(self, "Host summary copied to clipboard.")
                logger.info("Copied Nmap host summary to clipboard.")
            else:
                logger.warning("Could not get clipboard for NmapPage.")
                show_global_toast(self, "Failed to access clipboard.")
        except Exception:
            logger.exception("Error copying Nmap host summary to clipboard.")
            show_global_toast(self, "Error copying summary.")

    def _add_host_details_expander(self, host_data: dict[str, Any], host_key: str):
        """
        Add an Adw.ExpanderRow to display general host information.

        :param host_data: The dictionary containing data for the host.
        :type host_data: dict[str, Any]
        :param host_key: The identifier for the host (e.g., IP address).
        :type host_key: str
        :rtype: None
        """
        expander = Adw.ExpanderRow(title=f"Host Information - {host_key}")
        expander.set_expanded(True)
        status_info = host_data.get("status", {})
        status_subtitle = (
            f"{status_info.get('state', 'N/A')} (Reason: {status_info.get('reason', 'N/A')})"
        )
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

    def _add_ports_expander(self, host_data: dict[str, Any], host_key: str):
        """
        Add an Adw.ExpanderRow to display detected network ports and their details.

        :param host_data: The dictionary containing data for the host.
        :type host_data: dict[str, Any]
        :param host_key: The identifier for the host (e.g., IP address).
        :type host_key: str
        :rtype: None
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
                        subtitle = (
                            f"{subtitle} (Reason: {reason})" if subtitle else f"Reason: {reason}"
                        )
                        expander.add_row(Adw.ActionRow(title=title, subtitle=subtitle))
        if not ports_found:
            expander.add_row(
                Adw.ActionRow(
                    title="Ports", subtitle="No open ports reported or port data available."
                )
            )
        self.nmap_detail_box.append(expander)

    def _add_os_expander(self, host_data: dict[str, Any], host_key: str):
        """
        Add an Adw.ExpanderRow to display OS detection results.

        :param host_data: The dictionary containing data for the host.
        :type host_data: dict[str, Any]
        :param host_key: The identifier for the host (e.g., IP address).
        :type host_key: str
        :rtype: None
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
            row = Adw.ActionRow(
                title=title, subtitle=subtitle if subtitle != "No OS class details." else ""
            )
            expander.add_row(row)
            os_details_added = True
        if not os_details_added:
            expander.add_row(
                Adw.ActionRow(title="OS Detection", subtitle="No specific OS matches found.")
            )
        self.nmap_detail_box.append(expander)

    def _update_results_view(self, hosts: list[str], results_map: dict[str, str]):
        """
        Update the host ListBox with new scan results.

        :param hosts: A list of host identifiers (e.g., IP addresses).
        :type hosts: list[str]
        :param results_map: A dictionary mapping host identifiers to their YAML scan data.
        :type results_map: dict[str, str]
        :rtype: None
        """
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()
        if not hosts:
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Found")
            self.nmap_detail_placeholder.set_description(
                "The scan did not find any responsive hosts."
            )
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
        :type status_type: ScanStatus
        :param message: The message to display in the status row.
        :type message: str
        :rtype: None
        """
        logger.info("Setting Nmap scan status: %s - %s", status_type.name, message)
        GLib.idle_add(self._update_status_ui, status_type, message)

    def _update_status_ui(self, status_type: ScanStatus, message: str):
        """
        Update the status row and spinner in the UI.

        :param status_type: The ScanStatus enum member.
        :type status_type: ScanStatus
        :param message: The message to display.
        :type message: str
        :rtype: None
        """
        self.status_row.set_subtitle(message)
        status_css_classes = ["success-color", "warning-color", "error-color", "accent-color"]
        style_context = self.status_row.get_style_context()
        for css_class in status_css_classes:
            style_context.remove_class(css_class)
        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True)
            self.scan_spinner.start()
            self.status_row.set_title("Scanning...")
            style_context.add_class("accent-color")
            if self.nmap_apply_button:
                self.nmap_apply_button.set_sensitive(False)
            if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(True)
                self.nmap_cancel_scan_button.set_sensitive(True)
        else:
            self.scan_spinner.stop()
            self.scan_spinner.set_visible(False)
            if self.nmap_apply_button:
                self.nmap_apply_button.set_sensitive(True)
            if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(False)
                self.nmap_cancel_scan_button.set_sensitive(False)

            if status_type == ScanStatus.COMPLETE:
                self.status_row.set_title("Scan Complete")
                style_context.add_class("success-color")
            elif status_type == ScanStatus.FAILED:
                self.status_row.set_title("Scan Failed")
                style_context.add_class("error-color")
            elif status_type == ScanStatus.IDLE:
                self.status_row.set_title("Idle")
            else:
                self.status_row.set_title("Scan Status")

    def _clear_results(self):
        """
        Clear all Nmap scan results from the UI.
        :rtype: None
        """
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
        self.nmap_target_entryrow.remove_css_class("error")
        self.nmap_target_entryrow.set_sensitive(True)
        if self.nmap_apply_button:
            self.nmap_apply_button.set_sensitive(True)
        if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
            self.nmap_cancel_scan_button.set_sensitive(False)
            self.nmap_cancel_scan_button.set_visible(False)
        self._set_scan_status(ScanStatus.IDLE, "Idle")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args) -> None:
        """
        Handle dismissal of the error banner by clearing the error state.

        :param _banner: The Adw.Banner that was dismissed.
        :type _banner: Adw.Banner
        :param _args: Additional arguments.
        :rtype: None
        """
        self._clear_error()

    def _clear_error(self):
        """
        Clear any displayed error message using the main window's banner.
        :rtype: None
        """
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error() # type: ignore[attr-defined] # WoesWindow method
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        """
        Create an NmapTargetRow for the host ListBox.

        :param item: The NmapItem to create a row for.
        :type item: NmapItem
        :return: A new NmapTargetRow.
        :rtype: Gtk.ListBoxRow
        """
        return NmapTargetRow(nmap_item=item)

    def _generate_human_readable_host_summary(self, host_data_dict: dict[str, Any]) -> str:
        """
        Generate a human-readable summary of host scan data.

        :param host_data_dict: A dictionary containing the scan data for a host.
        :type host_data_dict: dict[str, Any]
        :return: A string containing the human-readable summary.
        :rtype: str
        """
        summary_lines = []
        status_info = host_data_dict.get("status", {})
        state = status_info.get("state", "N/A")
        reason = status_info.get("reason", "N/A")
        summary_lines.append(f"Host is {state} (reason: {reason}).")
        addresses_info = host_data_dict.get("addresses", {})
        if ipv4 := addresses_info.get("ipv4"):
            summary_lines.append(f"IPv4 Address: {ipv4}")
        if ipv6 := addresses_info.get("ipv6"):
            summary_lines.append(f"IPv6 Address: {ipv6}")
        if mac := addresses_info.get("mac"):
            summary_lines.append(f"MAC Address: {mac}")

        prescan_results = host_data_dict.get("prescript_results")
        if prescan_results and isinstance(prescan_results, list):
            summary_lines.append("\nPre-scan script results:")
            for script_item in prescan_results:
                if isinstance(script_item, dict):
                    script_id, script_output = (
                        script_item.get("id", "N/A"),
                        script_item.get("output", "N/A"),
                    )
                    if script_output and isinstance(script_output, str):
                        formatted_output = "\n".join(
                            [
                                f"|_ {script_id}: {line.strip()}"
                                if i == 0
                                else f"|  {line.strip()}"
                                for i, line in enumerate(script_output.strip().split("\n"))
                            ]
                        )
                        summary_lines.append(formatted_output)
                    else:
                        summary_lines.append(f"|_ {script_id}: (no output)")

        hostnames_list = host_data_dict.get("hostnames", [])
        if hostnames_list:
            summary_lines.append("\nHostnames:")
            for hn_entry in hostnames_list:
                summary_lines.append(
                    f"  {hn_entry.get('name', 'N/A')} ({hn_entry.get('type', 'N/A')})"
                )

        host_scripts = host_data_dict.get("hostscript")
        if host_scripts and isinstance(host_scripts, list):
            summary_lines.append("\nHost script output:")
            for script_item in host_scripts:
                if isinstance(script_item, dict):
                    script_id, script_output = (
                        script_item.get("id", "N/A"),
                        script_item.get("output", "N/A"),
                    )
                    formatted_output = "\n".join(
                        [f"    {line.strip()}" for line in script_output.strip().split("\n")]
                    )
                    summary_lines.append(f"  Script: {script_id}\n{formatted_output}")

        ports_data = []
        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data_dict.get(proto):
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        p_state = port_info.get("state", "N/A")
                        if p_state not in ["closed", "filtered out"]:
                            p_name, p_product, p_version, p_reason = (
                                port_info.get(k, "")
                                for k in ["name", "product", "version", "reason"]
                            )
                            port_str = f"{port_id}/{proto.upper():<4} {p_state:<10} {p_name}"
                            if p_product:
                                port_str += f" {p_product}"
                            if p_version:
                                port_str += f" {p_version}"
                            if p_reason:
                                port_str += f" (reason: {p_reason})"
                            ports_data.append(port_str)
                        if port_scripts := port_info.get("script"):
                            if isinstance(port_scripts, dict):
                                for script_id, script_output in port_scripts.items():
                                    if script_output and isinstance(script_output, str):
                                        formatted_script_output = "\n".join(
                                            [
                                                f"  |_{script_id}: {line.strip()}"
                                                if i == 0
                                                else f"  | {line.strip()}"
                                                for i, line in enumerate(
                                                    script_output.strip().split("\n")
                                                )
                                            ]
                                        )
                                        ports_data.append(formatted_script_output)
        if ports_data:
            summary_lines.append("\nPORT      STATE SERVICE      VERSION")
            summary_lines.extend(ports_data)
        else:
            summary_lines.append("No open ports reported or port data available.")

        osmatch_data = host_data_dict.get("osmatch", [])
        if osmatch_data:
            summary_lines.append("\nOS details:")
            for match in osmatch_data:
                summary_lines.append(f"  Name: {match.get('name', 'N/A')}")
                summary_lines.append(f"  Accuracy: {match.get('accuracy', 'N/A')}%")
                if "osclass" in match:
                    osclasses = (
                        match["osclass"]
                        if isinstance(match["osclass"], list)
                        else [match["osclass"]]
                    )
                    for os_class in osclasses:
                        if isinstance(os_class, dict):
                            summary_lines.append("  OS Class:")
                            summary_lines.append(
                                f"    Type: {os_class.get('type', 'N/A')}, Vendor: {os_class.get('vendor', 'N/A')}, Family: {os_class.get('osfamily', 'N/A')}, Gen: {os_class.get('osgen', 'N/A')}"
                            )
        else:
            summary_lines.append("No OS data available.")
        return "\n".join(summary_lines)

    def trigger_scan(self) -> None:
        """
        Programmatically trigger the Nmap 'Scan' action.
        :rtype: None
        """
        if self.nmap_apply_button and self.nmap_apply_button.get_sensitive():
            self.nmap_apply_button.activate()
        elif self.current_nmap_task and not self.current_nmap_task.is_done():
            show_global_toast(self, "A scan is already in progress. Cancel it or wait.")
        else:
            logger.warning("Nmap scan button not available or not sensitive, cannot trigger scan.")


NMAP_SCAN_ERROR_DOMAIN = "nmap-scan-error-domain"


class NmapScanErrorType(int, Enum):
    """Enumeration of Nmap scan error types for :class:`Gio.Task` error reporting."""

    SCAN_FAILED = 0
    UNEXPECTED = 1
    CANCELLED = 2
