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
    DEFAULT = "-T4"
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
    nmap_target_entryrow = Gtk.Template.Child("nmap_target_entryrow")
    nmap_target_listbox = Gtk.Template.Child("nmap_target_listbox")
    nmap_results_scolled_window = Gtk.Template.Child("nmap_results_scolled_window")
    nmap_target_frame = Gtk.Template.Child("nmap_target_frame")
    nmap_results_frame = Gtk.Template.Child("nmap_results_frame")
    nmap_target_scrolled_window = Gtk.Template.Child("nmap_target_scrolled_window")
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow")
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow")
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown")
    nmap_spinner = Gtk.Template.Child("nmap_spinner")
    nmap_status = Gtk.Template.Child("nmap_status")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logging.debug("Initializing NmapPage...")
        self.scan_all_ports_enabled = False
        self.os_fingerprinting_enabled = False
        self.selected_script = None
        self.results_by_host = {}
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)

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
        self.nmap_target_listbox.bind_model(self.nmap_target_listbox_store, self.create_listbox_row)

        self.connect_signals()

        # Initially hide the output widgets
        self.set_visible(
            self.nmap_spinner,
            self.nmap_status,
            self.nmap_target_frame,
            self.nmap_results_frame,
            self.nmap_target_listbox,
            self.source_view,
            self.nmap_target_scrolled_window,
            visible=False,
        )

        logging.debug("UI components initialized.")

    def connect_signals(self):
        try:
            self.nmap_target_entryrow.connect("entry-activated", self.on_nmap_target_entryrow_activated)
            self.nmap_target_entryrow.connect("apply", self.on_nmap_target_entryrow_activated)
            self.nmap_fingerprint_switchrow.connect("notify::active", self.on_nmap_fingerprint_switchrow_toggled)
            self.nmap_all_ports_switchrow.connect("notify::active", self.on_nmap_all_ports_switchrow_toggled)
            self.nmap_scripts_dropdown.connect("notify::selected-item", self.on_nmap_scripts_dropdown_changed)
            self.nmap_target_listbox.connect("row-selected", self.on_nmap_target_listbox_row_selected)
            logging.debug("Connected signals for UI components.")
        except Exception as e:
            logging.error(f"Error connecting signals for UI components: {e}")

    # Nmap Scan Operations
    def validate_target_input(self, target: str) -> bool:
        ipv4_segment = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        ipv4_address = r"(?:{}\.{}\.{}\.{})".format(ipv4_segment, ipv4_segment, ipv4_segment, ipv4_segment)
        fqdn = r"(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,}"
        cidr = r"{}\/[0-9]{{1,2}}".format(ipv4_address)  # Escaping curly braces for the CIDR notation

        addr_regex = (
            r"^(localhost|"
            r"{}|"
            r"{}|"
            r"{})$".format(ipv4_address, fqdn, cidr)
        )

        targets = re.split(r"[ ,]+", target.strip())

        for t in targets:
            if re.match(addr_regex, t):
                logging.debug(f"Target '{t}' matched the pattern.")
            else:
                logging.debug(f"Target '{t}' did NOT match the pattern.")

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
            error_message = f"Nmap scan failed: {e}"
            logging.error(error_message)
            GLib.idle_add(self.handle_scan_error, target, error_message)
            GLib.idle_add(self.set_scan_status, ScanStatus.FAILED.value[0], "Nmap scan failed due to scanner error")
        except Exception as e:
            error_message = f"Unexpected error during scan: {e}"
            logging.error(error_message)
            GLib.idle_add(self.handle_scan_error, target, error_message)
            GLib.idle_add(self.set_scan_status, ScanStatus.FAILED.value[0], "Scan failed unexpectedly")
        finally:
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)

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
    def handle_scan_error(self, target: str, error_message: str):
        nmap_item = NmapItem(key=target, value=error_message)
        self.nmap_target_listbox_store.append(nmap_item)
        self.results_by_host[target] = error_message

        # Display the error message in the source view
        self.source_buffer.set_text(error_message)

        self.set_visible(
            self.source_view,
            self.nmap_target_listbox,
            self.nmap_results_frame,
            self.nmap_target_frame,
            self.nmap_target_scrolled_window,
            visible=True,
        )

        self.refresh_source_view()

    def on_nmap_target_listbox_row_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow):
        logging.debug("on_target_listbox_row_selected called")

        if row is not None:
            selected_target = row.get_child().get_label()
            logging.debug(f"Selected target: {selected_target}")

            results = self.results_by_host.get(selected_target, "")
            logging.debug(f"Results for {selected_target}: {results[:200]}")  # Log first 200 characters of results

            if results:
                self.source_buffer.set_text(results)
                self.refresh_source_view()
                logging.debug(f"Source view updated with results for {selected_target}")
            else:
                logging.warning(f"No results found for {selected_target}")
        else:
            logging.debug("No row is currently selected.")


    def update_nmap_results_view(self, args: tuple):
        logging.debug("update_nmap_results_view called")
        hosts, results = args

        logging.debug(f"Hosts: {hosts}")
        logging.debug(f"Results: {results}")

        self.nmap_target_listbox_store.remove_all()
        self.source_buffer.set_text("")

        self.results_by_host = {}
        for target in hosts:
            result_text = results.get(target, "No results available")
            logging.debug(f"Adding target: {target} with results: {result_text[:200]}")  # First 200 chars

            nmap_item = NmapItem(key=target, value=result_text)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[target] = result_text

        logging.debug(f"Total items in list store after update: {self.nmap_target_listbox_store.get_n_items()}")

        if hosts:
            first_host = self.nmap_target_listbox_store.get_item(0)
            if first_host:
                logging.debug(f"Automatically selecting first host: {first_host.key}")
                self.nmap_target_listbox.select_row(self.nmap_target_listbox.get_row_at_index(0))
                self.source_buffer.set_text(self.results_by_host[first_host.key])
                logging.debug(f"Source view updated for first host: {first_host.key}")

        self.set_visible(
            self.source_view,
            self.nmap_target_listbox,
            self.nmap_results_frame,
            self.nmap_target_frame,
            self.nmap_target_scrolled_window,
            visible=True,
        )

        self.refresh_source_view()


    def refresh_source_view(self):
        logging.debug("Entering refresh_source_view")

        if not self.source_view:
            logging.error("Source view is None; cannot refresh")
            return

        # Force the source view to redraw
        logging.debug("Queuing redraw for source view")
        self.source_view.queue_draw()

        parent = self.source_view.get_parent()
        if parent:
            logging.debug("Source view has a parent; queuing resize and redraw for the parent")
            parent.queue_resize()
            parent.queue_draw()
        else:
            logging.warning("Source view does not have a parent; skipping parent refresh")

        logging.debug("Exiting refresh_source_view")

    def set_scan_status(self, progress: float, status_message: str):
        if progress == ScanStatus.IN_PROGRESS.value[0]:
            self.nmap_spinner.set_visible(True)
            self.nmap_status.set_visible(True)
            self.nmap_status.set_label(status_message)
        elif progress == ScanStatus.COMPLETE.value[0]:
            self.nmap_spinner.set_visible(False)
            self.nmap_status.set_visible(False)

    def clear_results(self):
        self.nmap_target_listbox_store.remove_all()
        self.source_buffer.set_text("")
        self.set_visible(
            self.nmap_results_frame,
            self.nmap_target_frame,
            self.nmap_target_listbox,
            self.source_view,
            visible=False,
        )

    # Signal Handlers
    def on_nmap_target_entryrow_activated(self, entryrow: Gtk.Widget):
        target = entryrow.get_text().strip()
        if not self.validate_target_input(target):
            entryrow.get_style_context().add_class("error")
            return
        else:
            entryrow.get_style_context().remove_class("error")

        if not target:
            GLib.idle_add(self.clear_results)
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)
            return

        # Delay setting the entry row as insensitive to ensure focus-out event is handled
        GLib.idle_add(self.nmap_target_entryrow.set_sensitive, False)

        self.os_fingerprinting_enabled = self.nmap_fingerprint_switchrow.get_active()
        self.scan_all_ports_enabled = self.nmap_all_ports_switchrow.get_active()
        selected_item = self.nmap_scripts_dropdown.get_selected_item()
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


    def on_nmap_fingerprint_switchrow_toggled(self, switch: Gtk.Switch, gparam: GObject.ParamSpec):
        self.os_fingerprinting_enabled = switch.get_active()

    def on_nmap_all_ports_switchrow_toggled(self, switch: Gtk.Switch, gparam: GObject.ParamSpec):
        self.scan_all_ports_enabled = switch.get_active()

    def on_nmap_scripts_dropdown_changed(self):
        selected_item = self.nmap_scripts_dropdown.get_selected_item()
        self.selected_script = (
            selected_item.get_string()
            if isinstance(selected_item, Gtk.StringObject)
            else None
        )

    def create_listbox_row(self, item: NmapItem, user_data: Any = None) -> Gtk.ListBoxRow:
        label = Gtk.Label(label=item.key)
        row = Gtk.ListBoxRow()
        row.set_child(label)
        logging.debug(f"Created listbox row for item: {item.key}")
        return row

