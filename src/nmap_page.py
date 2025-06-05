import logging
import nmap
import re
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gio, GLib, GObject, Gtk, GtkSource

from .constants import APP_ID, RESOURCE_PREFIX
from .nmap_scanner import NmapScanner, ScanStatus
from .style_utils import apply_source_style_scheme
from .utils import create_source_view


class NmapItem(GObject.Object):
    """A simple GObject to hold key-value pairs for Nmap results display."""
    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


class NmapTargetRow(Gtk.ListBoxRow):
    nmap_item = GObject.Property(type=NmapItem)

    def __init__(self, nmap_item: NmapItem, **kwargs):
        super().__init__(**kwargs)
        self.nmap_item = nmap_item
        label = Gtk.Label(label=nmap_item.key, halign=Gtk.Align.START, margin_start=6, margin_end=6)
        self.set_child(label)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/nmap_page.ui")
class NmapPage(Adw.PreferencesPage):
    __gtype_name__ = "NmapPage"

    # Scan Parameters Group
    nmap_target_entryrow = Gtk.Template.Child("nmap_target_entryrow")
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow")
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow")
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown")
    nmap_service_version_switchrow = Gtk.Template.Child("nmap_service_version_switchrow")
    nmap_no_ping_switchrow = Gtk.Template.Child("nmap_no_ping_switchrow")
    nmap_timing_template_comborow = Gtk.Template.Child("nmap_timing_template_comborow")
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
        # error_banner dismiss is connected in UI template

    def _on_target_activate(self, entry_row: Adw.EntryRow):
        target = entry_row.get_text().strip()
        self._clear_error()

        if not self.scanner.validate_target_input(target):
            entry_row.add_css_class("error")
            self._display_error("Invalid target format. Please enter a valid IP, CIDR, or hostname.")
            return
        else:
            entry_row.remove_css_class("error")

        if not target:
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

        service_version_detection = self.nmap_service_version_switchrow.get_active()
        no_ping_scan = self.nmap_no_ping_switchrow.get_active()
        selected_timing_item = self.nmap_timing_template_comborow.get_selected_item()
        timing_template_str = selected_timing_item.get_string() # e.g., "Normal (T3)"
        # Extract the T-number (e.g., "T3") or just the number
        timing_match = re.search(r"\(T([0-5])\)", timing_template_str)
        timing_template = f"T{timing_match.group(1)}" if timing_match else "T3" # Default to T3 if parsing fails

        logging.info(
            "Submitting Nmap scan for target: %s, OS Fingerprint: %s, All Ports: %s, Script: %s, "
            "Service Version Detection: %s, No Ping: %s, Timing: %s",
            target, os_fingerprinting, all_ports, script_name,
            service_version_detection, no_ping_scan, timing_template
        )
        self.scanner.executor.submit(
            self._run_nmap_scan_task,
            target,
            os_fingerprinting,
            all_ports,
            script_name,
            service_version_detection, # new
            no_ping_scan,              # new
            timing_template            # new
        )

    def _run_nmap_scan_task(self, target, os_fingerprinting, all_ports, script_name, service_version_detection, no_ping_scan, timing_template):
        logging.info("Nmap scan task started for %s in executor thread.", target)
        try:
            nm = self.scanner.run_nmap_scan(target, os_fingerprinting, all_ports, script_name, service_version_detection, no_ping_scan, timing_template)
            GLib.idle_add(self._process_scan_results, nm, target)
        except nmap.PortScannerError as e:
            logging.error("Nmap PortScannerError for %s: %s", target, e, exc_info=True)
            GLib.idle_add(self._handle_scan_error, target, "Nmap scan error: %s" % str(e))
        except Exception as e:
            logging.error(
                "Unexpected exception in Nmap scan task for %s (%s): %s",
                target, type(e).__name__, e, exc_info=True
            )
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
            self.targets_group.set_visible(False)
            self.results_group.set_visible(False)
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

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow):
        if row is None:
            self.source_buffer.set_text("")
            return

        if isinstance(row, NmapTargetRow):
            item_obj = row.nmap_item
        else:
            item_obj = None # Or handle error appropriately

        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            logging.debug("Target selected: %s", selected_target_key)

            result_yaml = self.results_by_host.get(selected_target_key, f"# No results found for {selected_target_key}")
            self.source_buffer.set_text(result_yaml)
            self._refresh_source_view()
            self.results_group.set_visible(True)
        else:
            logging.warning("Could not retrieve NmapItem from selected row.")
            self.source_buffer.set_text("")

    def _refresh_source_view(self):
        if self.source_view:
            self.source_view.queue_draw()

    def _update_results_view(self, hosts: list, results_map: dict):
        logging.info("Updating Nmap results view for hosts: %s", hosts)
        self.nmap_target_listbox_store.remove_all()  # Clear previous targets
        self.results_by_host.clear()  # Clear previous results mapping

        if not hosts:
            self.targets_group.set_visible(False)
            self.results_group.set_visible(False)
            self.source_buffer.set_text("# No hosts found in this scan.")
            return

        for host_key in hosts:
            yaml_data = results_map.get(host_key, f"# No YAML data for {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data

        self.targets_group.set_visible(True)

        if self.nmap_target_listbox_store.get_n_items() > 0:
            self.nmap_target_listbox.select_row(self.nmap_target_listbox.get_row_at_index(0))
        else:
            self.results_group.set_visible(False)
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

        self.targets_group.set_visible(False)
        self.results_group.set_visible(False)
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
        return NmapTargetRow(nmap_item=item)
