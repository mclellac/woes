# nmap_page.py
import logging
import nmap
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Gio, GLib, GObject, Gtk, GtkSource
from .constants import RESOURCE_PREFIX
from .nmap_scanner import NmapScanner, ScanStatus
from .style_utils import apply_source_style_scheme


logging.basicConfig(level=logging.WARN, format="%(asctime)s - %(levelname)s - %(message)s")

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

    nmap_target_entryrow = Gtk.Template.Child("nmap_target_entryrow")
    nmap_target_listbox = Gtk.Template.Child("nmap_target_listbox")
    nmap_results_scrolled_window = Gtk.Template.Child("nmap_results_scrolled_window")
    nmap_target_scrolled_window = Gtk.Template.Child("nmap_target_scrolled_window")
    nmap_target_frame = Gtk.Template.Child("nmap_target_frame")
    nmap_results_frame = Gtk.Template.Child("nmap_results_frame")
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow")
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow")
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown")
    nmap_spinner = Gtk.Template.Child("nmap_spinner")
    nmap_status = Gtk.Template.Child("nmap_status")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logging.debug("Initializing NmapPage...")
        self.results_by_host = {}
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)
        self.scanner = NmapScanner()
        self.source_buffer = self.init_source_buffer()
        self.source_view = self.init_source_view(self.source_buffer)
        self.apply_source_view_style()
        self.init_ui()



    def __del__(self):
        del self.scanner

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
        self.nmap_results_scrolled_window.set_child(source_view)
        return source_view

    def apply_source_view_style(self):
        settings = Gio.Settings.new("com.github.mclellac.WebOpsEvaluationSuite")
        source_style_scheme = settings.get_string("source-style-scheme")
        logging.debug(f"Applying style scheme: {source_style_scheme}")
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            self.source_buffer,
            source_style_scheme,
        )

    def set_visible(self, *widgets, visible: bool):
        for widget in widgets:
            if widget is not None:
                widget.set_visible(visible)
            else:
                logging.error("Attempted to set visibility of a None widget")

    def init_ui(self):
        logging.debug("Initializing UI components.")
        self.nmap_target_listbox.bind_model(self.nmap_target_listbox_store, self.create_listbox_row)
        self.connect_signals()
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

    def on_nmap_target_entryrow_activated(self, entryrow: Gtk.Widget):
        target = entryrow.get_text().strip()
        if not self.scanner.validate_target_input(target):
            entryrow.get_style_context().add_class("error")
            return
        else:
            entryrow.get_style_context().remove_class("error")

        if not target:
            GLib.idle_add(self.clear_results)
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)
            return

        GLib.idle_add(self.nmap_target_entryrow.set_sensitive, False)

        os_fingerprinting_enabled = self.nmap_fingerprint_switchrow.get_active()
        scan_all_ports_enabled = self.nmap_all_ports_switchrow.get_active()
        selected_script = self.get_selected_script()

        status_message = ScanStatus.IN_PROGRESS.value[1].format(target=target)
        self.set_scan_status(ScanStatus.IN_PROGRESS.value[0], status_message)

        self.scanner.executor.submit(
            self._run_nmap_scan_task, target, os_fingerprinting_enabled, scan_all_ports_enabled, selected_script
        )

    def get_selected_script(self):
        selected_item = self.nmap_scripts_dropdown.get_selected_item()
        return selected_item.get_string() if isinstance(selected_item, Gtk.StringObject) else None

    def _run_nmap_scan_task(self, target, os_fingerprinting_enabled, scan_all_ports_enabled, selected_script):
        try:
            nm = self.scanner.run_nmap_scan(target, os_fingerprinting_enabled, scan_all_ports_enabled, selected_script)
            self.process_scan_results(nm)
        except Exception as e:
            GLib.idle_add(self.handle_scan_error, target, str(e))
            GLib.idle_add(self.set_scan_status, ScanStatus.FAILED.value[0], "Scan failed unexpectedly")

    def process_scan_results(self, nm: nmap.PortScanner):
        logging.debug(f"Processing Nmap scan results for hosts: {nm.all_hosts()}")
        results = self.scanner.convert_results_to_yaml(nm)
        GLib.idle_add(self.update_nmap_results_view, (nm.all_hosts(), results))
        GLib.idle_add(self.set_scan_status, ScanStatus.COMPLETE.value[0], "Scan complete")
        GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)

    def handle_scan_error(self, target: str, error_message: str):
        nmap_item = NmapItem(key=target, value=error_message)
        self.nmap_target_listbox_store.append(nmap_item)
        self.results_by_host[target] = error_message
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
        logging.debug("on_nmap_target_listbox_row_selected called")
        if row is not None:
            child = row.get_child()
            if isinstance(child, Gtk.Label):
                selected_target = child.get_label()
                logging.debug(f"Row selected: {row.get_index()} - Target: {selected_target}")

                results = self.results_by_host.get(selected_target, "")
                logging.debug(f"Results for {selected_target}: {results[:200]}")

                if results:
                    self.source_buffer.set_text(results)
                    self.refresh_source_view()
                    logging.debug(f"Source view updated with results for {selected_target}")
                else:
                    logging.warning(f"No results found for {selected_target}")
            else:
                logging.error("Row child is not a Gtk.Label, cannot retrieve target")
        else:
            logging.debug("No row is currently selected.")
            self.source_buffer.set_text("")
            self.refresh_source_view()
            logging.debug("Cleared source view because no row is selected")

    def refresh_source_view(self):
        logging.debug("Entering refresh_source_view")
        if not self.source_view:
            logging.error("Source view is None; cannot refresh")
            return

        logging.debug(f"Source buffer text length: {len(self.source_buffer.get_text(self.source_buffer.get_start_iter(), self.source_buffer.get_end_iter(), False))}")
        self.source_view.queue_draw()

        parent = self.source_view.get_parent()
        if parent:
            logging.debug("Source view has a parent; queuing resize and redraw for the parent")
            parent.queue_resize()
            parent.queue_draw()
        else:
            logging.warning("Source view does not have a parent; skipping parent refresh")
        logging.debug("Exiting refresh_source_view")

    def update_nmap_results_view(self, args: tuple):
        logging.debug("update_nmap_results_view called")
        hosts, results = args
        self.nmap_target_listbox_store.remove_all()
        self.source_buffer.set_text("")

        self.results_by_host = {}
        for target in hosts:
            result_text = results.get(target, "No results available")
            logging.debug(f"Adding target: {target} with results: {result_text[:200]}")
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

        self.set_visible(
            self.source_view,
            self.nmap_target_listbox,
            self.nmap_results_frame,
            self.nmap_target_frame,
            self.nmap_target_scrolled_window,
            visible=True,
        )
        self.refresh_source_view()

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

    def on_nmap_fingerprint_switchrow_toggled(self, switch: Gtk.Switch, gparam: GObject.ParamSpec):
        self.os_fingerprinting_enabled = switch.get_active()

    def on_nmap_all_ports_switchrow_toggled(self, switch: Gtk.Switch, gparam: GObject.ParamSpec):
        self.scan_all_ports_enabled = switch.get_active()

    def on_nmap_scripts_dropdown_changed(self):
        self.selected_script = self.get_selected_script()

    def create_listbox_row(self, item: NmapItem, user_data: any = None) -> Gtk.ListBoxRow:
        label = Gtk.Label(label=item.key)
        row = Gtk.ListBoxRow()
        row.set_child(label)
        logging.debug(f"Created listbox row for item: {item.key}")
        return row

