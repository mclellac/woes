"""Defines the DNS lookup page for the Woes application."""
import logging
logger = logging.getLogger(__name__)
import re

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.rdataclass
import gi
from gi.repository import Adw, Gio, Gtk, Pango, GObject, Gdk # Added Gdk
from typing import Tuple, Sequence, Any, List, Dict

from .constants import APP_ID, RESOURCE_PREFIX
# GtkSource specific imports are no longer needed.


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
            if clipboard: # Check if clipboard is available
                clipboard.set(text)
                logger.info("Copied to clipboard: %s", text)
            else:
                logger.warning("Could not get clipboard from widget: %s", widget)
        except Exception as e: # pylint: disable=broad-except
            logger.exception("Error copying to clipboard:")


    def _is_ip_address(self, input_str: str) -> bool:
        """Check if the input string is a valid IP address (IPv4 or IPv6).

        Args:
        ----
            input_str: The string to check.

        Returns:
        -------
            True if the string is a valid IP address, False otherwise.

        """
        try:
            dns.reversename.from_address(input_str)
            return True
        except dns.exception.SyntaxError:
            return False

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """Validate whether the input string is a syntactically valid IP address or domain name.

        Args:
        ----
            input_str: The string to validate.

        Returns:
        -------
            True if the string is a valid IP address or domain name, False otherwise.

        """
        # Basic regex for IPv4, not covering all edge cases but good for quick check.
        # For more robust validation, specific libraries might be better but this is for UI feedback.
        ip_pattern = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
        # Basic domain pattern: starts with a letter or digit, can contain hyphens (not at start/end),
        # and ends with a TLD of at least 2 letters.
        domain_pattern = re.compile(
            r"^(?=.{1,253}$)(?!-)([A-Za-z0-9-]{1,63}(?<!-)\.)+[A-Za-z]{2,63}$"
        )
        is_ip = bool(ip_pattern.match(input_str))
        is_domain = bool(domain_pattern.match(input_str))
        return is_ip or is_domain

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
        if self._is_ip_address(user_input):
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
            self._show_error("Input cannot be empty.")
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop()
                self.dns_lookup_spinner.set_visible(False)
            return

        self._clear_error()

        if not self._is_valid_ip_or_domain(user_input):
            self._show_error("Invalid IP address or domain name.")
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
            if self._is_ip_address(user_input) and actual_type_used == "PTR":
                model = self.dns_record_type_dropdown.get_model()
                for i in range(model.get_n_items()):
                    if model.get_string(i) == "PTR":
                        self.dns_record_type_dropdown.set_selected(i)
                        break

            self._display_result(result_data, user_input, actual_type_used, resolver.nameservers)

        except dns.resolver.NXDOMAIN:
            self._show_error(f"Domain not found: {user_input} (NXDOMAIN)")
        except dns.resolver.NoAnswer:
            # Determine which record type was actually attempted for the error message
            record_type_for_error = (
                "PTR" if self._is_ip_address(user_input) else requested_record_type
            )
            self._show_error(
                f"No {record_type_for_error} records found for {user_input} (NoAnswer)"
            )
        except dns.resolver.Timeout:
            self._show_error(f"DNS query timed out for {user_input}")
        except dns.exception.DNSException as e:  # Catch other DNS-specific exceptions
            logger.exception("DNS lookup failed for %s, type %s:", user_input, requested_record_type)
            self._show_error(f"DNS Error: {str(e)}")
        except Exception as e:  # pylint: disable=broad-except
            logger.exception( # Changed to logger.exception
                "Unexpected error during DNS lookup for %s, type %s:",
                user_input,
                requested_record_type,
            )
            self._show_error(f"An unexpected error occurred: {str(e)}")
        finally:
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop()
                self.dns_lookup_spinner.set_visible(False)

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown.

        Returns
        -------
            The selected record type as a string (e.g., "A", "MX").

        """
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        return model.get_string(selected_index)

    def _show_error(self, message: str):
        """Display an error message in the UI banner and style the entry row.

        Args:
        ----
            message: The error message to display.

        """
        self.domain_entry.add_css_class("error")
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'show_error'):
            main_window.show_error(message)
        else:
            logger.warning("Could not find main window or show_error method to display: %s", message)


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
        """Look up DNS records using the provided resolver and parse them.

        Args:
        ----
            domain_or_ip: The domain name or IP address to query.
            record_type_str: The DNS record type string (e.g., "A", "MX", "PTR").
            resolver: The DNS resolver instance.

        Returns:
        -------
            A list of dictionaries, where each dictionary represents a parsed DNS record.

        Raises:
        ------
            dns.resolver.NXDOMAIN: If the domain does not exist.
            dns.resolver.NoAnswer: If no records of the requested type exist.
            dns.resolver.Timeout: If the query times out.
            dns.exception.DNSException: For other DNS-related errors.

        """
        # Let DNSExceptions propagate
        if record_type_str == "PTR":
            query_name = dns.reversename.from_address(domain_or_ip)
        else:
            query_name = domain_or_ip

        answer = resolver.resolve(query_name, record_type_str)

        parsed_records: List[Dict[str, Any]] = []
        for rdata in answer:
            record: Dict[str, Any] = {
                'name': answer.qname.to_text(),  # The name that was queried
                'ttl': rdata.ttl if hasattr(rdata, 'ttl') else answer.response.answer[0].ttl,  # SOA might not have ttl on rdata itself
                'class': dns.rdataclass.to_text(rdata.rdclass),
                'type': dns.rdatatype.to_text(rdata.rdtype)
            }

            if rdata.rdtype == dns.rdatatype.A:
                record['address'] = rdata.address
            elif rdata.rdtype == dns.rdatatype.AAAA:
                record['address'] = rdata.address
            elif rdata.rdtype == dns.rdatatype.CNAME:
                record['target'] = rdata.target.to_text()
            elif rdata.rdtype == dns.rdatatype.MX:
                record['preference'] = rdata.preference
                record['exchange'] = rdata.exchange.to_text()
            elif rdata.rdtype == dns.rdatatype.TXT:
                record['texts'] = [s.decode('utf-8', 'replace') for s in rdata.strings]  # Assuming strings are UTF-8
            elif rdata.rdtype == dns.rdatatype.NS:
                record['target'] = rdata.target.to_text()
            elif rdata.rdtype == dns.rdatatype.PTR:
                record['target'] = rdata.target.to_text()
            elif rdata.rdtype == dns.rdatatype.SOA:
                record['mname'] = rdata.mname.to_text()
                record['rname'] = rdata.rname.to_text()
                record['serial'] = rdata.serial
                record['refresh'] = rdata.refresh
                record['retry'] = rdata.retry
                record['expire'] = rdata.expire
                record['minimum'] = rdata.minimum
            else:
                record['data'] = rdata.to_text()  # For unhandled types

            parsed_records.append(record)
        return parsed_records

    def _display_result(self, result_records: List[Dict[str, Any]], domain_or_ip: str, record_type: str, dns_servers: Sequence[Any]):
        """Display the DNS lookup results in the UI.

        Args:
        ----
            result_records: The list of parsed DNS record dictionaries.
            domain_or_ip: The domain or IP that was queried.
            record_type: The record type used for the query.
            dns_servers: A list of DNS servers that were used.

        """
        logger.debug( # Added logger.debug
            "Displaying %d results for %s (type %s) using servers %s.",
            len(result_records), domain_or_ip, record_type, dns_servers
        )
        # The existing logging.info and logging.debug for individual records are good.
        logger.info(
            "Query for %s, type %s, using servers %s, returned %d records.",
            domain_or_ip,
            record_type,
            dns_servers,
            len(result_records)
        )
        for rec in result_records:
            logger.debug("Record: %s", rec)

        # Clear previous results
        while (child := self.dns_results_box_container.get_first_child()):
            self.dns_results_box_container.remove(child)

        # Header Info
        query_info_row = Adw.ActionRow(
            title=f"Query: {domain_or_ip}",
            subtitle=f"Record type queried: {record_type}"
        )
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row)

        servers_info_row = Adw.ActionRow(
            title="DNS Servers Used",
            subtitle=", ".join(map(str, dns_servers)) if dns_servers else "System default"
        )
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row)

        self.dns_results_box_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        if not result_records:
            no_records_row = Adw.ActionRow(title="No records found for this query.")
            no_records_row.set_selectable(False)
            self.dns_results_box_container.append(no_records_row)
            return

        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row:
                self.dns_results_box_container.append(row)

    def _create_record_row(self, record_data: Dict[str, Any]) -> Gtk.Widget | None:
        """Create a UI row for a single DNS record dictionary."""
        record_type = record_data.get('type', '').upper()
        name = record_data.get('name', 'N/A')
        ttl = record_data.get('ttl', '')
        rd_class_str = record_data.get('class', '')

        base_subtitle = f"Class: {rd_class_str}, TTL: {ttl}"

        if record_type in ("A", "AAAA"):
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

            summary_a = f"{name} {ttl} {rd_class_str} {record_type} {address_value}"
            copy_full_button_a = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_full_button_a.set_valign(Gtk.Align.CENTER)
            copy_full_button_a.set_tooltip_text("Copy Full Record Summary")
            copy_full_button_a.connect("clicked", lambda _btn, text=summary_a, w=row: DNSPage._copy_to_clipboard(text, w))
            suffix_box.append(copy_full_button_a)
            row.add_suffix(suffix_box)
        elif record_type in ("CNAME", "NS", "PTR"):
            row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}")
            icon_name = "emblem-shared-symbolic"
            if record_type == "NS":
                icon_name = "network-server-symbolic"
            elif record_type == "PTR":
                icon_name = "system-search-symbolic"
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

            summary_cname_ns_ptr = f"{name} {ttl} {rd_class_str} {record_type} {target_value}"
            copy_full_button_cname = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_full_button_cname.set_valign(Gtk.Align.CENTER)
            copy_full_button_cname.set_tooltip_text("Copy Full Record Summary")
            copy_full_button_cname.connect("clicked", lambda _btn, text=summary_cname_ns_ptr, w=row: DNSPage._copy_to_clipboard(text, w))
            suffix_box.append(copy_full_button_cname)
            row.add_suffix(suffix_box)
        elif record_type == "MX":
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

            summary_mx = f"{name} {ttl} {rd_class_str} MX {preference_value} {exchange_value}"
            copy_full_mx_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_full_mx_button.set_valign(Gtk.Align.CENTER)
            copy_full_mx_button.set_tooltip_text("Copy Full MX Record")
            copy_full_mx_button.connect("clicked", lambda _btn, text=summary_mx, w=row: DNSPage._copy_to_clipboard(text, w))
            row.add_suffix(copy_full_mx_button)
        elif record_type == "TXT":
            row = Adw.ExpanderRow(title=name, subtitle=f"TXT Records ({base_subtitle})")
            row.add_prefix(Gtk.Image(icon_name="document-properties-symbolic"))
            texts = record_data.get('texts', [])
            if not texts:
                 row.add_row(Adw.ActionRow(title="No text data.", selectable=False))
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
            summary_txt = f"{name} {ttl} {rd_class_str} TXT {texts_str_summary}"
            copy_full_txt_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_full_txt_button.set_valign(Gtk.Align.CENTER)
            copy_full_txt_button.set_tooltip_text("Copy Full TXT Record")
            copy_full_txt_button.connect("clicked", lambda _btn, text=summary_txt, w=row: DNSPage._copy_to_clipboard(text, w))
            row.add_suffix(copy_full_txt_button)
        elif record_type == "SOA":
            row = Adw.ExpanderRow(title=name, subtitle=f"SOA Record ({base_subtitle})")
            row.add_prefix(Gtk.Image(icon_name="document-settings-symbolic"))
            mname_val = str(record_data.get('mname', 'N/A'))
            rname_val = str(record_data.get('rname', 'N/A'))
            serial_val = str(record_data.get('serial', 'N/A'))
            refresh_val = str(record_data.get('refresh', 'N/A'))
            retry_val = str(record_data.get('retry', 'N/A'))
            expire_val = str(record_data.get('expire', 'N/A'))
            minimum_val = str(record_data.get('minimum', 'N/A'))

            soa_fields = [
                ("MNAME", mname_val), ("RNAME", rname_val), ("Serial", serial_val),
                ("Refresh", refresh_val), ("Retry", retry_val), ("Expire", expire_val),
                ("Minimum TTL", minimum_val)
            ]
            for field_name, field_value in soa_fields:
                field_label = Gtk.Label(label=field_value, halign=Gtk.Align.START, selectable=True)
                copy_button_field = Gtk.Button.new_from_icon_name("content-copy-symbolic")
                copy_button_field.set_valign(Gtk.Align.CENTER)
                copy_button_field.set_tooltip_text(f"Copy {field_name}: {field_value}")
                copy_button_field.connect("clicked", lambda _btn, text=field_value, w=row: DNSPage._copy_to_clipboard(text, w))

                suffix_box_soa_field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                suffix_box_soa_field.append(field_label)
                suffix_box_soa_field.append(copy_button_field)

                soa_field_row = Adw.ActionRow(title=field_name)
                soa_field_row.add_suffix(suffix_box_soa_field)
                soa_field_row.set_selectable(False)
                row.add_row(soa_field_row)
            row.set_expanded(True)

            summary_soa = f"{name} {ttl} {rd_class_str} SOA {mname_val} {rname_val} {serial_val} {refresh_val} {retry_val} {expire_val} {minimum_val}"
            copy_full_soa_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_full_soa_button.set_valign(Gtk.Align.CENTER)
            copy_full_soa_button.set_tooltip_text("Copy Full SOA Record")
            copy_full_soa_button.connect("clicked", lambda _btn, text=summary_soa, w=row: DNSPage._copy_to_clipboard(text, w))
            row.add_suffix(copy_full_soa_button)
        elif record_data.get('data'):
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

            summary_data = f"{name} {ttl} {rd_class_str} {record_type} {data_value}"
            copy_full_button_data = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_full_button_data.set_valign(Gtk.Align.CENTER)
            copy_full_button_data.set_tooltip_text("Copy Full Record Summary")
            copy_full_button_data.connect("clicked", lambda _btn, text=summary_data, w=row: DNSPage._copy_to_clipboard(text, w))
            suffix_box.append(copy_full_button_data)
            row.add_suffix(suffix_box)
        else:
            logger.warning("Could not create row for unknown record_data: %s", record_data)
            return None

        if not isinstance(row, Adw.ExpanderRow):
             row.set_selectable(False)
        return row

    # Removed _format_result_in_buffer
