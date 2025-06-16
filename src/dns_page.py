"""
Defines the DNS lookup page for the Woes application.

This module contains the :class:`.DNSPage` class, which provides UI and
functionality for performing DNS lookups.
"""

import logging

logger = logging.getLogger(__name__)

import gi
from gi.repository import Adw, Gio, Gtk, Pango, GObject
from typing import Optional, Sequence, Any, List, Dict

from .constants import APP_ID, RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_ip, is_valid_domain
from .gtk_utils import ( # Import all new utilities
    create_copy_button,
    _copy_to_clipboard as gtk_utils_copy_to_clipboard,
    create_detail_action_row,
    create_expander_row,
    add_detail_to_expander
)
from .dns_client import (
    DnsResolverClient,
    DnsClientError,
    DnsResolutionTimeoutError,
    DnsNxDomainError,
    DnsNoAnswerError,
    DnsGenericError,
)


gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Gtk.Box):
    """
    Activity page for performing DNS lookups and displaying results.
    """

    __gtype_name__ = "DNSPage"

    domain_entry = Gtk.Template.Child()
    dns_apply_button = Gtk.Template.Child()
    dns_record_type_dropdown = Gtk.Template.Child()
    dns_results_box_container = Gtk.Template.Child()
    dns_clear_results_button = Gtk.Template.Child()
    dns_copy_all_results_button = Gtk.Template.Child()
    dns_status_row = Gtk.Template.Child()
    dns_status_spinner = Gtk.Template.Child()

    def __init__(self, **kwargs: GObject.GObject):
        """Initialize the DNSPage."""
        super().__init__(**kwargs)
        logger.debug("DNSPage initialized.")
        self.settings = Gio.Settings.new(APP_ID)

        self._output_font_gsettings_key = "output-font"
        output_font_str = self.settings.get_string(self._output_font_gsettings_key)
        self._output_font_desc = Pango.FontDescription.from_string(output_font_str if output_font_str else "Sans 10")

        self._current_result_records: Optional[List[Dict[str, Any]]] = None
        self._current_user_input: Optional[str] = None
        self._current_requested_record_type: Optional[str] = None
        self._current_dns_servers: Optional[Sequence[Any]] = None

        self._connect_signals()

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.domain_entry.connect("activate", self._on_entry_activated)
        self.dns_apply_button.connect("clicked", self._on_entry_activated)
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed)
        if self.dns_clear_results_button:
            self.dns_clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.connect("clicked", self._on_copy_all_results_clicked)
        self.settings.connect(f"changed::{self._output_font_gsettings_key}", self._on_global_output_font_changed)

    def _on_global_output_font_changed(self, settings: Gio.Settings, key: str) -> None:
        logger.debug("DNSPage: Global output font setting changed for key: %s", key)
        if key == self._output_font_gsettings_key:
            output_font_str = settings.get_string(key)
            self._output_font_desc = Pango.FontDescription.from_string(
                output_font_str if output_font_str else "Sans 10"
            )
            if (
                self._current_result_records is not None
                and self._current_user_input is not None
                and self._current_requested_record_type is not None
                and self._current_dns_servers is not None
            ):
                logger.debug("DNSPage: Re-displaying results due to global font change.")
                self._display_result(
                    self._current_result_records,
                    self._current_user_input,
                    self._current_requested_record_type,
                    self._current_dns_servers,
                )

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Clearing DNS results.")
        while child := self.dns_results_box_container.get_first_child():
            self.dns_results_box_container.remove(child)
        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(False)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(False)
        self._current_result_records = None
        self._current_user_input = None
        self._current_requested_record_type = None
        self._current_dns_servers = None

    def _on_copy_all_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Copying all DNS results to clipboard.")
        all_results_text = []
        child = self.dns_results_box_container.get_first_child()
        while child:
            text_parts_for_child = []
            if isinstance(child, Adw.ActionRow): # Covers Adw.ExpanderRow headers too
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
            show_global_toast(self, "No results to copy.")
            return
        final_text_to_copy = "\n".join(all_results_text)
        gtk_utils_copy_to_clipboard(final_text_to_copy, self) # Use the imported utility
        show_global_toast(self, "All results copied to clipboard.")

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        if not input_str:
            return False
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget) -> None:
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec) -> None:
        self._perform_lookup()

    # --- Modified _build_*_record_row methods ---
    def _build_address_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        address_value = str(record_data.get("address", "N/A"))
        summary_text = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {address_value}"
        )
        return create_detail_action_row(
            title=name,
            subtitle=f"Type: {record_type}, {base_subtitle}",
            icon_name="network-wired-symbolic",
            value_text=address_value,
            value_font_desc=self._output_font_desc,
            copy_value_tooltip=f"Copy Address: {address_value}",
            full_summary_text_for_copy=summary_text,
            parent_widget_for_clipboard=self,
        )

    def _build_cname_ns_ptr_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        icon_name = "emblem-shared-symbolic"
        if record_type == "NS":
            icon_name = "network-server-symbolic"
        elif record_type == "PTR":
            icon_name = "system-search-symbolic"
        target_value = str(record_data.get("target", "N/A"))
        summary_text = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {target_value}"
        )
        return create_detail_action_row(
            title=name,
            subtitle=f"Type: {record_type}, {base_subtitle}",
            icon_name=icon_name,
            value_text=target_value,
            value_font_desc=self._output_font_desc,
            copy_value_tooltip=f"Copy Target: {target_value}",
            full_summary_text_for_copy=summary_text,
            parent_widget_for_clipboard=self,
        )

    def _build_generic_data_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        data_value = str(record_data.get("data", "N/A"))
        summary_text = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {data_value}"
        return create_detail_action_row(
            title=name,
            subtitle=f"Type: {record_type}, {base_subtitle}",
            icon_name="help-question-symbolic",
            value_text=data_value,
            value_font_desc=self._output_font_desc,
            copy_value_tooltip=f"Copy Data: {data_value}",
            full_summary_text_for_copy=summary_text,
            parent_widget_for_clipboard=self,
        )

    def _build_mx_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:
        exchange_value = str(record_data.get("exchange", "N/A"))
        preference_value = str(record_data.get("preference", "N/A"))
        summary_mx = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} MX {preference_value} {exchange_value}"
        )
        copy_full_button = create_copy_button(summary_mx, "Copy Full Record Summary", self)
        row = create_expander_row(
            title=name,
            subtitle=f"MX Record ({base_subtitle})",
            icon_name="mail-send-receive-symbolic",
            header_suffixes=[copy_full_button],
            initially_expanded=True
        )
        exchange_value_label = Gtk.Label(
            label=exchange_value,
            halign=Gtk.Align.FILL,
            hexpand=True,
            selectable=True,
            wrap=False,
            lines=1,
            ellipsize=Pango.EllipsizeMode.END,
        )
        exchange_value_label.override_font(self._output_font_desc)
        copy_button_exchange = create_copy_button(exchange_value, f"Copy Exchange: {exchange_value}", self)
        mx_detail_row = Adw.ActionRow(subtitle=f"Preference: {preference_value}")
        mx_detail_row.add_prefix(exchange_value_label)
        mx_detail_row.add_prefix(copy_button_exchange)
        mx_detail_row.set_selectable(True)
        row.add_row(mx_detail_row)
        return row

    def _build_txt_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:
        texts = record_data.get("texts", [])
        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} TXT {texts_str_summary}"
        copy_full_button = create_copy_button(summary_txt, "Copy Full Record Summary", self)
        row = create_expander_row(
            title=name,
            subtitle=f"TXT Records ({base_subtitle})",
            icon_name="document-properties-symbolic",
            header_suffixes=[copy_full_button],
            initially_expanded=bool(texts)
        )
        if not texts:
            no_text_row = Adw.ActionRow(title="No text data.", selectable=False)
            row.add_row(no_text_row)
        else:
            for text_string in texts:
                add_detail_to_expander(
                    expander_row=row,
                    title=None,
                    value_text=text_string,
                    copy_tooltip_prefix="Copy Text Segment",
                    parent_widget_for_clipboard=self,
                    value_font_desc=self._output_font_desc,
                    is_value_primary_content=True
                )
        return row

    def _build_soa_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:
        mname_val = str(record_data.get("mname", "N/A"))
        rname_val = str(record_data.get("rname", "N/A"))
        serial_val = str(record_data.get("serial", "N/A"))
        refresh_val = str(record_data.get("refresh", "N/A"))
        retry_val = str(record_data.get("retry", "N/A"))
        expire_val = str(record_data.get("expire", "N/A"))
        minimum_val = str(record_data.get("minimum", "N/A"))
        summary_soa = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} SOA {mname_val} {rname_val} {serial_val} {refresh_val} {retry_val} {expire_val} {minimum_val}"
        copy_full_button = create_copy_button(summary_soa, "Copy Full Record Summary", self)
        row = create_expander_row(
            title=name,
            subtitle=f"SOA Record ({base_subtitle})",
            icon_name="document-settings-symbolic",
            header_suffixes=[copy_full_button],
            initially_expanded=True
        )
        soa_fields = [
            ("MNAME", mname_val),("RNAME", rname_val), ("Serial", serial_val),
            ("Refresh", refresh_val), ("Retry", retry_val), ("Expire", expire_val),
            ("Minimum TTL", minimum_val),
        ]
        for field_name_str, field_value in soa_fields:
            add_detail_to_expander(
                expander_row=row,
                title=field_name_str,
                value_text=field_value,
                copy_tooltip_prefix=f"Copy {field_name_str}",
                parent_widget_for_clipboard=self,
                value_font_desc=self._output_font_desc
            )
        return row

    def _set_loading_state(self, active: bool, message: Optional[str] = None) -> None:
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
        if not user_input:
            show_global_toast(self, "Input cannot be empty.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, "show_toast")):
                show_global_error(self, "Input cannot be empty.")
            return False
        if not self._is_valid_ip_or_domain(user_input):
            show_global_toast(self, "Invalid IP address or domain name.")
            main_window = self.get_native()
            if not (main_window and hasattr(main_window, "show_toast")):
                show_global_error(self, "Invalid IP address or domain name.")
            return False
        return True

    def _update_ptr_dropdown(self, user_input: str, requested_record_type: str) -> str:
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
        self, result_data: List[Dict[str, Any]], user_input: str,
        requested_record_type: str, dns_client: DnsResolverClient,
    ) -> None:
        actual_record_type_displayed = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            actual_record_type_displayed = self._update_ptr_dropdown(user_input, requested_record_type)
        nameservers_used = dns_client.resolver.nameservers
        self._current_result_records = result_data
        self._current_user_input = user_input
        self._current_requested_record_type = actual_record_type_displayed
        self._current_dns_servers = nameservers_used
        self._display_result(result_data, user_input, actual_record_type_displayed, nameservers_used)
        status_message = (
            f"{len(result_data)} {actual_record_type_displayed} record(s) found."
            if result_data
            else f"No {actual_record_type_displayed} records found for {user_input}."
        )
        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_message)
        show_global_toast(self, status_message)

    def _handle_dns_lookup_exception(
        self, error: Exception, user_input: str,
        requested_record_type: str, dns_client: DnsResolverClient,
    ) -> None:
        error_message = str(error)
        status_subtitle = f"Error: {error_message.splitlines()[0]}"
        if isinstance(error, DnsNxDomainError):
            show_global_error(self, error_message)
            status_subtitle = f"NXDOMAIN: Domain '{user_input}' not found."
            self._current_result_records = None
        elif isinstance(error, DnsNoAnswerError):
            logger.info("DNSPage: %s", error_message)
            nameservers_used = dns_client.resolver.nameservers
            self._current_result_records = []
            self._current_user_input = user_input
            self._current_requested_record_type = requested_record_type
            self._current_dns_servers = nameservers_used
            self._display_result([], user_input, requested_record_type, nameservers_used)
            status_subtitle = f"No {requested_record_type} records found for {user_input} (No Answer)."
        elif isinstance(error, DnsResolutionTimeoutError):
            show_global_error(self, error_message)
            status_subtitle = f"Timeout: Could not resolve {user_input}."
            self._current_result_records = None
        elif isinstance(error, DnsGenericError):
            logger.exception("DNSPage: DNS lookup failed for %s, type %s (DnsGenericError):", user_input, requested_record_type)
            show_global_error(self, error_message)
            status_subtitle = f"DNS Error: {error_message.splitlines()[0]}"
        elif isinstance(error, DnsClientError):
            logger.exception("DNSPage: Unexpected DnsClientError for %s, type %s:", user_input, requested_record_type)
            show_global_error(self, f"DNS Client Error: {error_message}")
            status_subtitle = f"Client Error: {error_message.splitlines()[0]}"
            self._current_result_records = None
        else:
            logger.exception("DNSPage: Unexpected error during DNS lookup for %s, type %s:", user_input, requested_record_type)
            error_message_short = f"An unexpected error occurred: {error_message.splitlines()[0]}"
            show_global_error(self, error_message_short)
            status_subtitle = error_message_short
            self._current_result_records = None
        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_subtitle)

    def _perform_lookup(self) -> None:
        self._set_loading_state(True, "Looking up...")
        user_input = self.domain_entry.get_text().strip()
        requested_record_type = self._get_selected_record_type()
        logger.debug(f"DNSPage: Performing DNS lookup for: {user_input}, type: {requested_record_type}")
        if not self._validate_dns_input(user_input):
            self._set_loading_state(False, "Idle - Invalid input.")
            return
        self._clear_error()
        custom_dns_server = self.settings.get_string("custom-dns-server")
        dns_client = DnsResolverClient(custom_dns_server=custom_dns_server or None)
        try:
            result_data = dns_client.resolve(user_input, requested_record_type)
            self._handle_dns_lookup_success(result_data, user_input, requested_record_type, dns_client)
        except Exception as e:
            self._handle_dns_lookup_exception(e, user_input, requested_record_type, dns_client)
        finally:
            self._set_loading_state(False)

    def _get_selected_record_type(self) -> str:
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        return model.get_string(selected_index)

    def _clear_error(self) -> None:
        self.domain_entry.remove_css_class("error")
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error()
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    def _display_result(
        self, result_records: List[Dict[str, Any]], domain_or_ip: str,
        record_type: str, dns_servers: Sequence[Any],
    ) -> None:
        logger.debug(
            "Displaying %d results for %s (type %s) using servers %s.",
            len(result_records), domain_or_ip, record_type, dns_servers,
        )
        logger.info(
            "Query for %s, type %s, using servers %s, returned %d records.",
            domain_or_ip, record_type, dns_servers, len(result_records),
        )
        while child := self.dns_results_box_container.get_first_child():
            self.dns_results_box_container.remove(child)
        query_info_row = Adw.ActionRow(title=f"Query: {domain_or_ip}", subtitle=f"Record type queried: {record_type}")
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row)
        servers_str = ", ".join(map(str, dns_servers)) if dns_servers else "System default"
        servers_info_row = Adw.ActionRow(title="DNS Servers Used", subtitle=servers_str)
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row)
        self.dns_results_box_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        if not result_records:
            no_records_message = f"No {record_type} records found for {domain_or_ip}."
            if record_type == "PTR" and is_valid_ip(domain_or_ip):
                no_records_message = f"No PTR records found for IP address {domain_or_ip}."
            elif record_type == "PTR":
                no_records_message = f"No PTR records found for {domain_or_ip}."
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

    def _create_record_row(self, record_data: Dict[str, Any]) -> Optional[Gtk.Widget]:
        record_type = record_data.get("type", "").upper()
        name = record_data.get("name", "N/A")
        ttl = record_data.get("ttl", "")
        rd_class_str = record_data.get("class", "")
        base_subtitle = f"Class: {rd_class_str}, TTL: {ttl}"
        if record_type in ("A", "AAAA"):
            return self._build_address_record_row(record_data, name, base_subtitle, record_type)
        elif record_type in ("CNAME", "NS", "PTR"):
            return self._build_cname_ns_ptr_record_row(record_data, name, base_subtitle, record_type)
        elif record_type == "MX":
            return self._build_mx_record_row(record_data, name, base_subtitle)
        elif record_type == "TXT":
            return self._build_txt_record_row(record_data, name, base_subtitle)
        elif record_type == "SOA":
            return self._build_soa_record_row(record_data, name, base_subtitle)
        elif record_data.get("data"):
            return self._build_generic_data_record_row(record_data, name, base_subtitle, record_type)
        else:
            logger.warning(
                "Could not create row for unknown record_data type or missing data field: %s (Type: %s)",
                record_data, record_type,
            )
            return None

    def trigger_lookup(self) -> None:
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive():
            self.dns_apply_button.activate()
        else:
            logger.warning("DNS lookup button not available or not sensitive, cannot trigger lookup.")
