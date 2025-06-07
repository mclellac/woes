import logging
import nmap
import re
import yaml
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
    nmap_apply_button = Gtk.Template.Child("nmap_apply_button")
    nmap_fingerprint_switchrow = Gtk.Template.Child("nmap_fingerprint_switchrow")
    nmap_all_ports_switchrow = Gtk.Template.Child("nmap_all_ports_switchrow")
    nmap_scripts_dropdown = Gtk.Template.Child("nmap_scripts_dropdown")
    nmap_service_version_switchrow = Gtk.Template.Child("nmap_service_version_switchrow")
    nmap_no_ping_switchrow = Gtk.Template.Child("nmap_no_ping_switchrow")
    nmap_timing_template_comborow = Gtk.Template.Child("nmap_timing_template_comborow")
    status_row = Gtk.Template.Child("status_row")
    scan_spinner = Gtk.Template.Child("scan_spinner")

    # Error Banner
    error_banner = Gtk.Template.Child("error_banner")

    # AdwOverlaySplitView and its children
    nmap_split_view = Gtk.Template.Child("nmap_split_view")
    nmap_host_listbox = Gtk.Template.Child("nmap_host_listbox")
    nmap_detail_box = Gtk.Template.Child("nmap_detail_box")
    nmap_detail_placeholder = Gtk.Template.Child("nmap_detail_placeholder")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logging.info("Initializing NmapPage...")
        self.results_by_host = {}
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)
        self.scanner = NmapScanner()

        self.settings = Gio.Settings.new(APP_ID)

        self._init_page_ui()
        self._connect_signals()
        logging.info("NmapPage initialized.")
        logging.debug("NmapPage __init__ completed.")

    def __del__(self):
        if hasattr(self, 'scanner') and self.scanner:
            del self.scanner

    def _on_source_style_scheme_setting_changed(self, _settings, key):
        """Handle changes to the source-style-scheme setting."""
        logging.debug("NmapPage: '%s' setting changed, applying new source view style.", key)
        self._apply_source_view_style()

    def _apply_source_view_style_to_buffer(self, buffer):
        source_style_scheme = self.settings.get_string("source-style-scheme")
        logging.debug("Applying style scheme to GtkSource.Buffer: %s", source_style_scheme)
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            buffer,
            source_style_scheme,
        )

    def _init_page_ui(self):
        logging.debug("Initializing NmapPage UI components.")
        self.nmap_host_listbox.bind_model(
            self.nmap_target_listbox_store, self._create_target_listbox_row
        )

        self.nmap_split_view.set_show_sidebar(True)
        self.nmap_detail_placeholder.set_visible(True)

        child = self.nmap_detail_box.get_first_child()
        while child and child != self.nmap_detail_placeholder:
            self.nmap_detail_box.remove(child)
            child = self.nmap_detail_box.get_first_child()

        self.error_banner.set_revealed(False)
        self.scan_spinner.set_spinning(False)
        self.scan_spinner.set_visible(False)
        self.status_row.set_subtitle("Idle")
        logging.debug("NmapPage _init_page_ui completed.")

    def _clear_dynamic_details(self):
        child = self.nmap_detail_box.get_first_child()
        while child:
            if child == self.nmap_detail_placeholder:
                child = child.get_next_sibling()
                continue
            current_child_to_remove = child
            child = child.get_next_sibling()
            self.nmap_detail_box.remove(current_child_to_remove)

    def _connect_signals(self):
        logging.debug("Connecting NmapPage signals.")
        self.nmap_target_entryrow.connect("entry-activated", self._on_target_activate)
        self.nmap_apply_button.connect("clicked", self._on_target_activate)
        self.nmap_host_listbox.connect("row-selected", self._on_target_selected)


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
        timing_template_str = selected_timing_item.get_string()
        timing_match = re.search(r"\(T([0-5])\)", timing_template_str)
        timing_template = f"T{timing_match.group(1)}" if timing_match else "T3"

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
            service_version_detection,
            no_ping_scan,
            timing_template
        )
        logging.debug(
            f"Scan task params: target={target}, os_fingerprint={os_fingerprinting}, "
            f"all_ports={all_ports}, script_name={script_name}, "
            f"service_version_detection={service_version_detection}, "
            f"no_ping_scan={no_ping_scan}, timing_template={timing_template}"
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
        hosts_found = nm.all_hosts()
        logging.debug(f"Processing results for {original_target}. nm.all_hosts(): {hosts_found}")
        if not hosts_found:
            logging.warning("No hosts found in Nmap results for target %s.", original_target)
            self._set_scan_status(
                ScanStatus.COMPLETE,
                "Scan complete for %s. No hosts found or responsive." % original_target
            )
            self._display_error("No hosts found or responsive for target: %s" % original_target)
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Responsive Hosts")
            self.nmap_detail_placeholder.set_description(f"The Nmap scan for '{original_target}' did not find any responsive hosts.")
            self.nmap_detail_placeholder.set_visible(True)
            return

        results_yaml_map = self.scanner.convert_results_to_yaml(nm)
        if results_yaml_map:
            logging.debug(f"results_yaml_map keys: {list(results_yaml_map.keys())}")
        else:
            logging.debug("results_yaml_map is empty.")
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
        self._clear_dynamic_details() # Clear previous host's details

        if row is None:
            self.nmap_detail_placeholder.set_title("No Host Selected")
            self.nmap_detail_placeholder.set_description("Select a host from the list to view details.")
            if not self.nmap_detail_placeholder.get_parent(): # Ensure placeholder is in the box
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return

        self.nmap_detail_placeholder.set_visible(False) # Hide placeholder when a row is selected

        if isinstance(row, NmapTargetRow):
            item_obj = row.nmap_item
        else:
            logging.warning("Selected row is not an NmapTargetRow instance.")
            item_obj = None

        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            logging.debug(f"Target selected: {selected_target_key}")

            try:
                host_data_dict = yaml.safe_load(item_obj.value)
                if not isinstance(host_data_dict, dict):
                    logging.warning(f"Parsed YAML for host {selected_target_key} is not a dictionary. Value: {item_obj.value[:100]}")
                    host_data_dict = {}
                logging.debug(f"Successfully parsed YAML for {selected_target_key}. Data keys: {list(host_data_dict.keys()) if host_data_dict else 'None'}")
            except yaml.YAMLError as e:
                logging.debug(f"YAML parsing failed for {selected_target_key}: {e}")
                logging.error(f"Error parsing YAML for host {selected_target_key}: {e}")
                error_label = Gtk.Label(label=f"Error: Could not parse scan results for {selected_target_key}.\n{e}")
                error_label.set_wrap(True)
                error_label.set_halign(Gtk.Align.START)
                self.nmap_detail_box.append(error_label)
                return

            self._add_host_details_expander(host_data_dict, selected_target_key)
            self._add_ports_expander(host_data_dict, selected_target_key)
            self._add_os_expander(host_data_dict, selected_target_key)
            self._add_raw_output_expander(item_obj.value, selected_target_key)

        else:
            logging.warning("Could not retrieve NmapItem from selected row or item_obj is None.")
            self.nmap_detail_placeholder.set_title("Error")
            self.nmap_detail_placeholder.set_description("Could not load details for the selected host.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)

    # def _refresh_source_view(self): # Removed, not using a single source_view anymore
    def _add_raw_output_expander(self, yaml_string: str, host_key: str):
        logging.debug(f"Adding raw output expander for {host_key}")
        expander = Adw.ExpanderRow(title=f"Raw Nmap Output (YAML) - {host_key}")
        expander.set_expanded(False)

        source_view, source_buffer = create_source_view(language_name='yaml')
        source_buffer.set_text(yaml_string, -1)
        self._apply_source_view_style_to_buffer(source_buffer)
        source_view.set_editable(False)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_child(source_view)
        scrolled_window.set_min_content_height(200)
        scrolled_window.set_max_content_height(400)
        scrolled_window.set_vexpand(True)

        expander.add_row(scrolled_window)
        self.nmap_detail_box.append(expander)

    def _add_host_details_expander(self, host_data: dict, host_key: str):
        logging.debug(f"Adding host details expander for {host_key}")
        expander = Adw.ExpanderRow(title=f"Host Information - {host_key}")
        expander.set_expanded(True)

        status_info = host_data.get('status', {})
        status_row = Adw.ActionRow(title="Status", subtitle=f"{status_info.get('state', 'N/A')} (Reason: {status_info.get('reason', 'N/A')})")
        expander.add_row(status_row)

        addresses_info = host_data.get('addresses', {})
        if addresses_info.get('ipv4'):
            ipv4_row = Adw.ActionRow(title="IPv4 Address", subtitle=addresses_info['ipv4'])
            expander.add_row(ipv4_row)
        if addresses_info.get('ipv6'):
            ipv6_row = Adw.ActionRow(title="IPv6 Address", subtitle=addresses_info['ipv6'])
            expander.add_row(ipv6_row)
        if addresses_info.get('mac'):
            mac_row = Adw.ActionRow(title="MAC Address", subtitle=addresses_info['mac'])
            expander.add_row(mac_row)

        hostnames_list = host_data.get('hostnames', [])
        if hostnames_list:
            for hn_entry in hostnames_list:
                hn_row = Adw.ActionRow(title=f"Hostname ({hn_entry.get('type', 'N/A')})", subtitle=hn_entry.get('name', 'N/A'))
                expander.add_row(hn_row)
        else:
            no_hn_row = Adw.ActionRow(title="Hostnames", subtitle="No hostnames reported")
            expander.add_row(no_hn_row)

        self.nmap_detail_box.append(expander)

    def _add_ports_expander(self, host_data: dict, host_key: str):
        logging.debug(f"Adding ports expander for {host_key}")
        expander = Adw.ExpanderRow(title=f"Network Ports - {host_key}")
        expander.set_expanded(True)

        ports_found = False
        for proto in ['tcp', 'udp', 'sctp', 'ip']:
            if proto_data := host_data.get(proto):
                logging.debug(f"Processing ports for proto {proto} in host {host_key}, data: {list(proto_data.keys()) if isinstance(proto_data, dict) else 'Not a dict'}")
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        ports_found = True
                        state = port_info.get('state', 'N/A')
                        name = port_info.get('name', '')
                        product = port_info.get('product', '')
                        version = port_info.get('version', '')
                        reason = port_info.get('reason', '')

                        title = f"Port {port_id}/{proto.upper()} ({state})"
                        subtitle_parts = [name, product, version]
                        subtitle = " ".join(filter(None, subtitle_parts))
                        if not subtitle:
                            subtitle = f"Reason: {reason}"
                        else:
                            subtitle += f" (Reason: {reason})"

                        row = Adw.ActionRow(title=title, subtitle=subtitle)
                        expander.add_row(row)

        if not ports_found:
            no_ports_row = Adw.ActionRow(title="Ports", subtitle="No open ports reported or port data available.")
            expander.add_row(no_ports_row)

        self.nmap_detail_box.append(expander)

    def _add_os_expander(self, host_data: dict, host_key: str):
        osmatch_data = host_data.get('osmatch', [])
        if not osmatch_data:
            logging.debug(f"No OS data for host {host_key}, skipping OS expander.")
            return

        logging.debug(f"Adding OS expander for {host_key}. OS match data: {osmatch_data}")
        expander = Adw.ExpanderRow(title=f"Operating System Detection - {host_key}")
        expander.set_expanded(True)

        os_details_added = False
        for match in osmatch_data:
            name = match.get('name', 'N/A')
            accuracy = match.get('accuracy', 'N/A')
            title = f"{name} (Accuracy: {accuracy}%)"

            osclass_details = []
            if 'osclass' in match and isinstance(match['osclass'], list):
                for os_class in match['osclass']:
                    if isinstance(os_class, dict):
                        vendor = os_class.get('vendor', 'N/A')
                        osfamily = os_class.get('osfamily', 'N/A')
                        osgen = os_class.get('osgen', 'N/A')
                        osclass_details.append(f"Type: {os_class.get('type', 'N/A')}, Vendor: {vendor}, Family: {osfamily}, Gen: {osgen}")
            elif 'osclass' in match and isinstance(match['osclass'], dict):
                os_class = match['osclass']
                vendor = os_class.get('vendor', 'N/A')
                osfamily = os_class.get('osfamily', 'N/A')
                osgen = os_class.get('osgen', 'N/A')
                osclass_details.append(f"Type: {os_class.get('type', 'N/A')}, Vendor: {vendor}, Family: {osfamily}, Gen: {osgen}")


            subtitle = "\n".join(osclass_details) if osclass_details else "No OS class details."

            row = Adw.ActionRow(title=title, subtitle=subtitle)
            if subtitle == "No OS class details.":
                 row.set_subtitle("")
            expander.add_row(row)
            os_details_added = True

        if not os_details_added:
            no_data_row = Adw.ActionRow(title="OS Detection", subtitle="No specific OS matches found.")
            expander.add_row(no_data_row)

        self.nmap_detail_box.append(expander)


    def _update_results_view(self, hosts: list, results_map: dict):
        logging.info("Updating Nmap results view for hosts: %s", hosts)
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()

        if not hosts:
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Found")
            self.nmap_detail_placeholder.set_description("The scan did not find any responsive hosts.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return

        for host_key in hosts:
            yaml_data = results_map.get(host_key, f"# No YAML data for {host_key}")
            nmap_item = NmapItem(key=host_key, value=yaml_data)
            self.nmap_target_listbox_store.append(nmap_item)
            self.results_by_host[host_key] = yaml_data

        if self.nmap_target_listbox_store.get_n_items() > 0:
            self.nmap_host_listbox.select_row(self.nmap_host_listbox.get_row_at_index(0))
        else:
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Available")
            self.nmap_detail_placeholder.set_description("No host data to display.")
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)


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
            else:
                self.status_row.set_title("Scan Status")

    def _clear_results(self):
        logging.info("Clearing Nmap results and detail view.")
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()

        self._clear_dynamic_details()

        self.nmap_detail_placeholder.set_title("No Host Selected")
        self.nmap_detail_placeholder.set_description("Select a host from the list to view details, or start a new scan.")
        if not self.nmap_detail_placeholder.get_parent():
            self.nmap_detail_box.append(self.nmap_detail_placeholder)
        self.nmap_detail_placeholder.set_visible(True)

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
