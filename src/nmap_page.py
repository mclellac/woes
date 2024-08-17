import re
import nmap
import yaml
import logging
from enum import Enum
from typing import Dict, Any, Union
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Gio, GLib, GObject, Gtk, GtkSource

from .constants import RESOURCE_PREFIX
from .style_utils import apply_source_style_scheme


logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

class ScanOptions(Enum):
    DEFAULT = "-sV -T4"
    OS_FINGERPRINTING = "-A"
    ALL_PORTS = "-p-"
    SCRIPT = "--script="

class ScanStatus(Enum):
    IN_PROGRESS = (0.0, "Scanning {target}...")
    COMPLETE = (1.0, "Scan complete")
    FAILED = (1.0, "Scan failed unexpectedly")

# Define NmapItem class before using it
class NmapItem(GObject.Object):
    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/nmap_page.ui")
class NmapPage(Gtk.Box):
    __gtype_name__ = "NmapPage"

    # Define template children
    target_entry_row = Gtk.Template.Child("target_entry_row")
    target_list_box = Gtk.Template.Child("target_list_box")
    nmap_results_scolled_window = Gtk.Template.Child("nmap_results_scolled_window")
    nmap_target_frame = Gtk.Template.Child("nmap_target_frame")
    nmap_results_frame = Gtk.Template.Child("nmap_results_frame")
    nmap_target_scrolled_window = Gtk.Template.Child("nmap_target_scrolled_window")
    fingerprint_switch_row = Gtk.Template.Child("fingerprint_switch_row")
    scan_all_ports_switch_row = Gtk.Template.Child("scan_all_ports_switch_row")
    nmap_scripts_drop_down = Gtk.Template.Child("nmap_scripts_drop_down")
    nmap_spinner = Gtk.Template.Child("nmap_spinner")
    nmap_status = Gtk.Template.Child("nmap_status")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logging.debug("Initializing NmapPage...")
        self.scan_all_ports_enabled = False
        self.os_fingerprinting_enabled = False
        self.selected_script = None
        self.results_by_host = {}
        self.target_list_box_store = Gio.ListStore(item_type=NmapItem)

        # Initialize SourceBuffer and SourceView
        self.source_buffer = self.init_source_buffer()
        self.source_view = self.init_source_view(self.source_buffer)

        # Apply the user's preferred style scheme
        self.apply_style_scheme()

        self.executor = ThreadPoolExecutor(max_workers=4)
        self.init_ui()

    def __del__(self):
        self.executor.shutdown(wait=True)

    def init_source_buffer(self) -> GtkSource.Buffer:
        source_buffer = GtkSource.Buffer()
        lang_manager = GtkSource.LanguageManager.get_default()
        yaml_lang = lang_manager.get_language("yaml")

        if yaml_lang is not None:
            source_buffer.set_language(yaml_lang)
        else:
            logging.error("YAML language definition not found.")

        source_buffer.set_highlight_syntax(True)
        return source_buffer

    def init_source_view(self, source_buffer: GtkSource.Buffer) -> GtkSource.View:
        source_view = GtkSource.View.new_with_buffer(source_buffer)
        source_view.set_show_line_numbers(False)
        source_view.set_editable(False)
        source_view.set_wrap_mode(Gtk.WrapMode.WORD)
        source_view.set_hexpand(True)
        source_view.set_vexpand(True)
        self.nmap_results_scolled_window.set_child(source_view)
        return source_view

    def apply_style_scheme(self):
        """Apply the user's preferred style scheme to the GtkSourceView."""
        try:
            settings = Gio.Settings.new("com.github.mclellac.WebOpsEvaluationSuite")
            source_style_scheme = settings.get_string("source-style-scheme")
            logging.debug(f"Applying style scheme: {source_style_scheme}")
            apply_source_style_scheme(GtkSource.StyleSchemeManager.get_default(), self.source_buffer, source_style_scheme)
        except Exception as e:
            logging.error(f"Error applying style scheme: {e}")

    def set_visible(self, *widgets, visible: bool):
        for widget in widgets:
            if widget is not None:
                widget.set_visible(visible)
            else:
                logging.error("Attempted to set visibility of a None widget")

    def init_ui(self):
        logging.debug("Initializing UI components.")

        # Bind the ListStore to the ListBox
        self.target_list_box.bind_model(self.target_list_box_store, self.create_listbox_row)

        self.connect_signals()

        # Initially hide the output widgets
        self.set_visible(
            self.nmap_spinner,
            self.nmap_status,
            self.nmap_target_frame,
            self.nmap_results_frame,
            self.target_list_box,
            self.source_view,
            self.nmap_target_scrolled_window,
            visible=False,
        )

        logging.debug("UI components initialized.")

    def connect_signals(self):
        try:
            self.target_entry_row.connect("entry-activated", self.on_target_entry_row_activated)
            self.target_entry_row.connect("apply", self.on_target_entry_row_activated)
            self.fingerprint_switch_row.connect("notify::active", self.on_fingerprint_switch_row_toggled)
            self.scan_all_ports_switch_row.connect("notify::active", self.on_scan_all_ports_switch_row_toggled)
            self.nmap_scripts_drop_down.connect("notify::selected-item", self.on_nmap_scripts_drop_down_changed)
            self.target_list_box.connect("row-selected", self.on_target_listbox_row_selected)
            logging.debug("Connected signals for UI components.")
        except Exception as e:
            logging.error(f"Error connecting signals for UI components: {e}")

    # Nmap Scan Operations
    def on_target_entry_row_activated(self, entry_row: Gtk.Widget):
        target = entry_row.get_text().strip()
        if not self.validate_target_input(target):
            entry_row.get_style_context().add_class("error")
            return
        else:
            entry_row.get_style_context().remove_class("error")

        if not target:
            GLib.idle_add(self.clear_results)
            GLib.idle_add(self.target_entry_row.set_sensitive, True)
            return

        # Delay setting the entry row as insensitive to ensure focus-out event is handled
        GLib.idle_add(self.target_entry_row.set_sensitive, False)

        self.os_fingerprinting_enabled = self.fingerprint_switch_row.get_active()
        self.scan_all_ports_enabled = self.scan_all_ports_switch_row.get_active()
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        self.selected_script = (
            selected_item.get_string()
            if isinstance(selected_item, Gtk.StringObject)
            else None
        )

        status_message = ScanStatus.IN_PROGRESS.value[1].format(target=target)
        self.set_scan_status(ScanStatus.IN_PROGRESS.value[0], status_message)

        self.executor.submit(self.run_nmap_scan, target, self.os_fingerprinting_enabled)

        # Optionally grab focus on another widget
        GLib.idle_add(self.nmap_spinner.grab_focus)

    def validate_target_input(self, target: str) -> bool:
        ipv4_segment = r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
        ipv4_address = fr"(?:{ipv4_segment}\.){3}{ipv4_segment}"
        fqdn = r"(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,}"
        cidr = r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}"

        addr_regex = (
            r"^(localhost|"  # Match 'localhost'
            fr"{ipv4_address}|"
            fr"{fqdn}|"
            fr"{cidr})$"
        )

        targets = re.split(r"[ ,]+", target.strip())
        return all(re.match(addr_regex, t) for t in targets)

    def build_nmap_options(self, os_fingerprinting: bool) -> str:
        options = ScanOptions.DEFAULT.value
        if os_fingerprinting:
            options += f" {ScanOptions.OS_FINGERPRINTING.value}"
        if self.scan_all_ports_enabled:
            options += f" {ScanOptions.ALL_PORTS.value}"
        if self.selected_script and self.selected_script != "None":
            options += f" {ScanOptions.SCRIPT.value}{self.selected_script}"
        logging.debug(f"Nmap options constructed: {options}")
        return options

    def run_nmap_scan(self, target: str, os_fingerprinting: bool):
        logging.debug(f"Running Nmap scan for target: {target} with options: {self.build_nmap_options(os_fingerprinting)}")
        options = self.build_nmap_options(os_fingerprinting)
        try:
            nm = nmap.PortScanner()
            nm.scan(hosts=target, arguments=options)
            logging.debug(f"Nmap scan completed with results: {nm.all_hosts()}")
            self.process_scan_results(nm)
        except nmap.PortScannerError as e:
            logging.error(f"Nmap scan failed: {e}")
            GLib.idle_add(self.set_scan_status, ScanStatus.FAILED.value[0], "Nmap scan failed due to scanner error")
        except Exception as e:
            logging.error(f"Unexpected error during scan: {e}")
            GLib.idle_add(self.set_scan_status, ScanStatus.FAILED.value[0], "Scan failed unexpectedly")
        finally:
            GLib.idle_add(self.target_entry_row.set_sensitive, True)

    def process_scan_results(self, nm: nmap.PortScanner):
        logging.debug(f"Processing Nmap scan results for hosts: {nm.all_hosts()}")
        results = self.convert_results_to_yaml(nm)
        GLib.idle_add(self.update_nmap_results_view, (nm.all_hosts(), results))
        GLib.idle_add(self.set_scan_status, ScanStatus.COMPLETE.value[0], "Scan complete")

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        all_results = {}
        for host in nm.all_hosts():
            logging.debug(f"Processing results for host: {host}")
            host_data = nm[host]
            logging.debug(f"Raw host data: {host_data}")
            plain_dict = self.to_plain_dict(host_data)
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            all_results[host] = yaml_output
        return all_results

    def to_plain_dict(self, data: Any) -> Union[Dict[str, Any], Any]:
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        elif isinstance(data, dict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        else:
            return data

    # UI Updates
    def update_nmap_results_view(self, args: tuple):
        hosts, results = args

        self.target_list_box_store.remove_all()
        self.source_buffer.set_text("")

        self.results_by_host = {}
        for target in hosts:
            result_text = results.get(target, "No results available")
            logging.debug(f"Updating listbox and source view with result for {target}: {result_text}")
            nmap_item = NmapItem(key=target, value=result_text)
            self.target_list_box_store.append(nmap_item)
            self.results_by_host[target] = result_text

        logging.debug(f"Total items in list store after update: {self.target_list_box_store.get_n_items()}")

        # Automatically select the first host to display its results
        if hosts:
            first_host = self.target_list_box_store.get_item(0)
            if first_host:
                self.target_list_box.select_row(self.target_list_box.get_row_at_index(0))
                self.source_buffer.set_text(self.results_by_host[first_host.key])

        self.set_visible(
            self.source_view,
            self.target_list_box,
            self.nmap_results_frame,
            self.nmap_target_frame,
            self.nmap_target_scrolled_window,
            visible=True,
        )

        self.refresh_source_view()

    def on_target_listbox_row_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow):
        if row is not None:
            selected_target = row.get_child().get_label()
            results = self.results_by_host.get(selected_target, "")
            if results:
                self.source_buffer.set_text(results)
                self.refresh_source_view()

    def refresh_source_view(self):
        logging.debug("Refreshing source view")
        if self.source_view:
            self.source_view.queue_draw()
            parent = self.source_view.get_parent()
            if parent:
                parent.queue_resize()
                parent.queue_draw()

    def set_scan_status(self, progress: float, status_message: str):
        if progress == ScanStatus.IN_PROGRESS.value[0]:
            self.nmap_spinner.set_visible(True)
            self.nmap_status.set_visible(True)
            self.nmap_status.set_label(status_message)
        elif progress == ScanStatus.COMPLETE.value[0]:
            self.nmap_spinner.set_visible(False)
            self.nmap_status.set_visible(False)

    def clear_results(self):
        self.target_list_box_store.remove_all()
        self.source_buffer.set_text("")
        self.set_visible(
            self.nmap_results_frame,
            self.nmap_target_frame,
            self.target_list_box,
            self.source_view,
            visible=False,
        )

    # Signal Handlers
    def on_fingerprint_switch_row_toggled(self, switch: Gtk.Switch, gparam: GObject.ParamSpec):
        self.os_fingerprinting_enabled = switch.get_active()

    def on_scan_all_ports_switch_row_toggled(self, switch: Gtk.Switch, gparam: GObject.ParamSpec):
        self.scan_all_ports_enabled = switch.get_active()

    def on_nmap_scripts_drop_down_changed(self):
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        self.selected_script = (
            selected_item.get_string()
            if isinstance(selected_item, Gtk.StringObject)
            else None
        )

    def create_listbox_row(self, item: NmapItem, user_data: Any = None) -> Gtk.ListBoxRow:
        label = Gtk.Label(label=item.key)
        row = Gtk.ListBoxRow()
        row.set_child(label)
        return row

