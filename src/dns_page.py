"""
Defines the DNS lookup page for the Woes application.

This module contains the :class:`.DNSPage` class, which provides the UI
and core functionality for performing DNS lookups. It supports various
record types, allows users to specify a domain name or IP address for queries,
and can use a custom DNS server configured via application settings.
DNS lookups are performed asynchronously to maintain UI responsiveness.
"""

import logging
from enum import Enum # Add missing import
from typing import Optional, Any, List, Dict, Final  # Added Final

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, Gtk, Pango, GObject, GLib

from .constants import APP_ID, RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_ip, is_valid_domain
from .gtk_utils import (
    create_copy_button,
    _copy_to_clipboard as gtk_utils_copy_to_clipboard,
    create_detail_action_row,
    create_expander_row,
    add_detail_to_expander,
)
from .dns_client import (
    DnsResolverClient,
    DnsClientError,
    DnsResolutionTimeoutError,
    DnsNxDomainError,
    DnsNoAnswerError,
    DnsGenericError,
)

logger = logging.getLogger(__name__)

DNS_LOOKUP_ERROR_DOMAIN = "woes-dns-lookup-error-domain"

class DnsLookupErrorType(int, Enum):
    RESOLVE_FAILED = 0  # Generic failure in resolution
    CLIENT_ERROR = 1    # Other DnsClientError
    TIMEOUT = 2
    NXDOMAIN = 3
    NO_ANSWER = 4
    CANCELLED = 5
    UNEXPECTED = 6      # Truly unexpected exceptions

@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Gtk.Box):
    """
    Provides the UI and logic for the DNS lookup page.

    This page allows users to enter a domain name or IP address, select a DNS
    record type, and perform lookups. Results are displayed in a structured
    list. Users can specify a custom DNS server via application settings.
    DNS lookups are performed asynchronously to keep the UI responsive.

    :ivar domain_entry: Entry for domain/IP input.
    :vartype domain_entry: Gtk.Entry
    :ivar dns_apply_button: Button to trigger DNS lookup.
    :vartype dns_apply_button: Gtk.Button
    :ivar dns_record_type_dropdown: Dropdown for selecting DNS record type.
    :vartype dns_record_type_dropdown: Adw.ComboRow
    :ivar dns_results_box_container: Container for displaying DNS results.
    :vartype dns_results_box_container: Gtk.Box
    :ivar dns_clear_results_button: Button to clear results.
    :vartype dns_clear_results_button: Gtk.Button
    :ivar dns_copy_all_results_button: Button to copy all results.
    :vartype dns_copy_all_results_button: Gtk.Button
    :ivar dns_status_row: ActionRow for displaying lookup status.
    :vartype dns_status_row: Adw.ActionRow
    :ivar dns_status_spinner: Spinner for indicating lookup progress.
    :vartype dns_status_spinner: Gtk.Spinner
    """

    __gtype_name__: Final[str] = "DNSPage"

    domain_entry: Gtk.Entry = Gtk.Template.Child()
    dns_apply_button: Gtk.Button = Gtk.Template.Child()
    dns_record_type_dropdown: Adw.ComboRow = Gtk.Template.Child()
    dns_results_box_container: Gtk.Box = Gtk.Template.Child()
    dns_clear_results_button: Gtk.Button = Gtk.Template.Child()
    dns_copy_all_results_button: Gtk.Button = Gtk.Template.Child()
    dns_status_row: Adw.ActionRow = Gtk.Template.Child()
    dns_status_spinner: Gtk.Spinner = Gtk.Template.Child()

    def __init__(self, **kwargs: Any):
        """
        Initialize the DNSPage.

        Sets up initial state, loads settings, and connects UI signals.

        :param kwargs: Keyword arguments passed to the :class:`Gtk.Box` constructor.
        """
        super().__init__(**kwargs)
        logger.debug("DNSPage initialized.")
        self.settings: Gio.Settings = Gio.Settings.new(APP_ID)

        self._output_font_gsettings_key: str = "output-font"
        output_font_str: str = self.settings.get_string(self._output_font_gsettings_key)
        self._output_font_desc: Pango.FontDescription = Pango.FontDescription.from_string(
            output_font_str if output_font_str else "Sans 10"
        )

        self._current_result_records: Optional[List[Dict[str, Any]]] = None
        self._current_user_input: Optional[str] = None
        self._current_requested_record_type: Optional[str] = None
        self._current_dns_servers: Optional[List[str]] = None
        self._dns_task_data_for_thread: Dict[str, Any] = {}

        self._connect_signals()
        self._current_dns_task: Optional[Gio.Task] = None

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        if self.domain_entry:
            self.domain_entry.connect("activate", self._on_entry_activated)
        if self.dns_apply_button:
            self.dns_apply_button.connect("clicked", self._on_entry_activated)
        if self.dns_record_type_dropdown:
            self.dns_record_type_dropdown.connect(
                "notify::selected-item", self._on_record_type_changed
            )
        if self.dns_clear_results_button:
            self.dns_clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.connect("clicked", self._on_copy_all_results_clicked)
        self.settings.connect(f"changed::{self._output_font_gsettings_key}", self._on_global_output_font_changed)

    def _on_global_output_font_changed(self, settings: Gio.Settings, key: str) -> None:
        """
        Handle changes to the global output font GSettings key.

        Updates the font description and re-displays current results if any.

        :param settings: The Gio.Settings object that changed.
        :param key: The GSettings key that changed.
        """
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
        """
        Handle click of the 'Clear Results' button.

        Clears the displayed results and resets stored result state.

        :param _button: The Gtk.Button that was clicked (unused).
        """
        logger.info("Clearing DNS results.")
        if self.dns_results_box_container:
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
        if self.dns_status_row:
            self.dns_status_row.set_subtitle("Idle")


    def _on_copy_all_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Handle click of the 'Copy All Results' button.

        Formats the currently stored DNS query and results into a text block
        and copies it to the clipboard.

        :param _button: The Gtk.Button that was clicked (unused).
        """
        logger.info("Copying all DNS results to clipboard.")
        if self._current_result_records is None:
            show_global_toast(self, "No results to copy.")
            return

        all_results_text_parts = []
        if self._current_user_input and self._current_requested_record_type:
            all_results_text_parts.append(
                f"Query: {self._current_user_input} (Type: {self._current_requested_record_type})"
            )
        if self._current_dns_servers:
            servers_str = ", ".join(self._current_dns_servers)
            all_results_text_parts.append(f"DNS Servers Used: {servers_str}")

        if not self._current_result_records:
            if not all_results_text_parts:
                 show_global_toast(self, "No results to copy.")
                 return
            all_results_text_parts.append("--- No records found ---")
        else:
            all_results_text_parts.append("--- Results ---")
            for record_data in self._current_result_records:
                parts = [
                    f"Name: {record_data.get('name', 'N/A')}",
                    f"TTL: {record_data.get('ttl', 'N/A')}",
                    f"Class: {record_data.get('class', 'N/A')}"
                ]
                rtype = record_data.get('type', '').upper()
                parts.append(f"Type: {rtype}")

                if rtype in ("A", "AAAA"):
                    parts.append(f"Address: {record_data.get('address', 'N/A')}")
                elif rtype in ("CNAME", "NS", "PTR"):
                    parts.append(f"Target: {record_data.get('target', 'N/A')}")
                elif rtype == "MX":
                    parts.append(
                        f"Preference: {record_data.get('preference', 'N/A')}"
                    )
                    parts.append(f"Exchange: {record_data.get('exchange', 'N/A')}")
                elif rtype == "TXT":
                    texts_val = record_data.get('texts', ['N/A'])
                    texts_join = "; ".join([f'"{s}"' for s in texts_val])
                    parts.append(f"Texts: {texts_join}")
                elif rtype == "SOA":
                    parts.append(f"MNAME: {record_data.get('mname', 'N/A')}")
                    parts.append(f"RNAME: {record_data.get('rname', 'N/A')}")
                    parts.append(f"Serial: {record_data.get('serial', 'N/A')}")
                    parts.append(f"Refresh: {record_data.get('refresh', 'N/A')}")
                    parts.append(f"Retry: {record_data.get('retry', 'N/A')}")
                    parts.append(f"Expire: {record_data.get('expire', 'N/A')}")
                    parts.append(f"Minimum: {record_data.get('minimum', 'N/A')}")
                elif "data" in record_data:
                    parts.append(f"Data: {record_data.get('data', 'N/A')}")
                all_results_text_parts.append(", ".join(parts))

        final_text_to_copy = "\n".join(all_results_text_parts)
        if not final_text_to_copy.strip():
            show_global_toast(self, "No content to copy.")
            return

        gtk_utils_copy_to_clipboard(final_text_to_copy, self)
        show_global_toast(self, "All results copied to clipboard.")

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """
        Validate if the input string is a valid IP address or domain name.

        :param input_str: The string to validate.
        :return: ``True`` if valid, ``False`` otherwise.
        """
        if not input_str:
            return False
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget) -> None:
        """
        Handle activation of the domain entry (e.g., Enter key press) or click of the apply button.

        Triggers a DNS lookup.

        :param _widget: The Gtk.Widget that triggered the signal (unused).
        """
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _combo_row: Adw.ComboRow, _param_spec: GObject.ParamSpec) -> None:
        """
        Handle change in the selected DNS record type from the dropdown.

        Triggers a DNS lookup if there is existing valid input in the domain entry field.

        :param _combo_row: The Adw.ComboRow whose selection changed (unused).
        :param _param_spec: The GObject.ParamSpec of the property that changed (unused).
        """
        if self.domain_entry and self.domain_entry.get_text().strip():
            logger.debug("Record type changed, performing lookup with existing input.")
            self._perform_lookup()

    def _build_address_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        """
        Build an Adw.ActionRow for A or AAAA records.

        :param record_data: Dictionary containing the parsed record data.
        :param name: The query name for the record.
        :param base_subtitle: Base subtitle string (Class, TTL).
        :param record_type: The specific record type ("A" or "AAAA").
        :return: Configured Adw.ActionRow for the address record.
        """
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
        """
        Build an Adw.ActionRow for CNAME, NS, or PTR records.

        :param record_data: Dictionary containing the parsed record data.
        :param name: The query name for the record.
        :param base_subtitle: Base subtitle string (Class, TTL).
        :param record_type: The specific record type ("CNAME", "NS", "PTR").
        :return: Configured Adw.ActionRow for the record.
        """
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
        """
        Build an Adw.ActionRow for generic DNS records with a 'data' field.

        :param record_data: Dictionary containing the parsed record data.
        :param name: The query name for the record.
        :param base_subtitle: Base subtitle string (Class, TTL).
        :param record_type: The specific record type.
        :return: Configured Adw.ActionRow for the generic record.
        """
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

    def _build_mx_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str) -> Adw.ExpanderRow:
        """
        Build an Adw.ExpanderRow for MX records.

        :param record_data: Dictionary containing the parsed MX record data.
        :param name: The query name for the record.
        :param base_subtitle: Base subtitle string (Class, TTL).
        :return: Configured Adw.ExpanderRow for the MX record.
        """
        exchange_value = str(record_data.get("exchange", "N/A"))
        preference_value = str(record_data.get("preference", "N/A"))
        summary_mx = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} MX {preference_value} {exchange_value}"
        )
        copy_full_button = create_copy_button(summary_mx, "Copy Full Record Summary", self)

        row_title = f"{name} (Preference: {preference_value})"
        row_subtitle = f"Type: MX, {base_subtitle}"

        row = create_expander_row(
            title=row_title,
            subtitle=row_subtitle,
            icon_name="mail-send-receive-symbolic",
            header_suffixes=[copy_full_button],
            initially_expanded=True,
        )

        add_detail_to_expander(
            expander_row=row,
            title="Exchange Server",
            value_text=exchange_value,
            copy_tooltip_prefix="Copy Exchange",
            parent_widget_for_clipboard=self,
            value_font_desc=self._output_font_desc
        )
        return row

    def _build_txt_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str) -> Adw.ExpanderRow:
        """
        Build an Adw.ExpanderRow for TXT records.

        Handles multiple text strings within a single TXT record.

        :param record_data: Dictionary containing the parsed TXT record data.
        :param name: The query name for the record.
        :param base_subtitle: Base subtitle string (Class, TTL).
        :return: Configured Adw.ExpanderRow for the TXT record.
        """
        texts = record_data.get("texts", [])
        texts_str_summary = " ".join([f'"{s}"' for s in texts]) if texts else "N/A"
        summary_txt = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} TXT {texts_str_summary}"
        copy_full_button = create_copy_button(summary_txt, "Copy Full Record Summary", self)

        row = create_expander_row(
            title=name,
            subtitle=f"Type: TXT, {base_subtitle} ({len(texts)} segment(s))",
            icon_name="document-properties-symbolic",
            header_suffixes=[copy_full_button],
            initially_expanded=bool(texts),
        )

        if not texts:
            no_text_row = Adw.ActionRow(title="No text data available for this record.", selectable=False)
            row.add_row(no_text_row)
        else:
            for i, text_string in enumerate(texts):
                add_detail_to_expander(
                    expander_row=row,
                    title=f"Segment {i+1}" if len(texts) > 1 else None,
                    value_text=text_string,
                    copy_tooltip_prefix="Copy Text Segment",
                    parent_widget_for_clipboard=self,
                    value_font_desc=self._output_font_desc,
                    is_value_primary_content=True,
                )
        return row

    def _build_soa_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str) -> Adw.ExpanderRow:
        """
        Build an Adw.ExpanderRow for SOA records.

        :param record_data: Dictionary containing the parsed SOA record data.
        :param name: The query name for the record.
        :param base_subtitle: Base subtitle string (Class, TTL).
        :return: Configured Adw.ExpanderRow for the SOA record.
        """
        mname_val = str(record_data.get("mname", "N/A"))
        rname_val = str(record_data.get("rname", "N/A"))
        serial_val = str(record_data.get("serial", "N/A"))
        refresh_val = str(record_data.get("refresh", "N/A"))
        retry_val = str(record_data.get("retry", "N/A"))
        expire_val = str(record_data.get("expire", "N/A"))
        minimum_val = str(record_data.get("minimum", "N/A"))

        summary_soa = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} SOA "
            f"{mname_val} {rname_val} {serial_val} {refresh_val} {retry_val} {expire_val} {minimum_val}"
        )
        copy_full_button = create_copy_button(summary_soa, "Copy Full Record Summary", self)

        row = create_expander_row(
            title=name,
            subtitle=f"Type: SOA, {base_subtitle}",
            icon_name="document-settings-symbolic",
            header_suffixes=[copy_full_button],
            initially_expanded=True,
        )

        soa_fields = [
            ("Primary Name Server (MNAME)", mname_val),
            ("Responsible Party (RNAME)", rname_val),
            ("Serial Number", serial_val),
            ("Refresh Interval", refresh_val),
            ("Retry Interval", retry_val),
            ("Expire Limit", expire_val),
            ("Minimum TTL", minimum_val),
        ]
        for field_name_str, field_value in soa_fields:
            add_detail_to_expander(
                expander_row=row,
                title=field_name_str,
                value_text=field_value,
                copy_tooltip_prefix=f"Copy {field_name_str}",
                parent_widget_for_clipboard=self,
                value_font_desc=self._output_font_desc,
            )
        return row

    def _set_loading_state(self, active: bool, message: Optional[str] = None) -> None:
        """
        Set the loading state of the UI.

        Manages spinner visibility, status subtitle, and sensitivity of input fields.

        :param active: If ``True``, sets UI to loading state; otherwise, sets to idle/active state.
        :param message: Optional message to display in the status row.
        """
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
            elif not active and (current_subtitle == "Looking up..." or not message):
                self.dns_status_row.set_subtitle("Idle")

        if self.domain_entry:
            self.domain_entry.set_sensitive(not active)
        if self.dns_apply_button:
            self.dns_apply_button.set_sensitive(not active)
        if self.dns_record_type_dropdown:
            self.dns_record_type_dropdown.set_sensitive(not active)

    def _validate_dns_input(self, user_input: str) -> bool:
        """
        Validate the user's DNS query input.

        Checks if the input is non-empty and a valid IP address or domain name.
        Shows appropriate toasts/errors if validation fails.

        :param user_input: The string input by the user.
        :return: ``True`` if input is valid, ``False`` otherwise.
        """
        if not user_input:
            msg = "Input cannot be empty."
            show_global_toast(self, msg)
            return False
        if not self._is_valid_ip_or_domain(user_input):
            msg = "Invalid IP address or domain name format."
            show_global_toast(self, msg)
            return False
        return True

    def _update_ptr_dropdown(self, user_input: str, requested_record_type: str) -> str:
        """
        Ensure "PTR" is selected in the dropdown if input is an IP and type is "PTR".

        This is a UI consistency helper. The actual reverse name conversion happens
        in the DnsResolverClient.

        :param user_input: The user's input string (domain or IP).
        :param requested_record_type: The currently selected record type.
        :return: The record type that is effectively used (e.g., "PTR" if updated).
        """
        actual_record_type_used = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            model = self.dns_record_type_dropdown.get_model()
            if model:
                for i in range(model.get_n_items()):  # type: ignore
                    item_str = model.get_string(i)  # type: ignore
                    if item_str and item_str.upper() == "PTR":
                        if self.dns_record_type_dropdown.get_selected() != i:
                            self.dns_record_type_dropdown.set_selected(i)
                            logger.debug(
                                "DNSPage: Switched dropdown to PTR for IP input."
                            )
                        break
            actual_record_type_used = "PTR"
        return actual_record_type_used

    def _perform_lookup(self) -> None:
        """
        Orchestrate the DNS lookup process.

        Validates input, sets loading state, and initiates an asynchronous
        DNS resolution task.
        """
        user_input = self.domain_entry.get_text().strip() if self.domain_entry else ""
        requested_record_type = self._get_selected_record_type()

        if not self._validate_dns_input(user_input):
            self._set_loading_state(False, "Idle - Invalid input.")
            return

        self._set_loading_state(True, f"Looking up {requested_record_type} for {user_input}...")
        self._clear_error()

        if self._current_dns_task and not self._current_dns_task.is_done():
            try:
                if self._current_dns_task.get_cancellable():  # Check if cancellable exists
                    self._current_dns_task.get_cancellable().cancel()
                logger.info("DNSPage: Previous DNS lookup task cancelled.")
            except Exception as e_cancel:
                logger.warning("DNSPage: Error trying to cancel previous DNS task: %s", e_cancel)

        cancellable = Gio.Cancellable()
        custom_dns_server = self.settings.get_string("custom-dns-server")

        task_data = {
            "user_input": user_input,
            "requested_record_type": requested_record_type,
            "custom_dns_server": custom_dns_server if custom_dns_server else None
        }
        self._dns_task_data_for_thread = task_data  # Store it on the instance
        self._current_dns_task = Gio.Task.new(
            self, cancellable, self._perform_lookup_done_cb, None  # Pass None for task_data
        )
        # self._current_dns_task.set_task_data(None) # This line is removed

        logger.debug(
            "DNSPage: Starting DNS lookup task with input: '%s', type: '%s', server: '%s'",
            user_input,
            requested_record_type,
            custom_dns_server or "System Default"
        )
        self._current_dns_task.run_in_thread(self._perform_lookup_thread_func)

    def _perform_lookup_thread_func(
        self, task: Gio.Task, _source_object: Any,
        _task_data_param: Any, cancellable: Gio.Cancellable
    ) -> None:
        """
        Perform the DNS lookup in a background thread.

        Retrieves parameters from the task data, performs the DNS resolution
        using :class:`DnsResolverClient`, and sets the result or error on the task.

        :param task: The :class:`Gio.Task` for this operation.
        :param _source_object: The source object (DNSPage instance, unused here).
        :param _task_data_param: Data passed via `run_in_thread` (unused here).
        :param cancellable: The :class:`Gio.Cancellable` for this task.
        """
        page_instance: DNSPage = _source_object  # type: ignore
        current_task_data: Dict[str, Any] = page_instance._dns_task_data_for_thread

        user_input: str = current_task_data["user_input"]
        requested_record_type: str = current_task_data["requested_record_type"]
        custom_dns_server: Optional[str] = current_task_data["custom_dns_server"]

        if cancellable.is_cancelled():
            task.return_error(
                GLib.Error.new_literal(
                    DNS_LOOKUP_ERROR_DOMAIN,
                    DnsLookupErrorType.CANCELLED.value,
                    "Lookup cancelled before starting."
                )
            )
            return

        try:
            dns_client = DnsResolverClient(custom_dns_server=custom_dns_server)
            if cancellable.is_cancelled():
                task.return_error(
                    GLib.Error.new_literal(
                        DNS_LOOKUP_ERROR_DOMAIN,
                        DnsLookupErrorType.CANCELLED.value,
                        "Lookup cancelled before resolving."
                    )
                )
                return

            result_data = dns_client.resolve(user_input, requested_record_type)

            if cancellable.is_cancelled():
                task.return_error(
                    GLib.Error.new_literal(
                        DNS_LOOKUP_ERROR_DOMAIN,
                        DnsLookupErrorType.CANCELLED.value,
                        "Lookup cancelled after resolving."
                    )
                )
                return

            task_result_payload = {
                "data": result_data,
                "client_nameservers": dns_client.resolver.nameservers or []
            }
            task.return_value(task_result_payload)
        except DnsResolutionTimeoutError as e_timeout:
            task.return_error(GLib.Error.new_literal(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.TIMEOUT.value, str(e_timeout)))
        except DnsNxDomainError as e_nx:
            task.return_error(GLib.Error.new_literal(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.NXDOMAIN.value, str(e_nx)))
        except DnsNoAnswerError as e_no_answer:
            task.return_error(GLib.Error.new_literal(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.NO_ANSWER.value, str(e_no_answer)))
        except (DnsClientError, DnsGenericError) as e_client: # DnsGenericError is base for some others too
            task.return_error(GLib.Error.new_literal(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.CLIENT_ERROR.value, str(e_client)))
        except Exception as e_generic: # Catch-all for other unexpected errors
            logger.exception("DNSPage Task: Unexpected error during DNS resolution for %s", user_input)
            task.return_error(GLib.Error.new_literal(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.UNEXPECTED.value, f"Unexpected internal error: {e_generic}"))

    def _perform_lookup_done_cb(
        self, _source_object: Any, result: Gio.AsyncResult, _user_data: Any
    ) -> None:
        """
        Process the result of the asynchronous DNS lookup.

        This method is called in the main GTK thread when the background task
        (started by `_perform_lookup`) completes. It updates the UI with
        the results or an error message.

        :param _source_object: The source object (DNSPage instance).
        :param result: The :class:`Gio.AsyncResult` from the completed task.
        :param _user_data: User data passed with the callback (unused).
        """
        active_task = self._current_dns_task
        if not active_task or not active_task.matches_async_result(result):
            logger.warning("DNSPage: Callback received for an outdated or mismatched DNS task.")
            if not active_task or active_task.is_done():  # If no current task or it's done
                self._set_loading_state(False, "Idle.")
            return

        # Retrieve operational data from instance variable
        user_input: str = self._dns_task_data_for_thread.get("user_input", "Unknown Input")
        requested_record_type: str = self._dns_task_data_for_thread.get(
            "requested_record_type", "Unknown Type"
        )
        # custom_dns_server for display in case of error can be fetched from self.settings or stored if needed.
        # For now, _display_result will use self._current_dns_servers which is set on success,
        # or use settings if error occurs before it's set.

        try:
            task_return_value = active_task.propagate_value(result)

            result_data: List[Dict[str, Any]] = []
            nameservers_used: List[str] = ["System Default"]

            if isinstance(task_return_value, dict):
                result_data = task_return_value.get("data", [])
                nameservers_used = task_return_value.get("client_nameservers", [])
            else:
                logger.warning(
                    "DNSPage: Task returned unexpected data type: %s", type(task_return_value)
                )
                # Attempt to get custom_dns_server from settings as a fallback for display
                custom_dns_setting = self.settings.get_string("custom-dns-server")
                if custom_dns_setting:
                    nameservers_used = [custom_dns_setting]

            self._handle_dns_lookup_success_async(
                result_data, user_input, requested_record_type, nameservers_used
            )

        except GLib.Error as e:
            error_message = str(e)
            status_subtitle = f"Error: {error_message.splitlines()[0]}"
            current_dns_for_display = self._current_dns_servers or \
                                      ([self.settings.get_string("custom-dns-server")]
                                       if self.settings.get_string("custom-dns-server")
                                       else ["System Default"])

            if e.matches(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.NXDOMAIN.value):
                show_global_error(self, error_message) # error_message is str(DnsNxDomainError)
                status_subtitle = f"NXDOMAIN: Domain '{user_input}' not found."
                self._current_result_records = None
                self._display_result([], user_input, requested_record_type, current_dns_for_display)
            elif e.matches(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.NO_ANSWER.value):
                logger.info("DNSPage: %s", error_message) # str(DnsNoAnswerError)
                self._current_result_records = []
                self._current_user_input = user_input
                self._current_requested_record_type = requested_record_type
                self._current_dns_servers = current_dns_for_display # This might be None if not set before
                self._display_result([], user_input, requested_record_type, current_dns_for_display)
                status_subtitle = f"No {requested_record_type} records found for '{user_input}' (No Answer)."
            elif e.matches(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.TIMEOUT.value):
                show_global_error(self, error_message) # str(DnsResolutionTimeoutError)
                status_subtitle = f"Timeout resolving '{user_input}' for {requested_record_type} records."
                self._current_result_records = None
                self._display_result([], user_input, requested_record_type, current_dns_for_display)
            elif e.matches(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.CLIENT_ERROR.value) or \
                 e.matches(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.RESOLVE_FAILED.value): # Covers DnsClientError, DnsGenericError
                logger.warning(
                    "DNSPage: DNS lookup failed for '%s', type '%s' (Code: %s): %s",
                    user_input, requested_record_type, e.code, error_message
                )
                show_global_error(self, error_message)
                status_subtitle = f"DNS Error: {error_message.splitlines()[0]}"
                self._current_result_records = None
                self._display_result([], user_input, requested_record_type, current_dns_for_display)
            elif e.matches(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.CANCELLED.value) or \
                 e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED): # Gio cancellation or our custom
                status_subtitle = "Lookup cancelled."
                show_global_toast(self, status_subtitle)
                # Do not clear results on user cancellation
            else: # UNEXPECTED or other GLib errors
                logger.exception(
                    "DNSPage: Unexpected GLib.Error during DNS lookup for '%s', type '%s':",
                    user_input, requested_record_type
                )
                error_message_short = f"An unexpected error occurred: {error_message.splitlines()[0]}"
                show_global_error(self, error_message_short)
                status_subtitle = error_message_short
                self._current_result_records = None
                self._display_result([], user_input, requested_record_type, current_dns_for_display)

            if self.dns_status_row:
                self.dns_status_row.set_subtitle(status_subtitle)

        except Exception as e_unexpected: # Catch-all for non-GLib.Error issues in this callback
            logger.exception(
                "DNSPage: Truly unexpected error processing DNS task result for '%s':", user_input
            )
            show_global_error(self, f"A critical unexpected error occurred: {str(e_unexpected).splitlines()[0]}")
            self._current_result_records = None # Clear results
            self._display_result(
                [], user_input, requested_record_type,
                self._current_dns_servers or ["System Default"] # Fallback for servers
            )
            if self.dns_status_row:
                self.dns_status_row.set_subtitle("Critical Error.")
        finally:
            self._set_loading_state(False)
            self._current_dns_task = None

    def _handle_dns_lookup_success_async(
        self,
        result_data: List[Dict[str, Any]],
        user_input: str,
        requested_record_type: str,
        nameservers_used: List[str],
    ) -> None:
        """
        Handle successful DNS lookup results from the asynchronous task.

        Updates UI elements with the fetched records and status messages.

        :param result_data: List of dictionaries representing parsed DNS records.
        :param user_input: The original domain/IP input by the user.
        :param requested_record_type: The DNS record type that was queried.
        :param nameservers_used: List of DNS server IP addresses used for the query.
        """
        actual_record_type_displayed = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            actual_record_type_displayed = self._update_ptr_dropdown(user_input, requested_record_type)

        self._current_result_records = result_data
        self._current_user_input = user_input
        self._current_requested_record_type = actual_record_type_displayed
        self._current_dns_servers = nameservers_used

        self._display_result(result_data, user_input, actual_record_type_displayed, nameservers_used)

        status_message = (
            f"{len(result_data)} {actual_record_type_displayed} record(s) found for '{user_input}'."
            if result_data
            else f"No {actual_record_type_displayed} records found for '{user_input}'."
        )
        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_message)
        show_global_toast(self, status_message)

    def _handle_dns_lookup_exception_async(
        self,
        result_data: List[Dict[str, Any]],
        user_input: str,
        requested_record_type: str,
        nameservers_used: List[str],
    ) -> None:
        """
        Handle successful DNS lookup results from the asynchronous task.

        Updates UI elements with the fetched records and status messages.

        :param result_data: List of dictionaries representing parsed DNS records.
        :param user_input: The original domain/IP input by the user.
        :param requested_record_type: The DNS record type that was queried.
        :param nameservers_used: List of DNS server IP addresses used for the query.
        """
        actual_record_type_displayed = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            actual_record_type_displayed = self._update_ptr_dropdown(user_input, requested_record_type)

        self._current_result_records = result_data
        self._current_user_input = user_input
        self._current_requested_record_type = actual_record_type_displayed
        self._current_dns_servers = nameservers_used

        self._display_result(result_data, user_input, actual_record_type_displayed, nameservers_used)

        status_message = (
            f"{len(result_data)} {actual_record_type_displayed} record(s) found for '{user_input}'."
            if result_data
            else f"No {actual_record_type_displayed} records found for '{user_input}'."
        )
        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_message)
        show_global_toast(self, status_message)

    # _handle_dns_lookup_exception_async is removed as its logic is now in _perform_lookup_done_cb.

    def _get_selected_record_type(self) -> str:
        """
        Get the currently selected DNS record type from the dropdown.

        :return: The selected record type as a string (e.g., "A", "MX"). Defaults to "A".
        """
        if self.dns_record_type_dropdown:
            model = self.dns_record_type_dropdown.get_model()
            selected_index = self.dns_record_type_dropdown.get_selected()
            # Ensure model and selected_index are valid before accessing item
            if model and selected_index >= 0 and selected_index < model.get_n_items():  # type: ignore[union-attr]
                item_obj = model.get_item(selected_index)  # Gtk.StringObject
                if isinstance(item_obj, Gtk.StringObject):
                    return item_obj.get_string()
                elif item_obj is not None:  # Should be StringObject, but defensive
                     return str(item_obj)

        logger.warning("DNSPage: Could not get selected record type from dropdown, defaulting to 'A'.")
        return "A"

    def _clear_error(self) -> None:
        """Clear any error styling from the domain entry and hide global error messages if shown by parent window."""
        if self.domain_entry:
            self.domain_entry.remove_css_class("error")

        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error") and callable(main_window.hide_error):
            main_window.hide_error()  # type: ignore
        else:
            logger.debug("DNSPage: Main window or hide_error method not found/callable.")

    def _display_result(
        self,
        result_records: List[Dict[str, Any]],
        domain_or_ip: str,
        record_type: str,
        dns_servers: Optional[List[str]],
    ) -> None:
        """
        Display the DNS lookup results in the UI.

        Clears previous results and populates the results container with new rows
        representing the fetched DNS records. Also displays query metadata.

        :param result_records: A list of dictionaries, each representing a DNS record.
        :param domain_or_ip: The domain or IP that was queried.
        :param record_type: The DNS record type that was queried (actual type used).
        :param dns_servers: A list of DNS server IP addresses used for the query. Can be None if system default.
        """
        logger.debug(
            "Displaying %d results for '%s' (type '%s') using servers %s.",
            len(result_records),
            domain_or_ip,
            record_type,
            dns_servers if dns_servers else "System Default",
        )

        if not self.dns_results_box_container:
            logger.error("DNSPage: dns_results_box_container is None, cannot display results.")
            return

        while child := self.dns_results_box_container.get_first_child():
            self.dns_results_box_container.remove(child)

        query_info_row = Adw.ActionRow(
            title=f"Query: {domain_or_ip}",
            subtitle=f"Record type: {record_type.upper()}"
        )
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row)

        servers_str = ", ".join(dns_servers) if dns_servers else "System default"
        servers_info_row = Adw.ActionRow(title="DNS Servers Used:", subtitle=servers_str)
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row)

        self.dns_results_box_container.append(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6, margin_bottom=6)
        )

        # Check if the task was cancelled before deciding to show "no records"
        task_was_cancelled = False
        if isinstance(self._current_dns_task, Gio.Task):
            cancellable = self._current_dns_task.get_cancellable()
            if cancellable and cancellable.is_cancelled():
                task_was_cancelled = True

        if not result_records and not task_was_cancelled:
            no_records_message = f"No {record_type.upper()} records found for '{domain_or_ip}'."
            # More specific for PTR
            if record_type.upper() == "PTR" and is_valid_ip(domain_or_ip):
                no_records_message = (
                    f"No PTR records found for IP address '{domain_or_ip}'."
                )
            no_records_row = Adw.ActionRow(title=no_records_message, selectable=False)
            self.dns_results_box_container.append(no_records_row)
        elif result_records: # Only iterate if there are records
            for record_data_item in result_records:
                row = self._create_record_row(record_data_item)
                if row:
                    self.dns_results_box_container.append(row)
        # If task_was_cancelled and no results, we don't add a "no records" message.

        # Enable clear/copy if a query was made, even if no results or cancelled
        has_content_to_manage = bool(domain_or_ip)
        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(True) # Always allow clear after a query
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(has_content_to_manage)

    def _create_record_row(self, record_data: Dict[str, Any]) -> Optional[Gtk.Widget]:
        """
        Create a Gtk.Widget (Adw.ActionRow or Adw.ExpanderRow) for a single DNS record.

        Dispatches to specific _build_*_record_row methods based on the record type.
        This method centralizes the creation of UI rows for different DNS record types,
        making the display logic more organized.

        :param record_data: A dictionary containing the parsed data for one DNS record.
                            Expected keys include 'type', 'name', 'ttl', 'class', and
                            type-specific data fields (e.g., 'address', 'target', 'texts').
        :return: A Gtk.Widget to display the record, or an Adw.ActionRow indicating
                 an unsupported/unknown type if a specific builder is not available.
        """
        record_type_val = record_data.get("type", "").upper()
        name = record_data.get("name", "N/A")
        ttl = record_data.get("ttl", "N/A")  # TTL can sometimes be missing or not applicable
        rd_class_str = record_data.get("class", "N/A")  # Class can sometimes be missing
        base_subtitle = f"Class: {rd_class_str}, TTL: {ttl}"

        if record_type_val in ("A", "AAAA"):
            return self._build_address_record_row(record_data, name, base_subtitle, record_type_val)
        elif record_type_val in ("CNAME", "NS", "PTR"):
            return self._build_cname_ns_ptr_record_row(record_data, name, base_subtitle, record_type_val)
        elif record_type_val == "MX":
            return self._build_mx_record_row(record_data, name, base_subtitle)
        elif record_type_val == "TXT":
            return self._build_txt_record_row(record_data, name, base_subtitle)
        elif record_type_val == "SOA":
            return self._build_soa_record_row(record_data, name, base_subtitle)
        elif "data" in record_data:
            return self._build_generic_data_record_row(record_data, name, base_subtitle, record_type_val)
        else:
            logger.warning(
                "DNSPage: Could not create row for unknown record_data format or missing 'data' field: %s (Type: %s)",
                record_data,
                record_type_val,
            )
            unsupported_row = Adw.ActionRow(
                title=name,
                subtitle=f"Type: {record_type_val} (Unsupported detailed view)"
            )
            # Show raw data in tooltip
            unsupported_row.set_tooltip_text(f"Raw data: {str(record_data)}")
            return unsupported_row

    def trigger_lookup(self) -> None:
        """
        Programmatically trigger a DNS lookup if the apply button is sensitive.

        This method is typically called via a keyboard shortcut (e.g., Ctrl+R or Enter)
        to initiate the DNS resolution process based on the current UI state.
        It simulates a click on the 'Apply' button.
        """
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive():
            self.dns_apply_button.activate()
        else:
            logger.warning("DNS lookup button not available or not sensitive, cannot trigger lookup.")
