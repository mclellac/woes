"""Defines the DNS lookup page for the Woes application.

This module contains the :class:`DNSPage` class, which provides UI and
functionality for performing DNS lookups.
"""
from .dns_client import (
    DnsResolverClient,
    DnsClientError,  # Base for catching all client errors
    DnsResolutionTimeoutError,
    DnsNxDomainError,
    DnsNoAnswerError,
    DnsGenericError
)
from .utils import show_global_error, show_global_toast, is_valid_ip, is_valid_domain
from .constants import APP_ID, RESOURCE_PREFIX
from typing import Optional, Sequence, Any, List, Dict
from gi.repository import Adw, Gio, Gtk, Pango, GObject
import gi
import logging
logger = logging.getLogger(__name__)

# DNS library imports (dns.resolver, etc.) are now primarily in dns_client.py


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
    dns_apply_button = Gtk.Template.Child()
    dns_record_type_dropdown = Gtk.Template.Child()  # type: ignore
    dns_results_box_container = Gtk.Template.Child()  # type: ignore
    dns_clear_results_button = Gtk.Template.Child()  # type: ignore
    dns_copy_all_results_button = Gtk.Template.Child()  # type: ignore
    dns_status_row = Gtk.Template.Child()  # type: ignore
    dns_status_spinner = Gtk.Template.Child()  # type: ignore

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

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.domain_entry.connect(
            "activate", self._on_entry_activated)  # type: ignore
        self.dns_apply_button.connect(
            "clicked", self._on_entry_activated)  # type: ignore
        self.dns_record_type_dropdown.connect(
            "notify::selected", self._on_record_type_changed)  # type: ignore
        if self.dns_clear_results_button:
            self.dns_clear_results_button.connect(
                "clicked", self._on_clear_results_clicked)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.connect(
                "clicked", self._on_copy_all_results_clicked)

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """Clear all DNS lookup results from the UI."""
        logger.info("Clearing DNS results.")
        while (child := self.dns_results_box_container.get_first_child()):
            self.dns_results_box_container.remove(child)

        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(False)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(False)

    def _on_copy_all_results_clicked(self, _button: Gtk.Button) -> None:
        """Copy all displayed DNS results to the clipboard."""
        logger.info("Copying all DNS results to clipboard.")
        all_results_text = []
        child = self.dns_results_box_container.get_first_child()
        while child:
            text_parts_for_child = []
            if isinstance(child, Adw.ActionRow):
                title = child.get_title()
                subtitle = child.get_subtitle()
                if title:
                    text_parts_for_child.append(title)
                if subtitle:
                    text_parts_for_child.append(subtitle)
            elif isinstance(child, Adw.ExpanderRow):
                title = child.get_title()
                subtitle = child.get_subtitle()
                if title:
                    text_parts_for_child.append(title)
                if subtitle:
                    text_parts_for_child.append(subtitle)
            if text_parts_for_child:
                all_results_text.append(" - ".join(text_parts_for_child))
            child = child.get_next_sibling()
        if not all_results_text:
            show_global_toast(self, "No results to copy.")  # type: ignore
            return
        final_text_to_copy = "\n".join(all_results_text)
        DNSPage._copy_to_clipboard(final_text_to_copy, self)
        show_global_toast(self, "All results copied to clipboard.")

    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        """Copy the given text to the clipboard."""
        try:
            clipboard = widget.get_clipboard()  # type: ignore
            if clipboard:
                clipboard.set(text)  # type: ignore
                logger.info("Copied to clipboard: %s", text)
            else:
                logger.warning(
                    "Could not get clipboard from widget: %s", widget)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error copying to clipboard:")

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """Validate if the input is a syntactically valid IP or domain."""
        if not input_str:
            return False
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget) -> None:
        """Handle activation of the domain entry or click of the 'Lookup' button."""
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec) -> None:
        """Handle changes in the selected DNS record type."""
        self._perform_lookup()

    # --- Helper methods for building record rows ---
    def _create_copy_button(
            self, text_to_copy: str, tooltip_text: str,
            widget_for_clipboard: Gtk.Widget) -> Gtk.Button:
        """Creates a Gtk.Button for copying text."""
        button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip_text)
        button.connect("clicked", lambda _btn, text=text_to_copy,
                       w=widget_for_clipboard:
                       DNSPage._copy_to_clipboard(text, w))
        return button

    def _create_base_action_row(
            self, name: str, record_type_label: str,
            base_subtitle_text: str,
            icon_name: Optional[str]) -> Adw.ActionRow:
        """Creates a basic Adw.ActionRow with title, subtitle, and optional
        icon."""
        row = Adw.ActionRow(
            title=name,
            subtitle=f"Type: {record_type_label}, {base_subtitle_text}")
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name))
        row.set_selectable(False)
        return row

    def _add_standard_suffix_box_to_row(
        self,
        row: Adw.ActionRow,
        main_value_text: str,
        main_value_tooltip_prefix: str,
        full_summary_text: str
    ) -> None:
        """Adds a standard suffix box (label, copy value button, copy summary
        button) to an ActionRow."""
        value_label = Gtk.Label(
            label=main_value_text, halign=Gtk.Align.FILL, hexpand=True,
            selectable=True, wrap=False, lines=1,
            ellipsize=Pango.EllipsizeMode.END)
        row.add_suffix(value_label)
        row.add_suffix(self._create_copy_button(
            main_value_text,
            f"{main_value_tooltip_prefix}: {main_value_text}", row))
        row.add_suffix(self._create_copy_button(
            full_summary_text, "Copy Full Record Summary", row))

    def _create_base_expander_row(
            self, name: str, subtitle_text: str, icon_name: Optional[str],
            full_summary_text: str) -> Adw.ExpanderRow:
        """Creates a basic Adw.ExpanderRow with title, subtitle, icon, and a
        full summary copy button."""
        row = Adw.ExpanderRow(title=name, subtitle=subtitle_text)
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name))
        copy_full_button = self._create_copy_button(
            full_summary_text, "Copy Full Record Summary", row)
        row.add_suffix(copy_full_button)
        return row

    def _add_expander_detail_row(
        self,
        expander_row: Adw.ExpanderRow,
        title: Optional[str],
        value_text: str,
        copy_tooltip_prefix: str,
        is_value_primary_content: bool = False
    ):
        """Adds a detail row (Adw.ActionRow) to an Adw.ExpanderRow."""
        detail_row = Adw.ActionRow(title=title if title else None)
        value_label = Gtk.Label(
            label=value_text, halign=Gtk.Align.FILL, hexpand=True,
            selectable=True, wrap=False, lines=1,
            ellipsize=Pango.EllipsizeMode.END)
        copy_button = self._create_copy_button(
            value_text, f"{copy_tooltip_prefix}: {value_text}", expander_row)
        if is_value_primary_content:
            detail_row.add_prefix(value_label)
            detail_row.add_prefix(copy_button)
        else:
            detail_row.add_suffix(value_label)
            detail_row.add_suffix(copy_button)
        detail_row.set_selectable(False)
        expander_row.add_row(detail_row)

    def _set_loading_state(self, active: bool,
                           message: Optional[str] = None) -> None:
        """Sets the UI loading state (spinner, status message, sensitivity)."""
        if self.dns_status_spinner:
            self.dns_status_spinner.set_visible(active)
            if active:
                self.dns_status_spinner.start()
            else:
                self.dns_status_spinner.stop()

        if self.dns_status_row:
            current_subtitle = self.dns_status_row.get_subtitle()
            if message:
                self.dns_status_row.set_subtitle(message)
            elif active and current_subtitle != "Looking up...":
                self.dns_status_row.set_subtitle("Looking up...")
            elif not active and not message:
                self.dns_status_row.set_subtitle("Idle")

        if self.domain_entry:
            self.domain_entry.set_sensitive(not active)
        if self.dns_apply_button:
            self.dns_apply_button.set_sensitive(not active)
        if self.dns_record_type_dropdown:
            self.dns_record_type_dropdown.set_sensitive(not active)

    def _validate_dns_input(self, user_input: str) -> bool:
        """Validates the DNS user input. Shows global error/toast if invalid.
        Returns:
            True if valid, False otherwise.
        """
        if not user_input:
            show_global_toast(self, "Input cannot be empty.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Input cannot be empty.")
            return False

        if not self._is_valid_ip_or_domain(user_input):
            show_global_toast(self, "Invalid IP address or domain name.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Invalid IP address or domain name.")
            return False
        return True

    def _update_ptr_dropdown(self, user_input: str,
                             requested_record_type: str) -> str:
        """Updates the record type dropdown to PTR if an IP was entered and PTR
        was requested. Returns the record type that was effectively used or set.
        """
        actual_record_type_used = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            model = self.dns_record_type_dropdown.get_model()
            if model:
                for i in range(model.get_n_items()):
                    if model.get_string(i) == "PTR":
                        self.dns_record_type_dropdown.set_selected(i)
                        actual_record_type_used = "PTR"
                        break
        return actual_record_type_used

    def _handle_dns_lookup_success(
        self,
        result_data: List[Dict[str, Any]],
        user_input: str,
        requested_record_type: str,
        dns_client: DnsResolverClient
    ) -> None:
        """Handles successful DNS lookup results."""
        actual_record_type_displayed = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            actual_record_type_displayed = self._update_ptr_dropdown(
                user_input, requested_record_type)

        nameservers_used = dns_client.resolver.nameservers
        self._display_result(result_data, user_input,
                             actual_record_type_displayed, nameservers_used)

        status_message = (f"{len(result_data)} "
                          f"{actual_record_type_displayed} record(s) found."
                          if result_data else
                          f"No {actual_record_type_displayed} records found "
                          f"for {user_input}.")
        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_message)
        show_global_toast(self, status_message)

    def _handle_dns_lookup_exception(
        self,
        error: Exception,
        user_input: str,
        requested_record_type: str,
        dns_client: DnsResolverClient
    ) -> None:
        """Handles exceptions from DnsResolverClient."""
        error_message = str(error)
        status_subtitle = f"Error: {error_message.splitlines()[0]}"

        if isinstance(error, DnsNxDomainError):
            show_global_error(self, error_message)
            status_subtitle = f"NXDOMAIN: Domain '{user_input}' not found."
        elif isinstance(error, DnsNoAnswerError):
            logger.info("DNSPage: %s", error_message)
            nameservers_used = dns_client.resolver.nameservers
            self._display_result(
                [], user_input, requested_record_type, nameservers_used)
            status_subtitle = (f"No {requested_record_type} records found for "
                               f"{user_input} (No Answer).")
        elif isinstance(error, DnsResolutionTimeoutError):
            show_global_error(self, error_message)
            status_subtitle = f"Timeout: Could not resolve {user_input}."
        elif isinstance(error, DnsGenericError):
            logger.exception(
                "DNSPage: DNS lookup failed for %s, type %s "
                "(DnsGenericError):", user_input, requested_record_type)
            show_global_error(self, error_message)
            status_subtitle = f"DNS Error: {error_message.splitlines()[0]}"
        elif isinstance(error, DnsClientError):
            logger.exception(
                "DNSPage: Unexpected DnsClientError for %s, type %s:",
                user_input, requested_record_type)
            show_global_error(self, f"DNS Client Error: {error_message}")
            status_subtitle = f"Client Error: {error_message.splitlines()[0]}"
        else:
            logger.exception(
                "DNSPage: Unexpected error during DNS lookup for %s, "
                "type %s:", user_input, requested_record_type)
            error_message_short = (f"An unexpected error occurred: "
                                   f"{error_message.splitlines()[0]}")
            show_global_error(self, error_message_short)
            status_subtitle = error_message_short

        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_subtitle)

    def _perform_lookup(self) -> None:  # noqa: C901
        """Perform the DNS lookup based on user input and selected record type.

        Orchestrates input validation, client interaction, and result/error display.
        """
        self._set_loading_state(True, "Looking up...")
        user_input = self.domain_entry.get_text().strip()  # type: ignore
        requested_record_type = self._get_selected_record_type()
        logger.debug(
            f"DNSPage: Performing DNS lookup for: {user_input}, type: {requested_record_type}")

        if not self._validate_dns_input(user_input):
            self._set_loading_state(False, "Idle - Invalid input.")
            return

        self._clear_error()

        custom_dns_server = self.settings.get_string("custom-dns-server")
        dns_client = DnsResolverClient(
            custom_dns_server=custom_dns_server or None)

        try:
            result_data = dns_client.resolve(user_input, requested_record_type)
            self._handle_dns_lookup_success(
                result_data, user_input, requested_record_type, dns_client)
        except Exception as e:
            self._handle_dns_lookup_exception(
                e, user_input, requested_record_type, dns_client)
        finally:
            self._set_loading_state(False)

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown.

        :return: The selected record type as a string (e.g., "A", "MX").
        :rtype: str
        """
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        return model.get_string(selected_index)

    def _clear_error(self) -> None:
        """Clear any existing error messages from the UI.

        This includes hiding the main window's error banner and removing
        the 'error' CSS class from the domain entry row.
        """
        self.domain_entry.remove_css_class("error")  # type: ignore
        main_window = self.get_native()
        if main_window and hasattr(main_window, 'hide_error'):
            main_window.hide_error()
        else:
            logger.warning(
                "Could not find main window or hide_error method to clear error.")

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
        """
        logger.debug(
            "Displaying %d results for %s (type %s) using servers %s.",
            len(result_records), domain_or_ip, record_type, dns_servers)
        logger.info(
            "Query for %s, type %s, using servers %s, returned %d records.",
            domain_or_ip, record_type, dns_servers, len(result_records))

        while (child := self.dns_results_box_container.get_first_child()):
            self.dns_results_box_container.remove(child)

        query_info_row = Adw.ActionRow(
            title=f"Query: {domain_or_ip}",
            subtitle=f"Record type queried: {record_type}")
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row)

        servers_str = (", ".join(map(str, dns_servers))
                       if dns_servers else "System default")
        servers_info_row = Adw.ActionRow(
            title="DNS Servers Used", subtitle=servers_str)
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row)
        self.dns_results_box_container.append(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        if not result_records:
            no_records_message = (f"No {record_type} records found for "
                                  f"{domain_or_ip}.")
            if record_type == "PTR" and is_valid_ip(domain_or_ip):
                no_records_message = (f"No PTR records found for IP address "
                                      f"{domain_or_ip}.")
            elif record_type == "PTR":
                no_records_message = (f"No PTR records found for "
                                      f"{domain_or_ip}.")
            no_records_row = Adw.ActionRow(title=no_records_message)
            no_records_row.set_selectable(False)
            self.dns_results_box_container.append(no_records_row)
            if self.dns_clear_results_button:
                self.dns_clear_results_button.set_sensitive(False)
            if self.dns_copy_all_results_button:
                self.dns_copy_all_results_button.set_sensitive(False)
            return

        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row:
                self.dns_results_box_container.append(row)

        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(True)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(True)

    # --- Modified _build_*_record_row methods ---

    def _build_address_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Build a UI row for an A or AAAA DNS record."""
        row = self._create_base_action_row(
            name, record_type, base_subtitle, "network-wired-symbolic")
        address_value = str(record_data.get('address', 'N/A'))
        summary_text = (f"{name} {record_data.get('ttl', '')} "
                        f"{record_data.get('class', '')} {record_type} "
                        f"{address_value}")
        self._add_standard_suffix_box_to_row(
            row, address_value, "Copy Address", summary_text)
        return row

    def _build_cname_ns_ptr_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Build a UI row for CNAME, NS, or PTR DNS records."""
        icon_name = "emblem-shared-symbolic"  # Default for CNAME
        if record_type == "NS":
            icon_name = "network-server-symbolic"
        elif record_type == "PTR":
            icon_name = "system-search-symbolic"

        row = self._create_base_action_row(
            name, record_type, base_subtitle, icon_name)
        target_value = str(record_data.get('target', 'N/A'))
        summary_text = (f"{name} {record_data.get('ttl', '')} "
                        f"{record_data.get('class', '')} {record_type} "
                        f"{target_value}")
        self._add_standard_suffix_box_to_row(
            row, target_value, "Copy Target", summary_text)
        return row

    def _build_generic_data_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Builds a UI row for generic DNS records that have a 'data' field."""
        row = self._create_base_action_row(
            name, record_type, base_subtitle, "help-question-symbolic")
        data_value = str(record_data.get('data', 'N/A'))
        summary_text = (f"{name} {record_data.get('ttl', '')} "
                        f"{record_data.get('class', '')} {record_type} "
                        f"{data_value}")
        self._add_standard_suffix_box_to_row(
            row, data_value, "Copy Data", summary_text)
        return row

    def _build_mx_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Build a UI row for an MX DNS record."""
        exchange_value = str(record_data.get('exchange', 'N/A'))
        preference_value = str(record_data.get('preference', 'N/A'))
        summary_mx = (f"{name} {record_data.get('ttl', '')} "
                      f"{record_data.get('class', '')} MX {preference_value} "
                      f"{exchange_value}")

        row = self._create_base_expander_row(
            name, f"MX Record ({base_subtitle})",
            "mail-send-receive-symbolic", summary_mx)

        exchange_value_label = Gtk.Label(
            label=exchange_value, halign=Gtk.Align.FILL, hexpand=True,
            selectable=True, wrap=False, lines=1,
            ellipsize=Pango.EllipsizeMode.END)
        copy_button_exchange = self._create_copy_button(
            exchange_value, f"Copy Exchange: {exchange_value}", row)

        mx_detail_row = Adw.ActionRow(
            subtitle=f"Preference: {preference_value}")
        mx_detail_row.add_prefix(exchange_value_label)
        mx_detail_row.add_prefix(copy_button_exchange)
        mx_detail_row.set_selectable(True)
        row.add_row(mx_detail_row)
        row.set_expanded(True)
        return row

    def _build_txt_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Build a UI row for a TXT DNS record."""
        texts = record_data.get('texts', [])
        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = (f"{name} {record_data.get('ttl', '')} "
                       f"{record_data.get('class', '')} TXT "
                       f"{texts_str_summary}")

        row = self._create_base_expander_row(
            name, f"TXT Records ({base_subtitle})",
            "document-properties-symbolic", summary_txt)

        if not texts:
            no_text_row = Adw.ActionRow(title="No text data.",
                                        selectable=False)
            row.add_row(no_text_row)
        else:
            for text_string in texts:
                self._add_expander_detail_row(
                    row, None, text_string, "Copy Text Segment",
                    is_value_primary_content=True)
        row.set_expanded(bool(texts))
        return row

    def _build_soa_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Build a UI row for an SOA DNS record."""
        mname_val = str(record_data.get('mname', 'N/A'))
        rname_val = str(record_data.get('rname', 'N/A'))
        serial_val = str(record_data.get('serial', 'N/A'))
        refresh_val = str(record_data.get('refresh', 'N/A'))
        retry_val = str(record_data.get('retry', 'N/A'))
        expire_val = str(record_data.get('expire', 'N/A'))
        minimum_val = str(record_data.get('minimum', 'N/A'))

        summary_soa = (f"{name} {record_data.get('ttl', '')} "
                       f"{record_data.get('class', '')} SOA {mname_val} "
                       f"{rname_val} {serial_val} {refresh_val} {retry_val} "
                       f"{expire_val} {minimum_val}")
        row = self._create_base_expander_row(
            name, f"SOA Record ({base_subtitle})",
            "document-settings-symbolic", summary_soa)

        soa_fields = [
            ("MNAME", mname_val), ("RNAME", rname_val),
            ("Serial", serial_val), ("Refresh", refresh_val),
            ("Retry", retry_val), ("Expire", expire_val),
            ("Minimum TTL", minimum_val)
        ]
        for field_name_str, field_value in soa_fields:
            self._add_expander_detail_row(
                row, field_name_str, field_value, f"Copy {field_name_str}")
        row.set_expanded(True)
        return row

    def _create_record_row(self, record_data: Dict[str, Any]) -> Optional[Gtk.Widget]:
        """Create a UI row for a single DNS record dictionary.

        Delegates to specific `_build_*_record_row` methods based on record type.
        """
        record_type = record_data.get('type', '').upper()
        name = record_data.get('name', 'N/A')
        ttl = record_data.get('ttl', '')
        rd_class_str = record_data.get('class', '')
        base_subtitle = f"Class: {rd_class_str}, TTL: {ttl}"
        if record_type in ("A", "AAAA"):
            return self._build_address_record_row(record_data, name, base_subtitle, record_type)
        elif record_type in ("CNAME", "NS", "PTR"):
            return self._build_cname_ns_ptr_record_row(record_data, name, base_subtitle, record_type)
        elif record_type == "MX":
            return self._build_mx_record_row(record_data, name, base_subtitle, record_type)
        elif record_type == "TXT":
            return self._build_txt_record_row(record_data, name, base_subtitle, record_type)
        elif record_type == "SOA":
            return self._build_soa_record_row(record_data, name, base_subtitle, record_type)
        elif record_data.get('data'):
            return self._build_generic_data_record_row(record_data, name, base_subtitle, record_type)
        else:
            logger.warning(
                "Could not create row for unknown record_data type or missing data field: %s (Type: %s)", record_data, record_type)
            return None

    def trigger_lookup(self) -> None:
        """Programmatically trigger the DNS 'Lookup' action."""
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive():  # type: ignore
            self.dns_apply_button.clicked()  # type: ignore
        else:
            logger.warning(
                "DNS lookup button not available or not sensitive, cannot trigger lookup.")
