"""Defines the Nmap scanning page for the Woes application.

This page allows users to configure and run Nmap scans against specified targets.
Results are displayed in a structured way, with hosts listed and detailed
information (ports, OS, etc.) shown in expandable sections.
"""
"""Defines the Nmap scanning page for the Woes application.

This page allows users to configure and run Nmap scans against specified targets.
Results are displayed in a structured way, with hosts listed and detailed
information (ports, OS, etc.) shown in expandable sections.
"""
"""Defines the Nmap scanning page for the Woes application.

This page allows users to configure and run Nmap scans against specified targets.
Results are displayed in a structured way, with hosts listed and detailed
information (ports, OS, etc.) shown in expandable sections.
"""
import logging
import re
from typing import Optional # Removed List as it's not used
import yaml

import gi
from gi.repository import Adw, Gio, GLib, GObject, Gtk, GtkSource

import nmap

from .constants import APP_ID, RESOURCE_PREFIX
from .nmap_scanner import NmapScanner, ScanStatus
from .style_utils import apply_source_style_scheme
from .utils import create_source_view


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")


class NmapItem(GObject.Object):
    """A simple GObject to hold key-value pairs for Nmap results display."""

    key = GObject.Property(type=str)
    value = GObject.Property(type=str)

    def __init__(self, key: str, value: str):
        """Initialize an NmapItem.

        Args:
        ----
            key: The key (e.g., host IP or name).
            value: The value (e.g., YAML string of scan results for the host).

        """
        super().__init__()
        self.key = key
        self.value = value


class NmapTargetRow(Gtk.ListBoxRow):
    """A Gtk.ListBoxRow customized to display an NmapItem's key."""

    nmap_item = GObject.Property(type=NmapItem)

    def __init__(self, nmap_item: NmapItem, **kwargs):
        """Initialize an NmapTargetRow.

        Args:
        ----
            nmap_item: The NmapItem to associate with this row.
            **kwargs: Additional keyword arguments for Gtk.ListBoxRow.

        """
        super().__init__(**kwargs)
        self.nmap_item = nmap_item
        label = Gtk.Label(label=nmap_item.key, halign=Gtk.Align.START, margin_start=6, margin_end=6)
        self.set_child(label)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/nmap_page.ui")
class NmapPage(Gtk.Box):
    """Activity page for performing Nmap scans and viewing results.

    Provides UI for setting Nmap scan parameters (target, options like OS detection,
    scripts, timing) and displays results in a split view with a host list
    and detailed YAML output for selected hosts.
    """

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

    # Left pane content box
    left_vbox_content = Gtk.Template.Child("left_vbox_content")

    # AdwOverlaySplitView and its children (nmap_split_view removed)
    nmap_host_listbox = Gtk.Template.Child("nmap_host_listbox") # nmap_host_listbox is inside left_vbox_content
    nmap_detail_box = Gtk.Template.Child("nmap_detail_box")
    nmap_detail_placeholder = Gtk.Template.Child("nmap_detail_placeholder")


    def __init__(self, **kwargs):
        """Initialize the NmapPage.

        Sets up UI elements from the template, initializes the NmapScanner,
        connects signal handlers, and sets the initial UI state.
        """
        super().__init__(**kwargs)
        logging.info("Initializing NmapPage...")

        # Manual binding fallback for nmap_host_listbox
        if self.nmap_host_listbox is None:
            logging.warning("NmapPage: nmap_host_listbox is None after init_template(). Attempting manual retrieval.")
            if self.left_vbox_content is not None: # This parent must be bound correctly by @Gtk.Template.Child
                # According to nmap_page.ui, left_vbox_content's children are:
                # 1. AdwPreferencesGroup (nmap_scan_parameters_group)
                # 2. GtkListBox (nmap_host_listbox)
                child = self.left_vbox_content.get_first_child()
                if child is not None:
                    listbox_candidate = child.get_next_sibling()
                    if isinstance(listbox_candidate, Gtk.ListBox):
                        # To be more certain, check the buildable ID
                        buildable_id = None
                        if isinstance(listbox_candidate, Gtk.Buildable):
                            buildable_id = listbox_candidate.get_buildable_id()

                        if buildable_id == "nmap_host_listbox":
                            self.nmap_host_listbox = listbox_candidate
                            logging.info("NmapPage: Successfully manually bound nmap_host_listbox via ID check.")
                        elif listbox_candidate.get_buildable_id() is None: # Fallback if get_buildable_id() returns None but type is right
                            self.nmap_host_listbox = listbox_candidate # Assign if type matches, assuming structure
                            logging.warning("NmapPage: Manually bound nmap_host_listbox (type match, ID not confirmed as string 'nmap_host_listbox').")
                        else:
                            logging.warning(f"NmapPage: Found ListBox sibling, but its buildable_id is '{buildable_id}' not 'nmap_host_listbox'. Manual binding skipped.")
                    elif listbox_candidate is not None:
                        logging.warning(f"NmapPage: Sibling of AdwPreferencesGroup is not a Gtk.ListBox, but a {type(listbox_candidate)}. Cannot bind manually this way.")
                    else:
                        logging.warning("NmapPage: left_vbox_content's first child (AdwPreferencesGroup) has no sibling. Cannot find nmap_host_listbox this way.")
                else:
                    logging.warning("NmapPage: left_vbox_content has no children. Cannot find nmap_host_listbox this way.")
            else:
                logging.warning("NmapPage: self.left_vbox_content is None. Cannot attempt manual binding of nmap_host_listbox.")

        if self.nmap_host_listbox is None:
            # This log should be prominent if it happens, as it means the page will crash.
            logging.critical("NmapPage: CRITICAL - nmap_host_listbox remains None after all binding attempts. Expect AttributeError.")
        else:
            logging.info("NmapPage: nmap_host_listbox appears to be bound.")

        self.results_by_host = {}
        self.nmap_target_listbox_store = Gio.ListStore(item_type=NmapItem)
        self.scanner = NmapScanner()

        self.settings = Gio.Settings.new(APP_ID)

        self._init_page_ui()
        self._connect_signals()
        if self.nmap_apply_button:
            self.nmap_apply_button.set_use_underline(True)
        logging.info("NmapPage initialized.")
        logging.debug("NmapPage __init__ completed.")

    def __del__(self):
        """Clean up resources, specifically the NmapScanner's thread pool."""
        if hasattr(self, "scanner") and self.scanner:
            del self.scanner # NmapScanner.__del__ handles executor shutdown

    def _on_source_style_scheme_setting_changed(self, _settings: Gio.Settings, key: str):
        """Handle changes to the 'source-style-scheme' GSettings key.

        Currently, this method calls `_apply_source_view_style` which is a placeholder
        as Nmap page might have multiple source views or might need style applied differently.
        Consider connecting this to `_apply_source_view_style_to_buffer` for active buffers if needed.

        Args:
        ----
            _settings: The Gio.Settings object that changed.
            key: The name of the GSettings key that changed.

        """
        logging.debug("NmapPage: '%s' setting changed, applying new source view style.", key)
        # self._apply_source_view_style() # Placeholder, actual views are dynamic
        # If a specific view needs update, it should be handled directly.
        # For now, new views created will pick up the new style.

    def _apply_source_view_style_to_buffer(self, buffer: GtkSource.Buffer):
        """Apply the current GSettings style scheme to a given GtkSource.Buffer.

        Args:
        ----
            buffer: The GtkSource.Buffer to apply the style to.

        """
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

        # self.nmap_split_view.set_show_sidebar(True) # Removed as nmap_split_view is gone
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
        target = self.nmap_target_entryrow.get_text().strip()
        self._clear_error()

        if not self.scanner.validate_target_input(target):
            self.nmap_target_entryrow.add_css_class("error")
            self._display_error(
                "Invalid target format. Please enter a valid IP, CIDR, or hostname."
            )
            return
        # R1705: Unnecessary "else" after "return", remove the "else" and de-indent the code inside it
        self.nmap_target_entryrow.remove_css_class("error")

        if not target:
            self._clear_results()
            return

        self.nmap_target_entryrow.set_sensitive(False)
        self._set_scan_status(ScanStatus.IN_PROGRESS, f"Scanning {target}...")

        os_fingerprinting = self.nmap_fingerprint_switchrow.get_active()
        all_ports = self.nmap_all_ports_switchrow.get_active()

        selected_script_item = self.nmap_scripts_dropdown.get_selected_item()
        script_name = (
            selected_script_item.get_string()
            if isinstance(selected_script_item, Gtk.StringObject)
            and selected_script_item.get_string() != "None"
            else None
        )

        service_version_detection = self.nmap_service_version_switchrow.get_active()
        no_ping_scan = self.nmap_no_ping_switchrow.get_active()
        selected_timing_item = self.nmap_timing_template_comborow.get_selected_item()
        timing_template_str = selected_timing_item.get_string()
        timing_match = re.search(r"\(T([0-5])\)", timing_template_str)
        timing_template = f"T{timing_match.group(1)}" if timing_match else "T3"

        custom_dns_server = self.settings.get_string("custom-dns-server")

        logging.info(
            "Nmap scan for target: %s (OSScan:%s, AllPorts:%s, Script:%s, Ver:%s, NoPing:%s, Time:%s, CustomDNS:%s)",
            target,
            os_fingerprinting,
            all_ports,
            script_name,
            service_version_detection,
            no_ping_scan,
            timing_template,
            custom_dns_server if custom_dns_server else "None",
        )
        self.scanner.executor.submit(
            self._run_nmap_scan_task,
            target,
            os_fingerprinting,
            all_ports,
            script_name,
            service_version_detection,
            no_ping_scan,
            timing_template,
            custom_dns_server,
        )
        logging.debug(
            "Scan task params: target=%s, os_fingerprint=%s, all_ports=%s, script_name=%s, "
            "service_version_detection=%s, no_ping_scan=%s, timing_template=%s",
            target,
            os_fingerprinting,
            all_ports,
            script_name,
            service_version_detection,
            no_ping_scan,
            timing_template,
        )

    def _run_nmap_scan_task(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        target: str,
        os_fingerprinting: bool,
        all_ports: bool,
        script_name: Optional[str],
        service_version_detection: bool,
        no_ping_scan: bool,
        timing_template: str,
        custom_dns_server: Optional[str],
    ):
        """Execute the Nmap scan in a separate thread via NmapScanner.

        This method is intended to be run by a ThreadPoolExecutor. It calls the
        NmapScanner's `run_nmap_scan` method and then schedules UI updates
        on the main thread using `GLib.idle_add`.

        Args:
        ----
            target: The target string for Nmap.
            os_fingerprinting: Whether to enable OS fingerprinting.
            all_ports: Whether to scan all ports.
            script_name: The name of an Nmap script to run, or None.
            service_version_detection: Whether to enable service version detection.
            no_ping_scan: Whether to disable host discovery ping.
            timing_template: The Nmap timing template (e.g., "T4").
            custom_dns_server: Optional custom DNS server IP to use for the scan.

        """
        logging.info("Nmap scan task started for %s in executor thread.", target)
        try:
            nm = self.scanner.run_nmap_scan(
                target,
                os_fingerprinting,
                all_ports,
                script_name,
                service_version_detection,
                no_ping_scan,
                timing_template,
                custom_dns_server=custom_dns_server,
            )
            GLib.idle_add(self._process_scan_results, nm, target)
        except nmap.PortScannerError as e:
            logging.error("Nmap PortScannerError for %s: %s", target, e, exc_info=True)
            GLib.idle_add(self._handle_scan_error, target, f"Nmap scan error: {e}")
        except Exception as e:  # pylint: disable=broad-except
            logging.error(
                "Unexpected exception in Nmap scan task for %s (%s): %s",
                target,
                type(e).__name__,
                e,
                exc_info=True,
            )
            GLib.idle_add(self._handle_scan_error, target, f"Scan failed unexpectedly: {e}")
        finally:
            GLib.idle_add(self.nmap_target_entryrow.set_sensitive, True)
            logging.info("Nmap scan task finished for %s.", target)

    def _process_scan_results(self, nm: nmap.PortScanner, original_target: str):
        """Process the Nmap scan results received from the scanner task.

        Updates the UI with the hosts found and their details. If no hosts are found,
        displays an appropriate message.

        Args:
        ----
            nm: The `nmap.PortScanner` object containing the scan results.
            original_target: The original target string for which the scan was run.

        """
        hosts_found = nm.all_hosts()
        logging.debug("Processing results for %s. nm.all_hosts(): %s", original_target, hosts_found)
        if not hosts_found:
            logging.warning("No hosts found in Nmap results for target %s.", original_target)
            self._set_scan_status(
                ScanStatus.COMPLETE,
                f"Scan complete for {original_target}. No hosts found or responsive.",
            )
            self._display_error(f"No hosts found or responsive for target: {original_target}")
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Responsive Hosts")
            self.nmap_detail_placeholder.set_description(
                (f"The Nmap scan for '{original_target}' did not find any responsive hosts.")
            )
            self.nmap_detail_placeholder.set_visible(True)
            return

        results_yaml_map = self.scanner.convert_results_to_yaml(nm)
        if results_yaml_map:
            logging.debug("results_yaml_map keys: %s", list(results_yaml_map.keys()))
        else:
            logging.debug("results_yaml_map is empty.")
        GLib.idle_add(self._update_results_view, hosts_found, results_yaml_map)
        self._set_scan_status(
            ScanStatus.COMPLETE,
            f"Scan complete for {original_target}. {len(hosts_found)} host(s) found.",
        )

    def _handle_scan_error(self, target: str, error_message: str):
        """Handle errors reported from the Nmap scan task.

        Displays an error message in the UI and sets the scan status to FAILED.

        Args:
        ----
            target: The target for which the scan failed.
            error_message: The error message to display.

        """
        logging.error("Handling scan error for target %s: %s", target, error_message)
        self._display_error(f"Error scanning {target}: {error_message}")
        self._set_scan_status(ScanStatus.FAILED, f"Scan failed for {target}")

    def _on_target_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None): # Changed Optional[] to | None
        """Handle selection of a host in the Nmap results ListBox.

        Clears previous details and displays the scan results (parsed from YAML)
        for the newly selected host using various expander rows.

        Args:
        ----
            _listbox: The Gtk.ListBox where the selection changed.
            row: The selected Gtk.ListBoxRow, or None if deselected.

        """
        self._clear_dynamic_details()

        if row is None:
            self.nmap_detail_placeholder.set_title("No Host Selected")
            self.nmap_detail_placeholder.set_description(
                "Select a host from the list to view details."
            )
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)
            return

        self.nmap_detail_placeholder.set_visible(False)

        if isinstance(row, NmapTargetRow):
            item_obj = row.nmap_item
        else:
            logging.warning("Selected row is not an NmapTargetRow instance.")
            item_obj = None

        if item_obj and isinstance(item_obj, NmapItem):
            selected_target_key = item_obj.key
            logging.debug("Target selected: %s", selected_target_key)

            try:
                host_data_dict = yaml.safe_load(item_obj.value)
                if not isinstance(host_data_dict, dict):
                    logging.warning(
                        "Parsed YAML for host %s is not a dictionary. Value: %s",
                        selected_target_key,
                        item_obj.value[:100],
                    )
                    host_data_dict = {}
                logging.debug(
                    "Successfully parsed YAML for %s. Data keys: %s",
                    selected_target_key,
                    list(host_data_dict.keys()) if host_data_dict else "None",
                )
            except yaml.YAMLError as e:
                logging.debug("YAML parsing failed for %s: %s", selected_target_key, e)
                logging.error("Error parsing YAML for host %s: %s", selected_target_key, e)
                error_label = Gtk.Label(
                    label=f"Error: Could not parse scan results for {selected_target_key}.\n{e}"
                )
                error_label.set_wrap(True)
                error_label.set_halign(Gtk.Align.START)
                self.nmap_detail_box.append(error_label)
                return

            self._add_host_details_expander(host_data_dict, selected_target_key)
            self._add_ports_expander(host_data_dict, selected_target_key)
            self._add_os_expander(host_data_dict, selected_target_key)
            self._add_raw_output_expander(host_data_dict, selected_target_key)

        else:
            logging.warning("Could not retrieve NmapItem from selected row or item_obj is None.")
            self.nmap_detail_placeholder.set_title("Error")
            self.nmap_detail_placeholder.set_description(
                "Could not load details for the selected host."
            )
            if not self.nmap_detail_placeholder.get_parent():
                self.nmap_detail_box.append(self.nmap_detail_placeholder)
            self.nmap_detail_placeholder.set_visible(True)

    def _add_raw_output_expander(self, host_data_dict: dict, host_key: str):
        """Add an Adw.ExpanderRow to display the human-readable text summary for a host.

        Args:
        ----
            host_data_dict: The dictionary containing scan data for the host.
            host_key: The identifier for the host (IP or name).

        """
        logging.debug("Adding text scan summary expander for %s", host_key)
        expander = Adw.ExpanderRow(title=f"Text Scan Summary - {host_key}")
        expander.set_expanded(False)

        human_readable_summary = self._generate_human_readable_host_summary(host_data_dict)

        source_view, source_buffer = create_source_view(language_name="txt")
        source_buffer.set_text(human_readable_summary, -1)
        self._apply_source_view_style_to_buffer(source_buffer)
        source_view.set_editable(False)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_child(source_view)
        scrolled_window.set_min_content_height(200)
        scrolled_window.set_max_content_height(400)
        scrolled_window.set_vexpand(True)

        expander.add_row(scrolled_window)
        self.nmap_detail_box.append(expander)

    def _add_host_details_expander(self, host_data: dict, host_key: str):  # pylint: disable=too-many-locals
        """Add an Adw.ExpanderRow to display general host information.

        Includes status, IP/MAC addresses, and hostnames.

        Args:
        ----
            host_data: A dictionary parsed from the Nmap YAML output for the host.
            host_key: The identifier for the host.

        """
        logging.debug("Adding host details expander for %s", host_key)
        expander = Adw.ExpanderRow(title=f"Host Information - {host_key}")
        expander.set_expanded(True)

        status_info = host_data.get("status", {})
        status_subtitle = (
            f"{status_info.get('state', 'N/A')} (Reason: {status_info.get('reason', 'N/A')})"
        )
        status_row = Adw.ActionRow(title="Status", subtitle=status_subtitle)
        expander.add_row(status_row)

        addresses_info = host_data.get("addresses", {})
        if addresses_info.get("ipv4"):
            ipv4_row = Adw.ActionRow(title="IPv4 Address", subtitle=addresses_info["ipv4"])
            expander.add_row(ipv4_row)
        if addresses_info.get("ipv6"):
            ipv6_row = Adw.ActionRow(title="IPv6 Address", subtitle=addresses_info["ipv6"])
            expander.add_row(ipv6_row)
        if addresses_info.get("mac"):
            mac_row = Adw.ActionRow(title="MAC Address", subtitle=addresses_info["mac"])
            expander.add_row(mac_row)

        hostnames_list = host_data.get("hostnames", [])
        if hostnames_list:
            for hn_entry in hostnames_list:
                hn_title = f"Hostname ({hn_entry.get('type', 'N/A')})"
                hn_subtitle = hn_entry.get("name", "N/A")
                hn_row = Adw.ActionRow(title=hn_title, subtitle=hn_subtitle)
                expander.add_row(hn_row)
        else:
            no_hn_row = Adw.ActionRow(title="Hostnames", subtitle="No hostnames reported")
            expander.add_row(no_hn_row)

        self.nmap_detail_box.append(expander)

    def _add_ports_expander(self, host_data: dict, host_key: str):  # pylint: disable=too-many-locals
        """Add an Adw.ExpanderRow to display detected network ports and their details.

        Covers TCP, UDP, SCTP, and IP protocols.

        Args:
        ----
            host_data: A dictionary parsed from the Nmap YAML output for the host.
            host_key: The identifier for the host.

        """
        logging.debug("Adding ports expander for %s", host_key)
        expander = Adw.ExpanderRow(title=f"Network Ports - {host_key}")
        expander.set_expanded(True)

        ports_found = False
        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data.get(proto):
                port_data_log_msg = (
                    f"Processing ports for proto {proto} in host {host_key}, data: "
                    f"{list(proto_data.keys()) if isinstance(proto_data, dict) else 'Not a dict'}"
                )
                logging.debug(port_data_log_msg)
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        ports_found = True
                        state = port_info.get("state", "N/A")
                        name = port_info.get("name", "")
                        product = port_info.get("product", "")
                        version = port_info.get("version", "")
                        reason = port_info.get("reason", "")

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
            no_ports_row = Adw.ActionRow(
                title="Ports", subtitle="No open ports reported or port data available."
            )
            expander.add_row(no_ports_row)

        self.nmap_detail_box.append(expander)

    def _add_os_expander(self, host_data: dict, host_key: str):  # pylint: disable=too-many-locals
        """Add an Adw.ExpanderRow to display OS detection results.

        Shows OS matches with accuracy and class details.

        Args:
        ----
            host_data: A dictionary parsed from the Nmap YAML output for the host.
            host_key: The identifier for the host.

        """
        osmatch_data = host_data.get("osmatch", [])
        if not osmatch_data:
            logging.debug("No OS data for host %s, skipping OS expander.", host_key)
            return

        logging.debug("Adding OS expander for %s. OS match data: %s", host_key, osmatch_data)
        expander = Adw.ExpanderRow(title=f"Operating System Detection - {host_key}")
        expander.set_expanded(True)

        os_details_added = False
        for match in osmatch_data:
            name = match.get("name", "N/A")
            accuracy = match.get("accuracy", "N/A")
            title = f"{name} (Accuracy: {accuracy}%)"

            osclass_details = []
            if "osclass" in match and isinstance(match["osclass"], list):
                for os_class in match["osclass"]:
                    if isinstance(os_class, dict):
                        vendor = os_class.get("vendor", "N/A")
                        osfamily = os_class.get("osfamily", "N/A")
                        osgen = os_class.get("osgen", "N/A")
                        osclass_details.append(
                            f"Type: {os_class.get('type', 'N/A')}, "
                            f"Vendor: {vendor}, Family: {osfamily}, Gen: {osgen}"
                        )
            elif "osclass" in match and isinstance(match["osclass"], dict):
                os_class = match["osclass"]
                vendor = os_class.get("vendor", "N/A")
                osfamily = os_class.get("osfamily", "N/A")
                osgen = os_class.get("osgen", "N/A")
                osclass_details.append(
                    f"Type: {os_class.get('type', 'N/A')}, "
                    f"Vendor: {vendor}, Family: {osfamily}, Gen: {osgen}"
                )

            subtitle = "\n".join(osclass_details) if osclass_details else "No OS class details."

            row = Adw.ActionRow(title=title, subtitle=subtitle)
            if subtitle == "No OS class details.":
                row.set_subtitle("")
            expander.add_row(row)
            os_details_added = True

        if not os_details_added:
            no_data_row = Adw.ActionRow(
                title="OS Detection", subtitle="No specific OS matches found."
            )
            expander.add_row(no_data_row)

        self.nmap_detail_box.append(expander)

    def _update_results_view(self, hosts: list, results_map: dict):
        """Update the host ListBox with new scan results.

        Clears previous hosts and populates the ListStore with NmapItems
        created from the `results_map`. Selects the first host if available.

        Args:
        ----
            hosts: A list of host identifiers (IPs/names) found by Nmap.
            results_map: A dictionary mapping host identifiers to their YAML scan data.

        """
        logging.info("Updating Nmap results view for hosts: %s", hosts)
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()

        if not hosts:
            self._clear_dynamic_details()
            self.nmap_detail_placeholder.set_title("No Hosts Found")
            self.nmap_detail_placeholder.set_description(
                "The scan did not find any responsive hosts."
            )
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
        """Set the scan status and update the UI via GLib.idle_add.

        Args:
        ----
            status_type: The `ScanStatus` enum member.
            message: The message string to display for the status.

        """
        logging.info("Setting Nmap scan status: %s - %s", status_type.name, message)
        GLib.idle_add(self._update_status_ui, status_type, message)

    def _update_status_ui(self, status_type: ScanStatus, message: str):
        """Update the status row and spinner in the UI. (Called via GLib.idle_add).

        Args:
        ----
            status_type: The `ScanStatus` to reflect in the UI.
            message: The status message to display.

        """
        self.status_row.set_subtitle(message)

        # CSS classes for status text color
        status_css_classes = ["success-color", "warning-color", "error-color", "accent-color"] # Include accent just in case
        style_context = self.status_row.get_style_context()

        # Remove any existing status color classes
        for css_class in status_css_classes:
            style_context.remove_class(css_class)

        if status_type == ScanStatus.IN_PROGRESS:
            self.scan_spinner.set_visible(True)
            self.scan_spinner.start()
            self.status_row.set_title("Scanning...")
            style_context.add_class("error-color") # Red for scanning
        else:  # COMPLETE, FAILED, IDLE or other
            self.scan_spinner.stop()
            self.scan_spinner.set_visible(False)
            if status_type == ScanStatus.COMPLETE:
                self.status_row.set_title("Scan Complete")
                style_context.add_class("success-color") # Green for complete
            elif status_type == ScanStatus.FAILED:
                self.status_row.set_title("Scan Failed")
                style_context.add_class("error-color") # Red for failed
            elif status_type == ScanStatus.IDLE:
                self.status_row.set_title("Idle")
                # No specific color for Idle, default will be used
            else: # Generic fallback
                self.status_row.set_title("Scan Status")
                # No specific color for other statuses, default will be used

    def _clear_results(self):
        """Clear all Nmap scan results from the UI.

        Resets the host list, detail view, error banner, and status indicators.
        """
        logging.info("Clearing Nmap results and detail view.")
        self.nmap_target_listbox_store.remove_all()
        self.results_by_host.clear()

        self._clear_dynamic_details()

        self.nmap_detail_placeholder.set_title("No Host Selected")
        self.nmap_detail_placeholder.set_description(
            "Select a host from the list to view details, " + "or start a new scan."
        )
        if not self.nmap_detail_placeholder.get_parent():
            self.nmap_detail_box.append(self.nmap_detail_placeholder)
        self.nmap_detail_placeholder.set_visible(True)

        self.error_banner.set_revealed(False)
        self.nmap_target_entryrow.remove_css_class("error")
        self.nmap_target_entryrow.set_sensitive(True)
        self._set_scan_status(ScanStatus.IDLE, "Idle")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        """Handle dismissal of the error banner by clearing the error state."""
        self._clear_error()

    def _display_error(self, message: str):
        """Display an error message in the UI banner.

        Args:
        ----
            message: The error message to display.

        """
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)

    def _clear_error(self):
        """Clear any displayed error message from the UI banner."""
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")

    def _create_target_listbox_row(self, item: NmapItem) -> Gtk.ListBoxRow:
        """Factory function to create an NmapTargetRow for the host ListBox.

        Args:
        ----
            item: The `NmapItem` to display in the row.

        Returns:
        -------
            An `NmapTargetRow` instance.

        """
        return NmapTargetRow(nmap_item=item)

    def _generate_human_readable_host_summary(self, host_data_dict: dict) -> str:
        """Generate a human-readable summary of host scan data.

        Args:
            host_data_dict: A dictionary containing the scan data for a single host.

        Returns:
            A string summarizing the host's scan data.
        """
        summary_lines = []

        # Host status
        status_info = host_data_dict.get("status", {})
        state = status_info.get("state", "N/A")
        reason = status_info.get("reason", "N/A")
        summary_lines.append(f"Host is {state} (reason: {reason}).")

        # Addresses
        addresses_info = host_data_dict.get("addresses", {})
        if ipv4 := addresses_info.get("ipv4"):
            summary_lines.append(f"IPv4 Address: {ipv4}")
        if ipv6 := addresses_info.get("ipv6"):
            summary_lines.append(f"IPv6 Address: {ipv6}")
        if mac := addresses_info.get("mac"):
            # Vendor information is often part of the MAC address string in Nmap output,
            # but not explicitly separated in the typical dict structure.
            # We'll display MAC as is.
            summary_lines.append(f"MAC Address: {mac}")

        # Pre-scan script results
        prescan_results = host_data_dict.get("prescript_results")
        if prescan_results and isinstance(prescan_results, list):
            summary_lines.append("\nPre-scan script results:")
            for script_item in prescan_results:
                if isinstance(script_item, dict):
                    script_id = script_item.get("id", "N/A")
                    script_output = script_item.get("output", "N/A")
                    if script_output and isinstance(script_output, str):
                        formatted_output = "\n".join(
                            [f"|_ {script_id}: {line.strip()}" if i == 0 else f"|  {line.strip()}"
                             for i, line in enumerate(script_output.strip().split('\n'))]
                        )
                        summary_lines.append(formatted_output)
                    else:
                        summary_lines.append(f"|_ {script_id}: (no output)")


        # Hostnames
        hostnames_list = host_data_dict.get("hostnames", [])
        if hostnames_list:
            summary_lines.append("\nHostnames:") # Added newline for spacing if prescan exists
            for hn_entry in hostnames_list:
                hn_type = hn_entry.get("type", "N/A")
                hn_name = hn_entry.get("name", "N/A")
                summary_lines.append(f"  {hn_name} ({hn_type})")

        # Host-level script output
        host_scripts = host_data_dict.get("hostscript")
        if host_scripts and isinstance(host_scripts, list):
            summary_lines.append("\nHost script output:")
            for script_item in host_scripts:
                if isinstance(script_item, dict):
                    script_id = script_item.get("id", "N/A")
                    script_output = script_item.get("output", "N/A")
                    # Indent script output for readability if it's multiline
                    formatted_output = "\n".join([f"    {line.strip()}" for line in script_output.strip().split('\n')])
                    summary_lines.append(f"  Script: {script_id}\n{formatted_output}")

        # Ports
        # Nmap console output often shows a summary of closed ports, e.g., "Not shown: 995 closed tcp ports (conn-refused)"
        # This dict structure doesn't directly provide that summary, so we'll list open/filtered ports.

        ports_data = []
        for proto in ["tcp", "udp", "sctp", "ip"]:
            if proto_data := host_data_dict.get(proto):
                if isinstance(proto_data, dict):
                    for port_id, port_info in proto_data.items():
                        p_state = port_info.get("state", "N/A")
                        # Only include open or otherwise interesting ports, not typically 'closed' ones unless explicitly detailed
                        if p_state not in ["closed", "filtered out"]: # Example filtering
                            p_name = port_info.get("name", "")
                            p_product = port_info.get("product", "")
                            p_version = port_info.get("version", "")
                            p_reason = port_info.get("reason", "") # Nmap reason for the port state

                            port_str = f"{port_id}/{proto.upper():<4} {p_state:<10} {p_name}"
                            if p_product:
                                port_str += f" {p_product}"
                            if p_version:
                                port_str += f" {p_version}"
                            if p_reason:
                                port_str += f" (reason: {p_reason})"
                            ports_data.append(port_str)

                        # Port-level script output
                        if port_scripts := port_info.get("script"):
                            if isinstance(port_scripts, dict):
                                for script_id, script_output in port_scripts.items():
                                    if script_output and isinstance(script_output, str):
                                        # Indent script output lines, prefix with |_
                                        formatted_script_output = "\n".join(
                                            [f"  |_{script_id}: {line.strip()}" if i == 0 else f"  | {line.strip()}"
                                             for i, line in enumerate(script_output.strip().split('\n'))]
                                        )
                                        ports_data.append(formatted_script_output)

        if ports_data:
            summary_lines.append("\nPORT      STATE SERVICE      VERSION") # Header similar to Nmap
            summary_lines.extend(ports_data)
        else:
            summary_lines.append("No open ports reported or port data available.")

        # OS Matches
        osmatch_data = host_data_dict.get("osmatch", [])
        if osmatch_data:
            summary_lines.append("\nOS details:")
            for match in osmatch_data:
                name = match.get("name", "N/A")
                accuracy = match.get("accuracy", "N/A")
                summary_lines.append(f"  Name: {name}")
                summary_lines.append(f"  Accuracy: {accuracy}%")

                if "osclass" in match:
                    # Ensure osclass is a list, as it can sometimes be a single dict
                    osclasses = match["osclass"] if isinstance(match["osclass"], list) else [match["osclass"]]
                    for os_class in osclasses:
                        if isinstance(os_class, dict):
                            oc_type = os_class.get("type", "N/A")
                            oc_vendor = os_class.get("vendor", "N/A")
                            oc_family = os_class.get("osfamily", "N/A")
                            oc_gen = os_class.get("osgen", "N/A")
                            summary_lines.append("  OS Class:")
                            summary_lines.append(f"    Type: {oc_type}, Vendor: {oc_vendor}, Family: {oc_family}, Gen: {oc_gen}")
        else:
            summary_lines.append("No OS data available.")

        return "\n".join(summary_lines)
