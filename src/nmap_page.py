import logging
from typing import Optional
import nmap

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gio, GLib, GObject, Gtk, GtkSource

from .constants import APP_ID, RESOURCE_PREFIX
from .nmap_scanner import NmapScanner, ScanStatus
from .style_utils import apply_source_style_scheme
from .utils import create_source_view

logger = logging.getLogger(__name__)


class NmapItem(GObject.Object):
    key: str
    value: str

    def __init__(self, key: str, value: str):
        super().__init__()
        self.key = key
        self.value = value


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/nmap_page.ui")
class NmapPage(Adw.PreferencesPage):
    __gtype_name__ = "NmapPage"

    nmap_target_entryrow = Gtk.Template.Child("nmap_target_entryrow")
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow")
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow")
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown")
    nmap_status_row = Gtk.Template.Child("nmap_status_row")
    scan_spinner = Gtk.Template.Child("scan_spinner")

    error_banner = Gtk.Template.Child("error_banner")
    nmap_warning_banner = Gtk.Template.Child("nmap_warning_banner")

    nmap_target_group = Gtk.Template.Child("nmap_target_group")
    nmap_target_listbox = Gtk.Template.Child("nmap_target_listbox")

    nmap_results_group = Gtk.Template.Child("nmap_results_group")
    nmap_results_adinw_scrolled_window = Gtk.Template.Child("nmap_results_adinw_scrolled_window")

    def __init__(self, **kwargs):
        logger.debug("NmapPage.__init__: Starting.")
        super().__init__(**kwargs)
        self.results_by_host = {}
        logger.debug(f"NmapPage.__init__: self.results_by_host initialized to: {self.results_by_host}")
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)
        logger.debug(f"NmapPage.__init__: self.nmap_target_listbox_store initialized: {self.nmap_target_listbox_store}")
        self.scanner = NmapScanner()
        logger.debug(f"NmapPage.__init__: self.scanner initialized: {self.scanner}")

        logger.debug("NmapPage.__init__: Calling create_source_view(language_name='yaml')")
        self.source_view, self.source_buffer = create_source_view(language_name='yaml')
        logger.debug(
            f"NmapPage.__init__: create_source_view returned source_view: {self.source_view}, "
            f"source_buffer: {self.source_buffer}"
        )
        if self.source_buffer and self.source_buffer.get_language() is None:
            logger.warning("NmapPage.__init__: Language 'yaml' not found for results view. Will use plain text.")
        if self.nmap_results_adinw_scrolled_window is None:
            logger.critical("NmapPage.__init__: Gtk.Template.Child 'nmap_results_adinw_scrolled_window' not found. UI will be broken.")
        else:
            self.nmap_results_adinw_scrolled_window.set_child(self.source_view)
            logger.debug("NmapPage.__init__: nmap_results_adinw_scrolled_window child set to source_view.")

        self.settings = Gio.Settings.new(APP_ID)
        logger.debug(f"NmapPage.__init__: self.settings initialized: {self.settings}")
        self._apply_source_view_style()
        logger.debug("NmapPage.__init__: Before self.settings.connect('changed::source-style-scheme')")
        self.settings.connect(
            "changed::source-style-scheme",
            self._on_source_style_scheme_setting_changed
        )
        logger.debug("NmapPage.__init__: After self.settings.connect('changed::source-style-scheme')")

        self._init_page_ui()
        self._connect_signals()
        logger.debug("NmapPage.__init__: Finished.")

    def __del__(self):
        if hasattr(self, 'scanner') and self.scanner:
            pass

    def _on_source_style_scheme_setting_changed(self, _settings, key):
        """Handle changes to the source-style-scheme setting."""
        logger.debug("NmapPage: '%s' setting changed, applying new source view style.", key)
        self._apply_source_view_style()

    def _apply_source_view_style(self):
        logger.debug("NmapPage._apply_source_view_style: Starting.")
        source_style_scheme = self.settings.get_string("source-style-scheme")
        logger.debug(f"NmapPage._apply_source_view_style: Applying style scheme to Nmap results: {source_style_scheme}")
        logger.debug("NmapPage._apply_source_view_style: Before apply_source_style_scheme()")
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            self.source_buffer,
            source_style_scheme,
        )
        logger.debug("NmapPage._apply_source_view_style: After apply_source_style_scheme()")
        self.source_view.set_editable(False)
        logger.debug("NmapPage._apply_source_view_style: source_view editable set to False.")
        logger.debug("NmapPage._apply_source_view_style: Finished.")

    def _init_page_ui(self):
        logger.debug("NmapPage._init_page_ui: Starting.")
        if self.nmap_target_listbox is None:
            logger.critical(
                "NmapPage._init_page_ui: Gtk.Template.Child 'nmap_target_listbox' not found. "
                "This is likely due to the UI template failing to load, possibly because of the "
                "'AdwPreferencesGroup.revealed' issue in nmap_page.ui. UI will be broken."
            )
        else:
            logger.debug("NmapPage._init_page_ui: Before nmap_target_listbox.bind_model()")
            self.nmap_target_listbox.bind_model(
                self.nmap_target_listbox_store, self._create_target_listbox_row
            )
            logger.debug("NmapPage._init_page_ui: After nmap_target_listbox.bind_model()")

        self.error_banner.set_revealed(False)
        logger.debug("NmapPage._init_page_ui: error_banner revealed set to False.")

        self.scan_spinner.set_spinning(False)
        logger.debug("NmapPage._init_page_ui: scan_spinner spinning set to False.")
        self.scan_spinner.set_visible(False)
        logger.debug("NmapPage._init_page_ui: scan_spinner visible set to False.")
        self.nmap_status_row.set_subtitle("Idle")
        logger.debug("NmapPage._init_page_ui: nmap_status_row subtitle set to 'Idle'.")
        logger.debug("NmapPage._init_page_ui: Finished.")

    def _connect_signals(self):
        logger.debug("NmapPage._connect_signals: Connecting signals.")
        self.nmap_target_entryrow.connect("apply", self._on_target_activate)
        logger.debug("NmapPage._connect_signals: Connected 'apply' for nmap_target_entryrow.")
        self.nmap_target_listbox.connect("row-selected", self._on_target_selected)
        logger.debug("NmapPage._connect_signals: Connected 'row-selected' for nmap_target_listbox.")
        self.error_banner.connect("button-clicked", self._on_error_banner_dismiss)
        logger.debug("NmapPage._connect_signals: Connected 'button-clicked' for error_banner.")
        self.nmap_warning_banner.connect("button-clicked", self._on_nmap_warning_banner_dismiss)
        logger.debug("NmapPage._connect_signals: Connected 'button-clicked' for nmap_warning_banner.")
        logger.debug("NmapPage._connect_signals: Finished connecting signals.")

    def _on_nmap_warning_banner_dismiss(self, _banner: Adw.Banner, *_args):
        logger.debug("NmapPage._on_nmap_warning_banner_dismiss: Triggered.")
        self.nmap_warning_banner.set_revealed(False)
        logger.debug("NmapPage._on_nmap_warning_banner_dismiss: nmap_warning_banner revealed set to False.")

    def _on_target_activate(self, entry_row: Adw.EntryRow):
        logger.debug(f"NmapPage._on_target_activate: Triggered for entry_row: {entry_row}")
        target = entry_row.get_text().strip()
        logger.debug(f"NmapPage._on_target_activate: Target from entry_row: '{target}'")
        self._clear_error()

        is_valid_target = self.scanner.validate_target_input(target)
        logger.debug(f"NmapPage._on_target_activate: Target validation status for '{target}': {is_valid_target}")
        if not is_valid_target:
            entry_row.add_css_class("error")
            logger.debug("NmapPage._on_target_activate: Added 'error' css class to entry_row.")
            self._display_error("Invalid target format. Please enter a valid IP, CIDR, or hostname.")
            logger.debug("NmapPage._on_target_activate: Finished due to invalid target.")
            return
        entry_row.remove_css_class("error")
        logger.debug("NmapPage._on_target_activate: Removed 'error' css class from entry_row.")

        if not target:
            logger.debug("NmapPage._on_target_activate: Target is empty (safeguard). Calling _clear_results().")
            self._clear_results()
            logger.debug("NmapPage._on_target_activate: Finished due to empty target (safeguard).")
            return

        self.nmap_target_entryrow.set_sensitive(False)
        logger.debug("NmapPage._on_target_activate: nmap_target_entryrow sensitivity set to False.")
        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Scanning {target}...")

        os_fingerprinting = self.nmap_fingerprint_switchrow.get_active()
        all_ports = self.nmap_all_ports_switchrow.get_active()
        selected_script_item = self.nmap_scripts_dropdown.get_selected_item()
        script_name = (
            selected_script_item.get_string()
            if isinstance(selected_script_item, Gtk.StringObject) and selected_script_item.get_string() != "None"
            else None
        )
        logger.debug(
            f"NmapPage._on_target_activate: Scan parameters - OS Fingerprint: {os_fingerprinting}, "
            f"All Ports: {all_ports}, Script: {script_name}"
        )
        logger.info(
            "NmapPage._on_target_activate: Submitting Nmap scan for target: %s, OS Fingerprint: %s, "
            "All Ports: %s, Script: %s",
            target, os_fingerprinting, all_ports, script_name
        )
        try:
            logger.debug("NmapPage._on_target_activate: Before self.scanner.executor.submit()")
            self.scanner.executor.submit(
                self._run_nmap_scan_task,
                target,
                os_fingerprinting,
                all_ports,
                script_name,
            )
            logger.debug("NmapPage._on_target_activate: After self.scanner.executor.submit()")
        except Exception as e:
            logger.exception(f"NmapPage._on_target_activate: Failed to submit Nmap scan task for target {target}. Exception: {e}")
            self._handle_scan_error(target, "Failed to start scan. Executor might be shut down.")
            self.nmap_target_entryrow.set_sensitive(True)
            logger.debug("NmapPage._on_target_activate: nmap_target_entryrow sensitivity set to True due to submit failure.")
        logger.debug("NmapPage._on_target_activate: Finished.")

    def _run_nmap_scan_task(self, target, os_fingerprinting, all_ports, script_name):
        logger.info("NmapPage._run_nmap_scan_task: Nmap scan task started for %s in executor thread.", target)
        try:
            logger.debug(
                f"NmapPage._run_nmap_scan_task: Before self.scanner.run_nmap_scan(target='{target}', "
                f"os_fingerprinting={os_fingerprinting}, all_ports={all_ports}, script_name='{script_name}')"
            )
            nm = self.scanner.run_nmap_scan(target, os_fingerprinting, all_ports, script_name)
            logger.debug(f"NmapPage._run_nmap_scan_task: After self.scanner.run_nmap_scan(), result nm: {type(nm)}")
            logger.debug("NmapPage._run_nmap_scan_task: Before GLib.idle_add(self._process_scan_results, ...)")
            GLib.idle_add(self._process_scan_results, nm, target)
            logger.debug("NmapPage._run_nmap_scan_task: After GLib.idle_add(self._process_scan_results, ...)")
        except nmap.PortScannerError as e:
            logger.error(f"NmapPage._run_nmap_scan_task: Nmap PortScannerError for {target}: {e}", exc_info=True)
            logger.debug("NmapPage._run_nmap_scan_task: Before GLib.idle_add(self._handle_scan_error, ...)")
            GLib.idle_add(self._handle_scan_error, target, f"Nmap engine error: {e}")
            logger.debug("NmapPage._run_nmap_scan_task: After GLib.idle_add(self._handle_scan_error, ...)")
        except Exception as e:
            logger.exception(f"NmapPage._run_nmap_scan_task: Unexpected exception in Nmap scan task for {target}. Exception: {e}")
            logger.debug("NmapPage._run_nmap_scan_task: Before GLib.idle_add(self._handle_scan_error, ...)")
            GLib.idle_add(self._handle_scan_error, target, "Scan failed unexpectedly (see logs).")
            logger.debug("NmapPage._run_nmap_scan_task: After GLib.idle_add(self._handle_scan_error, ...)")
        finally:
            logger.debug("NmapPage._run_nmap_scan_task: In finally block, before GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)")
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)
            logger.debug("NmapPage._run_nmap_scan_task: After GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)")
            logger.info("NmapPage._run_nmap_scan_task: Nmap scan task finished for %s.", target)
        logger.debug(f"NmapPage._run_nmap_scan_task: Finished for target {target}.")

    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str):
        logger.debug(f"NmapPage._process_scan_results: Starting for original_target: '{original_target}', nm object: {type(nm)}")
        hosts_found = nm.all_hosts()
        logger.debug(f"NmapPage._process_scan_results: nm.all_hosts() returned: {hosts_found}")
        if not hosts_found:
            logger.warning(f"NmapPage._process_scan_results: No hosts found in Nmap results for target {original_target}.")
            msg = f"Scan complete for {original_target}. No hosts found or responsive."
            self._set_scan_status(ScanStatus.COMPLETE, msg)
            self._display_error(f"No hosts found or responsive for target: {original_target}")
            self.nmap_target_group.set_revealed(False)
            logger.debug("NmapPage._process_scan_results: nmap_target_group revealed set to False.")
            self.nmap_results_group.set_revealed(False)
            logger.debug("NmapPage._process_scan_results: nmap_results_group revealed set to False.")
            logger.debug(f"NmapPage._process_scan_results: Finished for '{original_target}' - no hosts found.")
            return

        results_yaml_map = self.scanner.convert_results_to_yaml(nm)
        logger.debug(f"NmapPage._process_scan_results: Converted results to YAML map (keys: {list(results_yaml_map.keys()) if results_yaml_map else 'None'})")
        logger.debug("NmapPage._process_scan_results: Before GLib.idle_add(self._update_results_view, ...)")
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        logger.debug("NmapPage._process_scan_results: After GLib.idle_add(self._update_results_view, ...)")
        msg = f"Scan complete for {original_target}. {len(hosts_found)} host(s) found."
        self._set_scan_status(ScanStatus.COMPLETE, msg)
        logger.debug(f"NmapPage._process_scan_results: Finished for '{original_target}'.")

    def _handle_scan_error(self, target: str, error_message: str):
        logger.debug(f"NmapPage._handle_scan_error: Starting for target '{target}', error_message: '{error_message}'")
        self._display_error(f"Error scanning {target}: {error_message}")
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")
        logger.debug(f"NmapPage._handle_scan_error: Finished for target '{target}'.")

    def _on_target_selected(self, _listbox: Adw.ListBox, row: Optional[Adw.ActionRow]):
        logger.debug(f"NmapPage._on_target_selected: Triggered with listbox: {_listbox}, row: {row}")
        if row is None:
            logger.debug("NmapPage._on_target_selected: Row is None, clearing source_buffer.")
            self.source_buffer.set_text("")
            logger.debug("NmapPage._on_target_selected: Finished due to None row.")
            return

        item_obj = row.get_data("NmapItem")
        logger.debug(f"NmapPage._on_target_selected: Retrieved item_obj from row data: {item_obj} (type: {type(item_obj)})")
        if isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            logger.debug(f"NmapPage._on_target_selected: Target selected: {selected_target_key}")

            result_yaml = self.results_by_host.get(
                selected_target_key,
                f"# No results found for {selected_target_key}"
            )
            logger.debug(f"NmapPage._on_target_selected: YAML for target '{selected_target_key}' (first 100 chars): '{result_yaml[:100]}...'")
            logger.debug("NmapPage._on_target_selected: Before self.source_buffer.set_text()")
            self.source_buffer.set_text(result_yaml)
            logger.debug("NmapPage._on_target_selected: After self.source_buffer.set_text()")
            self._refresh_source_view()
            self.nmap_results_group.set_revealed(True)
            logger.debug("NmapPage._on_target_selected: nmap_results_group revealed set to True.")
            logger.debug(f"NmapPage._on_target_selected: Finished for target '{selected_target_key}'.")
            return

        logger.warning("NmapPage._on_target_selected: Could not retrieve NmapItem from selected row.")
        logger.debug("NmapPage._on_target_selected: Before self.source_buffer.set_text('') (due to bad item).")
        self.source_buffer.set_text("")
        logger.debug("NmapPage._on_target_selected: After self.source_buffer.set_text('') (due to bad item).")
        logger.debug("NmapPage._on_target_selected: Finished with bad item.")

    def _refresh_source_view(self):
        logger.debug("NmapPage._refresh_source_view: Called.")
        if self.source_view:
            logger.debug("NmapPage._refresh_source_view: self.source_view exists, calling queue_draw().")
            self.source_view.queue_draw()
        else:
            logger.debug("NmapPage._refresh_source_view: self.source_view is None, not calling queue_draw().")
        logger.debug("NmapPage._refresh_source_view: Finished.")

    def _update_results_view(self, hosts: list, results_map: dict):
        logger.debug(
            f"NmapPage._update_results_view: Starting for hosts: {hosts}, "
            f"results_map keys: {list(results_map.keys()) if results_map else 'None'}"
        )
        logger.debug("NmapPage._update_results_view: Before nmap_target_listbox_store.remove_all()")
        self.nmap_target_listbox_store.remove_all()
        logger.debug("NmapPage._update_results_view: After nmap_target_listbox_store.remove_all()")
        logger.debug("NmapPage._update_results_view: Before results_by_host.clear()")
        self.results_by_host.clear()
        logger.debug("NmapPage._update_results_view: After results_by_host.clear()")

        if not hosts:
            logger.debug("NmapPage._update_results_view: No hosts provided.")
            self.nmap_target_group.set_revealed(False)
            logger.debug("NmapPage._update_results_view: nmap_target_group revealed set to False.")
            self.nmap_results_group.set_revealed(False)
            logger.debug("NmapPage._update_results_view: nmap_results_group revealed set to False.")
            logger.debug("NmapPage._update_results_view: Before source_buffer.set_text('# No hosts found...')")
            self.source_buffer.set_text("# No hosts found in this scan.")
            logger.debug("NmapPage._update_results_view: After source_buffer.set_text('# No hosts found...')")
            logger.debug("NmapPage._update_results_view: Finished (no hosts).")
            return

        logger.debug(f"NmapPage._update_results_view: Processing {len(hosts)} hosts.")
        for host_key in hosts:
            yaml_data = results_map.get(host_key, f"# No YAML data for {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data
        logger.debug("NmapPage._update_results_view: Finished processing and appending hosts.")

        self.nmap_target_group.set_revealed(True)
        logger.debug("NmapPage._update_results_view: nmap_target_group revealed set to True.")

        if self.nmap_target_listbox_store.get_n_items() > 0:
            logger.debug("NmapPage._update_results_view: Selecting first row in nmap_target_listbox.")
            self.nmap_target_listbox.select_row(self.nmap_target_listbox.get_row_at_index(0))
        else:
            logger.debug("NmapPage._update_results_view: No items in listbox_store, hiding results_group and clearing source_buffer.")
            self.nmap_results_group.set_revealed(False)
            self.source_buffer.set_text("")
        logger.debug("NmapPage._update_results_view: Finished.")

    def _set_scan_status(self, status_type: ScanStatus, message: str):
        logger.debug(f"NmapPage._set_scan_status: Setting Nmap scan status to {status_type.name} - '{message}'")
        logger.debug("NmapPage._set_scan_status: Before GLib.idle_add(self._update_status_ui, ...)")
        GLib.idle_add(self._update_status_ui, status_type, message)
        logger.debug("NmapPage._set_scan_status: After GLib.idle_add(self._update_status_ui, ...)")
        logger.debug("NmapPage._set_scan_status: Finished.")

    def _update_status_ui(self, status_type: ScanStatus, message: str):
        logger.debug(f"NmapPage._update_status_ui: Starting with status_type: {status_type.name}, message: '{message}'")
        self.nmap_status_row.set_subtitle(message)
        logger.debug(f"NmapPage._update_status_ui: nmap_status_row subtitle set to '{message}'.")
        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True)
            logger.debug("NmapPage._update_status_ui: scan_spinner visibility set to True.")
            self.scan_spinner.start()
            logger.debug("NmapPage._update_status_ui: scan_spinner started.")
            self.nmap_status_row.set_title("Scanning...")
            logger.debug("NmapPage._update_status_ui: nmap_status_row title set to 'Scanning...'.")
        else:
            self.scan_spinner.stop()
            logger.debug("NmapPage._update_status_ui: scan_spinner stopped.")
            self.scan_spinner.set_visible(False)
            logger.debug("NmapPage._update_status_ui: scan_spinner visibility set to False.")
            if status_type == ScanStatus.COMPLETE:
                self.nmap_status_row.set_title("Scan Complete")
                logger.debug("NmapPage._update_status_ui: nmap_status_row title set to 'Scan Complete'.")
            elif status_type == ScanStatus.FAILED:
                self.nmap_status_row.set_title("Scan Failed")
                logger.debug("NmapPage._update_status_ui: nmap_status_row title set to 'Scan Failed'.")
            else:
                self.nmap_status_row.set_title("Scan Status")
                logger.debug("NmapPage._update_status_ui: nmap_status_row title set to 'Scan Status'.")
        logger.debug("NmapPage._update_status_ui: Finished.")

    def _clear_results(self):
        logger.debug("NmapPage._clear_results: Starting.")
        logger.debug("NmapPage._clear_results: Before nmap_target_listbox_store.remove_all()")
        self.nmap_target_listbox_store.remove_all()
        logger.debug("NmapPage._clear_results: After nmap_target_listbox_store.remove_all()")
        logger.debug("NmapPage._clear_results: Before source_buffer.set_text('')")
        self.source_buffer.set_text("")
        logger.debug("NmapPage._clear_results: After source_buffer.set_text('')")
        logger.debug("NmapPage._clear_results: Before results_by_host.clear()")
        self.results_by_host.clear()
        logger.debug("NmapPage._clear_results: After results_by_host.clear()")

        self.nmap_target_group.set_revealed(False)
        logger.debug("NmapPage._clear_results: nmap_target_group revealed set to False.")
        self.nmap_results_group.set_revealed(False)
        logger.debug("NmapPage._clear_results: nmap_results_group revealed set to False.")
        self.error_banner.set_revealed(False)
        logger.debug("NmapPage._clear_results: error_banner revealed set to False.")
        self.nmap_target_entryrow.remove_css_class("error")
        logger.debug("NmapPage._clear_results: 'error' css class removed from nmap_target_entryrow.")
        self.nmap_target_entryrow.set_sensitive(True)
        logger.debug("NmapPage._clear_results: nmap_target_entryrow sensitivity set to True.")
        self._set_scan_status(ScanStatus.IDLE, "Idle")
        logger.debug("NmapPage._clear_results: Finished.")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        logger.debug(f"NmapPage._on_error_banner_dismiss: Triggered for banner: {_banner}")
        logger.debug("NmapPage._on_error_banner_dismiss: Before self._clear_error()")
        self._clear_error()
        logger.debug("NmapPage._on_error_banner_dismiss: After self._clear_error()")
        logger.debug("NmapPage._on_error_banner_dismiss: Finished.")

    def _display_error(self, message: str):
        logger.debug(f"NmapPage._display_error: Called with message: '{message}'")
        self.error_banner.set_title(message)
        logger.debug("NmapPage._display_error: error_banner title set.")
        self.error_banner.set_revealed(True)
        logger.debug("NmapPage._display_error: error_banner revealed set to True.")
        logger.debug("NmapPage._display_error: Finished.")

    def _clear_error(self):
        logger.debug("NmapPage._clear_error: Called.")
        self.error_banner.set_revealed(False)
        logger.debug("NmapPage._clear_error: error_banner revealed set to False.")
        self.error_banner.set_title("")
        logger.debug("NmapPage._clear_error: error_banner title set to empty string.")
        logger.debug("NmapPage._clear_error: Finished.")

    def _create_target_listbox_row(self, item: NmapItem) -> Adw.ActionRow:
        logger.debug(f"NmapPage._create_target_listbox_row: Creating ActionRow for item with key: '{item.key}'")
        simple_row = Adw.ActionRow(title=item.key)
        logger.debug(f"NmapPage._create_target_listbox_row: Adw.ActionRow created: {simple_row} with title '{item.key}'")
        simple_row.set_data("NmapItem", item)
        logger.debug(f"NmapPage._create_target_listbox_row: Stored NmapItem ({item}) as data on ActionRow.")
        logger.debug(f"NmapPage._create_target_listbox_row: Returning row: {simple_row}")
        return simple_row
