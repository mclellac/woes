"""Defines the Nmap scanning page for the Woes application.

This page allows users to configure and run Nmap scans against specified targets.
Results are displayed in a structured way, with hosts listed and detailed
information (ports, OS, etc.) shown in expandable sections.
"""
import logging
logger = logging.getLogger(__name__)
import re
from typing import Optional, List, Dict, Any

import gi
from gi.repository import Adw, Gio, GLib, GObject, Gtk, GtkSource

import nmap
import yaml

from .constants import APP_ID, RESOURCE_PREFIX
from .nmap_scanner import NmapScanner, ScanStatus
from .style_utils import apply_source_style_scheme
from .utils import create_source_view, show_global_error, show_global_toast


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")


class NmapItem(GObject.Object):
    """A simple GObject to hold key-value pairs for Nmap results display."""
    key = GObject.Property(type=str)
    value = GObject.Property(type=str)
    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value

class NmapTargetRow(Gtk.ListBoxRow):
    """A Gtk.ListBoxRow customized to display an NmapItem's key."""
    nmap_item = GObject.Property(type=NmapItem)
    def __init__(self, nmap_item: NmapItem, **kwargs):
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
    left_vbox_content = Gtk.Template.Child("left_vbox_content")
    nmap_host_listbox = Gtk.Template.Child("nmap_host_listbox")
    nmap_detail_box = Gtk.Template.Child("nmap_detail_box")
    nmap_detail_placeholder = Gtk.Template.Child("nmap_detail_placeholder")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.info("Initializing NmapPage...")
        self.results_by_host = {}
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)
        self.scanner = NmapScanner()
        self.settings = Gio.Settings.new(APP_ID)
        self._init_page_ui()
        self._connect_signals()
        if self.nmap_apply_button: self.nmap_apply_button.set_use_underline(True)
        logger.info("NmapPage initialized.")
        logger.debug("NmapPage __init__ completed.")

    def __del__(self):
        if hasattr(self, "scanner") and self.scanner: del self.scanner

    def _on_source_style_scheme_setting_changed(self, _settings: Gio.Settings, key: str):
        logger.debug("NmapPage: '%s' setting changed, applying new source view style.", key)

    def _apply_source_view_style_to_buffer(self, buffer: GtkSource.Buffer):
        source_style_scheme = self.settings.get_string("source-style-scheme")
        logger.debug("Applying style scheme to GtkSource.Buffer: %s", source_style_scheme)
        apply_source_style_scheme(GtkSource.StyleSchemeManager.get_default(), buffer, source_style_scheme)

    def _init_page_ui(self):
        logger.debug("Initializing NmapPage UI components.")
        self.nmap_host_listbox.bind_model(self.nmap_target_listbox_store, self._create_target_listbox_row)
        self.nmap_detail_placeholder.set_visible(True)
        child = self.nmap_detail_box.get_first_child()
        while child and child != self.nmap_detail_placeholder:
            self.nmap_detail_box.remove(child)
            child = self.nmap_detail_box.get_first_child()
        self.scan_spinner.set_spinning(False)
        self.scan_spinner.set_visible(False)
        self.status_row.set_subtitle("Idle")
        logger.debug("NmapPage _init_page_ui completed.")

    def _clear_dynamic_details(self):
        child = self.nmap_detail_box.get_first_child()
        while child:
            if child == self.nmap_detail_placeholder: child = child.get_next_sibling(); continue
            current_child_to_remove = child
            child = child.get_next_sibling()
            self.nmap_detail_box.remove(current_child_to_remove)

    def _connect_signals(self):
        logger.debug("Connecting NmapPage signals.")
        self.nmap_target_entryrow.connect("entry-activated", self._on_target_activate)
        self.nmap_apply_button.connect("clicked", self._on_target_activate)
        self.nmap_host_listbox.connect("row-selected", self._on_target_selected)

    def _on_target_activate(self, entry_row: Adw.EntryRow):
        logger.debug(f"_on_target_activate called by {entry_row}.")
        target = self.nmap_target_entryrow.get_text().strip()
        self._clear_error()
        if not self.scanner.validate_target_input(target):
            message = "Invalid target format. Please enter a valid IP, CIDR, or hostname."
            main_window = self.get_native()
            if main_window and hasattr(main_window, 'show_toast'): show_global_toast(self, message)
            else: show_global_error(self, message)
            return
        self.nmap_target_entryrow.remove_css_class("error")
        if not target: self._clear_results(); return
        self.nmap_target_entryrow.set_sensitive(False)
        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Scanning {target}...")
        os_fingerprinting = self.nmap_fingerprint_switchrow.get_active()
        all_ports = self.nmap_all_ports_switchrow.get_active()
        selected_script_item = self.nmap_scripts_dropdown.get_selected_item()
        script_name = selected_script_item.get_string() if isinstance(selected_script_item, Gtk.StringObject) and selected_script_item.get_string() != "None" else None
        service_version_detection = self.nmap_service_version_switchrow.get_active()
        no_ping_scan = self.nmap_no_ping_switchrow.get_active()
        selected_timing_item = self.nmap_timing_template_comborow.get_selected_item()
        timing_template_str = selected_timing_item.get_string()
        timing_match = re.search(r"\(T([0-5])\)", timing_template_str)
        timing_template = f"T{timing_match.group(1)}" if timing_match else "T3"
        custom_dns_server = self.settings.get_string("custom-dns-server")
        logger.debug("Nmap scan parameters collected: target=%s, os_fingerprint=%s, all_ports=%s, script_name=%s, service_version_detection=%s, no_ping_scan=%s, timing_template=%s, custom_dns_server=%s", target, os_fingerprinting, all_ports, script_name, service_version_detection, no_ping_scan, timing_template, custom_dns_server)
        logger.info("Nmap scan for target: %s (OSScan:%s, AllPorts:%s, Script:%s, Ver:%s, NoPing:%s, Time:%s, CustomDNS:%s)", target, os_fingerprinting, all_ports, script_name, service_version_detection, no_ping_scan, timing_template, custom_dns_server if custom_dns_server else "None")
        self.scanner.executor.submit(self._run_nmap_scan_task, target, os_fingerprinting, all_ports, script_name, service_version_detection, no_ping_scan, timing_template, custom_dns_server)

    def _run_nmap_scan_task(self, target: str, os_fingerprinting: bool, all_ports: bool, script_name: Optional[str], service_version_detection: bool, no_ping_scan: bool, timing_template: str, custom_dns_server: Optional[str]):
        logger.debug("_run_nmap_scan_task started for target: %s with options - OSScan:%s, AllPorts:%s, Script:%s, Ver:%s, NoPing:%s, Time:%s, CustomDNS:%s", target, os_fingerprinting, all_ports, script_name, service_version_detection, no_ping_scan, timing_template, custom_dns_server)
        try:
            nm = self.scanner.run_nmap_scan(target, os_fingerprinting, all_ports, script_name, service_version_detection, no_ping_scan, timing_template, custom_dns_server=custom_dns_server)
            GLib.idle_add(self._process_scan_results, nm, target)
        except nmap.PortScannerError as e:
            logger.exception("Nmap PortScannerError for %s:", target)
            GLib.idle_add(self._handle_scan_error, target, f"Nmap scan error: {e}")
        except Exception as e:
            logger.exception("Unexpected exception in Nmap scan task for %s (%s):", target, type(e).__name__)
            GLib.idle_add(self._handle_scan_error, target, f"Scan failed unexpectedly: {e}")
        finally:
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)
            logger.info("Nmap scan task finished for %s.", target)

    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str):
        logger.debug("Processing Nmap scan results for target: %s", original_target)
        hosts_found = nm.all_hosts()
        logger.debug("Hosts found by Nmap: %s", hosts_found)
        if not hosts_found:
            logger.warning("No hosts found in Nmap results for target %s.", original_target)
            self._set_scan_status(ScanStatus.COMPLETE, f"Scan complete for {original_target}. No hosts found or responsive.")
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Responsive Hosts")
            self.nmap_detail_placeholder.set_description(f"The Nmap scan for '{original_target}' did not find any responsive hosts.")
            self.nmap_detail_placeholder.set_visible(True)
            return
        results_yaml_map = self.scanner.convert_results_to_yaml(nm)
        if results_yaml_map: logger.debug("results_yaml_map keys: %s", list(results_yaml_map.keys()))
        else: logger.debug("results_yaml_map is empty.")
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        self._set_scan_status(ScanStatus.COMPLETE, f"Scan complete for {original_target}. {len(hosts_found)} host(s) found.")

    def _handle_scan_error(self, target: str, error_message: str):
        logger.debug("Handling Nmap scan error for target %s: %s", target, error_message)
        show_global_error(self, f"Error scanning {target}: {error_message}")
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None):
        self._clear_dynamic_details()
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
            logger.debug("Target selected: %s", selected_target_key)
            try:
                host_data_dict = yaml.safe_load(item_obj.value)
                if not isinstance(host_data_dict, dict):
                    logger.warning("Parsed YAML for host %s is not a dictionary. Value: %s", selected_target_key, item_obj.value[:100])
                    host_data_dict = {}
                logger.debug("Successfully parsed YAML for %s. Data keys: %s", selected_target_key, list(host_data_dict.keys()) if host_data_dict else "None")
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
        else:
            logger.warning("Could not retrieve NmapItem from selected row or item_obj is None.")
            self.nmap_detail_placeholder.set_title("Error")
            self.nmap_detail_placeholder.set_description("Could not load details for the selected host.")
            if not self.nmap_detail_placeholder.get_parent(): self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)

    def _add_raw_output_expander(self, host_data_dict: dict, host_key: str):
        logger.debug("Adding text scan summary expander for %s", host_key)
        expander = Adw.ExpanderRow(title=f"Text Scan Summary - {host_key}", expanded=False)
        human_readable_summary = self._generate_human_readable_host_summary(host_data_dict)
        source_view, source_buffer = create_source_view(language_name="txt")
        source_buffer.set_text(human_readable_summary, -1)
        self._apply_source_view_style_to_buffer(source_buffer)
        source_view.set_editable(False)
        scrolled_window = Gtk.ScrolledWindow(child=source_view, min_content_height=200, max_content_height=400, vexpand=True)
        expander.add_row(scrolled_window)
        self.nmap_detail_box.append(expander)

    def _add_host_details_expander(self, host_data: dict, host_key: str):
        logger.debug("Adding host details expander for %s", host_key)
        expander = Adw.ExpanderRow(title=f"Host Information - {host_key}", expanded=True)
        status_info = host_data.get("status", {})
        status_subtitle = f"{status_info.get('state', 'N/A')} (Reason: {status_info.get('reason', 'N/A')})"
        expander.add_row(Adw.ActionRow(title="Status", subtitle=status_subtitle))
        addresses_info = host_data.get("addresses", {})
        if addresses_info.get("ipv4"): expander.add_row(Adw.ActionRow(title="IPv4 Address", subtitle=addresses_info["ipv4"]))
        if addresses_info.get("ipv6"): expander.add_row(Adw.ActionRow(title="IPv6 Address", subtitle=addresses_info["ipv6"]))
        if addresses_info.get("mac"): expander.add_row(Adw.ActionRow(title="MAC Address", subtitle=addresses_info["mac"]))
        hostnames_list = host_data.get("hostnames", [])
        if hostnames_list:
            for hn_entry in hostnames_list: expander.add_row(Adw.ActionRow(title=f"Hostname ({hn_entry.get('type', 'N/A')})", subtitle=hn_entry.get("name", "N/A")))
        else: expander.add_row(Adw.ActionRow(title="Hostnames", subtitle="No hostnames reported"))
        self.nmap_detail_box.append(expander)

    def _add_ports_expander(self, host_data: dict, host_key: str):
        logger.debug("Adding ports expander for %s", host_key)
        expander = Adw.ExpanderRow(title=f"Network Ports - {host_key}", expanded=True)
        ports_found = False
        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data.get(proto):
                logger.debug(f"Processing ports for proto {proto} in host {host_key}, data: {list(proto_data.keys()) if isinstance(proto_data, dict) else 'Not a dict'}")
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        ports_found = True
                        state, name, product, version, reason = (port_info.get(k, "") for k in ["state", "name", "product", "version", "reason"])
                        title = f"Port {port_id}/{proto.upper()} ({state})"
                        subtitle = " ".join(filter(None, [name, product, version]))
                        subtitle = f"{subtitle} (Reason: {reason})" if subtitle else f"Reason: {reason}"
                        expander.add_row(Adw.ActionRow(title=title, subtitle=subtitle))
        if not ports_found: expander.add_row(Adw.ActionRow(title="Ports", subtitle="No open ports reported or port data available."))
        self.nmap_detail_box.append(expander)

    def _add_os_expander(self, host_data: dict, host_key: str):
        osmatch_data = host_data.get("osmatch", [])
        if not osmatch_data: logger.debug("No OS data for host %s, skipping OS expander.", host_key); return
        logger.debug("Adding OS expander for %s. OS match data: %s", host_key, osmatch_data)
        expander = Adw.ExpanderRow(title=f"Operating System Detection - {host_key}", expanded=True)
        os_details_added = False
        for match in osmatch_data:
            name, accuracy = match.get("name", "N/A"), match.get("accuracy", "N/A")
            title = f"{name} (Accuracy: {accuracy}%)"
            osclass_details = []
            osclass_data = match.get("osclass", [])
            osclasses_to_process = osclass_data if isinstance(osclass_data, list) else [osclass_data] if isinstance(osclass_data, dict) else []
            for os_class in osclasses_to_process:
                if isinstance(os_class, dict):
                    vendor, osfamily, osgen = (os_class.get(k, "N/A") for k in ["vendor", "osfamily", "osgen"])
                    osclass_details.append(f"Type: {os_class.get('type', 'N/A')}, Vendor: {vendor}, Family: {osfamily}, Gen: {osgen}")
            subtitle = "\n".join(osclass_details) if osclass_details else "No OS class details."
            expander.add_row(Adw.ActionRow(title=title, subtitle=subtitle if subtitle != "No OS class details." else ""))
            os_details_added = True
        if not os_details_added: expander.add_row(Adw.ActionRow(title="OS Detection", subtitle="No specific OS matches found."))
        self.nmap_detail_box.append(expander)

    def _update_results_view(self, hosts: list, results_map: dict):
        logger.info("Updating Nmap results view for hosts: %s", hosts)
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
            self.nmap_target_listbox_store.append(NmapItem(key=host_key, value=yaml_data))
            self.results_by_host[host_key] = yaml_data # This might be redundant if NmapItem holds all needed data
        if self.nmap_target_listbox_store.get_n_items() > 0:
            self.nmap_host_listbox.select_row(self.nmap_host_listbox.get_row_at_index(0))
        else: # Should not be reached if hosts list is not empty, but defensive
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
            self.status_row.set_title("Scanning..."); style_context.add_class("error-color")
        else:
            self.scan_spinner.stop(); self.scan_spinner.set_visible(False)
            if status_type == ScanStatus.COMPLETE: self.status_row.set_title("Scan Complete"); style_context.add_class("success-color")
            elif status_type == ScanStatus.FAILED: self.status_row.set_title("Scan Failed"); style_context.add_class("error-color")
            elif status_type == ScanStatus.IDLE: self.status_row.set_title("Idle")
            else: self.status_row.set_title("Scan Status")

    def _clear_results(self):
        logger.info("Clearing Nmap results and detail view.")
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()
        self._clear_dynamic_details()
        self.nmap_detail_placeholder.set_title("No Host Selected")
        self.nmap_detail_placeholder.set_description("Select a host from the list to view details, or start a new scan.")
        if not self.nmap_detail_placeholder.get_parent(): self.nmap_detail_box.append(self.nmap_detail_placeholder)
        self.nmap_detail_placeholder.set_visible(True)
        self.nmap_target_entryrow.remove_css_class("error")
        self.nmap_target_entryrow.set_sensitive(True)
        self._set_scan_status(ScanStatus.IDLE, "Idle")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        self._clear_error()

    def _clear_error(self):
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'hide_error'): main_window.hide_error()
        else: logger.warning("Could not find main window or hide_error method to clear error.")

    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        return NmapTargetRow(nmap_item=item)

    def _format_status_summary(self, host_data_dict: Dict[str, Any]) -> List[str]:
        status_info = host_data_dict.get("status", {})
        state = status_info.get("state", "N/A")
        reason = status_info.get("reason", "N/A")
        return [f"Host is {state} (reason: {reason})."]

    def _format_addresses_summary(self, host_data_dict: Dict[str, Any]) -> List[str]:
        lines = []
        addresses_info = host_data_dict.get("addresses", {})
        if ipv4 := addresses_info.get("ipv4"): lines.append(f"IPv4 Address: {ipv4}")
        if ipv6 := addresses_info.get("ipv6"): lines.append(f"IPv6 Address: {ipv6}")
        if mac := addresses_info.get("mac"): lines.append(f"MAC Address: {mac}")
        return lines

    def _format_scripts_summary(self, script_results: Optional[List[Dict[str, str]]], script_section_title: str) -> List[str]:
        lines = []
        if script_results and isinstance(script_results, list):
            lines.append("")  # Add a blank line for spacing if there are results
            lines.append(script_section_title)
            for script_item in script_results:
                if isinstance(script_item, dict):
                    script_id = script_item.get("id", "N/A")
                    script_output = script_item.get("output", "N/A")
                    if script_output and isinstance(script_output, str):
                        # Specific formatting based on original logic for prescript vs hostscript
                        if "Pre-scan" in script_section_title:
                             formatted_output = "\n".join([f"|_ {script_id}: {line.strip()}" if i == 0 else f"|  {line.strip()}" for i, line in enumerate(script_output.strip().split('\n'))])
                        else: # Host script output
                             formatted_output = "\n".join([f"    {line.strip()}" for line in script_output.strip().split('\n')])
                             formatted_output = f"  Script: {script_id}\n{formatted_output}" # Add Script ID line
                        lines.append(formatted_output)
                    else:
                        if "Pre-scan" in script_section_title:
                            lines.append(f"|_ {script_id}: (no output)")
                        else:
                            lines.append(f"  Script: {script_id} (no output)")
        return lines

    def _format_hostnames_summary(self, host_data_dict: Dict[str, Any]) -> List[str]:
        lines = []
        hostnames_list = host_data_dict.get("hostnames", [])
        if hostnames_list:
            lines.append("") # Add a blank line for spacing
            lines.append("Hostnames:")
            for hn_entry in hostnames_list:
                lines.append(f"  {hn_entry.get('name', 'N/A')} ({hn_entry.get('type', 'N/A')})")
        return lines

    def _format_ports_summary(self, host_data_dict: Dict[str, Any]) -> List[str]:
        lines = []
        ports_data_found = False
        temp_ports_lines = []

        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data_dict.get(proto):
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        p_state = port_info.get("state", "N/A")
                        if p_state not in ["closed", "filtered out"]:
                            ports_data_found = True
                            p_name, p_product, p_version, p_reason = (port_info.get(k, "") for k in ["name", "product", "version", "reason"])
                            port_str = f"{port_id}/{proto.upper():<4} {p_state:<10} {p_name}"
                            if p_product: port_str += f" {p_product}"
                            if p_version: port_str += f" {p_version}"
                            if p_reason: port_str += f" (reason: {p_reason})"
                            temp_ports_lines.append(port_str)

                        if port_scripts := port_info.get("script"):
                            ports_data_found = True # Script output implies port is of interest
                            if isinstance(port_scripts, dict):
                                for script_id, script_output in port_scripts.items():
                                    if script_output and isinstance(script_output, str):
                                        formatted_script_output = "\n".join([f"  |_{script_id}: {line.strip()}" if i == 0 else f"  | {line.strip()}" for i, line in enumerate(script_output.strip().split('\n'))])
                                        temp_ports_lines.append(formatted_script_output)
        if ports_data_found:
            lines.append("") # Add a blank line for spacing
            lines.append("PORT      STATE SERVICE      VERSION")
            lines.extend(temp_ports_lines)
        else:
            lines.append("")
            lines.append("No open or noteworthy ports reported.") # Changed from "No open ports reported or port data available."
        return lines

    def _format_os_summary(self, host_data_dict: Dict[str, Any]) -> List[str]:
        lines = []
        osmatch_data = host_data_dict.get("osmatch", [])
        if osmatch_data:
            lines.append("") # Add a blank line for spacing
            lines.append("OS details:")
            for match in osmatch_data:
                lines.append(f"  Name: {match.get('name', 'N/A')}")
                lines.append(f"  Accuracy: {match.get('accuracy', 'N/A')}%")
                if "osclass" in match:
                    osclasses = match["osclass"] if isinstance(match["osclass"], list) else [match["osclass"]]
                    for os_class in osclasses:
                        if isinstance(os_class, dict):
                            lines.append("  OS Class:")
                            lines.append(f"    Type: {os_class.get('type', 'N/A')}, Vendor: {os_class.get('vendor', 'N/A')}, Family: {os_class.get('osfamily', 'N/A')}, Gen: {os_class.get('osgen', 'N/A')}")
        else:
            lines.append("")
            lines.append("No OS data available.")
        return lines

    def _generate_human_readable_host_summary(self, host_data_dict: dict) -> str:
        """Generate a human-readable summary of host scan data by calling helper methods."""
        summary_lines: List[str] = []

        # Host Status
        summary_lines.extend(self._format_status_summary(host_data_dict))

        # Addresses
        summary_lines.extend(self._format_addresses_summary(host_data_dict))

        # Pre-scan script results
        prescan_results = host_data_dict.get("prescript_results", [])
        # _format_scripts_summary handles empty list by returning [], so section title won't be added.
        summary_lines.extend(self._format_scripts_summary(prescan_results, "Pre-scan script results"))

        # Hostnames
        # _format_hostnames_summary handles its own header and empty case.
        summary_lines.extend(self._format_hostnames_summary(host_data_dict))

        # Host script output
        host_scripts = host_data_dict.get("hostscript", [])
        # _format_scripts_summary handles empty list.
        summary_lines.extend(self._format_scripts_summary(host_scripts, "Host script output"))

        # Ports
        summary_lines.extend(self._format_ports_summary(host_data_dict))

        # OS details
        summary_lines.extend(self._format_os_summary(host_data_dict))

        # Clean up potential multiple blank lines that might arise from sections adding their own padding
        # and then some sections being empty.
        # A simple approach: filter out completely empty strings AFTER all sections are added.
        # However, if helpers are designed to return a list that includes their own spacing (e.g. a leading ""),
        # this might remove intended spacing.
        # For now, let's join directly. If formatting issues arise (e.g. too many blank lines),
        # this part can be refined by either adjusting helpers or filtering summary_lines.
        # Example of filtering:
        # final_summary = "\n".join([line for line in summary_lines if line.strip() or line == ""]).strip()
        # return final_summary
        # For now, simpler join:
        return "\n".join(summary_lines).strip() # Add strip() to remove leading/trailing newlines from the whole block

    def trigger_scan(self):
        """Programmatically triggers the Nmap 'Scan' action."""
        logger.debug("Nmap scan triggered by shortcut.")
        if self.nmap_apply_button and self.nmap_apply_button.get_sensitive():
            self.nmap_apply_button.clicked()
        else:
            logger.warning("Nmap scan button not available or not sensitive, cannot trigger scan.")
