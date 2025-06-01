import logging
from typing import Optional  # For type hinting
import nmap  # For nmap.PortScannerError
import gi

# GTK version requirements must be called before importing from gi.repository
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')

# Now import GTK libraries and other dependencies
# pylint: disable=wrong-import-position
from gi.repository import Adw, Gio, GLib, GObject, Gtk, GtkSource
# pylint: disable=wrong-import-position
from .constants import APP_ID, RESOURCE_PREFIX
# pylint: disable=wrong-import-position
from .nmap_scanner import NmapScanner, ScanStatus
# pylint: disable=wrong-import-position
from .style_utils import apply_source_style_scheme
# pylint: disable=wrong-import-position
from .utils import create_source_view

# Configure logger for the module - AFTER all imports
logger = logging.getLogger(__name__)


class NmapItem(GObject.Object):
    # key = GObject.Property(type=str) # Direct attribute access is used.
    # value = GObject.Property(type=str) # No need for GObject.Property if not using bindings.
    key: str
    value: str

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
        logger.info("Initializing NmapPage...")
        self.results_by_host = {}  # Stores YAML results string per host
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)
        self.scanner = NmapScanner()

        self.source_view, self.source_buffer = create_source_view(language_name='yaml')
        if self.nmap_results_scrolled_window is None:
            logger.critical("NmapPage: Gtk.Template.Child 'nmap_results_scrolled_window' not found. UI will be broken.")
        else:
            self.nmap_results_scrolled_window.set_child(self.source_view)

        self.settings = Gio.Settings.new(APP_ID)
        self._apply_source_view_style()  # Initial style application
        self.settings.connect(
            "changed::source-style-scheme",
            self._on_source_style_scheme_setting_changed
        )

        self._init_page_ui()
        self._connect_signals()
        logger.info("NmapPage initialized.")

    def __del__(self):
        # NmapScanner's __del__ should handle executor shutdown.
        # Explicitly deleting self.scanner here might be redundant.
        # Consider an explicit shutdown method if needed for clarity or specific timing.
        if hasattr(self, 'scanner') and self.scanner:
            # If NmapScanner needs explicit cleanup not handled by its __del__, call it here.
            # e.g., self.scanner.shutdown()
            pass  # For now, relying on NmapScanner.__del__

    def _on_source_style_scheme_setting_changed(self, _settings, key):
        """Handle changes to the source-style-scheme setting."""
        logger.debug("NmapPage: '%s' setting changed, applying new source view style.", key)
        self._apply_source_view_style()

    def _apply_source_view_style(self):
        source_style_scheme = self.settings.get_string("source-style-scheme")
        logger.debug("Applying style scheme to Nmap results: %s", source_style_scheme)
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            self.source_buffer,
            source_style_scheme,
        )
        self.source_view.set_editable(False)

    def _init_page_ui(self):
        logger.debug("Initializing NmapPage UI components.")
        self.nmap_target_listbox.bind_model(
            self.nmap_target_listbox_store, self._create_target_listbox_row
        )
        # Initial visibility states are typically set in the UI file.
        self.error_banner.set_revealed(False)
        self.scan_spinner.set_spinning(False)
        self.scan_spinner.set_visible(False)
        self.status_row.set_subtitle("Idle")

    def _connect_signals(self):
        logger.debug("Connecting NmapPage signals.")
        self.nmap_target_entryrow.connect("apply", self._on_target_activate)
        self.nmap_target_listbox.connect("row-selected", self._on_target_selected)
        # error_banner dismiss is connected in UI template.

    def _on_target_activate(self, entry_row: Adw.EntryRow):  # entry_row is used
        target = entry_row.get_text().strip()
        self._clear_error()  # Clear previous errors

        if not self.scanner.validate_target_input(target):
            entry_row.add_css_class("error")
            self._display_error("Invalid target format. Please enter a valid IP, CIDR, or hostname.")
            return
        # No 'else' needed here due to early return (R1705)
        entry_row.remove_css_class("error")

        if not target:  # Should be caught by validation, but as a safeguard
            self._clear_results()
            return

        self.nmap_target_entryrow.set_sensitive(False)
        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Scanning {target}...")

        os_fingerprinting = self.nmap_fingerprint_switchrow.get_active()
        all_ports = self.nmap_all_ports_switchrow.get_active()

        selected_script_item = self.nmap_scripts_dropdown.get_selected_item()
        script_name = (
            selected_script_item.get_string()
            if isinstance(selected_script_item, Gtk.StringObject) and selected_script_item.get_string() != "None"
            else None
        )

        logger.info(
            "Submitting Nmap scan for target: %s, OS Fingerprint: %s, All Ports: %s, Script: %s",
            target, os_fingerprinting, all_ports, script_name
        )
        try:
            self.scanner.executor.submit(
                self._run_nmap_scan_task,
                target,
                os_fingerprinting,
                all_ports,
                script_name,
            )
        except Exception:  # Broad catch if submit itself fails (e.g., executor shutdown)
            logger.exception("Failed to submit Nmap scan task for target %s.", target)
            self._handle_scan_error(target, "Failed to start scan. Executor might be shut down.")
            self.nmap_target_entryrow.set_sensitive(True)  # Re-enable entry if submit fails

    def _run_nmap_scan_task(self, target, os_fingerprinting, all_ports, script_name):
        logger.info("Nmap scan task started for %s in executor thread.", target)
        try:
            nm = self.scanner.run_nmap_scan(target, os_fingerprinting, all_ports, script_name)
            GLib.idle_add(self._process_scan_results, nm, target)
        except nmap.PortScannerError as e:
            logger.error("Nmap PortScannerError for %s: %s", target, e, exc_info=True)
            GLib.idle_add(self._handle_scan_error, target, f"Nmap engine error: {e}")
        except Exception:  # General fallback for unexpected issues within the task
            logger.exception("Unexpected exception in Nmap scan task for %s.", target)
            GLib.idle_add(self._handle_scan_error, target, "Scan failed unexpectedly (see logs).")
        finally:
            # Ensure UI elements that depend on scan completion are updated in the main thread
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)
            logger.info("Nmap scan task finished for %s.", target)

    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str):
        logger.info("Processing Nmap scan results for %s.", original_target)
        hosts_found = nm.all_hosts()
        if not hosts_found:
            logger.warning("No hosts found in Nmap results for target %s.", original_target)
            msg = f"Scan complete for {original_target}. No hosts found or responsive."
            self._set_scan_status(ScanStatus.COMPLETE, msg)
            self._display_error(f"No hosts found or responsive for target: {original_target}")
            self.targets_group.set_revealed(False)
            self.results_group.set_revealed(False)
            return

        results_yaml_map = self.scanner.convert_results_to_yaml(nm)
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        msg = f"Scan complete for {original_target}. {len(hosts_found)} host(s) found."
        self._set_scan_status(ScanStatus.COMPLETE, msg)

    def _handle_scan_error(self, target: str, error_message: str):
        logger.error("Handling scan error for target %s: %s", target, error_message)
        self._display_error(f"Error scanning {target}: {error_message}")
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")
        # Optionally, add to list to show the attempt, though it might be confusing if it's an error.
        # For now, we only populate the list with successful scans that found hosts.

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]):
        if row is None:
            self.source_buffer.set_text("")
            # self.results_group.set_revealed(False)  # Optionally hide results if no row selected
            return

        child_widget = row.get_child()
        if child_widget:
            item_obj = child_widget.get_data("NmapItem")
            if isinstance(item_obj, NmapItem):
                selected_target_key = item_obj.key
                logger.debug("Target selected: %s", selected_target_key)

                result_yaml = self.results_by_host.get(
                    selected_target_key,
                    f"# No results found for {selected_target_key}"
                )
                self.source_buffer.set_text(result_yaml)
                self._refresh_source_view()
                self.results_group.set_revealed(True)
                return  # Success case

        logger.warning("Could not retrieve NmapItem from selected row or row child.")
        self.source_buffer.set_text("")
        # self.results_group.set_revealed(False)  # Optionally hide if data is bad

    def _refresh_source_view(self):
        if self.source_view:
            self.source_view.queue_draw()

    def _update_results_view(self, hosts: list, results_map: dict):
        logger.info("Updating Nmap results view for hosts: %s", hosts)
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()

        if not hosts:
            self.targets_group.set_revealed(False)
            self.results_group.set_revealed(False)
            self.source_buffer.set_text("# No hosts found in this scan.")
            return

        for host_key in hosts:
            yaml_data = results_map.get(host_key, f"# No YAML data for {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data

        self.targets_group.set_revealed(True)

        if self.nmap_target_listbox_store.get_n_items() > 0:
            self.nmap_target_listbox.select_row(self.nmap_target_listbox.get_row_at_index(0))
        else:
            self.results_group.set_revealed(False)
            self.source_buffer.set_text("")

    def _set_scan_status(self, status_type: ScanStatus, message: str):
        logger.info("Setting Nmap scan status: %s - %s", status_type.name, message)
        GLib.idle_add(self._update_status_ui, status_type, message)

    def _update_status_ui(self, status_type: ScanStatus, message: str):
        self.status_row.set_subtitle(message)
        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True)
            self.scan_spinner.start()
            self.status_row.set_title("Scanning...")
        else:  # COMPLETE, FAILED, IDLE
            self.scan_spinner.stop()
            self.scan_spinner.set_visible(False)
            if status_type == ScanStatus.COMPLETE:
                self.status_row.set_title("Scan Complete")
            elif status_type == ScanStatus.FAILED:
                self.status_row.set_title("Scan Failed")
            else:  # IDLE
                self.status_row.set_title("Scan Status")  # Reset title

    def _clear_results(self):
        logger.info("Clearing Nmap results.")
        self.nmap_target_listbox_store.remove_all()
        self.source_buffer.set_text("")
        self.results_by_host.clear()

        self.targets_group.set_revealed(False)
        self.results_group.set_revealed(False)
        self.error_banner.set_revealed(False)
        self.nmap_target_entryrow.remove_css_class("error")
        self.nmap_target_entryrow.set_sensitive(True)
        self._set_scan_status(ScanStatus.IDLE, "Idle")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        self._clear_error()

    def _display_error(self, message: str):
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)

    def _clear_error(self):
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")

    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        # Using a simple Gtk.Label in a Gtk.ListBoxRow for clarity and data storage.
        simple_row = Gtk.ListBoxRow()
        simple_label = Gtk.Label(label=item.key, halign=Gtk.Align.START, margin_start=6, margin_end=6)
        simple_label.set_data("NmapItem", item)  # Store the actual NmapItem object
        simple_row.set_child(simple_label)
        return simple_row
