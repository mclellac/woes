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
from typing import Optional, List, Dict, Any
import yaml

import gi
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

import nmap

from .constants import APP_ID, RESOURCE_PREFIX
from .nmap_scanner import NmapScanner, ScanStatus, ScanCancelledError
from .utils import show_global_error, show_global_toast, process_task_result # Import new utility
from .gtk_utils import (
    create_copy_button,
    create_detail_action_row,
    create_expander_row
)


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


class NmapItem(GObject.Object):
    """A simple :class:`GObject.Object` to hold key-value pairs for Nmap results display."""

    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


class NmapTargetRow(Gtk.ListBoxRow):
    """A :class:`Gtk.ListBoxRow` customized to display an NmapItem's key."""

    nmap_item = GObject.Property(type=NmapItem)

    def __init__(self, nmap_item: NmapItem, **kwargs: Any):
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
        super().__init__(**kwargs)
        logger.info("Initializing NmapPage...")
        self.results_by_host: dict[str, str] = {}
        self.nmap_target_listbox_store: Gio.ListStore = Gio.ListStore(item_type=NmapItem)
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

    def __del__(self): # Should be do_dispose, will be fixed in a later task if not already.
        if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel()
            logger.info("NmapPage finalized, ongoing scan cancelled.")
        if hasattr(self, "scanner") and self.scanner:
            self.scanner.shutdown(wait=False) # Use the new shutdown method
        # super().__del__() # GObject does not recommend overriding __del__

    def _init_page_ui(self):
        self.nmap_host_listbox.bind_model(
            self.nmap_target_listbox_store, self._create_target_listbox_row
        )
        child = self.nmap_detail_box.get_first_child()
        while child and child != self.nmap_detail_placeholder:
            self.nmap_detail_box.remove(child)
            child = self.nmap_detail_box.get_first_child()

    def _clear_dynamic_details(self):
        child = self.nmap_detail_box.get_first_child()
        while child:
            if child == self.nmap_detail_placeholder:
                child = child.get_next_sibling()
                continue
            current_child_to_remove = child
            child = child.get_next_sibling()
            self.nmap_detail_box.remove(current_child_to_remove)

    def _connect_signals(self):
        self.nmap_target_entryrow.connect("entry-activated", self._on_target_activate)
        self.nmap_apply_button.connect("clicked", self._on_target_activate)
        self.nmap_host_listbox.connect("row-selected", self._on_target_selected)
        if self.nmap_cancel_scan_button:
            self.nmap_cancel_scan_button.connect("clicked", self._on_cancel_scan_clicked)
        self.settings.connect(f"changed::{self._output_font_gsettings_key}", self._on_global_output_font_changed)

    def _on_global_output_font_changed(self, settings: Gio.Settings, key: str) -> None:
        logger.debug("NmapPage: Global output font setting changed for key: %s", key)
        if key == self._output_font_gsettings_key:
            output_font_str = settings.get_string(key)
            self._output_font_desc = Pango.FontDescription.from_string(
                output_font_str if output_font_str else "Monospace 10"
            )
            self._update_font_css()

    def _update_font_css(self) -> None:
        if not hasattr(self, "font_css_provider") or not self.font_css_provider:
            logger.warning("NmapPage: font_css_provider is not available to update CSS.")
            return
        if not hasattr(self, "_output_font_desc") or not self._output_font_desc:
            logger.warning("NmapPage: _output_font_desc is not available to update CSS.")
            return
        font_family = self._output_font_desc.get_family()
        size_in_pango_units = self._output_font_desc.get_size()
        size_in_points = (size_in_pango_units / Pango.SCALE) if size_in_pango_units > 0 else 10.0
        effective_font_family = font_family if font_family else "Monospace"
        css = f"textview#nmap-raw-output-textview {{ font-family: '{effective_font_family}'; font-size: {size_in_points:.1f}pt; }}"
        try:
            self.font_css_provider.load_from_string(css)
        except GLib.Error as e:
            logger.error(f"NmapPage: Error loading CSS string '{css}': {e}")

    def _on_cancel_scan_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Cancel scan button clicked.")
        if self.current_nmap_cancellable and not self.current_nmap_cancellable.is_cancelled():
            self.current_nmap_cancellable.cancel()
            logger.info("Scan cancellation requested.")
        else:
            logger.warning("No active scan or cancellable to cancel.")

    def _on_target_activate(self, _widget: Adw.EntryRow) -> None:
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
            if not self.current_nmap_task.is_done():
                logger.warning("Previous scan task still running. Please cancel it explicitly or wait.")
                return
        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Starting scan for {target}...")
        self.current_nmap_cancellable = Gio.Cancellable()
        scan_params: dict[str, Any] = {
            "target": target,
            "os_fingerprinting": self.nmap_fingerprint_switchrow.get_active(),
            "scan_all_ports": self.nmap_all_ports_switchrow.get_active(),
            "selected_script": (item.get_string() if isinstance(item := self.nmap_scripts_dropdown.get_selected_item(), Gtk.StringObject) and item.get_string() != "None" else None),
            "service_version": self.nmap_service_version_switchrow.get_active(),
            "no_ping": self.nmap_no_ping_switchrow.get_active(),
            "timing_template": (f"T{m.group(1)}" if (m := re.search(r"\(T([0-5])\)",self.nmap_timing_template_comborow.get_selected_item().get_string())) else "T3"),
            "custom_dns_server": self.settings.get_string("custom-dns-server"),
        }
        logger.info(f"NmapPage: Starting Nmap scan task with params: {scan_params}")
        self.current_nmap_task = Gio.Task.new(self, self.current_nmap_cancellable, self._nmap_scan_task_done_cb, None)
        self._current_nmap_scan_params = scan_params
        self.current_nmap_task.run_in_thread(self._run_nmap_scan_thread_func)

    def _run_nmap_scan_thread_func(self, task: Gio.Task, _source_object: GObject.Object, _task_data: Optional[dict[str, Any]], cancellable: Optional[Gio.Cancellable]) -> None:
        page_instance: NmapPage = _source_object
        params = page_instance._current_nmap_scan_params
        if not params:
            logger.error("NmapPage: _run_nmap_scan_thread_func: _current_nmap_scan_params is None.")
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.UNEXPECTED.value, "Internal error: Scan parameters not found.")
            return
        target = params["target"]
        if cancellable and cancellable.is_cancelled():
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value, "Scan cancelled before start.")
            return
        try:
            nm = self.scanner.run_nmap_scan(params, cancellable=cancellable)
            if cancellable and cancellable.is_cancelled():
                task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value, "Scan cancelled during operation.")
            else:
                task.return_value(nm)
        except ScanCancelledError as e:
            logger.info("Nmap scan for %s was cancelled: %s", target, e)
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.CANCELLED.value, str(e))
        except nmap.PortScannerError as e:
            logger.exception("Nmap PortScannerError for %s:", target)
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.SCAN_FAILED.value, f"Nmap scan error: {e}")
        except Exception as e:
            logger.exception("Unexpected exception in Nmap scan task for %s (%s):", target, type(e).__name__)
            error_msg_details = f"Scan failed unexpectedly: {e}"
            task.return_new_error_literal(GLib.quark_from_string(NMAP_SCAN_ERROR_DOMAIN), NmapScanErrorType.UNEXPECTED.value, error_msg_details)
        finally:
            logger.info("Nmap scan thread finished for %s.", target)

    def _nmap_scan_task_done_cb(self, _source_object: GObject.Object, result: Gio.AsyncResult, _user_data: Optional[Any]) -> None:
        original_target = (self._current_nmap_scan_params["target"] if self._current_nmap_scan_params else "unknown target")
        logger.info(f"Nmap scan task done for {original_target}.")

        task_being_processed = self.current_nmap_task # Keep a reference
        self.current_nmap_task = None # Clear early

        data, error_msg = process_task_result(task_being_processed, result, logger)

        if error_msg:
            # Specific NmapPage handling for "cancelled" if desired for UI
            if "cancel" in error_msg.lower(): # Basic check
                self._set_scan_status(ScanStatus.IDLE, f"Scan for {original_target} cancelled.")
                self._clear_results() # Clear results on cancel for NmapPage
            else:
                self._handle_scan_error(original_target, error_msg)
        elif data is not None:
            nm_results_final: Optional[nmap.PortScanner] = None
            if isinstance(data, nmap.PortScanner):
                nm_results_final = data
            elif hasattr(data, "value") and isinstance(data.value, nmap.PortScanner): # Handle GObject.Value wrapping
                nm_results_final = data.value
            else:
                logger.error(f"NmapPage: Unexpected data type from process_task_result: {type(data)}")
                self._handle_scan_error(original_target, "Scan returned an unexpected data type.")

            if nm_results_final:
                self._process_scan_results(nm_results_final, original_target)
        else: # No error, but data is None
            logger.error(f"NmapPage: Scan for {original_target} resulted in no data and no error_msg from process_task_result.")
            self._handle_scan_error(original_target, "Scan completed with no data and no error.")
        finally:
            self.current_nmap_task = None
            self.current_nmap_cancellable = None
            self._set_scan_status(ScanStatus.IDLE, "Idle")
            self.nmap_target_entryrow.set_sensitive(True)
            if self.nmap_apply_button: self.nmap_apply_button.set_sensitive(True)
            if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_sensitive(False)
                self.nmap_cancel_scan_button.set_visible(False)

    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str) -> None:
        hosts_found = nm.all_hosts()
        if not hosts_found:
            logger.warning("No hosts found in Nmap results for target %s.", original_target)
            self._set_scan_status(ScanStatus.COMPLETE, f"Scan complete for {original_target}. No hosts found or responsive.")
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Responsive Hosts")
            self.nmap_detail_placeholder.set_description((f"The Nmap scan for '{original_target}' did not find any responsive hosts."))
            self.nmap_detail_placeholder.set_visible(True)
            return
        results_yaml_map: dict[str, str] = self.scanner.convert_results_to_yaml(nm)
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        self._set_scan_status(ScanStatus.COMPLETE, f"Scan complete for {original_target}. {len(hosts_found)} host(s) found.")

    def _handle_scan_error(self, target: str, error_message: str) -> None:
        show_global_error(self, f"Error scanning {target}: {error_message}")
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        self._clear_dynamic_details()
        self._current_selected_host_key = None
        self._current_selected_host_data_dict = None
        if row is None:
            self.nmap_detail_placeholder.set_title("No Host Selected")
            self.nmap_detail_placeholder.set_description("Select a host from the list to view details.")
            if not self.nmap_detail_placeholder.get_parent(): self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return
        self.nmap_detail_placeholder.set_visible(False)
        item_obj = row.nmap_item if isinstance(row, NmapTargetRow) else None
        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            try:
                host_data_dict: dict[str, Any] = yaml.safe_load(item_obj.value)
                if not isinstance(host_data_dict, dict):
                    host_data_dict = {}
                self._current_selected_host_key = selected_target_key
                self._current_selected_host_data_dict = host_data_dict
            except yaml.YAMLError as e:
                logger.error("Error parsing YAML for host %s: %s", selected_target_key, e)
                error_label = Gtk.Label(label=f"Error: Could not parse scan results for {selected_target_key}.\n{e}")
                error_label.set_wrap(True); error_label.set_halign(Gtk.Align.START)
                self.nmap_detail_box.append(error_label)
                return
            self._add_host_details_expander(host_data_dict, selected_target_key)
            self._add_ports_expander(host_data_dict, selected_target_key)
            self._add_os_expander(host_data_dict, selected_target_key)
            self._add_raw_output_expander(host_data_dict, selected_target_key)
        # ... (rest of the error handling for selection)

    def _add_raw_output_expander(self, host_data_dict: Dict[str, Any], host_key: str):
        human_readable_summary = self._generate_human_readable_host_summary(host_data_dict)
        copy_summary_button = create_copy_button(
            text_to_copy=human_readable_summary,
            tooltip_text="Copy Host Summary",
            widget_for_clipboard=self
        )
        expander = create_expander_row(
            title=f"Text Scan Summary - {host_key}",
            header_suffixes=[copy_summary_button],
            initially_expanded=True
        )
        source_buffer = self.nmap_output_textview.get_buffer()
        if source_buffer: source_buffer.set_text(human_readable_summary, -1)
        else:
            new_buffer = Gtk.TextBuffer(); new_buffer.set_text(human_readable_summary, -1)
            self.nmap_output_textview.set_buffer(new_buffer)
        current_parent = self.nmap_output_scrolled_window.get_parent()
        if current_parent: # Detach if already parented
             if hasattr(current_parent, "remove"): current_parent.remove(self.nmap_output_scrolled_window)
             elif hasattr(current_parent, "set_child") and current_parent.get_child() == self.nmap_output_scrolled_window: current_parent.set_child(None)
        expander.add_row(self.nmap_output_scrolled_window)
        self.nmap_detail_box.append(expander)

    def _on_copy_host_summary_clicked(self, summary_text: str): # This is now directly connected by create_copy_button
        # The actual copy logic is handled by gtk_utils._copy_to_clipboard via create_copy_button
        # This method can be removed if no other logic is needed here.
        # For now, keeping it to show it's acknowledged, but it's effectively bypassed.
        if not summary_text: show_global_toast(self, "No summary text available to copy."); return
        show_global_toast(self, "Host summary copied to clipboard.") # Feedback, actual copy done by util

    def _add_host_details_expander(self, host_data: Dict[str, Any], host_key: str):
        expander = create_expander_row(title=f"Host Information - {host_key}", initially_expanded=True)
        status_info = host_data.get("status", {})
        status_value = f"{status_info.get('state', 'N/A')} (Reason: {status_info.get('reason', 'N/A')})"
        expander.add_row(create_detail_action_row(title="Status", value_text=status_value, copy_value_tooltip="Copy Status", parent_widget_for_clipboard=self))
        addresses_info = host_data.get("addresses", {})
        if ipv4 := addresses_info.get("ipv4"):
            expander.add_row(create_detail_action_row(title="IPv4 Address", value_text=ipv4, copy_value_tooltip="Copy IPv4", parent_widget_for_clipboard=self))
        if ipv6 := addresses_info.get("ipv6"):
            expander.add_row(create_detail_action_row(title="IPv6 Address", value_text=ipv6, copy_value_tooltip="Copy IPv6", parent_widget_for_clipboard=self))
        if mac := addresses_info.get("mac"):
            expander.add_row(create_detail_action_row(title="MAC Address", value_text=mac, copy_value_tooltip="Copy MAC", parent_widget_for_clipboard=self))
        hostnames_list = host_data.get("hostnames", [])
        if hostnames_list:
            for hn_entry in hostnames_list:
                hn_title = f"Hostname ({hn_entry.get('type', 'N/A')})"
                hn_value = hn_entry.get('name', 'N/A')
                expander.add_row(create_detail_action_row(title=hn_title, value_text=hn_value, copy_value_tooltip=f"Copy {hn_title}", parent_widget_for_clipboard=self))
        else:
            expander.add_row(create_detail_action_row(title="Hostnames", value_text="No hostnames reported", parent_widget_for_clipboard=self))
        self.nmap_detail_box.append(expander)

    def _add_ports_expander(self, host_data: Dict[str, Any], host_key: str):
        expander = create_expander_row(title=f"Network Ports - {host_key}", initially_expanded=True)
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
                        port_title = f"Port {port_id}/{proto.upper()} ({state})"
                        value_parts = [name, product, version]
                        port_value = " ".join(filter(None, value_parts))
                        port_value = f"{port_value} (Reason: {reason})" if port_value else f"Reason: {reason}"
                        expander.add_row(create_detail_action_row(title=port_title, value_text=port_value, copy_value_tooltip="Copy Port Details", parent_widget_for_clipboard=self))
        if not ports_found:
            expander.add_row(create_detail_action_row(title="Ports", value_text="No open ports reported or port data available.", parent_widget_for_clipboard=self))
        self.nmap_detail_box.append(expander)

    def _add_os_expander(self, host_data: Dict[str, Any], host_key: str):
        osmatch_data = host_data.get("osmatch", [])
        if not osmatch_data: return
        expander = create_expander_row(title=f"Operating System Detection - {host_key}", initially_expanded=True)
        os_details_added = False
        for match in osmatch_data:
            name = match.get("name", "N/A")
            accuracy = match.get("accuracy", "N/A")
            os_title = f"{name} (Accuracy: {accuracy}%)"
            osclass_details_parts = []
            osclass_data = match.get("osclass", [])
            osclasses_to_process = (osclass_data if isinstance(osclass_data, list) else [osclass_data] if isinstance(osclass_data, dict) else [])
            for os_class in osclasses_to_process:
                if isinstance(os_class, dict):
                    vendor = os_class.get("vendor", "N/A"); osfamily = os_class.get("osfamily", "N/A"); osgen = os_class.get("osgen", "N/A")
                    osclass_details_parts.append(f"Type: {os_class.get('type', 'N/A')}, Vendor: {vendor}, Family: {osfamily}, Gen: {osgen}")
            os_value = "\n".join(osclass_details_parts) if osclass_details_parts else "No OS class details."
            expander.add_row(create_detail_action_row(title=os_title, value_text=os_value if os_value != "No OS class details." else "", copy_value_tooltip="Copy OS Details", parent_widget_for_clipboard=self))
            os_details_added = True
        if not os_details_added:
            expander.add_row(create_detail_action_row(title="OS Detection", value_text="No specific OS matches found.", parent_widget_for_clipboard=self))
        self.nmap_detail_box.append(expander)

    def _update_results_view(self, hosts: List[str], results_map: Dict[str, str]):
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()
        if not hosts:
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Found")
            self.nmap_detail_placeholder.set_description("The scan did not find any responsive hosts.")
            if not self.nmap_detail_placeholder.get_parent(): self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return
        for host_key in hosts:
            yaml_data = results_map.get(host_key, f"# No YAML data for {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data
        if self.nmap_target_listbox_store.get_n_items() > 0:
            self.nmap_host_listbox.select_row(self.nmap_host_listbox.get_row_at_index(0))
        else: # Should not happen if hosts list is not empty
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Available")
            self.nmap_detail_placeholder.set_description("No host data to display.")
            if not self.nmap_detail_placeholder.get_parent(): self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)

    def _set_scan_status(self, status_type: ScanStatus, message: str):
        logger.info("Setting Nmap scan status: %s - %s", status_type.name, message)
        GLib.idle_add(self._update_status_ui, status_type, message)

    def _update_status_ui(self, status_type: ScanStatus, message: str):
        self.status_row.set_subtitle(message)
        status_css_classes = ["success-color", "warning-color", "error-color", "accent-color"]
        style_context = self.status_row.get_style_context()
        for css_class in status_css_classes: style_context.remove_class(css_class)
        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True); self.scan_spinner.start()
            self.status_row.set_title("Scanning...")
            style_context.add_class("accent-color")
            if self.nmap_apply_button: self.nmap_apply_button.set_sensitive(False)
            if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(True); self.nmap_cancel_scan_button.set_sensitive(True)
        else:
            self.scan_spinner.stop(); self.scan_spinner.set_visible(False)
            if self.nmap_apply_button: self.nmap_apply_button.set_sensitive(True)
            if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
                self.nmap_cancel_scan_button.set_visible(False); self.nmap_cancel_scan_button.set_sensitive(False)
            if status_type == ScanStatus.COMPLETE: self.status_row.set_title("Scan Complete"); style_context.add_class("success-color")
            elif status_type == ScanStatus.FAILED: self.status_row.set_title("Scan Failed"); style_context.add_class("error-color")
            elif status_type == ScanStatus.IDLE: self.status_row.set_title("Idle")
            else: self.status_row.set_title("Scan Status")

    def _clear_results(self):
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()
        self._clear_dynamic_details()
        self.nmap_detail_placeholder.set_title("No Host Selected")
        self.nmap_detail_placeholder.set_description("Select a host from the list to view details, or start a new scan.")
        if not self.nmap_detail_placeholder.get_parent(): self.nmap_detail_box.append(self.nmap_detail_placeholder)
        self.nmap_detail_placeholder.set_visible(True)
        self.nmap_target_entryrow.remove_css_class("error")
        self.nmap_target_entryrow.set_sensitive(True)
        if self.nmap_apply_button: self.nmap_apply_button.set_sensitive(True)
        if hasattr(self, "nmap_cancel_scan_button") and self.nmap_cancel_scan_button:
            self.nmap_cancel_scan_button.set_sensitive(False); self.nmap_cancel_scan_button.set_visible(False)
        self._set_scan_status(ScanStatus.IDLE, "Idle")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        self._clear_error()

    def _clear_error(self):
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"): main_window.hide_error()
        else: logger.warning("Could not find main window or hide_error method to clear error.")

    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        return NmapTargetRow(nmap_item=item)

    def _generate_human_readable_host_summary(self, host_data_dict: Dict[str, Any]) -> str:
        summary_lines = []
        status_info = host_data_dict.get("status", {}); state = status_info.get("state", "N/A"); reason = status_info.get("reason", "N/A")
        summary_lines.append(f"Host is {state} (reason: {reason}).")
        addresses_info = host_data_dict.get("addresses", {})
        if ipv4 := addresses_info.get("ipv4"): summary_lines.append(f"IPv4 Address: {ipv4}")
        if ipv6 := addresses_info.get("ipv6"): summary_lines.append(f"IPv6 Address: {ipv6}")
        if mac := addresses_info.get("mac"): summary_lines.append(f"MAC Address: {mac}")
        if prescan_results := host_data_dict.get("prescript_results"):
            if isinstance(prescan_results, list):
                summary_lines.append("\nPre-scan script results:")
                for item in prescan_results:
                    if isinstance(item, dict):
                        script_id, script_output = item.get("id", "N/A"), item.get("output", "N/A")
                        if script_output and isinstance(script_output, str):
                            summary_lines.append("\n".join([f"|_ {script_id}: {l.strip()}" if i == 0 else f"|  {l.strip()}" for i, l in enumerate(script_output.strip().split("\n"))]))
                        else: summary_lines.append(f"|_ {script_id}: (no output)")
        if hostnames_list := host_data_dict.get("hostnames", []):
            summary_lines.append("\nHostnames:")
            for hn in hostnames_list: summary_lines.append(f"  {hn.get('name', 'N/A')} ({hn.get('type', 'N/A')})")
        if host_scripts := host_data_dict.get("hostscript"):
            if isinstance(host_scripts, list):
                summary_lines.append("\nHost script output:")
                for item in host_scripts:
                    if isinstance(item, dict):
                        script_id, script_output = item.get("id", "N/A"), item.get("output", "N/A")
                        summary_lines.append(f"  Script: {script_id}\n" + "\n".join([f"    {l.strip()}" for l in script_output.strip().split("\n")]))
        ports_data = []
        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data_dict.get(proto):
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        p_state = port_info.get("state", "N/A")
                        if p_state not in ["closed", "filtered out"]:
                            p_name, p_prod, p_ver, p_reason = (port_info.get(k, "") for k in ["name", "product", "version", "reason"])
                            port_str = f"{port_id}/{proto.upper():<4} {p_state:<10} {p_name}"
                            if p_prod: port_str += f" {p_prod}"
                            if p_ver: port_str += f" {p_ver}"
                            if p_reason: port_str += f" (reason: {p_reason})"
                            ports_data.append(port_str)
                        if p_scripts := port_info.get("script"):
                            if isinstance(p_scripts, dict):
                                for sid, sout in p_scripts.items():
                                    if sout and isinstance(sout, str): ports_data.append("\n".join([f"  |_{sid}: {l.strip()}" if i == 0 else f"  | {l.strip()}" for i, l in enumerate(sout.strip().split("\n"))]))
        if ports_data: summary_lines.extend(["\nPORT      STATE SERVICE      VERSION"] + ports_data)
        else: summary_lines.append("No open ports reported or port data available.")
        if osmatch_data := host_data_dict.get("osmatch", []):
            summary_lines.append("\nOS details:")
            for match in osmatch_data:
                summary_lines.extend([f"  Name: {match.get('name', 'N/A')}", f"  Accuracy: {match.get('accuracy', 'N/A')}%"])
                if "osclass" in match:
                    ocs = (match["osclass"] if isinstance(match["osclass"], list) else [match["osclass"]])
                    for oc in ocs:
                        if isinstance(oc, dict): summary_lines.extend(["  OS Class:", f"    Type: {oc.get('type', 'N/A')}, Vendor: {oc.get('vendor', 'N/A')}, Family: {oc.get('osfamily', 'N/A')}, Gen: {oc.get('osgen', 'N/A')}"])
        else: summary_lines.append("No OS data available.")
        return "\n".join(summary_lines)

    def trigger_scan(self) -> None:
        if self.nmap_apply_button and self.nmap_apply_button.get_sensitive(): self.nmap_apply_button.activate()
        elif self.current_nmap_task and not self.current_nmap_task.is_done(): show_global_toast(self, "A scan is already in progress. Cancel it or wait.")
        else: logger.warning("Nmap scan button not available or not sensitive, cannot trigger scan.")

NMAP_SCAN_ERROR_DOMAIN = "nmap-scan-error-domain"

class NmapScanErrorType(int, Enum):
    SCAN_FAILED = 0
    UNEXPECTED = 1
    CANCELLED = 2
