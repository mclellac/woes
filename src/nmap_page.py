import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

import gi
import yaml

from .constants import RESOURCE_PREFIX

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import nmap
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

# Configure logging
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT)


class NmapItem(GObject.Object):
    """Represents an Nmap scan result item."""

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
    scan_column_view = Gtk.Template.Child("scan_column_view")
    fingerprint_switch_row = Gtk.Template.Child("fingerprint_switch_row")
    scan_all_ports_switch_row = Gtk.Template.Child("scan_all_ports_switch_row")
    nmap_scripts_drop_down = Gtk.Template.Child("nmap_scripts_drop_down")
    nmap_spinner = Gtk.Template.Child("nmap_spinner")
    nmap_status = Gtk.Template.Child("nmap_status")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.scan_all_ports_enabled = False
        self.os_fingerprinting_enabled = False
        self.selected_script = None
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.init_ui()

    def init_ui(self):
        """Initialize UI components and connect signals."""
        self.target_entry_row.connect(
            "entry-activated", self.on_target_entry_row_activated
        )
        self.target_entry_row.connect("apply", self.on_target_entry_row_activated)
        self.fingerprint_switch_row.connect(
            "notify::active", self.on_fingerprint_switch_row_toggled
        )
        self.scan_all_ports_switch_row.connect(
            "notify::active", self.on_scan_all_ports_switch_row_toggled
        )
        self.nmap_scripts_drop_down.connect(
            "notify::selected-item", self.on_nmap_scripts_drop_down_changed
        )

        self.nmap_spinner.set_visible(False)
        self.nmap_status.set_visible(False)

    def validate_target_input(self, target: str) -> bool:
        """Validate the target input to ensure it is an IP, FQDN, CIDR, or 'localhost'."""
        ip_regex = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
        fqdn_regex = r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*(\.[A-Za-z]{2,})$"
        cidr_regex = r"^((25[0-5]|(2[0-4]|1[0-9]|[1-9]|)[0-9])\.(?!$)|\d{1,3}(?<!$))*(\/([0-9]|[1-2][0-9]|3[0-2]))$"
        localhost_regex = r"^localhost$"

        targets = re.split(r"[ ,]+", target)
        for t in targets:
            if not (
                re.match(ip_regex, t)
                or re.match(fqdn_regex, t)
                or re.match(cidr_regex, t)
                or re.match(localhost_regex, t)
            ):
                logging.debug(f"Invalid target detected: {t}")
                return False
        return True

    def on_target_entry_row_activated(self, entry_row):
        """Handle the target entry row activation event."""
        target = entry_row.get_text().strip()
        logging.debug(f"Entry activated with text: {target}")

        if not self.validate_target_input(target):
            logging.error("Invalid target input.")
            entry_row.get_style_context().add_class("error")
            return
        else:
            entry_row.get_style_context().remove_class("error")

        if not target:
            logging.error("Target is empty.")
            self.update_nmap_column_view(None)
            GLib.idle_add(self.target_entry_row.set_sensitive, True)
            return

        self.target_entry_row.set_sensitive(False)

        os_fingerprinting = self.fingerprint_switch_row.get_active()
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        scripts = (
            selected_item.get_string()
            if isinstance(selected_item, Gtk.StringObject)
            else None
        )

        self.set_scan_status(0.0, "Scanning...")

        # Use ThreadPoolExecutor to run the scan
        self.executor.submit(self.run_nmap_scan, target, os_fingerprinting, scripts)

    def on_fingerprint_switch_row_toggled(self, widget, gparam):
        """Handle the fingerprint switch row toggle event."""
        self.os_fingerprinting_enabled = self.fingerprint_switch_row.get_active()
        logging.debug(f"OS Fingerprinting enabled: {self.os_fingerprinting_enabled}")

    def on_scan_all_ports_switch_row_toggled(self, widget, gparam):
        """Handle the scan all ports switch row toggle event."""
        self.scan_all_ports_enabled = self.scan_all_ports_switch_row.get_active()
        logging.debug(f"Scan all ports enabled: {self.scan_all_ports_enabled}")

    def on_nmap_scripts_drop_down_changed(self, widget, gparam):
        """Handle the Nmap scripts dropdown change event."""
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        self.selected_script = (
            selected_item.get_string()
            if isinstance(selected_item, Gtk.StringObject)
            else None
        )
        logging.debug(f"Selected script: {self.selected_script}")

    def copy_to_clipboard(self, text: str):
        """Copy the given text to the clipboard."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        logging.info("Copied to clipboard")

    def set_scan_status(self, progress: float, status_message: str):
        """Update the Nmap progress spinner and status label."""
        if progress == 0.0:
            self.nmap_spinner.set_visible(True)
            self.nmap_status.set_visible(True)
            self.nmap_status.set_label(status_message)
        elif progress == 1.0:
            self.nmap_spinner.set_visible(False)
            self.nmap_status.set_visible(False)

    def build_nmap_options(
        self, os_fingerprinting: bool, scripts: Optional[str]
    ) -> str:
        """Build the Nmap command options based on user selection."""
        options = "-sV -T4"
        if os_fingerprinting:
            options += " -O"
        if self.scan_all_ports_enabled:
            options += " -p-"
        if self.selected_script:
            options += f" --script={scripts}"
        return options

    def run_nmap_scan(
        self, target: str, os_fingerprinting: bool, scripts: Optional[str]
    ):
        """Run the Nmap scan in a separate thread."""
        options = self.build_nmap_options(os_fingerprinting, scripts)
        logging.debug(f"Running Nmap scan with options: {options} on target: {target}")

        try:
            nm = nmap.PortScanner()
            nm.scan(hosts=target, arguments=options)
            logging.debug(f"Nmap scan completed. Hosts found: {nm.all_hosts()}")
            self.process_scan_results(nm)
        except nmap.PortScannerError as e:
            logging.error(f"Nmap scan failed with PortScannerError: {e}")
            GLib.idle_add(
                self.set_scan_status, 1.0, "Nmap scan failed due to scanner error"
            )
        except Exception as e:
            logging.error(f"Scan failed with unexpected error: {e}")
            GLib.idle_add(self.set_scan_status, 1.0, "Scan failed unexpectedly")
        finally:
            GLib.idle_add(self.target_entry_row.set_sensitive, True)

    def process_scan_results(self, nm: nmap.PortScanner):
        """Process and update UI with Nmap scan results."""
        yaml_results = self.convert_results_to_yaml(nm)
        logging.debug(f"Processed Nmap results in YAML: {yaml_results}")
        GLib.idle_add(self.update_nmap_column_view, yaml_results)
        GLib.idle_add(self.set_scan_status, 1.0, "Scan complete")

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        """Convert the Nmap results to YAML format."""
        results = {}
        for host in nm.all_hosts():
            host_data = nm[host]

            # Convert nmap.PortScannerHostDict and other complex objects to plain dicts
            plain_dict = self.to_plain_dict(host_data)

            # Convert to YAML
            yaml_output = yaml.safe_dump({host: plain_dict}, default_flow_style=False)

            # Store the YAML output in the results dictionary
            results[host] = yaml_output

        return results

    def to_plain_dict(self, data):
        """Recursively convert an Nmap result to a plain Python dictionary."""
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        elif isinstance(data, dict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        else:
            return data

    def update_nmap_column_view(self, results: Optional[Dict[str, str]]):
        """Update the ColumnView with Nmap scan results."""
        logging.debug("Updating ColumnView with results:")
        logging.debug(results)

        while self.scan_column_view.get_columns():
            self.scan_column_view.remove_column(self.scan_column_view.get_columns()[0])

        list_store = Gio.ListStore.new(NmapItem)
        if results:
            for key, value in results.items():
                logging.debug(f"Adding item to list store: key={key}, value={value}")
                list_store.append(NmapItem(key=key, value=value))

            selection_model = Gtk.SingleSelection.new(list_store)
            self.scan_column_view.set_model(selection_model)

            def create_factory(attr_name: str) -> Gtk.SignalListItemFactory:
                factory = Gtk.SignalListItemFactory()
                factory.connect(
                    "setup",
                    lambda factory, list_item: list_item.set_child(
                        Gtk.Label(xalign=0, yalign=0)
                    ),
                )
                factory.connect(
                    "bind",
                    lambda factory, list_item: list_item.get_child().set_text(
                        getattr(list_item.get_item(), attr_name, "")
                    ),
                )
                return factory

            target_column = Gtk.ColumnViewColumn.new("Target", create_factory("key"))
            self.scan_column_view.append_column(target_column)

            scan_output_column = Gtk.ColumnViewColumn.new(
                "Scan Output", create_factory("value")
            )
            scan_output_column.set_expand(True)
            self.scan_column_view.append_column(scan_output_column)

            logging.debug("Columns added to ColumnView.")
        else:
            logging.warning("No results to display.")
