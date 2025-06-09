"""Defines the DNS lookup page for the Woes application.

This module contains the :class:`DNSPage` class, which provides UI and
functionality for performing DNS lookups.
"""
import logging
logger = logging.getLogger(__name__)

# DNS library imports (dns.resolver, etc.) are now primarily in dns_client.py
import gi
from gi.repository import Adw, Gio, Gtk, Pango, GObject # Added GObject
from typing import Tuple, Sequence, Any, List, Dict # Kept for type hints if _display_result uses them

from .constants import APP_ID, RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_ip, is_valid_domain
from .dns_client import (
    DnsResolverClient,
    DnsClientError, # Base for catching all client errors
    DnsResolutionTimeoutError,
    DnsNxDomainError,
    DnsNoAnswerError,
    DnsGenericError
)


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Adw.PreferencesPage):
    """Activity page for performing DNS lookups and displaying results.

    This page allows users to enter a domain name or IP address, select a DNS
    record type, and view the lookup results. It uses the `dnspython` library
    for DNS resolution and supports using a custom DNS server specified in
    application settings.
    """

    __gtype_name__ = "DNSPage"

    domain_entry = Gtk.Template.Child()
    dns_lookup_spinner = Gtk.Template.Child()
    dns_apply_button = Gtk.Template.Child()
    dns_record_type_dropdown = Gtk.Template.Child()
    dns_results_box_container = Gtk.Template.Child()

    def __init__(self, **kwargs: GObject.GObject):
        """Initialize the DNSPage.

        Sets up UI elements, connects signals, and initializes GSettings.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage`
                       constructor.
        :type kwargs: GObject.GObject
        """
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
        self.domain_entry.connect("activate", self._on_entry_activated) # type: ignore
        self.dns_apply_button.connect("clicked", self._on_entry_activated) # type: ignore
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed) # type: ignore

    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        """Copy the given text to the clipboard.

        :param text: The text to copy.
        :type text: str
        :param widget: The widget from which to get the clipboard.
        :type widget: Gtk.Widget
        """
        try:
            clipboard = widget.get_clipboard() # type: ignore
            if clipboard:
                clipboard.set(text) # type: ignore
                logger.info("Copied to clipboard: %s", text)
            else:
                logger.warning("Could not get clipboard from widget: %s", widget)
        except Exception: # pylint: disable=broad-except
            logger.exception("Error copying to clipboard:")

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """Validate if the input is a syntactically valid IP or domain.

        :param input_str: The string to validate.
        :type input_str: str
        :return: ``True`` if the input is a valid IP address or domain name,
                 ``False`` otherwise.
        :rtype: bool
        """
        if not input_str:
            return False
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget) -> None:
        """Handle activation of the domain entry or click of the 'Lookup' button.

        Triggers the DNS lookup process.

        :param _widget: The widget that triggered the action (unused).
        :type _widget: Gtk.Widget
        """
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec) -> None:
        """Handle changes in the selected DNS record type.

        Triggers a new DNS lookup with the new record type.

        :param _dropdown: The :class:`Gtk.DropDown` widget whose selection changed.
        :type _dropdown: Gtk.DropDown
        :param _param_spec: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _param_spec: GObject.ParamSpec
        """
        self._perform_lookup()

    # _prepare_resolver was moved to DnsResolverClient
    # _fetch_dns_records logic is now part of _perform_lookup using DnsResolverClient
    # _lookup_record was moved to DnsResolverClient (as _lookup_record_internal)

    def _perform_lookup(self):  # noqa: C901 # Function complexity is high
        """Perform the DNS lookup based on user input and selected record type.

        Handles input validation, uses :class:`.dns_client.DnsResolverClient`
        for performing the lookup, and then displays results or error messages.
        """
        if self.dns_lookup_spinner:
            self.dns_lookup_spinner.set_visible(True) # type: ignore
            self.dns_lookup_spinner.start() # type: ignore

        user_input = self.domain_entry.get_text().strip() # type: ignore
        requested_record_type = self._get_selected_record_type()
        logger.debug(f"DNSPage: Performing DNS lookup for: {user_input}, type: {requested_record_type}")

        if not user_input:
            show_global_toast(self, "Input cannot be empty.") # type: ignore
            main_window = self.get_native() # type: ignore
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Input cannot be empty.") # type: ignore
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop() # type: ignore
                self.dns_lookup_spinner.set_visible(False) # type: ignore
            return

        self._clear_error()

        if not self._is_valid_ip_or_domain(user_input):
            show_global_toast(self, "Invalid IP address or domain name.") # type: ignore
            main_window = self.get_native() # type: ignore
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Invalid IP address or domain name.") # type: ignore
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop() # type: ignore
                self.dns_lookup_spinner.set_visible(False) # type: ignore
            return

        custom_dns_server = self.settings.get_string("custom-dns-server")
        dns_client = DnsResolverClient(custom_dns_server=custom_dns_server if custom_dns_server else None)

        actual_record_type_used = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() != "PTR":
            # If input is an IP but PTR not selected, inform user PTR will be used
            # Or, automatically switch to PTR if that's desired UX.
            # For now, DnsResolverClient handles PTR if record_type is "PTR".
            # If user enters IP and selects "A", DnsResolverClient will try "A" for IP (likely error/empty).
            # This behavior is kept from original, but could be a UX improvement point.
            pass # No automatic switch here, client will handle based on type.
            # If an IP is given, and record type is not PTR, DnsResolverClient will try to resolve it as is.
            # If PTR is selected, DnsResolverClient's resolve method handles reverse name conversion.

        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
             actual_record_type_used = "PTR" # Ensure this is set if logic relies on it later

        try:
            # DnsResolverClient's resolve method handles PTR for IPs internally if type is "PTR"
            result_data = dns_client.resolve(user_input, requested_record_type)

            # Update dropdown if an IP was entered and PTR was effectively used by client
            # (or if user selected PTR for an IP)
            if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
                 model = self.dns_record_type_dropdown.get_model() # type: ignore
                 if model:
                    for i in range(model.get_n_items()): # type: ignore
                        if model.get_string(i) == "PTR": # type: ignore
                            self.dns_record_type_dropdown.set_selected(i) # type: ignore
                            actual_record_type_used = "PTR"
                            break

            nameservers_used = dns_client.resolver.nameservers
            self._display_result(result_data, user_input, actual_record_type_used, nameservers_used)

        except DnsNxDomainError as e:
            show_global_error(self, str(e)) # type: ignore
        except DnsNoAnswerError as e:
            logger.info("DNSPage: %s", str(e))
            nameservers_used = dns_client.resolver.nameservers
            self._display_result([], user_input, requested_record_type, nameservers_used)
        except DnsResolutionTimeoutError as e:
            show_global_error(self, str(e)) # type: ignore
        except DnsGenericError as e: # Covers other DNS specific errors from client
            logger.exception("DNSPage: DNS lookup failed for %s, type %s:", user_input, requested_record_type)
            show_global_error(self, str(e)) # type: ignore
        except DnsClientError as e: # Catch-all for any other client base errors
            logger.exception("DNSPage: Unexpected DnsClientError for %s, type %s:", user_input, requested_record_type)
            show_global_error(self, f"DNS Client Error: {str(e)}") # type: ignore
        except Exception as e: # Catch any other unexpected errors
            logger.exception("DNSPage: Unexpected error during DNS lookup for %s, type %s:", user_input, requested_record_type)
            show_global_error(self, f"An unexpected error occurred: {str(e)}") # type: ignore
        finally:
            if self.dns_lookup_spinner:
                self.dns_lookup_spinner.stop() # type: ignore
                self.dns_lookup_spinner.set_visible(False) # type: ignore

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown.

        :return: The selected record type as a string (e.g., "A", "MX").
        :rtype: str
        """
        model = self.dns_record_type_dropdown.get_model() # type: ignore
        selected_index = self.dns_record_type_dropdown.get_selected() # type: ignore
        return model.get_string(selected_index) # type: ignore

    def _clear_error(self) -> None:
        """Clear any existing error messages from the UI.

        This includes hiding the main window's error banner and removing
        the 'error' CSS class from the domain entry row.
        """
        self.domain_entry.remove_css_class("error") # type: ignore
        main_window = self.get_native() # type: ignore
        if main_window and hasattr(main_window, 'hide_error'):
            main_window.hide_error() # type: ignore
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    # _lookup_record is now part of DnsResolverClient

    def _display_result(
        self,
        result_records: List[Dict[str, Any]],
        domain_or_ip: str,
        record_type: str,
        dns_servers: Sequence[Any],
    ) -> None:
        """Display the DNS lookup results in the UI.

        Clears previous results and populates the results container with new
        rows based on the lookup outcome.

        :param result_records: A list of parsed DNS record dictionaries.
        :type result_records: list[dict[str, any]]
        :param domain_or_ip: The domain or IP that was queried.
        :type domain_or_ip: str
        :param record_type: The record type that was queried.
        :type record_type: str
        :param dns_servers: A sequence of DNS server addresses used for the query.
        :type dns_servers: collections.abc.Sequence[any]
        """
        logger.debug("Displaying %d results for %s (type %s) using servers %s.", len(result_records), domain_or_ip, record_type, dns_servers)
        logger.info("Query for %s, type %s, using servers %s, returned %d records.", domain_or_ip, record_type, dns_servers, len(result_records))

        while (child := self.dns_results_box_container.get_first_child()): # type: ignore
            self.dns_results_box_container.remove(child) # type: ignore

        query_info_row = Adw.ActionRow(title=f"Query: {domain_or_ip}", subtitle=f"Record type queried: {record_type}") # type: ignore
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row) # type: ignore

        servers_str = ", ".join(map(str, dns_servers)) if dns_servers else "System default"
        servers_info_row = Adw.ActionRow(title="DNS Servers Used", subtitle=servers_str) # type: ignore
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row) # type: ignore
        self.dns_results_box_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)) # type: ignore

        if not result_records:
            no_records_message = f"No {record_type} records found for {domain_or_ip}."
            if record_type == "PTR" and is_valid_ip(domain_or_ip):
                no_records_message = f"No PTR records found for IP address {domain_or_ip}."
            elif record_type == "PTR": # For domain name PTR queries (less common but possible)
                no_records_message = f"No PTR records found for {domain_or_ip}."
            no_records_row = Adw.ActionRow(title=no_records_message) # type: ignore
            no_records_row.set_selectable(False)
            self.dns_results_box_container.append(no_records_row) # type: ignore
            return

        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row:
                self.dns_results_box_container.append(row) # type: ignore

    def _build_address_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Build a UI row for an A or AAAA DNS record.

        :param record_data: Dictionary containing parsed record data.
        :type record_data: dict[str, any]
        :param name: The record name.
        :type name: str
        :param base_subtitle: Base subtitle string (Class, TTL).
        :type base_subtitle: str
        :param record_type: The record type ("A" or "AAAA").
        :type record_type: str
        :return: An :class:`Adw.ActionRow` displaying the address record.
        :rtype: Adw.ActionRow
        """
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}") # type: ignore
        row.add_prefix(Gtk.Image(icon_name="network-wired-symbolic")) # type: ignore
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
        copy_full_button_a.connect("clicked", lambda _btn, text=summary_a, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
        suffix_box.append(copy_full_button_a)
        row.add_suffix(suffix_box) # type: ignore
        row.set_selectable(False)
        return row

    def _build_cname_ns_ptr_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Build a UI row for CNAME, NS, or PTR DNS records.

        :param record_data: Dictionary containing parsed record data.
        :type record_data: dict[str, any]
        :param name: The record name.
        :type name: str
        :param base_subtitle: Base subtitle string (Class, TTL).
        :type base_subtitle: str
        :param record_type: The record type ("CNAME", "NS", or "PTR").
        :type record_type: str
        :return: An :class:`Adw.ActionRow` displaying the record.
        :rtype: Adw.ActionRow
        """
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}") # type: ignore
        icon_name = "emblem-shared-symbolic"
        if record_type == "NS": icon_name = "network-server-symbolic"
        elif record_type == "PTR": icon_name = "system-search-symbolic"
        row.add_prefix(Gtk.Image(icon_name=icon_name)) # type: ignore
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
        copy_full_button_cname.connect("clicked", lambda _btn, text=summary_cname_ns_ptr, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
        suffix_box.append(copy_full_button_cname)
        row.add_suffix(suffix_box) # type: ignore
        row.set_selectable(False)
        return row

    def _build_mx_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Build a UI row for an MX DNS record.

        :param record_data: Dictionary containing parsed record data.
        :type record_data: dict[str, any]
        :param name: The record name.
        :type name: str
        :param base_subtitle: Base subtitle string (Class, TTL).
        :type base_subtitle: str
        :param record_type: The record type ("MX").
        :type record_type: str
        :return: An :class:`Adw.ExpanderRow` displaying the MX record details.
        :rtype: Adw.ExpanderRow
        """
        row = Adw.ExpanderRow(title=name, subtitle=f"MX Record ({base_subtitle})") # type: ignore
        row.add_prefix(Gtk.Image(icon_name="mail-send-receive-symbolic")) # type: ignore
        exchange_value = str(record_data.get('exchange', 'N/A'))
        preference_value = str(record_data.get('preference', 'N/A'))

        mx_detail_row_title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mx_detail_row_title_box.append(Gtk.Label(label=exchange_value, halign=Gtk.Align.START, selectable=True))
        copy_button_exchange = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_button_exchange.set_valign(Gtk.Align.CENTER)
        copy_button_exchange.set_tooltip_text(f"Copy Exchange: {exchange_value}")
        copy_button_exchange.connect("clicked", lambda _btn, text=exchange_value, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
        mx_detail_row_title_box.append(copy_button_exchange)

        mx_detail_row = Adw.ActionRow(subtitle=f"Preference: {preference_value}") # type: ignore
        mx_detail_row.add_prefix(mx_detail_row_title_box) # type: ignore
        mx_detail_row.set_selectable(True)
        row.add_row(mx_detail_row) # type: ignore
        row.set_expanded(True) # type: ignore

        summary_mx = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} MX {preference_value} {exchange_value}"
        copy_full_mx_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_mx_button.set_valign(Gtk.Align.CENTER)
        copy_full_mx_button.set_tooltip_text("Copy Full MX Record")
        copy_full_mx_button.connect("clicked", lambda _btn, text=summary_mx, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
        row.add_suffix(copy_full_mx_button) # type: ignore
        return row

    def _build_txt_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Build a UI row for a TXT DNS record.

        :param record_data: Dictionary containing parsed record data.
        :type record_data: dict[str, any]
        :param name: The record name.
        :type name: str
        :param base_subtitle: Base subtitle string (Class, TTL).
        :type base_subtitle: str
        :param record_type: The record type ("TXT").
        :type record_type: str
        :return: An :class:`Adw.ExpanderRow` displaying TXT record strings.
        :rtype: Adw.ExpanderRow
        """
        row = Adw.ExpanderRow(title=name, subtitle=f"TXT Records ({base_subtitle})") # type: ignore
        row.add_prefix(Gtk.Image(icon_name="document-properties-symbolic")) # type: ignore
        texts = record_data.get('texts', [])
        if not texts:
            row.add_row(Adw.ActionRow(title="No text data.", selectable=False)) # type: ignore
        for text_string in texts:
            text_label = Gtk.Label(label=text_string, halign=Gtk.Align.START, selectable=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            copy_button_segment = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_button_segment.set_valign(Gtk.Align.CENTER)
            copy_button_segment.set_tooltip_text("Copy Text Segment")
            copy_button_segment.connect("clicked", lambda _btn, text=text_string, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
            text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            text_box.append(text_label)
            text_box.append(copy_button_segment)
            text_action_row = Adw.ActionRow() # type: ignore
            text_action_row.add_prefix(text_box) # type: ignore
            text_action_row.set_selectable(False)
            row.add_row(text_action_row) # type: ignore
        row.set_expanded(True if texts else False) # type: ignore

        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} TXT {texts_str_summary}"
        copy_full_txt_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_txt_button.set_valign(Gtk.Align.CENTER)
        copy_full_txt_button.set_tooltip_text("Copy Full TXT Record")
        copy_full_txt_button.connect("clicked", lambda _btn, text=summary_txt, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
        row.add_suffix(copy_full_txt_button) # type: ignore
        return row

    def _build_soa_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Build a UI row for an SOA DNS record.

        :param record_data: Dictionary containing parsed record data.
        :type record_data: dict[str, any]
        :param name: The record name.
        :type name: str
        :param base_subtitle: Base subtitle string (Class, TTL).
        :type base_subtitle: str
        :param record_type: The record type ("SOA").
        :type record_type: str
        :return: An :class:`Adw.ExpanderRow` displaying SOA record details.
        :rtype: Adw.ExpanderRow
        """
        row = Adw.ExpanderRow(title=name, subtitle=f"SOA Record ({base_subtitle})") # type: ignore
        row.add_prefix(Gtk.Image(icon_name="document-settings-symbolic")) # type: ignore
        mname_val, rname_val, serial_val, refresh_val, retry_val, expire_val, minimum_val = \
            (str(record_data.get(k, 'N/A')) for k in ['mname','rname','serial','refresh','retry','expire','minimum'])
        soa_fields = [
            ("MNAME", mname_val), ("RNAME", rname_val), ("Serial", serial_val),
            ("Refresh", refresh_val), ("Retry", retry_val), ("Expire", expire_val),
            ("Minimum TTL", minimum_val)
        ]
        for field_name_str, field_value in soa_fields:
            field_label = Gtk.Label(label=field_value, halign=Gtk.Align.START, selectable=True)
            copy_button_field = Gtk.Button.new_from_icon_name("content-copy-symbolic")
            copy_button_field.set_valign(Gtk.Align.CENTER)
            copy_button_field.set_tooltip_text(f"Copy {field_name_str}: {field_value}")
            copy_button_field.connect("clicked", lambda _btn, text=field_value, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
            suffix_box_soa_field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            suffix_box_soa_field.append(field_label)
            suffix_box_soa_field.append(copy_button_field)
            soa_field_row = Adw.ActionRow(title=field_name_str) # type: ignore
            soa_field_row.add_suffix(suffix_box_soa_field) # type: ignore
            soa_field_row.set_selectable(False)
            row.add_row(soa_field_row) # type: ignore
        row.set_expanded(True) # type: ignore

        summary_soa = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} SOA {mname_val} {rname_val} {serial_val} {refresh_val} {retry_val} {expire_val} {minimum_val}"
        copy_full_soa_button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        copy_full_soa_button.set_valign(Gtk.Align.CENTER)
        copy_full_soa_button.set_tooltip_text("Copy Full SOA Record")
        copy_full_soa_button.connect("clicked", lambda _btn, text=summary_soa, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
        row.add_suffix(copy_full_soa_button) # type: ignore
        return row

    def _build_generic_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Build a UI row for a generic DNS record (displays raw 'data' field).

        :param record_data: Dictionary containing parsed record data.
        :type record_data: dict[str, any]
        :param name: The record name.
        :type name: str
        :param base_subtitle: Base subtitle string (Class, TTL).
        :type base_subtitle: str
        :param record_type: The record type.
        :type record_type: str
        :return: An :class:`Adw.ActionRow` displaying the generic record data.
        :rtype: Adw.ActionRow
        """
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, {base_subtitle}") # type: ignore
        row.add_prefix(Gtk.Image(icon_name="help-question-symbolic")) # type: ignore
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
        copy_full_button_data.connect("clicked", lambda _btn, text=summary_data, w=row: DNSPage._copy_to_clipboard(text, w)) # type: ignore
        suffix_box.append(copy_full_button_data)
        row.add_suffix(suffix_box) # type: ignore
        row.set_selectable(False)
        return row

    def _create_record_row(self, record_data: Dict[str, Any]) -> Optional[Gtk.Widget]:
        """Create a UI row for a single DNS record dictionary.

        Delegates to specific `_build_*_record_row` methods based on record type.

        :param record_data: A dictionary containing the parsed data for one DNS record.
        :type record_data: dict[str, any]
        :return: A :class:`Gtk.Widget` (typically an :class:`Adw.ActionRow` or
                 :class:`Adw.ExpanderRow`) representing the record, or ``None`` if
                 the record type is unknown or cannot be displayed.
        :rtype: Gtk.Widget | None
        """
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

    def trigger_lookup(self) -> None:
        """Programmatically trigger the DNS 'Lookup' action.

        This is typically called via a keyboard shortcut. It simulates a click
        on the 'Lookup' button if it's available and sensitive.
        """
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive(): # type: ignore
            self.dns_apply_button.clicked() # type: ignore
        else:
            logger.warning("DNS lookup button not available or not sensitive, cannot trigger lookup.")
