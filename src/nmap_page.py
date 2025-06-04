import logging
# import functools # For functools.partial with GLib.idle_add - Removed unused import
import logging # Added missing import, used throughout the file

import nmap
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gio, GLib, GObject, Gtk, GtkSource

from .constants import APP_ID, RESOURCE_PREFIX # Sorted
from .nmap_scanner import NmapScanner, ScanStatus
from .style_utils import apply_source_style_scheme # Keep for source view
from .utils import create_source_view


# pylint: disable=too-few-public-methods
class NmapItem(GObject.Object):
    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/nmap_page.ui")
class NmapPage(Adw.PreferencesPage):
    __gtype_name__ = "NmapPage"

    # Scan Parameters Group
    nmap_target_entryrow = Gtk.Template.Child("nmap_target_entryrow")
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow")
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow")
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown")
    status_row = Gtk.Template.Child("status_row")  # AdwActionRow for status
    scan_spinner = Gtk.Template.Child("scan_spinner")  # GtkSpinner within status_row

    # Error Banner
    error_banner = Gtk.Template.Child("error_banner")  # AdwBanner for errors

    # Targets Group
    targets_group = Gtk.Template.Child("targets_group")  # AdwPreferencesGroup
    nmap_target_listbox = Gtk.Template.Child("nmap_target_listbox")
    # nmap_target_scrolled_window is part of targets_group in UI

    # Results Group
    results_group = Gtk.Template.Child("results_group")  # AdwPreferencesGroup
    nmap_results_scrolled_window = Gtk.Template.Child("nmap_results_scrolled_window")
    # warning_banner is static in UI, no Template.Child needed unless interactive

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logging.info("Initializing NmapPage...")
        self.results_by_host = {}  # Stores YAML results string per host
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)
        self.scanner = NmapScanner()

        self.source_view, self.source_buffer = create_source_view(language_name='yaml')
        self.nmap_results_scrolled_window.set_child(self.source_view)

        self.settings = Gio.Settings.new(APP_ID)  # Initialize self.settings
        self._apply_source_view_style()  # Initial style application
        self.settings.connect(
            "changed::source-style-scheme",
            self._on_source_style_scheme_setting_changed
        )  # Connect listener

        self._init_page_ui()
        self._connect_signals()
        logging.info("NmapPage initialized.")

    def __del__(self):
        if hasattr(self, 'scanner') and self.scanner:
            del self.scanner  # Ensure executor shutdown if NmapScanner has __del__

    def _on_source_style_scheme_setting_changed(self, _settings, key):
        """Handle changes to the source-style-scheme setting."""
        logging.debug("NmapPage: '%s' setting changed, applying new source view style.", key)
        self._apply_source_view_style()

    def _apply_source_view_style(self):
        # Assuming APP_ID is available or using a hardcoded string for settings
        # settings = Gio.Settings.new(APP_ID)  # Use imported APP_ID - Now uses self.settings
        source_style_scheme = self.settings.get_string("source-style-scheme")
        logging.debug("Applying style scheme to Nmap results: %s", source_style_scheme)
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            self.source_buffer,
            source_style_scheme,
        )
        self.source_view.set_editable(False)

    def _init_page_ui(self):
        logging.debug("Initializing NmapPage UI components.")
        self.nmap_target_listbox.bind_model(
            self.nmap_target_listbox_store, self._create_target_listbox_row
        )
        # Initial visibility states
        self.targets_group.set_visible(False)
        self.results_group.set_visible(False)
        self.error_banner.set_revealed(False)
        self.scan_spinner.set_spinning(False)
        self.scan_spinner.set_visible(False)
        self.status_row.set_subtitle("Idle")

    def _connect_signals(self):
        logging.debug("Connecting NmapPage signals.")
        self.nmap_target_entryrow.connect("apply", self._on_target_activate)
        self.nmap_target_listbox.connect("row-selected", self._on_target_selected)
        # Switches and dropdown can trigger a new scan if parameters change,
        # but typically Nmap scans are explicitly started. For now, they just update internal state.
        # self.nmap_fingerprint_switchrow.connect("notify::active", self._on_scan_param_changed)
        # self.nmap_all_ports_switchrow.connect("notify::active", self._on_scan_param_changed)
        # self.nmap_scripts_dropdown.connect("notify::selected-item", self._on_scan_param_changed)
        # error_banner dismiss is connected in UI template

    def _on_target_activate(self, entry_row: Adw.EntryRow):  # entry_row is used
        target = entry_row.get_text().strip()
        self._clear_error()  # Clear previous errors

        if not self.scanner.validate_target_input(target):
            entry_row.add_css_class("error")
            self._display_error("Invalid target format. Please enter a valid IP, CIDR, or hostname.")
            return
        else:
            entry_row.remove_css_class("error")

        if not target:  # Should be caught by validation, but as a safeguard
            self._clear_results()
            return

        self.nmap_target_entryrow.set_sensitive(False)
        self._set_scan_status(ScanStatus.IN_PROGRESS, "Scanning %s..." % target)

        os_fingerprinting = self.nmap_fingerprint_switchrow.get_active()
        all_ports = self.nmap_all_ports_switchrow.get_active()

        selected_script_item = self.nmap_scripts_dropdown.get_selected_item()
        script_name = (
            selected_script_item.get_string()
            if isinstance(selected_script_item, Gtk.StringObject) and selected_script_item.get_string() != "None"
            else None
        )

        logging.info(
            "Submitting Nmap scan for target: %s, OS Fingerprint: %s, All Ports: %s, Script: %s",
            target, os_fingerprinting, all_ports, script_name
        )
        self.scanner.executor.submit(
            self._run_nmap_scan_task,
            target,
            os_fingerprinting,
            all_ports,
            script_name,
        )

    def _run_nmap_scan_task(self, target, os_fingerprinting, all_ports, script_name):
        logging.info("Nmap scan task started for %s in executor thread.", target)
        try:
            nm = self.scanner.run_nmap_scan(target, os_fingerprinting, all_ports, script_name)
            GLib.idle_add(self._process_scan_results, nm, target)  # Pass target for context
        except nmap.PortScannerError as e: # More specific for nmap issues
            logging.error("Nmap PortScannerError for %s: %s", target, e, exc_info=True)
            GLib.idle_add(self._handle_scan_error, target, "Nmap scan error: %s" % str(e))
        except Exception as e: # General fallback
            logging.error("Unexpected exception in Nmap scan task for %s (%s): %s", target, type(e).__name__, e, exc_info=True)
            GLib.idle_add(self._handle_scan_error, target, "Scan failed unexpectedly: %s" % str(e))
        finally:
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)
            logging.info("Nmap scan task finished for %s.", target)

    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str):
        logging.info("Processing Nmap scan results for %s.", original_target)
        hosts_found = nm.all_hosts()
        if not hosts_found:
            logging.warning("No hosts found in Nmap results for target %s.", original_target)
            self._set_scan_status(
                ScanStatus.COMPLETE,
                "Scan complete for %s. No hosts found or responsive." % original_target
            )
            self._display_error("No hosts found or responsive for target: %s" % original_target)
            self.targets_group.set_revealed(False)
            self.results_group.set_revealed(False)
            return

        results_yaml_map = self.scanner.convert_results_to_yaml(nm)
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        self._set_scan_status(
            ScanStatus.COMPLETE,
            "Scan complete for %s. %d host(s) found." % (original_target, len(hosts_found))
        )

    def _handle_scan_error(self, target: str, error_message: str):
        logging.error("Handling scan error for target %s: %s", target, error_message)
        self._display_error("Error scanning %s: %s" % (target, error_message))
        self._set_scan_status(ScanStatus.FAILED, "Scan failed for %s" % target)
        # Optionally, still add to list to show the attempt
        # nmap_item = NmapItem(key=f"{target} (Error)", value=error_message)
        # self.nmap_target_listbox_store.append(nmap_item)
        # self.results_by_host[target] = error_message
        # self.targets_group.set_revealed(self.nmap_target_listbox_store.get_n_items() > 0)

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow): # row is used
        if row is None:
            self.source_buffer.set_text("")
            # self.results_group.set_revealed(False)  # Don't hide if just deselecting
            return

        item_obj = row.get_child().get_data("NmapItem")  # Assuming NmapItem is stored on label
        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            logging.debug("Target selected: %s", selected_target_key)

            result_yaml = self.results_by_host.get(selected_target_key, f"# No results found for {selected_target_key}")
            self.source_buffer.set_text(result_yaml)
            self._refresh_source_view()  # Ensure view updates if text was same
            self.results_group.set_revealed(True)  # Ensure results are visible when a target is selected
        else:
            logging.warning("Could not retrieve NmapItem from selected row.")
            self.source_buffer.set_text("")

    def _refresh_source_view(self):  # Keep this as it forces redraw
        if self.source_view:
            self.source_view.queue_draw()

    def _update_results_view(self, hosts: list, results_map: dict):
        logging.info("Updating Nmap results view for hosts: %s", hosts)
        self.nmap_target_listbox_store.remove_all()  # Clear previous targets
        self.results_by_host.clear()  # Clear previous results mapping

        if not hosts:
            self.targets_group.set_revealed(False)
            self.results_group.set_revealed(False)
            self.source_buffer.set_text("# No hosts found in this scan.")
            return

        for host_key in hosts:  # host_key is usually IP or hostname
            yaml_data = results_map.get(host_key, f"# No YAML data for {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)  # Store YAML in value for now
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data

        self.targets_group.set_revealed(True)

        if self.nmap_target_listbox_store.get_n_items() > 0:
            self.nmap_target_listbox.select_row(self.nmap_target_listbox.get_row_at_index(0))
            # This will trigger _on_target_selected and load the first result
        else:  # Should not happen if hosts list is not empty
            self.results_group.set_revealed(False)
            self.source_buffer.set_text("")

    def _set_scan_status(self, status_type: ScanStatus, message: str):
        logging.info("Setting Nmap scan status: %s - %s", status_type.name, message)
        GLib.idle_add(self._update_status_ui, status_type, message)

    def _update_status_ui(self, status_type: ScanStatus, message: str):
        self.status_row.set_subtitle(message)
        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True)
            self.scan_spinner.start()
            self.status_row.set_title("Scanning...")
        else:  # COMPLETE, FAILED, or other
            self.scan_spinner.stop()
            self.scan_spinner.set_visible(False)
            if status_type == ScanStatus.COMPLETE:
                self.status_row.set_title("Scan Complete")
            elif status_type == ScanStatus.FAILED:
                self.status_row.set_title("Scan Failed")
            else:  # IDLE or other
                self.status_row.set_title("Scan Status")  # Reset title

    def _clear_results(self):
        logging.info("Clearing Nmap results.")
        self.nmap_target_listbox_store.remove_all()
        self.source_buffer.set_text("")
        self.results_by_host.clear()

        self.targets_group.set_revealed(False)
        self.results_group.set_revealed(False)
        self.error_banner.set_revealed(False)
        self.nmap_target_entryrow.remove_css_class("error")
        self.nmap_target_entryrow.set_sensitive(True)
        self._set_scan_status(ScanStatus.IDLE, "Idle")


    # Removed @Gtk.Template.Callback() as it's a direct signal handler in UI
    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        self._clear_error()

    def _display_error(self, message: str):
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)

    def _clear_error(self):
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")


    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        # Using AdwActionRow for better styling in PreferencesPage context
        # row = Adw.ActionRow(title=item.key, activatable=True) # F841: local variable 'row' is assigned to but never used
        # Store the item itself on a widget in the row if needed for `_on_target_selected`
        # A common pattern is to use a simple Gtk.Label and store data on it,
        # but Adw.ActionRow itself can hold it if we don't need a custom child for display.
        # For simplicity, if Adw.ActionRow is directly used, we might need to iterate model in selection.
        # Let's try making a label and attaching data for now for easier porting.
        # label = Gtk.Label(label=item.key) # This label is not directly added to row if using ActionRow title
        # label.set_data("NmapItem", item) # Store NmapItem on the label

        # If Adw.ActionRow's title is sufficient, we might not need a separate child label.
        # However, to retrieve the NmapItem easily in _on_target_selected,
        # we need a way to get it from the row. Gtk.ListBoxRow.get_child() works if child is set.
        # Adw.ActionRow is complex. For now, let's use a simple Gtk.Label in a Gtk.ListBoxRow.

        simple_row = Gtk.ListBoxRow()
        simple_label = Gtk.Label(label=item.key, halign=Gtk.Align.START, margin_start=6, margin_end=6)
        simple_label.set_data("NmapItem", item)  # Store the actual NmapItem object
        simple_row.set_child(simple_label)
        return simple_row
