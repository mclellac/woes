"""Defines the DNS lookup page for the Woes application."""
import logging
import re
from datetime import datetime

import dns.resolver
import dns.reversename
import dns.rdatatype # Added
import dns.rdataclass # Added
import gi
from gi.repository import Adw, Gio, Gtk, Pango, GLib, GObject # Removed GtkSource
from typing import Tuple, Sequence, Any, List, Dict # Added List, Dict, changed from typing.Tuple

from .constants import APP_ID, RESOURCE_PREFIX
# Removed GtkSource specific imports:
# from .style_utils import apply_source_style_scheme
# from .utils import create_source_view

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
# gi.require_version("GtkSource", "5") # Removed GtkSource requirement


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Adw.PreferencesPage):
    """Activity page for performing DNS lookups and displaying results."""

    __gtype_name__ = "DNSPage"

    domain_entry = Gtk.Template.Child("domain_entry") # Changed from dns_ip_entryrow
    dns_lookup_spinner = Gtk.Template.Child("dns_lookup_spinner") # Added spinner
    dns_apply_button = Gtk.Template.Child("dns_apply_button")
    dns_record_type_dropdown = Gtk.Template.Child("dns_record_type_dropdown")
    dns_results_box_container = Gtk.Template.Child("dns_results_box_container") # Changed from dns_results_scrolled_window
    error_banner = Gtk.Template.Child("error_banner")

    def __init__(self, **kwargs):
        """Initialize the DNSPage."""
        super().__init__(**kwargs)
        # self.header_tag = None # Removed Pango tag
        self._connect_signals()
        # Removed GtkSourceView related initializations
        # self.source_view, self.source_buffer = create_source_view()
        # self.dns_results_scrolled_window.set_child(self.source_view)
        self.settings = Gio.Settings.new(APP_ID)
        # self._apply_source_view_style() # Removed
        # Removed GSettings connection for source-style-scheme
        # self.settings.connect(
        # "changed::source-style-scheme", self._on_source_style_scheme_setting_changed
        # )
        if self.dns_apply_button:
            self.dns_apply_button.set_use_underline(True)

        # Initialize spinner state
        if self.dns_lookup_spinner:
            self.dns_lookup_spinner.set_spinning(False)
            self.dns_lookup_spinner.set_visible(False)

        # Removed Pango tag creation
        # try:
        #     self.bold_tag = self.source_buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        #     self.domain_color_tag = self.source_buffer.create_tag(
        #         "domain_color", foreground="#3465a4"
        #     )
        #     self.record_type_color_tag = self.source_buffer.create_tag(
        #         "record_type_color", foreground="#cc0000"
        #     )
        #     self.value_color_tag = self.source_buffer.create_tag(
        #         "value_color", foreground="#73d216"
        #     )
        #     self.ttl_color_tag = self.source_buffer.create_tag("ttl_color", foreground="#fce94f")
        #     self.class_color_tag = self.source_buffer.create_tag(
        #         "class_color", foreground="#75507b"
        #     )
        # except GLib.Error as e:
        #     logging.error("Error creating Pango text tags: %s", e)
        # except Exception as e:  # pylint: disable=broad-except
        #     logging.error("Unexpected error creating text tags (%s): %s", type(e).__name__, e)

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.domain_entry.connect("activate", self._on_entry_activated) # Changed signal from "entry-activated" to "activate"
        self.dns_apply_button.connect("clicked", self._on_entry_activated)
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed)
        if self.error_banner:
            self.error_banner.connect("button-clicked", self._on_error_banner_dismiss)

    # Removed _on_source_style_scheme_setting_changed
    # Removed _apply_source_view_style

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
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec): # Changed GLib.ParamSpec to GObject.ParamSpec
        """Handle the record type dropdown change event.

        Args:
        ----
            _dropdown: The Gtk.DropDown widget whose selection changed.
            _param_spec: The GLib.ParamSpec of the property that changed.

        """
        self._perform_lookup()

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        """Handle the error banner dismiss button click by clearing the error display."""
        self._clear_error() # Corrected from self._on_error_banner_dismiss()

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
    ) -> Tuple[List[Dict[str, Any]], str]: # Changed return type
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
        actual_record_type = requested_record_type
        if self._is_ip_address(user_input):
            actual_record_type = "PTR"  # Override to PTR for IP addresses

        # _lookup_record will call resolver.resolve() which can raise the exceptions
        parsed_records = self._lookup_record(user_input, actual_record_type, resolver) # Changed variable name
        return parsed_records, actual_record_type

    def _perform_lookup(self):  # noqa: C901 # Function complexity is high, consider refactoring.
        """Perform the DNS lookup based on user input and selected record type.

        Handles input validation, prepares the resolver, fetches records,
        and updates the UI with results or error messages.
        """
        if self.dns_lookup_spinner:
            self.dns_lookup_spinner.set_visible(True)
            self.dns_lookup_spinner.start()

        user_input = self.domain_entry.get_text().strip() # Changed from dns_ip_entryrow
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
            logging.error("DNS lookup failed: %s", e)
            self._show_error(f"DNS Error: {str(e)}")
        except Exception as e:  # pylint: disable=broad-except
            logging.error(
                "Unexpected error during DNS lookup (%s): %s",
                type(e).__name__,
                e,
                exc_info=True,
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
        self.domain_entry.add_css_class("error") # Changed from dns_ip_entryrow
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)

    def _clear_error(self):
        """Clear any existing error messages from the UI banner and entry row styling."""
        self.domain_entry.remove_css_class("error") # Changed from dns_ip_entryrow
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")

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
                'name': answer.qname.to_text(), # The name that was queried
                'ttl': rdata.ttl if hasattr(rdata, 'ttl') else answer.response.answer[0].ttl, # SOA might not have ttl on rdata itself
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
                # Assuming strings are UTF-8 encoded, adjust if needed
                record['texts'] = [s.decode('utf-8', 'replace') for s in rdata.strings]
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
                # For unhandled types, store the raw to_text() representation
                record['data'] = rdata.to_text()

            parsed_records.append(record)
        return parsed_records

    def _display_result(self, result_records: List[Dict[str, Any]], domain_or_ip: str, record_type: str, dns_servers: Sequence[Any]):
        """Display the DNS lookup results. (Currently logs, will populate UI later)

        Args:
        ----
            result_records: The list of parsed DNS record dictionaries.
            domain_or_ip: The domain or IP that was queried.
            record_type: The record type used for the query.
            dns_servers: A list of DNS servers that were used.
        """
        # Clear previous results (if any) - UI part will be handled in next step
        # For now, just log the structured data
        logging.info(
            "Query for %s, type %s, using servers %s, returned %d records.",
            domain_or_ip,
            record_type,
            dns_servers,
            len(result_records)
        )
        for rec in result_records:
            logging.debug("Record: %s", rec)

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
            if row: # _create_record_row might return None if a type is somehow unhandled
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
            address_label = Gtk.Label(label=str(record_data.get('address', 'N/A')), halign=Gtk.Align.START, selectable=True)
            row.add_suffix(address_label)
            row.set_activatable_widget(address_label) # Allows text selection on suffix
        elif record_type in ("CNAME", "NS", "PTR"):
            row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}")
            icon_name = "emblem-shared-symbolic" # General purpose, adjust if specific icons are better
            if record_type == "NS":
                icon_name = "network-server-symbolic"
            elif record_type == "PTR":
                icon_name = "system-search-symbolic" # Or similar for reverse lookup
            row.add_prefix(Gtk.Image(icon_name=icon_name))
            target_label = Gtk.Label(label=str(record_data.get('target', 'N/A')), halign=Gtk.Align.START, selectable=True)
            row.add_suffix(target_label)
            row.set_activatable_widget(target_label)
        elif record_type == "MX":
            row = Adw.ExpanderRow(title=name, subtitle=f"MX Record ({base_subtitle})")
            row.add_prefix(Gtk.Image(icon_name="mail-send-receive-symbolic"))

            mx_detail_row = Adw.ActionRow(
                title=str(record_data.get('exchange', 'N/A')),
                subtitle=f"Preference: {record_data.get('preference', 'N/A')}"
            )
            mx_detail_row.set_selectable(True) # Allow selection of exchange/preference
            row.add_row(mx_detail_row)
            row.set_expanded(True) # Usually good to see MX details by default
        elif record_type == "TXT":
            row = Adw.ExpanderRow(title=name, subtitle=f"TXT Records ({base_subtitle})")
            row.add_prefix(Gtk.Image(icon_name="document-properties-symbolic"))
            texts = record_data.get('texts', [])
            if not texts:
                 row.add_row(Adw.ActionRow(title="No text data.", selectable=False))
            for text_string in texts:
                text_row = Adw.ActionRow(title=text_string, selectable=True, subtitle="Text Segment")
                # For very long TXT strings, could add a Gtk.Label with wrapping
                row.add_row(text_row)
            row.set_expanded(True) if texts else row.set_expanded(False)
        elif record_type == "SOA":
            row = Adw.ExpanderRow(title=name, subtitle=f"SOA Record ({base_subtitle})")
            row.add_prefix(Gtk.Image(icon_name="document-settings-symbolic"))
            row.add_row(Adw.ActionRow(title="MNAME", subtitle=str(record_data.get('mname', 'N/A')), selectable=True))
            row.add_row(Adw.ActionRow(title="RNAME", subtitle=str(record_data.get('rname', 'N/A')), selectable=True))
            row.add_row(Adw.ActionRow(title="Serial", subtitle=str(record_data.get('serial', 'N/A')), selectable=True))
            row.add_row(Adw.ActionRow(title="Refresh", subtitle=str(record_data.get('refresh', 'N/A')), selectable=True))
            row.add_row(Adw.ActionRow(title="Retry", subtitle=str(record_data.get('retry', 'N/A')), selectable=True))
            row.add_row(Adw.ActionRow(title="Expire", subtitle=str(record_data.get('expire', 'N/A')), selectable=True))
            row.add_row(Adw.ActionRow(title="Minimum TTL", subtitle=str(record_data.get('minimum', 'N/A')), selectable=True))
            row.set_expanded(True)
        elif record_data.get('data'): # Fallback for unhandled but parsed types
            row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}")
            row.add_prefix(Gtk.Image(icon_name="help-question-symbolic")) # Generic icon
            data_label = Gtk.Label(label=str(record_data.get('data', 'N/A')), halign=Gtk.Align.START, selectable=True, wrap=True)
            row.add_suffix(data_label)
            row.set_activatable_widget(data_label)
        else: # Should not happen if parsing is robust
            logging.warning("Could not create row for unknown record_data: %s", record_data)
            return None

        if not isinstance(row, Adw.ExpanderRow): # ExpanderRow itself is not selectable in this way
             row.set_selectable(False) # Make rows non-interactive for now.
        return row

    # Removed _format_result_in_buffer
