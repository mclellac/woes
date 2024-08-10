import gi
import logging
import subprocess
import re
import threading
from typing import Dict, List, Optional

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, GObject, GLib

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class NmapItem(GObject.Object):
    """Represents an Nmap scan result item."""
    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value

@Gtk.Template(resource_path='/com/github/mclellac/WebOpsEvaluationSuite/gtk/nmap_page.ui')
class NmapPage(Gtk.Box):
    __gtype_name__ = 'NmapPage'

    target_entry_row = Gtk.Template.Child('target_entry_row')
    scan_column_view = Gtk.Template.Child('scan_column_view')
    fingerprint_switch_row = Gtk.Template.Child('fingerprint_switch_row')
    scan_all_ports_switch_row = Gtk.Template.Child('scan_all_ports_switch_row')
    nmap_scripts_drop_down = Gtk.Template.Child('nmap_scripts_drop_down')
    nmap_progress = Gtk.Template.Child('nmap_progress')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.scan_all_ports_enabled = False
        self.os_fingerprinting_enabled = False
        self.selected_script = None
        self.init_ui()

    def init_ui(self):
        """Initialize UI components and connect signals."""
        self.target_entry_row.connect("entry-activated", self.on_target_entry_row_activated)
        self.target_entry_row.connect("apply", self.on_target_entry_row_activated)
        self.fingerprint_switch_row.connect("notify::active", self.on_fingerprint_switch_row_toggled)
        self.scan_all_ports_switch_row.connect("notify::active", self.on_scan_all_ports_switch_row_toggled)
        self.nmap_scripts_drop_down.connect("notify::selected-item", self.on_nmap_scripts_drop_down_changed)

    def on_target_entry_row_activated(self, entry_row):
        """Handle the target entry row activation event."""
        target = entry_row.get_text().strip()
        logging.debug(f"Entry activated with text: {target}")

        if not target:
            logging.error("Target is empty.")
            self.update_nmap_column_view(None)
            GLib.idle_add(self.target_entry_row.set_sensitive, True)
            return

        self.target_entry_row.set_sensitive(False)  # Disable entry row

        os_fingerprinting = self.fingerprint_switch_row.get_active()
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        scripts = selected_item.get_string() if isinstance(selected_item, Gtk.StringObject) else None

        self.update_nmap_progress(0.0, "Starting scan...")

        scan_thread = threading.Thread(target=self.run_nmap_scan, args=(target, os_fingerprinting, scripts))
        scan_thread.start()

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
        self.selected_script = selected_item.get_string() if isinstance(selected_item, Gtk.StringObject) else None
        logging.debug(f"Selected script: {self.selected_script}")

    def copy_to_clipboard(self, text: str):
        """Copy the given text to the clipboard."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        logging.debug("Copied to clipboard")

    def run_nmap_scan(self, target: str, os_fingerprinting: bool, scripts: Optional[str]):
        command = ["nmap", "-sV", "--stats-every", "2s"]

        if os_fingerprinting:
            command.append("-A")
        if self.scan_all_ports_enabled:
            command.append("-p-")
        else:
            command.extend(["--top-ports", "200"])

        if scripts:
            command.extend(["-Pn", "--script", scripts])
        command.append(target)

        logging.debug(f"Running command: {' '.join(command)}")

        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            nmap_output = ""

            for line in iter(process.stdout.readline, ''):
                if line.startswith("Stats:") or line.startswith("NSE Timing:"):
                    continue
                nmap_output += line
                self.get_nmap_progress(line)
                logging.debug(f"Output: {line.strip()}")

            process.wait()  # Ensure the process has finished

            if process.returncode == 0:
                results = self.get_nmap_results(nmap_output)
                GLib.idle_add(self.update_nmap_progress, 1.0, "Scan complete")
            else:
                results = {target: f"Error: {process.stderr.read().strip()}"}
                GLib.idle_add(self.update_nmap_progress, 1.0, "Scan completed with errors")

        except Exception as e:
            results = {target: f"Exception: {str(e)}"}
            logging.error(f"Exception occurred: {str(e)}")
            GLib.idle_add(self.update_nmap_progress, 1.0, "Scan failed")

        finally:
            GLib.idle_add(self.update_nmap_column_view, results)
            GLib.idle_add(self.target_entry_row.set_sensitive, True)

    def update_nmap_progress(self, fraction: float, text: str):
        """Update the progress bar and text."""
        logging.debug(f"Updating progress: {fraction*100}% - {text}")
        self.nmap_progress.set_fraction(fraction)
        self.nmap_progress.set_text(text)

    def get_nmap_results(self, output: str) -> Dict[str, str]:
        """Parse Nmap output and return results as a dictionary."""
        results = {}
        target_host = None
        for line in output.splitlines():
            if line.startswith("Stats:") or line.startswith("NSE Timing:"):
                continue
            if "Nmap scan report for" in line:
                target_host = line.split()[-1].strip('()')
                results[target_host] = []
            elif target_host:
                results[target_host].append(line.strip())
        for target_host, lines in results.items():
            results[target_host] = "\n".join(lines)
        logging.debug(f"Nmap results: {results}")
        return results

    def get_nmap_progress(self, line: str):
        """Extract and update progress from Nmap output line."""
        stats_pattern = re.compile(r'About (\d+(\.\d+)?)% done')
        match = stats_pattern.search(line)
        if match:
            progress_percent = float(match.group(1))
            fraction = progress_percent / 100.0
            logging.debug(f"Extracted progress: {progress_percent}%")
            self.update_nmap_progress(fraction, f"Scanning... {progress_percent}% done")

    def update_nmap_column_view(self, results: Optional[Dict[str, str]]):
        """Update the ColumnView with Nmap scan results."""
        logging.debug("Updating ColumnView with results:")
        logging.debug(results)

        # Create a ListStore with the NmapItem model
        list_store = Gio.ListStore.new(NmapItem)
        if results:
            for key, value in results.items():
                list_store.append(NmapItem(key=key, value=value))

            selection_model = Gtk.SingleSelection.new(list_store)
            self.scan_column_view.set_model(selection_model)

            # Remove existing columns, if any
            for column in self.scan_column_view.get_columns():
                if column is not None:
                    self.scan_column_view.remove_column(column)

            # Define a factory function to create ListItemFactories
            def create_factory(attr_name: str) -> Gtk.SignalListItemFactory:
                factory = Gtk.SignalListItemFactory()
                factory.connect("setup", lambda factory, list_item: list_item.set_child(Gtk.Label(xalign=0)))
                factory.connect("bind", lambda factory, list_item: list_item.get_child().set_text(getattr(list_item.get_item(), attr_name)))
                return factory

            key_factory = create_factory("key")
            value_factory = create_factory("value")

            key_column = Gtk.ColumnViewColumn(title="Target", factory=key_factory)
            value_column = Gtk.ColumnViewColumn(title="Output", factory=value_factory)

            self.scan_column_view.append_column(key_column)
            self.scan_column_view.append_column(value_column)
        else:
            self.scan_column_view.set_model(None)

        self.scan_column_view.show()

