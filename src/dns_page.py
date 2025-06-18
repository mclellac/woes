"""
Defines the DNS lookup page for the Woes application.

This module contains the :class:`.DNSPage` class, which provides UI and
functionality for performing DNS lookups.
"""

import logging
import errno # For Gio.IOErrorEnum.CANCELLED

logger = logging.getLogger(__name__)

# DNS library imports (dns.resolver, etc.) are now primarily in dns_client.py
import gi
from gi.repository import Adw, Gio, Gtk, Pango, GObject
from typing import Optional, Sequence, Any, List, Dict

from .constants import APP_ID, RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_ip, is_valid_domain
from .dns_client import (
    DnsResolverClient,
    DnsClientError,  # Base for catching all client errors
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

    This page allows users to enter a domain name or IP address, select a DNS
    record type, and view the lookup results. It uses the `dnspython` library
    for DNS resolution and supports using a custom DNS server specified in
    application settings.
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
        logging.debug("DNSPage.__init__ called")
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

        self._dns_lookup_cancellable: Optional[Gio.Cancellable] = None

        self._connect_signals()

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.domain_entry.connect("activate", self._on_entry_activated)  # type: ignore
        self.dns_apply_button.connect("clicked", self._on_entry_activated)  # type: ignore
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed)  # type: ignore
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
        while child := self.dns_results_box_container.get_first_child():  # type: ignore
            self.dns_results_box_container.remove(child)  # type: ignore

        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(False)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(False)

        self._current_result_records = None
        self._current_user_input = None
        self._current_requested_record_type = None
        self._current_dns_servers = None

        if self._dns_lookup_cancellable and not self._dns_lookup_cancellable.is_cancelled():
            self._dns_lookup_cancellable.cancel()
            logger.info("DNSPage: Cancelled DNS lookup task due to clearing results.")

    def _on_copy_all_results_clicked(self, _button: Gtk.Button) -> None:
        logger.info("Copying all DNS results to clipboard.")
        all_results_text = []
        child = self.dns_results_box_container.get_first_child()  # type: ignore
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
        show_global_toast(self, "All results copied to clipboard.")  # type: ignore

    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        try:
            clipboard = widget.get_clipboard()  # type: ignore
            if clipboard:
                clipboard.set(text)  # type: ignore
                logger.info("Copied to clipboard: %s", text)
            else:
                logger.warning("Could not get clipboard from widget: %s", widget)
        except Exception:
            logger.exception("Error copying to clipboard:")

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        if not input_str:
            return False
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget) -> None:
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec) -> None:
        self._perform_lookup()

    def _create_copy_button(self, text_to_copy: str, tooltip_text: str, widget_for_clipboard: Gtk.Widget) -> Gtk.Button:
        button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip_text)
        button.connect(
            "clicked",
            lambda _btn, text=text_to_copy, w=widget_for_clipboard: DNSPage._copy_to_clipboard(text, w),
        )
        return button

    def _create_base_action_row(
        self, name: str, record_type_label: str, base_subtitle_text: str, icon_name: Optional[str]
    ) -> Adw.ActionRow:
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type_label}, {base_subtitle_text}")  # type: ignore
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name))  # type: ignore
        row.set_selectable(False)
        return row

    def _add_standard_suffix_box_to_row(
        self,
        row: Adw.ActionRow,
        main_value_text: str,
        main_value_tooltip_prefix: str,
        full_summary_text: str,
    ) -> None:
        value_label = Gtk.Label(
            label=main_value_text,
            halign=Gtk.Align.FILL,
            hexpand=True,
            selectable=True,
            wrap=False,
            lines=1,
            ellipsize=Pango.EllipsizeMode.END,
        )
        value_label.override_font(self._output_font_desc)
        row.add_suffix(value_label)  # type: ignore
        row.add_suffix(
            self._create_copy_button(main_value_text, f"{main_value_tooltip_prefix}: {main_value_text}", row)
        )  # type: ignore
        row.add_suffix(self._create_copy_button(full_summary_text, "Copy Full Record Summary", row))  # type: ignore

    def _create_base_expander_row(
        self, name: str, subtitle_text: str, icon_name: Optional[str], full_summary_text: str
    ) -> Adw.ExpanderRow:
        row = Adw.ExpanderRow(title=name, subtitle=subtitle_text)  # type: ignore
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name))  # type: ignore
        copy_full_button = self._create_copy_button(full_summary_text, "Copy Full Record Summary", row)
        row.add_suffix(copy_full_button)  # type: ignore
        return row

    def _add_expander_detail_row(
        self,
        expander_row: Adw.ExpanderRow,
        title: Optional[str],
        value_text: str,
        copy_tooltip_prefix: str,
        is_value_primary_content: bool = False,
    ):
        detail_row = Adw.ActionRow(title=title if title else None)  # type: ignore
        value_label = Gtk.Label(
            label=value_text,
            halign=Gtk.Align.FILL,
            hexpand=True,
            selectable=True,
            wrap=False,
            lines=1,
            ellipsize=Pango.EllipsizeMode.END,
        )
        value_label.override_font(self._output_font_desc)
        copy_button = self._create_copy_button(value_text, f"{copy_tooltip_prefix}: {value_text}", expander_row)
        if is_value_primary_content:
            detail_row.add_prefix(value_label)  # type: ignore
            detail_row.add_prefix(copy_button)  # type: ignore
        else:
            detail_row.add_suffix(value_label)  # type: ignore
            detail_row.add_suffix(copy_button)  # type: ignore
        detail_row.set_selectable(False)
        expander_row.add_row(detail_row)  # type: ignore

    def _set_loading_state(self, active: bool, message: Optional[str] = None) -> None:
        if self.dns_status_spinner:
            self.dns_status_spinner.set_visible(active)  # type: ignore
            if active:
                self.dns_status_spinner.start()  # type: ignore
            else:
                self.dns_status_spinner.stop()  # type: ignore
        if self.dns_status_row:
            current_subtitle = self.dns_status_row.get_subtitle()  # type: ignore
            if message:
                self.dns_status_row.set_subtitle(message)  # type: ignore
            elif active and current_subtitle != "Looking up...":
                self.dns_status_row.set_subtitle("Looking up...")  # type: ignore
            elif not active and not message:
                self.dns_status_row.set_subtitle("Idle")  # type: ignore
        if self.domain_entry:
            self.domain_entry.set_sensitive(not active)  # type: ignore
        if self.dns_apply_button:
            self.dns_apply_button.set_sensitive(not active)  # type: ignore
        if self.dns_record_type_dropdown:
            self.dns_record_type_dropdown.set_sensitive(not active)  # type: ignore

    def _validate_dns_input(self, user_input: str) -> bool:
        if not user_input:
            show_global_toast(self, "Input cannot be empty.")  # type: ignore
            main_window = self.get_native()  # type: ignore
            if not (main_window and hasattr(main_window, "show_toast")):  # type: ignore
                show_global_error(self, "Input cannot be empty.")  # type: ignore
            return False
        if not self._is_valid_ip_or_domain(user_input):
            show_global_toast(self, "Invalid IP address or domain name.")  # type: ignore
            main_window = self.get_native()  # type: ignore
            if not (main_window and hasattr(main_window, "show_toast")):
                show_global_error(self, "Invalid IP address or domain name.")  # type: ignore
            return False
        return True

    def _update_ptr_dropdown(self, user_input: str, requested_record_type: str) -> str:
        actual_record_type_used = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            model = self.dns_record_type_dropdown.get_model()  # type: ignore
            if model:
                for i in range(model.get_n_items()):  # type: ignore
                    if model.get_string(i) == "PTR":  # type: ignore
                        self.dns_record_type_dropdown.set_selected(i)  # type: ignore
                        actual_record_type_used = "PTR"
                        break
        return actual_record_type_used

    def _handle_dns_lookup_success(
        self,
        result_data: List[Dict[str, Any]],
        user_input: str,
        requested_record_type: str,
        dns_client: DnsResolverClient,
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
            self.dns_status_row.set_subtitle(status_message)  # type: ignore
        show_global_toast(self, status_message)  # type: ignore
        self._set_loading_state(False, message=status_message)

    def _handle_dns_lookup_exception(
        self,
        error: Exception,
        user_input: str,
        requested_record_type: str,
        dns_client_nameservers: List[str],
    ) -> None:
        error_message = str(error)
        status_subtitle = f"Error: {error_message.splitlines()[0]}"
        if isinstance(error, DnsNxDomainError):
            show_global_error(self, error_message)  # type: ignore
            status_subtitle = f"NXDOMAIN: Domain '{user_input}' not found."
            self._current_result_records = None
        elif isinstance(error, DnsNoAnswerError):
            logger.info("DNSPage: %s", error_message)
            self._current_result_records = []
            self._current_user_input = user_input
            self._current_requested_record_type = requested_record_type
            self._current_dns_servers = dns_client_nameservers
            self._display_result( [], user_input, requested_record_type, dns_client_nameservers)
            status_subtitle = f"No {requested_record_type} records found for {user_input} (No Answer)."
        elif isinstance(error, DnsResolutionTimeoutError):
            show_global_error(self, error_message)  # type: ignore
            status_subtitle = f"Timeout: Could not resolve {user_input}."
            self._current_result_records = None
        elif isinstance(error, DnsGenericError):
            logger.exception("DNSPage: DNS lookup failed for %s, type %s (DnsGenericError):", user_input, requested_record_type,)
            show_global_error(self, error_message)  # type: ignore
            status_subtitle = f"DNS Error: {error_message.splitlines()[0]}"
        elif isinstance(error, DnsClientError):
            logger.exception("DNSPage: Unexpected DnsClientError for %s, type %s:", user_input, requested_record_type,)
            show_global_error(self, f"DNS Client Error: {error_message}")  # type: ignore
            status_subtitle = f"Client Error: {error_message.splitlines()[0]}"
            self._current_result_records = None
        elif isinstance(error, Gio.IOErrorEnum): # type: ignore
             gio_cancelled_error_val = getattr(Gio.IOErrorEnum, 'CANCELLED', errno.ECANCELED)
             if error == gio_cancelled_error_val :
                status_subtitle = "Lookup cancelled by user."
                logger.info("DNSPage: DNS lookup cancelled by user for %s.", user_input)
             elif hasattr(error, 'value_name'):
                logger.exception("DNSPage: Unexpected GIO error during DNS lookup for %s, type %s: %s", user_input, requested_record_type, error.value_name)
                error_message_short = f"An unexpected GIO error occurred: {error.value_name}"
                show_global_error(self, error_message_short)
                status_subtitle = error_message_short
             else:
                status_subtitle = "An unexpected GIO error occurred."
                show_global_error(self, status_subtitle)
             self._current_result_records = None
        else:
            logger.exception("DNSPage: Unexpected error during DNS lookup for %s, type %s:", user_input, requested_record_type,)
            error_message_short = f"An unexpected error occurred: {error_message.splitlines()[0]}"
            show_global_error(self, error_message_short)  # type: ignore
            status_subtitle = error_message_short
            self._current_result_records = None
        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_subtitle)  # type: ignore
        self._set_loading_state(False, message=status_subtitle)

    def _perform_lookup_thread_func(
        self, task: Gio.Task, _source_object: Any, task_data: Dict[str, Any], cancellable: Optional[Gio.Cancellable]
    ) -> None:
        user_input: str = task_data["user_input"]
        requested_record_type: str = task_data["requested_record_type"]
        custom_dns_server: Optional[str] = task_data["custom_dns_server"]
        dns_client = DnsResolverClient(custom_dns_server=custom_dns_server)
        try:
            if cancellable and cancellable.is_cancelled():
                task.return_error(Gio.io_error_from_errno(errno.ECANCELED)) # type: ignore
                return
            result_data = dns_client.resolve(user_input, requested_record_type)
            gvariant_result_data_list = []
            for record_dict in result_data:
                builder = GObject.VariantBuilder.new(GObject.VariantType.new("a{sv}")) # type: ignore
                for key, value in record_dict.items():
                    if isinstance(value, list) and all(isinstance(s, str) for s in value):
                        builder.add_value(GObject.Variant("s", key)) # type: ignore
                        builder.add_value(GObject.Variant("as", value)) # type: ignore
                    elif isinstance(value, (str, int, bool)):
                        builder.add_value(GObject.Variant("s", key)) # type: ignore
                        builder.add_value(GObject.Variant.new_auto(value)) # type: ignore
                    else:
                        builder.add_value(GObject.Variant("s", key)) # type: ignore
                        builder.add_value(GObject.Variant("s", str(value) if value is not None else "")) # type: ignore
                gvariant_result_data_list.append(builder.end())
            return_bundle = (
                user_input, requested_record_type, gvariant_result_data_list,
                tuple(dns_client.resolver.nameservers)
            )
            task.return_value(GObject.Variant("(ss@a(a{sv})as)", return_bundle)) # type: ignore
        except Exception as e:
            task.return_error(e)

    def _perform_lookup_finish_callback(self, _source_object: Any, result: Gio.AsyncResult, _user_data: Any) -> None:
        task: Gio.Task = result # type: ignore
        original_task_data = task.get_task_data()
        user_input = original_task_data["user_input"] # type: ignore
        requested_record_type = original_task_data["requested_record_type"] # type: ignore
        custom_dns_server = original_task_data["custom_dns_server"] # type: ignore
        dns_client_nameservers_for_error = DnsResolverClient(custom_dns_server=custom_dns_server).resolver.nameservers
        try:
            returned_variant: GObject.Variant = task.propagate_value() # type: ignore
            _returned_user_input, _returned_req_type, gv_result_data_list, nameservers_used_tuple = returned_variant.unpack()
            py_result_data_list = []
            for gv_dict_item in gv_result_data_list: # type: ignore
                py_dict: Dict[str, Any] = {}
                for key_str in gv_dict_item: # type: ignore
                    value_variant = gv_dict_item.lookup_value(key_str, None) # type: ignore
                    if value_variant:
                         if value_variant.is_of_type(GObject.VariantType.new("as")): # type: ignore
                             py_dict[key_str] = list(value_variant.get_strv()) # type: ignore
                         else:
                             py_dict[key_str] = value_variant.unpack() # type: ignore
                py_result_data_list.append(py_dict)
            nameservers_used = list(nameservers_used_tuple) # type: ignore
            temp_success_dns_client = DnsResolverClient(custom_dns_server=custom_dns_server)
            temp_success_dns_client.resolver.nameservers = nameservers_used
            self._handle_dns_lookup_success(
                py_result_data_list, user_input, requested_record_type, temp_success_dns_client
            )
        except Exception as e:
            is_cancelled_error = False
            if isinstance(e, Gio.IOErrorEnum): # type: ignore
                 gio_cancelled_error_val = getattr(Gio.IOErrorEnum, 'CANCELLED', errno.ECANCELED)
                 if e == gio_cancelled_error_val: # type: ignore
                    is_cancelled_error = True
            if is_cancelled_error:
                logger.info("DNSPage: DNS lookup task was cancelled for %s.", user_input)
                self._set_loading_state(False, message="Lookup cancelled.")
            else:
                self._handle_dns_lookup_exception(e, user_input, requested_record_type, dns_client_nameservers_for_error)

    def _perform_lookup(self) -> None:
        self._set_loading_state(True, "Looking up...")
        user_input = self.domain_entry.get_text().strip() # type: ignore
        requested_record_type = self._get_selected_record_type()
        logger.debug(f"DNSPage: Performing DNS lookup for: {user_input}, type: {requested_record_type}")
        if not self._validate_dns_input(user_input):
            self._set_loading_state(False, "Idle - Invalid input.")
            return
        self._clear_error()
        if self._dns_lookup_cancellable and not self._dns_lookup_cancellable.is_cancelled():
            logger.info("DNSPage: Cancelling previous DNS lookup task.")
            self._dns_lookup_cancellable.cancel()
        self._dns_lookup_cancellable = Gio.Cancellable()
        custom_dns_server = self.settings.get_string("custom-dns-server")
        task_data = {
            "user_input": user_input,
            "requested_record_type": requested_record_type,
            "custom_dns_server": custom_dns_server or None,
        }
        task = Gio.Task.new(
            source_object=self, # type: ignore
            task_data=task_data,
            cancellable=self._dns_lookup_cancellable,
            callback=self._perform_lookup_finish_callback, # type: ignore
        )
        task.run_in_thread_sync(self._perform_lookup_thread_func) # type: ignore

    def _get_selected_record_type(self) -> str:
        model = self.dns_record_type_dropdown.get_model()  # type: ignore
        selected_index = self.dns_record_type_dropdown.get_selected()  # type: ignore
        return model.get_string(selected_index)  # type: ignore

    def _clear_error(self) -> None:
        self.domain_entry.remove_css_class("error")  # type: ignore
        main_window = self.get_native()  # type: ignore
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error()  # type: ignore
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    def _display_result(
        self,
        result_records: List[Dict[str, Any]],
        domain_or_ip: str,
        record_type: str,
        dns_servers: Sequence[Any],
    ) -> None:
        logger.debug(
            "Displaying %d results for %s (type %s) using servers %s.",
            len(result_records), domain_or_ip, record_type, dns_servers,
        )
        logger.info(
            "Query for %s, type %s, using servers %s, returned %d records.",
            domain_or_ip, record_type, dns_servers, len(result_records),
        )
        while child := self.dns_results_box_container.get_first_child():  # type: ignore
            self.dns_results_box_container.remove(child)  # type: ignore
        query_info_row = Adw.ActionRow(title=f"Query: {domain_or_ip}", subtitle=f"Record type queried: {record_type}")
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row)  # type: ignore
        servers_str = ", ".join(map(str, dns_servers)) if dns_servers else "System default"
        servers_info_row = Adw.ActionRow(title="DNS Servers Used", subtitle=servers_str)
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row)  # type: ignore
        self.dns_results_box_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))  # type: ignore
        if not result_records:
            no_records_message = f"No {record_type} records found for {domain_or_ip}."
            if record_type == "PTR" and is_valid_ip(domain_or_ip):
                no_records_message = f"No PTR records found for IP address {domain_or_ip}."
            elif record_type == "PTR":
                no_records_message = f"No PTR records found for {domain_or_ip}."
            no_records_row = Adw.ActionRow(title=no_records_message)
            no_records_row.set_selectable(False)
            self.dns_results_box_container.append(no_records_row)  # type: ignore
            if self.dns_clear_results_button:
                self.dns_clear_results_button.set_sensitive(False)
            if self.dns_copy_all_results_button:
                self.dns_copy_all_results_button.set_sensitive(False)
            return
        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row:
                self.dns_results_box_container.append(row)  # type: ignore
        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(True)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(True)

    def _build_address_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        row = self._create_base_action_row(name, record_type, base_subtitle, "network-wired-symbolic")
        address_value = str(record_data.get("address", "N/A"))
        summary_text = (f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {address_value}")
        self._add_standard_suffix_box_to_row(row, address_value, "Copy Address", summary_text)
        return row

    def _build_cname_ns_ptr_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        icon_name = "emblem-shared-symbolic"
        if record_type == "NS":
            icon_name = "network-server-symbolic"
        elif record_type == "PTR":
            icon_name = "system-search-symbolic"
        row = self._create_base_action_row(name, record_type, base_subtitle, icon_name)
        target_value = str(record_data.get("target", "N/A"))
        summary_text = (f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {target_value}")
        self._add_standard_suffix_box_to_row(row, target_value, "Copy Target", summary_text)
        return row

    def _build_generic_data_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        row = self._create_base_action_row(name, record_type, base_subtitle, "help-question-symbolic")
        data_value = str(record_data.get("data", "N/A"))
        summary_text = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {data_value}"
        self._add_standard_suffix_box_to_row(row, data_value, "Copy Data", summary_text)
        return row

    def _build_mx_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:
        exchange_value = str(record_data.get("exchange", "N/A"))
        preference_value = str(record_data.get("preference", "N/A"))
        summary_mx = (f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} MX {preference_value} {exchange_value}")
        row = self._create_base_expander_row(name, f"MX Record ({base_subtitle})", "mail-send-receive-symbolic", summary_mx)
        exchange_value_label = Gtk.Label(label=exchange_value, halign=Gtk.Align.FILL, hexpand=True, selectable=True, wrap=False, lines=1, ellipsize=Pango.EllipsizeMode.END,)
        copy_button_exchange = self._create_copy_button(exchange_value, f"Copy Exchange: {exchange_value}", row)
        mx_detail_row = Adw.ActionRow(subtitle=f"Preference: {preference_value}")  # type: ignore
        mx_detail_row.add_prefix(exchange_value_label)  # type: ignore
        mx_detail_row.add_prefix(copy_button_exchange)  # type: ignore
        mx_detail_row.set_selectable(True)
        row.add_row(mx_detail_row)  # type: ignore
        row.set_expanded(True)
        return row

    def _build_txt_record_row(
        self, record_data: Dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:
        texts = record_data.get("texts", [])
        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} TXT {texts_str_summary}"
        row = self._create_base_expander_row(name, f"TXT Records ({base_subtitle})", "document-properties-symbolic", summary_txt)
        if not texts:
            no_text_row = Adw.ActionRow(title="No text data.", selectable=False)  # type: ignore
            row.add_row(no_text_row)  # type: ignore
        else:
            for text_string in texts:
                self._add_expander_detail_row(row, None, text_string, "Copy Text Segment", is_value_primary_content=True)
        row.set_expanded(bool(texts))
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
        row = self._create_base_expander_row(name, f"SOA Record ({base_subtitle})", "document-settings-symbolic", summary_soa)
        soa_fields = [
            ("MNAME", mname_val), ("RNAME", rname_val), ("Serial", serial_val),
            ("Refresh", refresh_val), ("Retry", retry_val), ("Expire", expire_val),
            ("Minimum TTL", minimum_val),
        ]
        for field_name_str, field_value in soa_fields:
            self._add_expander_detail_row(row, field_name_str, field_value, f"Copy {field_name_str}")
        row.set_expanded(True)
        return row

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
            logger.warning("Could not create row for unknown record_data type or missing data field: %s (Type: %s)", record_data, record_type,)
            return None

    def trigger_lookup(self) -> None:
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive():  # type: ignore
            self.dns_apply_button.activate()  # type: ignore[attr-defined]
        else:
            logger.warning("DNS lookup button not available or not sensitive, cannot trigger lookup.")
