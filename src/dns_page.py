"""Defines the DNS lookup page for the Woes application."""
import logging
logger = logging.getLogger(__name__)
import re # Keep for existing _is_valid_ip_or_domain if not fully replaced by utils initially

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.rdataclass
import gi
from gi.repository import Adw, Gio, Gtk, Pango, GObject
from typing import Tuple, Sequence, Any, List, Dict

from .constants import APP_ID, RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_ip, is_valid_domain


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Adw.PreferencesPage):
    """Activity page for performing DNS lookups and displaying results."""

    __gtype_name__ = "DNSPage"

    domain_entry = Gtk.Template.Child()
    dns_lookup_spinner = Gtk.Template.Child()
    dns_apply_button = Gtk.Template.Child()
    dns_record_type_dropdown = Gtk.Template.Child()
    dns_results_box_container = Gtk.Template.Child()

    def __init__(self, **kwargs):
        """Initialize the DNSPage."""
        super().__init__(**kwargs)
        logger.debug("DNSPage initialized.")
        self._connect_signals()
        self.settings = Gio.Settings.new(APP_ID)
        if self.dns_apply_button:
            self.dns_apply_button.set_use_underline(True)

        if self.dns_lookup_spinner:
            self.dns_lookup_spinner.set_spinning(False)
            self.dns_lookup_spinner.set_visible(False)

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.domain_entry.connect("activate", self._on_entry_activated)
        self.dns_apply_button.connect("clicked", self._on_entry_activated)
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed)

    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        """Copy the given text to the clipboard.

        Args:
        ----
            text: The text to copy.
            widget: A Gtk.Widget to get the clipboard from.

        """
        try:
            clipboard = widget.get_clipboard()
            if clipboard:
                clipboard.set(text)
                logger.info("Copied to clipboard: %s", text)
            else:
                logger.warning("Could not get clipboard from widget: %s", widget)
        except Exception: # pylint: disable=broad-except
            logger.exception("Error copying to clipboard:")

    # _is_ip_address method is removed. Callsites will use utils.is_valid_ip.

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """Validate whether the input string is a syntactically valid IP address or domain name
        using utility functions.
        """
        if not input_str: # Utility functions also check for empty, but good to be explicit.
            return False
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget):
        """Handle DNS entry activation (e.g., pressing Enter or clicking Apply).

        Args:
        ----
            _widget: The widget that emitted the signal.

        """
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec):
        """Handle the record type dropdown change event.

        Args:
        ----
            _dropdown: The Gtk.DropDown widget whose selection changed.
            _param_spec: The GLib.ParamSpec of the property that changed.

        """
        self._perform_lookup()

    def _prepare_resolver(self) -> dns.resolver.Resolver:
        """Prepare a DNS resolver, incorporating custom server settings if configured.

        Returns
        -------
            dns.resolver.Resolver: The configured DNS resolver instance.

        """
        resolver = dns.resolver.Resolver()
        custom_dns_server = self.settings.get_string("custom-dns-server")
        if custom_dns_server:
            resolver.nameservers = [custom_dns_server]
        return resolver

    def _fetch_dns_records(
        self,
        user_input: str,
        requested_record_type: str,
        resolver: dns.resolver.Resolver,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Fetch DNS records for the given input.

        Args:
        ----
            user_input: The domain name or IP address to query.
            requested_record_type: The DNS record type as a string (e.g., "A", "MX").
            resolver: The DNS resolver instance to use for the query.

        Returns:
        -------
            A tuple containing the list of parsed DNS record dictionaries and the actual record type used.

        Raises:
        ------
            dns.resolver.NXDOMAIN: If the domain does not exist.
            dns.resolver.NoAnswer: If the query succeeded but no records of the requested type exist.
            dns.resolver.Timeout: If the query timed out.

        """
        logger.debug(f"Fetching DNS records for: {user_input}, type: {requested_record_type}")
        actual_record_type = requested_record_type
        if is_valid_ip(user_input): # Use new util function
            actual_record_type = "PTR"  # Override to PTR for IP addresses

        parsed_records = self._lookup_record(user_input, actual_record_type, resolver)
        return parsed_records, actual_record_type

    def _perform_lookup(self):  # noqa: C901 # Function complexity is high, consider refactoring.
        """Perform the DNS lookup based on user input and selected record type.

        Handles input validation, prepares the resolver, fetches records,
        and updates the UI with results or error messages.
        """
        if self.dns_lookup_spinner:
            self.dns_lookup_spinner.set_visible(True)
            self.dns_lookup_spinner.start()

        user_input = self.domain_entry.get_text().strip()
        requested_record_type = self._get_selected_record_type() # Get it early for logging
        logger.debug(f"Performing DNS lookup for: {user_input}, type: {requested_record_type}")

        if not user_input:
            show_global_toast(self, "Input cannot be empty.")
            main_window = self.get_native() # Still need to check for fallback condition
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Input cannot be empty.")
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop()
                self.dns_lookup_spinner.set_visible(False)
            return

        self._clear_error() # Clears banner and CSS error class

        if not self._is_valid_ip_or_domain(user_input): # This now uses new utils
            show_global_toast(self, "Invalid IP address or domain name.")
            main_window = self.get_native() # Still need to check for fallback condition
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Invalid IP address or domain name.")
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop()
                self.dns_lookup_spinner.set_visible(False)
            return

        requested_record_type = self._get_selected_record_type()

        try:
            resolver = self._prepare_resolver()
            result_data, actual_type_used = self._fetch_dns_records(
                user_input, requested_record_type, resolver
            )

            # If an IP was given, _fetch_dns_records used PTR. Update dropdown to reflect this.
            if is_valid_ip(user_input) and actual_type_used == "PTR": # Use new util function
                model = self.dns_record_type_dropdown.get_model()
                for i in range(model.get_n_items()):
                    if model.get_string(i) == "PTR":
                        self.dns_record_type_dropdown.set_selected(i)
                        break

            self._display_result(result_data, user_input, actual_type_used, resolver.nameservers)

        except dns.resolver.NXDOMAIN:
            show_global_error(self, f"Domain not found: {user_input} (NXDOMAIN)")
        except dns.resolver.NoAnswer:
            logger.info("No %s records found for %s (NoAnswer).", requested_record_type, user_input)
            self._display_result([], user_input, requested_record_type, resolver.nameservers if hasattr(resolver, 'nameservers') else ["System default"])
        except dns.resolver.Timeout:
            show_global_error(self, f"DNS query timed out for {user_input}")
        except dns.exception.DNSException as e:
            logger.exception("DNS lookup failed for %s, type %s:", user_input, requested_record_type)
            show_global_error(self, f"DNS Error: {str(e)}")
        except Exception as e:
            logger.exception(
                "Unexpected error during DNS lookup for %s, type %s:",
                user_input,
                requested_record_type,
            )
            show_global_error(self, f"An unexpected error occurred: {str(e)}")
        finally:
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop()
                self.dns_lookup_spinner.set_visible(False)

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown."""
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        return model.get_string(selected_index)

    def _clear_error(self):
        """Clear any existing error messages from the UI banner and entry row styling."""
        self.domain_entry.remove_css_class("error")
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'hide_error'):
            main_window.hide_error()
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    @staticmethod
    def _lookup_record(domain_or_ip: str, record_type_str: str, resolver: dns.resolver.Resolver) -> List[Dict[str, Any]]:
        """Look up DNS records using the provided resolver and parse them."""
        if record_type_str == "PTR":
            query_name = dns.reversename.from_address(domain_or_ip)
        else:
            query_name = domain_or_ip

        answer = resolver.resolve(query_name, record_type_str)
        parsed_records: List[Dict[str, Any]] = []
        for rdata in answer:
            record: Dict[str, Any] = {
                'name': answer.qname.to_text(),
                'ttl': rdata.ttl if hasattr(rdata, 'ttl') else answer.response.answer[0].ttl,
                'class': dns.rdataclass.to_text(rdata.rdclass),
                'type': dns.rdatatype.to_text(rdata.rdtype)
            }
            if rdata.rdtype == dns.rdatatype.A: record['address'] = rdata.address
            elif rdata.rdtype == dns.rdatatype.AAAA: record['address'] = rdata.address
            elif rdata.rdtype == dns.rdatatype.CNAME: record['target'] = rdata.target.to_text()
            elif rdata.rdtype == dns.rdatatype.MX:
                record['preference'] = rdata.preference
                record['exchange'] = rdata.exchange.to_text()
            elif rdata.rdtype == dns.rdatatype.TXT: record['texts'] = [s.decode('utf-8', 'replace') for s in rdata.strings]
            elif rdata.rdtype == dns.rdatatype.NS: record['target'] = rdata.target.to_text()
            elif rdata.rdtype == dns.rdatatype.PTR: record['target'] = rdata.target.to_text()
            elif rdata.rdtype == dns.rdatatype.SOA:
                record['mname'] = rdata.mname.to_text()
                record['rname'] = rdata.rname.to_text()
                record['serial'] = rdata.serial
                record['refresh'] = rdata.refresh
                record['retry'] = rdata.retry
                record['expire'] = rdata.expire
                record['minimum'] = rdata.minimum
            else: record['data'] = rdata.to_text()
            parsed_records.append(record)
        return parsed_records

    def _display_result(self, result_records: List[Dict[str, Any]], domain_or_ip: str, record_type: str, dns_servers: Sequence[Any]):
        """Display the DNS lookup results in the UI."""
        logger.debug("Displaying %d results for %s (type %s) using servers %s.", len(result_records), domain_or_ip, record_type, dns_servers)
        logger.info("Query for %s, type %s, using servers %s, returned %d records.", domain_or_ip, record_type, dns_servers, len(result_records))
        for rec in result_records: logger.debug("Record: %s", rec)
        while (child := self.dns_results_box_container.get_first_child()): self.dns_results_box_container.remove(child)
        query_info_row = Adw.ActionRow(title=f"Query: {domain_or_ip}", subtitle=f"Record type queried: {record_type}")
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row)
        servers_info_row = Adw.ActionRow(title="DNS Servers Used", subtitle=", ".join(map(str, dns_servers)) if dns_servers else "System default")
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row)
        self.dns_results_box_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        if not result_records:
            no_records_message = f"No {record_type} records found for {domain_or_ip}."
            if record_type == "PTR" and is_valid_ip(domain_or_ip): no_records_message = f"No PTR records found for IP address {domain_or_ip}."
            elif record_type == "PTR": no_records_message = f"No PTR records found for {domain_or_ip}."
            no_records_row = Adw.ActionRow(title=no_records_message)
            no_records_row.set_selectable(False)
            self.dns_results_box_container.append(no_records_row)
            return
        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row: self.dns_results_box_container.append(row)

    def _build_address_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Builds a UI row for A or AAAA DNS records."""
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}")
        row.add_prefix(Gtk.Image(icon_name="network-wired-symbolic"))
        address_value = str(record_data.get('address', 'N/A'))
        address_label = Gtk.Label(label=address_value, halign=Gtk.Align.START, selectable=True)
        copy_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_button.set_valign(Gtk.Align.CENTER)
        copy_button.set_tooltip_text(f"Copy Address: {address_value}")
        copy_button.connect("clicked", lambda _btn, text=address_value, w=row: DNSPage._copy_to_clipboard(text, w))
        suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        suffix_box.append(address_label)
        suffix_box.append(copy_button)
        summary_a = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {address_value}"
        copy_full_button_a = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_button_a.set_valign(Gtk.Align.CENTER)
        copy_full_button_a.set_tooltip_text("Copy Full Record Summary")
        copy_full_button_a.connect("clicked", lambda _btn, text=summary_a, w=row: DNSPage._copy_to_clipboard(text, w))
        suffix_box.append(copy_full_button_a)
        row.add_suffix(suffix_box)
        row.set_selectable(False)
        return row

    def _build_cname_ns_ptr_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Builds a UI row for CNAME, NS, or PTR DNS records."""
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}")
        icon_name = "emblem-shared-symbolic"
        if record_type == "NS": icon_name = "network-server-symbolic"
        elif record_type == "PTR": icon_name = "system-search-symbolic"
        row.add_prefix(Gtk.Image(icon_name=icon_name))
        target_value = str(record_data.get('target', 'N/A'))
        target_label = Gtk.Label(label=target_value, halign=Gtk.Align.START, selectable=True)
        copy_button_target = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_button_target.set_valign(Gtk.Align.CENTER)
        copy_button_target.set_tooltip_text(f"Copy Target: {target_value}")
        copy_button_target.connect("clicked", lambda _btn, text=target_value, w=row: DNSPage._copy_to_clipboard(text, w))
        suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        suffix_box.append(target_label)
        suffix_box.append(copy_button_target)
        summary_cname_ns_ptr = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {target_value}"
        copy_full_button_cname = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_button_cname.set_valign(Gtk.Align.CENTER)
        copy_full_button_cname.set_tooltip_text("Copy Full Record Summary")
        copy_full_button_cname.connect("clicked", lambda _btn, text=summary_cname_ns_ptr, w=row: DNSPage._copy_to_clipboard(text, w))
        suffix_box.append(copy_full_button_cname)
        row.add_suffix(suffix_box)
        row.set_selectable(False)
        return row

    def _build_mx_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Builds a UI row for MX DNS records."""
        row = Adw.ExpanderRow(title=name, subtitle=f"MX Record ({base_subtitle})")
        row.add_prefix(Gtk.Image(icon_name="mail-send-receive-symbolic"))
        exchange_value = str(record_data.get('exchange', 'N/A'))
        preference_value = str(record_data.get('preference', 'N/A'))
        mx_detail_row_title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mx_detail_row_title_box.append(Gtk.Label(label=exchange_value, halign=Gtk.Align.START, selectable=True))
        copy_button_exchange = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_button_exchange.set_valign(Gtk.Align.CENTER)
        copy_button_exchange.set_tooltip_text(f"Copy Exchange: {exchange_value}")
        copy_button_exchange.connect("clicked", lambda _btn, text=exchange_value, w=row: DNSPage._copy_to_clipboard(text, w))
        mx_detail_row_title_box.append(copy_button_exchange)
        mx_detail_row = Adw.ActionRow(subtitle=f"Preference: {preference_value}")
        mx_detail_row.add_prefix(mx_detail_row_title_box)
        mx_detail_row.set_selectable(True)
        row.add_row(mx_detail_row)
        row.set_expanded(True)
        summary_mx = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} MX {preference_value} {exchange_value}"
        copy_full_mx_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_mx_button.set_valign(Gtk.Align.CENTER)
        copy_full_mx_button.set_tooltip_text("Copy Full MX Record")
        copy_full_mx_button.connect("clicked", lambda _btn, text=summary_mx, w=row: DNSPage._copy_to_clipboard(text, w))
        row.add_suffix(copy_full_mx_button)
        return row

    def _build_txt_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Builds a UI row for TXT DNS records."""
        row = Adw.ExpanderRow(title=name, subtitle=f"TXT Records ({base_subtitle})")
        row.add_prefix(Gtk.Image(icon_name="document-properties-symbolic"))
        texts = record_data.get('texts', [])
        if not texts: row.add_row(Adw.ActionRow(title="No text data.", selectable=False))
        for text_string in texts:
            text_label = Gtk.Label(label=text_string, halign=Gtk.Align.START, selectable=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            copy_button_segment = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_button_segment.set_valign(Gtk.Align.CENTER)
            copy_button_segment.set_tooltip_text("Copy Text Segment")
            copy_button_segment.connect("clicked", lambda _btn, text=text_string, w=row: DNSPage._copy_to_clipboard(text, w))
            text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            text_box.append(text_label)
            text_box.append(copy_button_segment)
            text_action_row = Adw.ActionRow()
            text_action_row.add_prefix(text_box)
            text_action_row.set_selectable(False)
            row.add_row(text_action_row)
        row.set_expanded(True) if texts else row.set_expanded(False)
        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} TXT {texts_str_summary}"
        copy_full_txt_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_txt_button.set_valign(Gtk.Align.CENTER)
        copy_full_txt_button.set_tooltip_text("Copy Full TXT Record")
        copy_full_txt_button.connect("clicked", lambda _btn, text=summary_txt, w=row: DNSPage._copy_to_clipboard(text, w))
        row.add_suffix(copy_full_txt_button)
        return row

    def _build_soa_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Builds a UI row for SOA DNS records."""
        row = Adw.ExpanderRow(title=name, subtitle=f"SOA Record ({base_subtitle})")
        row.add_prefix(Gtk.Image(icon_name="document-settings-symbolic"))
        mname_val, rname_val, serial_val, refresh_val, retry_val, expire_val, minimum_val = (str(record_data.get(k, 'N/A')) for k in ['mname','rname','serial','refresh','retry','expire','minimum'])
        soa_fields = [("MNAME",mname_val), ("RNAME",rname_val), ("Serial",serial_val), ("Refresh",refresh_val), ("Retry",retry_val), ("Expire",expire_val), ("Minimum TTL",minimum_val)]
        for field_name_str, field_value in soa_fields:
            field_label = Gtk.Label(label=field_value, halign=Gtk.Align.START, selectable=True)
            copy_button_field = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_button_field.set_valign(Gtk.Align.CENTER)
            copy_button_field.set_tooltip_text(f"Copy {field_name_str}: {field_value}")
            copy_button_field.connect("clicked", lambda _btn, text=field_value, w=row: DNSPage._copy_to_clipboard(text, w))
            suffix_box_soa_field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            suffix_box_soa_field.append(field_label)
            suffix_box_soa_field.append(copy_button_field)
            soa_field_row = Adw.ActionRow(title=field_name_str)
            soa_field_row.add_suffix(suffix_box_soa_field)
            soa_field_row.set_selectable(False)
            row.add_row(soa_field_row)
        row.set_expanded(True)
        summary_soa = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} SOA {mname_val} {rname_val} {serial_val} {refresh_val} {retry_val} {expire_val} {minimum_val}"
        copy_full_soa_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_soa_button.set_valign(Gtk.Align.CENTER)
        copy_full_soa_button.set_tooltip_text("Copy Full SOA Record")
        copy_full_soa_button.connect("clicked", lambda _btn, text=summary_soa, w=row: DNSPage._copy_to_clipboard(text, w))
        row.add_suffix(copy_full_soa_button)
        return row

    def _build_generic_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Builds a UI row for generic DNS records that have a 'data' field."""
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}")
        row.add_prefix(Gtk.Image(icon_name="help-question-symbolic"))
        data_value = str(record_data.get('data', 'N/A'))
        data_label = Gtk.Label(label=data_value, halign=Gtk.Align.START, selectable=True, wrap=True)
        copy_button_data = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_button_data.set_valign(Gtk.Align.CENTER)
        copy_button_data.set_tooltip_text(f"Copy Data: {data_value}")
        copy_button_data.connect("clicked", lambda _btn, text=data_value, w=row: DNSPage._copy_to_clipboard(text, w))
        suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        suffix_box.append(data_label)
        suffix_box.append(copy_button_data)
        summary_data = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {data_value}"
        copy_full_button_data = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_button_data.set_valign(Gtk.Align.CENTER)
        copy_full_button_data.set_tooltip_text("Copy Full Record Summary")
        copy_full_button_data.connect("clicked", lambda _btn, text=summary_data, w=row: DNSPage._copy_to_clipboard(text, w))
        suffix_box.append(copy_full_button_data)
        row.add_suffix(suffix_box)
        row.set_selectable(False)
        return row

    def _create_record_row(self, record_data: Dict[str, Any]) -> Gtk.Widget | None:
        """Create a UI row for a single DNS record dictionary."""
        record_type = record_data.get('type', '').upper()
        name = record_data.get('name', 'N/A')
        ttl = record_data.get('ttl', '')
        rd_class_str = record_data.get('class', '')
        base_subtitle = f"Class: {rd_class_str}, TTL: {ttl}"
        if record_type in ("A", "AAAA"): return self._build_address_record_row(record_data, name, base_subtitle, record_type)
        elif record_type in ("CNAME", "NS", "PTR"): return self._build_cname_ns_ptr_record_row(record_data, name, base_subtitle, record_type)
        elif record_type == "MX": return self._build_mx_record_row(record_data, name, base_subtitle, record_type)
        elif record_type == "TXT": return self._build_txt_record_row(record_data, name, base_subtitle, record_type)
        elif record_type == "SOA": return self._build_soa_record_row(record_data, name, base_subtitle, record_type)
        elif record_data.get('data'): return self._build_generic_record_row(record_data, name, base_subtitle, record_type)
        else:
            logger.warning("Could not create row for unknown record_data type or missing data field: %s (Type: %s)", record_data, record_type)
            return None

    def trigger_lookup(self):
        """Programmatically triggers the DNS 'Lookup' action."""
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive(): self.dns_apply_button.clicked()
        else: logger.warning("DNS lookup button not available or not sensitive, cannot trigger lookup.")
