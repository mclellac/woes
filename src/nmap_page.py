import gi
import logging
import subprocess
import re
from typing import Dict, List

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
    nmap_scripts_drop_down = Gtk.Template.Child('nmap_scripts_drop_down')
    nmap_progress = Gtk.Template.Child('nmap_progress')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.init_ui()

    def init_ui(self):
        self.target_entry_row.connect("entry-activated", self.on_target_entry_row_activated)
        self.target_entry_row.connect("apply", self.on_target_entry_row_activated)
        self.fingerprint_switch_row.connect("notify::active", self.on_fingerprint_switch_row_toggled)
        self.nmap_scripts_drop_down.connect("notify::selected-item", self.on_nmap_scripts_drop_down_changed)

    def on_target_entry_row_activated(self, entry_row):
        logging.debug("Entry activated")

        self.target_entry_row.set_sensitive(False)
        target = entry_row.get_text().strip()

        if not target:
            logging.error("Target is empty.")
            self.update_nmap_column_view(None)
            self.target_entry_row.set_sensitive(True)
            return

        os_fingerprinting = self.fingerprint_switch_row.get_active()
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        scripts = selected_item if isinstance(selected_item, str) else None

        self.update_nmap_progress(0.0, "Starting scan...")
        GLib.idle_add(self.run_nmap_scan, target, os_fingerprinting, scripts)

    def on_fingerprint_switch_row_toggled(self, widget, gparam):
        if self.target_entry_row.get_text().strip():
            self.on_target_entry_row_activated(self.target_entry_row)

    def on_nmap_scripts_drop_down_changed(self, widget, gparam):
        selected_item = self.nmap_scripts_drop_down.get_selected_item()
        logging.debug(f"Selected item type: {type(selected_item)}")
        script = selected_item if isinstance(selected_item, str) else None
        logging.debug(f"Selected script: {script}")

    def run_nmap_scan(self, target: str, os_fingerprinting: bool, scripts: str):
        command = ["nmap", "-sV", "-P0-65535", "--stats-every", "2s"]

        if os_fingerprinting:
            command.append("-O")
        if scripts:
            command.extend(["--script", scripts])
        command.append(target)

        try:
            logging.debug(f"Running command: {' '.join(command)}")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            nmap_output = ""
            for line in iter(process.stdout.readline, ''):
                logging.debug(f"Nmap Output: {line.strip()}")
                self.get_nmap_progress(line)
                nmap_output += line

            stdout, stderr = process.communicate()
            logging.debug(f"Process return code: {process.returncode}")

            if process.returncode == 0:
                results = self.get_nmap_results(nmap_output)
                self.update_nmap_progress(1.0, "Scan complete")
            else:
                results = {target: f"Error: {stderr}"}
                self.update_nmap_progress(1.0, "Scan completed with errors")

        except Exception as e:
            results = {target: f"Exception: {str(e)}"}
            self.update_nmap_progress(1.0, "Scan failed")

        GLib.idle_add(self.update_nmap_column_view, results)
        GLib.idle_add(self.target_entry_row.set_sensitive, True)

    def get_nmap_progress(self, line: str):
        # Adjust this pattern based on actual Nmap output format
        stats_pattern = re.compile(r'SYN Stealth Scan Timing: About (\d+(\.\d+)?)% done')
        match = stats_pattern.search(line)
        if match:
            progress_percent = float(match.group(1))
            fraction = progress_percent / 100.0
            self.update_nmap_progress(fraction, f"Scanning... {progress_percent}% done")

    def get_nmap_results(self, output):
        results = {}
        host = None
        for line in output.splitlines():
            if line.startswith("Stats:") or line.startswith("NSE Timing:"):
                continue
            if "Nmap scan report for" in line:
                host = line.split()[-1].strip('()')
                results[host] = []
            elif host is not None:
                results[host].append(line.strip())
        for host, lines in results.items():
            formatted_output = "\n".join(lines)
            results[host] = formatted_output
        return results

    def update_nmap_progress(self, fraction, text="Scanning"):
        logging.debug(f"Updating progress: {fraction*100}% - {text}")
        GLib.idle_add(self._update_nmap_progress_bar, fraction, text)

    def _update_nmap_progress_bar(self, fraction, text):
        self.nmap_progress.set_fraction(fraction)
        self.nmap_progress.set_text(text)

    def update_nmap_column_view(self, results):
        logging.debug("update_nmap_column_view called with results:")
        logging.debug(results)

        if results:
            logging.debug("Results are not empty, creating ListStore")
            list_store = Gio.ListStore.new(NmapItem)
            for key, value in results.items():
                logging.debug(f"Adding target: {key}, output: {value}")
                list_store.append(NmapItem(key=key, value=value))

            logging.debug("Setting ListStore as model for ColumnView")
            selection_model = Gtk.SingleSelection.new(list_store)
            self.scan_column_view.set_model(selection_model)

            logging.debug("Clearing existing columns")
            for column in self.scan_column_view.get_columns():
                if column:
                    self.scan_column_view.remove_column(column)

            logging.debug("Creating and adding columns")
            def create_factory(attr_name):
                factory = Gtk.SignalListItemFactory()
                factory.connect("setup", lambda factory, list_item: list_item.set_child(Gtk.Label(xalign=0)))
                factory.connect("bind", lambda factory, list_item: list_item.get_child().set_text(getattr(list_item.get_item(), attr_name)))
                return factory

            target_factory = create_factory('key')
            output_factory = create_factory('value')

            target_column = Gtk.ColumnViewColumn.new("Target")
            output_column = Gtk.ColumnViewColumn.new("Output")

            target_column.set_factory(target_factory)
            output_column.set_factory(output_factory)

            self.scan_column_view.append_column(target_column)
            self.scan_column_view.append_column(output_column)

            logging.debug("Results successfully displayed in ColumnView.")
        else:
            logging.debug("No results found, setting column view model to None")
            self.scan_column_view.set_model(None)

    def setup_nmap_factory(self, factory, list_item):
        text_view = Gtk.TextView()
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        list_item.set_child(text_view)
        list_item.text_view = text_view  # Store the text view in the list item

    def bind_nmap_target_factory(self, list_item, item):
        buffer = list_item.text_view.get_buffer()
        buffer.set_text(item.key)
        logging.debug(f"bind_nmap_target_factory: item.key = {item.key}")

    def bind_nmap_factory(self, list_item, item):
        buffer = list_item.text_view.get_buffer()
        text = "\n".join(item.value)
        buffer.set_text(text)
        logging.debug(f"bind_nmap_factory: item.value = {item.value}")
