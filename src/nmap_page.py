import re
import nmap
import yaml
import logging
from typing import Dict
from concurrent.futures import ThreadPoolExecutor

from .constants import RESOURCE_PREFIX

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Gio, GLib, GObject, Gtk, GtkSource

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

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
        self.executor = ThreadPoolExecutor(max_workers=4)

        self.target_list_box_store = Gio.ListStore(item_type=NmapItem)

        self.source_buffer = GtkSource.Buffer()
        lang_manager = GtkSource.LanguageManager.get_default()
        yaml_lang = lang_manager.get_language("yaml")
        self.source_buffer.set_language(yaml_lang)
        self.source_buffer.set_highlight_syntax(True)
        self.source_view = GtkSource.View.new_with_buffer(self.source_buffer)
        self.source_view.set_show_line_numbers(False)
        self.source_view.set_editable(False)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.source_view.set_hexpand(True)
        self.source_view.set_vexpand(True)

        self.nmap_results_scolled_window.set_child(self.source_view)

        self.target_list_box.bind_model(self.target_list_box_store, self.create_listbox_row)

        self.nmap_target_frame.set_visible(False)
        self.nmap_results_frame.set_visible(False)
        self.target_list_box.set_visible(False)
        self.source_view.set_visible(False)

        self.init_ui()

    # UI Initialization and Signal Connections
    def init_ui(self):
        logging.debug("Initializing UI components.")
        self.connect_signals()

        self.nmap_spinner.set_visible(False)
        self.nmap_status.set_visible(False)
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
    def on_target_entry_row_activated(self, entry_row):
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

        self.target_entry_row.set_sensitive(False)

        self.os_fingerprinting_enabled = self.fingerprint_switch_row.get_active()
        self.scan_all_ports_enabled = self.scan_all_ports_switch_row.get_active()
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        self.selected_script = (
            selected_item.get_string()
            if isinstance(selected_item, Gtk.StringObject)
            else None
        )

        self.set_scan_status(0.0, f"Scanning {target}...")

        self.executor.submit(self.run_nmap_scan, target, self.os_fingerprinting_enabled)

    def validate_target_input(self, target: str) -> bool:
        ip_regex = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
        fqdn_regex = r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*(\.[A-Za-z]{2,})$"
        cidr_regex = r"^((25[0-5]|(2[0-4]|1[0-9]|[1-9]|)[0-9])\.(?!$)|\d{1,3}(?<!$))*(\/([0-9]|[1-2][0-9]|3[0-2]))$"
        localhost_regex = r"^localhost$"

        targets = re.split(r"[ ,]+", target.strip())
        for t in targets:
            if not (
                re.match(ip_regex, t)
                or re.match(fqdn_regex, t)
                or re.match(cidr_regex, t)
                or re.match(localhost_regex, t)
            ):
                return False
        return True

    def build_nmap_options(self, os_fingerprinting: bool) -> str:
        options = "-sV -T4"
        if os_fingerprinting:
            options += " -O"
        if self.scan_all_ports_enabled:
            options += " -p-"
        if self.selected_script and self.selected_script != "None":
            options += f" --script={self.selected_script}"
        return options

    def run_nmap_scan(self, target: str, os_fingerprinting: bool):
        options = self.build_nmap_options(os_fingerprinting)
        try:
            nm = nmap.PortScanner()
            nm.scan(hosts=target, arguments=options)
            self.process_scan_results(nm)
        except nmap.PortScannerError as e:
            GLib.idle_add(self.set_scan_status, 1.0, "Nmap scan failed due to scanner error")
        except Exception as e:
            GLib.idle_add(self.set_scan_status, 1.0, "Scan failed unexpectedly")
        finally:
            GLib.idle_add(self.target_entry_row.set_sensitive, True)

    def process_scan_results(self, nm: nmap.PortScanner):
        results = self.convert_results_to_yaml(nm)
        GLib.idle_add(self.update_nmap_results_view, (nm.all_hosts(), results))
        GLib.idle_add(self.set_scan_status, 1.0, "Scan complete")

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        all_results = {}
        for host in nm.all_hosts():
            host_data = nm[host]
            plain_dict = self.to_plain_dict(host_data)
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            all_results[host] = yaml_output
        return all_results

    def to_plain_dict(self, data):
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        elif isinstance(data, dict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        else:
            return data

    # UI Updates
    def update_nmap_results_view(self, args):
        hosts, results = args

        self.target_list_box_store.remove_all()
        self.source_buffer.set_text("")

        self.results_by_host = {}
        for target in hosts:
            nmap_item = NmapItem(key=target, value=results.get(target, "No results available"))
            self.target_list_box_store.append(nmap_item)
            self.results_by_host[target] = results.get(target, "No results available")

        self.source_view.set_visible(True)
        self.target_list_box.set_visible(True)
        self.nmap_results_frame.set_visible(True)
        self.nmap_target_frame.set_visible(True)
        self.nmap_target_scrolled_window.set_visible(True)

        self.refresh_source_view()

    def on_target_listbox_row_selected(self, listbox, row):
        if row is not None:
            selected_target = row.get_child().get_label()
            results = self.results_by_host.get(selected_target, "")
            if results:
                self.source_buffer.set_text(results)
                self.refresh_source_view()

    def refresh_source_view(self):
        self.source_view.queue_draw()
        parent = self.source_view.get_parent()
        if parent is not None:
            parent.queue_resize()
            parent.queue_draw()

    def set_scan_status(self, progress: float, status_message: str):
        if progress == 0.0:
            self.nmap_spinner.set_visible(True)
            self.nmap_status.set_visible(True)
            self.nmap_status.set_label(status_message)
        elif progress == 1.0:
            self.nmap_spinner.set_visible(False)
            self.nmap_status.set_visible(False)

    def clear_results(self):
        self.target_list_box_store.remove_all()
        self.source_buffer.set_text("")
        self.nmap_results_frame.set_visible(False)
        self.nmap_target_frame.set_visible(False)
        self.target_list_box.set_visible(False)
        self.source_view.set_visible(False)

    # Color Scheme Management
    def update_nmap_color_scheme(self, style_scheme):
        self.source_buffer.set_style_scheme(style_scheme)
        self.refresh_source_view()

    # Signal Handlers
    def on_fingerprint_switch_row_toggled(self, switch, gparam):
        self.os_fingerprinting_enabled = switch.get_active()

    def on_scan_all_ports_switch_row_toggled(self, switch, gparam):
        self.scan_all_ports_enabled = switch.get_active()

    def on_nmap_scripts_drop_down_changed(self):
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        self.selected_script = (
            selected_item.get_string()
            if isinstance(selected_item, Gtk.StringObject)
            else None
        )

    def create_listbox_row(self, item, user_data=None):
        label = Gtk.Label(label=item.key)
        row = Gtk.ListBoxRow()
        row.set_child(label)
        return row
