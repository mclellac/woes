"""
Defines the DNS lookup page for the Woes application.

This module contains the :class:`DNSPage` class, which provides UI and
functionality for performing DNS lookups.
"""
import logging
logger = logging.getLogger(__name__)

# DNS library imports (dns.resolver, etc.) are now primarily in dns_client.py
import gi
from gi.repository import Adw, Gio, Gtk, Pango, GObject
from typing import Optional, Sequence, Any, List, Dict

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
        """
        Initialize the DNSPage.

        Sets up UI elements, connects signals, and initializes GSettings.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage`
                       constructor.
        :type kwargs: GObject.GObject
        """
        super().__init__(**kwargs)
        logger.debug("DNSPage initialized.")
        self._connect_signals()
        self.settings = Gio.Settings.new(APP_ID)

        # Initialize new status row and spinner
        # Initially disable clear/copy buttons as there are no results

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.domain_entry.connect("activate", self._on_entry_activated) # type: ignore
        self.dns_apply_button.connect("clicked", self._on_entry_activated) # type: ignore
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed) # type: ignore
        if self.dns_clear_results_button:
            self.dns_clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.connect("clicked", self._on_copy_all_results_clicked)

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Clear all DNS lookup results from the UI.

        :param _button: The Gtk.Button that was clicked (unused).
        :type _button: Gtk.Button
        """
        logger.info("Clearing DNS results.")
        while (child := self.dns_results_box_container.get_first_child()): # type: ignore
            self.dns_results_box_container.remove(child) # type: ignore

        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(False)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(False)

    def _on_copy_all_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Copy all displayed DNS results to the clipboard.

        :param _button: The Gtk.Button that was clicked (unused).
        :type _button: Gtk.Button
        """
        logger.info("Copying all DNS results to clipboard.")
        all_results_text = []

        # Iterate through children of dns_results_box_container
        child = self.dns_results_box_container.get_first_child() # type: ignore
        while child:
            text_parts_for_child = []
            if isinstance(child, Adw.ActionRow):
                title = child.get_title()
                subtitle = child.get_subtitle()
                if title:
                    text_parts_for_child.append(title)
                if subtitle:
                    text_parts_for_child.append(subtitle)

                # Attempt to get text from suffixes if they are labels
                # This is a simplified approach; real implementation might need to traverse deeper
                # or access data from the model that generated the rows.
                # For this example, we'll focus on title/subtitle of ActionRows.
                # If the row has a Gtk.Label in a suffix, try to get its text.
                # This part is heuristic as direct access to full record data isn't stored on rows.

            elif isinstance(child, Adw.ExpanderRow):
                title = child.get_title()
                subtitle = child.get_subtitle()
                if title:
                    text_parts_for_child.append(title)
                if subtitle:
                    text_parts_for_child.append(subtitle)
                # Could iterate expander's rows too, but keeping it simple for now.
                # A more robust way would be to have the data that generated these rows
                # stored in an instance variable and iterate that.

            if text_parts_for_child:
                all_results_text.append(" - ".join(text_parts_for_child))

            child = child.get_next_sibling()

        if not all_results_text:
            show_global_toast(self, "No results to copy.") # type: ignore
            return

        final_text_to_copy = "\n".join(all_results_text)
        DNSPage._copy_to_clipboard(final_text_to_copy, self)
        show_global_toast(self, "All results copied to clipboard.") # type: ignore


    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        """
        Copy the given text to the clipboard.

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
        """
        Validate if the input is a syntactically valid IP or domain.

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
        """
        Handle activation of the domain entry or click of the 'Lookup' button.

        Triggers the DNS lookup process.

        :param _widget: The widget that triggered the action (unused).
        :type _widget: Gtk.Widget
        """
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec) -> None:
        """
        Handle changes in the selected DNS record type.

        Triggers a new DNS lookup with the new record type.

        :param _dropdown: The :class:`Gtk.DropDown` widget whose selection changed.
        :type _dropdown: Gtk.DropDown
        :param _param_spec: The :class:`GObject.ParamSpec` of the property that changed (unused).
        :type _param_spec: GObject.ParamSpec
        """
        self._perform_lookup()

    # --- Helper methods for building record rows ---

    def _create_copy_button(self, text_to_copy: str, tooltip_text: str, widget_for_clipboard: Gtk.Widget) -> Gtk.Button:
        """
        Create a Gtk.Button for copying text.

        :param text_to_copy: The text to be copied when the button is clicked.
        :type text_to_copy: str
        :param tooltip_text: The tooltip text for the button.
        :type tooltip_text: str
        :param widget_for_clipboard: The widget from which to get the clipboard.
        :type widget_for_clipboard: Gtk.Widget
        :return: A new Gtk.Button configured for copying.
        :rtype: Gtk.Button
        """
        button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip_text)
        button.connect("clicked", lambda _btn, text=text_to_copy, w=widget_for_clipboard: DNSPage._copy_to_clipboard(text, w))
        return button

    def _create_base_action_row(self, name: str, record_type_label: str, base_subtitle_text: str, icon_name: Optional[str]) -> Adw.ActionRow:
        """
        Create a basic Adw.ActionRow with title, subtitle, and optional icon.

        :param name: The title for the ActionRow, typically the record name.
        :type name: str
        :param record_type_label: The string representation of the record type (e.g., "A", "MX").
        :type record_type_label: str
        :param base_subtitle_text: Base text for the subtitle (e.g., class and TTL info).
        :type base_subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :return: A new Adw.ActionRow.
        :rtype: Adw.ActionRow
        """
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type_label}, {base_subtitle_text}") # type: ignore
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name)) # type: ignore
        row.set_selectable(False)
        return row

    def _add_standard_suffix_box_to_row(
        self,
        row: Adw.ActionRow,
        main_value_text: str,
        main_value_tooltip_prefix: str,
        full_summary_text: str
    ) -> None:
        """
        Add a standard suffix box (label, copy value button, copy summary button) to an ActionRow.

        :param row: The Adw.ActionRow to add suffixes to.
        :type row: Adw.ActionRow
        :param main_value_text: The main value to display as a label and for the value copy button.
        :type main_value_text: str
        :param main_value_tooltip_prefix: The prefix for the tooltip of the value copy button.
        :type main_value_tooltip_prefix: str
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        """
        value_label = Gtk.Label(label=main_value_text, halign=Gtk.Align.FILL, hexpand=True, selectable=True, wrap=False, lines=1, ellipsize=Pango.EllipsizeMode.END)
        # suffix_box removed
        row.add_suffix(value_label) # type: ignore
        row.add_suffix(self._create_copy_button(main_value_text, f"{main_value_tooltip_prefix}: {main_value_text}", row)) # type: ignore
        row.add_suffix(self._create_copy_button(full_summary_text, "Copy Full Record Summary", row)) # type: ignore

    def _create_base_expander_row(self, name: str, subtitle_text: str, icon_name: Optional[str], full_summary_text: str) -> Adw.ExpanderRow:
        """
        Create a basic Adw.ExpanderRow with title, subtitle, icon, and a full summary copy button.

        :param name: The title for the ExpanderRow.
        :type name: str
        :param subtitle_text: The subtitle for the ExpanderRow.
        :type subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        :return: A new Adw.ExpanderRow.
        :rtype: Adw.ExpanderRow
        """
        row = Adw.ExpanderRow(title=name, subtitle=subtitle_text) # type: ignore
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name)) # type: ignore

        copy_full_button = self._create_copy_button(full_summary_text, "Copy Full Record Summary", row)
        row.add_suffix(copy_full_button) # type: ignore
        return row

    def _add_expander_detail_row(
            self,
            expander_row: Adw.ExpanderRow,
            title: Optional[str],
            value_text: str,
            copy_tooltip_prefix: str,
            is_value_primary_content: bool = False
        ):
        """
        Add a detail row (Adw.ActionRow) to an Adw.ExpanderRow.

        :param expander_row: The Adw.ExpanderRow to add the detail row to.
        :type expander_row: Adw.ExpanderRow
        :param title: Optional title for the detail ActionRow.
        :type title: Optional[str]
        :param value_text: The value text to display in the detail row.
        :type value_text: str
        :param copy_tooltip_prefix: The prefix for the tooltip of the copy button for the value.
        :type copy_tooltip_prefix: str
        :param is_value_primary_content: If True, value_text is the primary content (e.g., TXT segments).
                                         Otherwise, it's a suffix to the title (e.g., SOA fields).
        :type is_value_primary_content: bool
        """
        detail_row = Adw.ActionRow(title=title if title else None) # type: ignore

        value_label = Gtk.Label(label=value_text, halign=Gtk.Align.FILL, hexpand=True, selectable=True, wrap=False, lines=1, ellipsize=Pango.EllipsizeMode.END)
        copy_button = self._create_copy_button(value_text, f"{copy_tooltip_prefix}: {value_text}", expander_row)

        # content_box removed
        if is_value_primary_content : # For TXT segments where the value is the main content of the row
            detail_row.add_prefix(value_label) # type: ignore
            detail_row.add_prefix(copy_button) # type: ignore
        else: # For SOA fields where title is present and value is a suffix
            detail_row.add_suffix(value_label) # type: ignore
            detail_row.add_suffix(copy_button) # type: ignore

        detail_row.set_selectable(False)
        expander_row.add_row(detail_row) # type: ignore

    def _set_loading_state(self, active: bool, message: Optional[str] = None) -> None:
        """
        Set the UI loading state (spinner, status message, sensitivity).

        :param active: True to set loading state, False to unset.
        :type active: bool
        :param message: Optional message to display in the status row.
        :type message: Optional[str]
        """
        if self.dns_status_spinner:
            self.dns_status_spinner.set_visible(active) # type: ignore
            if active:
                self.dns_status_spinner.start() # type: ignore
            else:
                self.dns_status_spinner.stop() # type: ignore

        if self.dns_status_row:
            current_subtitle = self.dns_status_row.get_subtitle() # type: ignore
            if message:
                self.dns_status_row.set_subtitle(message) # type: ignore
            elif active and current_subtitle != "Looking up...": # Default message when starting an operation
                self.dns_status_row.set_subtitle("Looking up...") # type: ignore
            elif not active and not message : # Default message when stopping (idle) and no specific message given
                self.dns_status_row.set_subtitle("Idle") # type: ignore
            # If not active and a message is present (e.g. error or success), it will be set by the caller.

        if self.domain_entry:
            self.domain_entry.set_sensitive(not active) # type: ignore
        if self.dns_apply_button:
            self.dns_apply_button.set_sensitive(not active) # type: ignore
        if self.dns_record_type_dropdown:
            self.dns_record_type_dropdown.set_sensitive(not active) # type: ignore

    def _validate_dns_input(self, user_input: str) -> bool:
        """
        Validate the DNS user input. Shows global error/toast if invalid.

        :param user_input: The user input string to validate.
        :type user_input: str
        :return: True if valid, False otherwise.
        :rtype: bool
        """
        if not user_input:
            show_global_toast(self, "Input cannot be empty.") # type: ignore
            main_window = self.get_native() # type: ignore
            if not (main_window and hasattr(main_window, 'show_toast')): # type: ignore
                show_global_error(self, "Input cannot be empty.") # type: ignore
            return False

        if not self._is_valid_ip_or_domain(user_input):
            show_global_toast(self, "Invalid IP address or domain name.") # type: ignore
            main_window = self.get_native() # type: ignore
            if not (main_window and hasattr(main_window, 'show_toast')):
                show_global_error(self, "Invalid IP address or domain name.") # type: ignore
            return False
        return True

    def _update_ptr_dropdown(self, user_input: str, requested_record_type: str) -> str:
        """
        Update the record type dropdown to PTR if an IP was entered and PTR was requested.

        Returns the record type that was effectively used or set.

        :param user_input: The user input string (domain or IP).
        :type user_input: str
        :param requested_record_type: The record type initially requested by the user.
        :type requested_record_type: str
        :return: The record type string that is effectively used or set in the UI.
        :rtype: str
        """
        actual_record_type_used = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            model = self.dns_record_type_dropdown.get_model() # type: ignore
            if model:
                for i in range(model.get_n_items()): # type: ignore
                    if model.get_string(i) == "PTR": # type: ignore
                        self.dns_record_type_dropdown.set_selected(i) # type: ignore
                        actual_record_type_used = "PTR"
                        break
        return actual_record_type_used

    def _handle_dns_lookup_success(
        self,
        result_data: List[Dict[str, Any]],
        user_input: str,
        requested_record_type: str, # The type initially selected by user
        dns_client: DnsResolverClient
    ) -> None:
        """
        Handle successful DNS lookup results.

        :param result_data: The list of DNS records obtained from the lookup.
        :type result_data: List[Dict[str, Any]]
        :param user_input: The domain or IP address that was queried.
        :type user_input: str
        :param requested_record_type: The DNS record type that was requested.
        :type requested_record_type: str
        :param dns_client: The DnsResolverClient instance used for the lookup.
        :type dns_client: DnsResolverClient
        """
        # Determine the actual type used, especially if PTR was auto-selected for an IP.
        # DnsResolverClient internally handles reverse name for PTR if domain_or_ip is an IP.
        # So, if user selected PTR for an IP, requested_record_type is already "PTR".
        # If user selected something else for an IP, DnsResolverClient tried that type.
        # This logic is mainly for updating the dropdown if it wasn't already PTR for an IP.
        actual_record_type_displayed = requested_record_type
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            actual_record_type_displayed = self._update_ptr_dropdown(user_input, requested_record_type)

        nameservers_used = dns_client.resolver.nameservers
        self._display_result(result_data, user_input, actual_record_type_displayed, nameservers_used)

        status_message = f"{len(result_data)} {actual_record_type_displayed} record(s) found." if result_data else f"No {actual_record_type_displayed} records found for {user_input}."
        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_message) # type: ignore
        # show_global_toast is good for transient notifications, status row is persistent.
        show_global_toast(self, status_message) # type: ignore

    def _handle_dns_lookup_exception(
        self,
        error: Exception,
        user_input: str,
        requested_record_type: str,
        dns_client: DnsResolverClient # Pass client to get nameservers for NoAnswer
    ) -> None:
        """
        Handle exceptions from DnsResolverClient.

        :param error: The exception object that was raised.
        :type error: Exception
        :param user_input: The domain or IP address that was queried.
        :type user_input: str
        :param requested_record_type: The DNS record type that was requested.
        :type requested_record_type: str
        :param dns_client: The DnsResolverClient instance used for the lookup.
        :type dns_client: DnsResolverClient
        """
        error_message = str(error) # Original full error message
        status_subtitle = f"Error: {error_message.splitlines()[0]}" # Default status: first line of error

        if isinstance(error, DnsNxDomainError):
            show_global_error(self, error_message) # type: ignore
            status_subtitle = f"NXDOMAIN: Domain '{user_input}' not found."
        elif isinstance(error, DnsNoAnswerError):
            logger.info("DNSPage: %s", error_message)
            nameservers_used = dns_client.resolver.nameservers
            self._display_result([], user_input, requested_record_type, nameservers_used) # Shows "No records found" in results area
            # Override the "No records found" status from _display_result with the actual error for clarity in status row
            status_subtitle = f"No {requested_record_type} records found for {user_input} (No Answer)."
        elif isinstance(error, DnsResolutionTimeoutError):
            show_global_error(self, error_message) # type: ignore
            status_subtitle = f"Timeout: Could not resolve {user_input}."
        elif isinstance(error, DnsGenericError):
            logger.exception("DNSPage: DNS lookup failed for %s, type %s (DnsGenericError):", user_input, requested_record_type)
            show_global_error(self, error_message) # type: ignore
            status_subtitle = f"DNS Error: {error_message.splitlines()[0]}"
        elif isinstance(error, DnsClientError): # Base client error
            logger.exception("DNSPage: Unexpected DnsClientError for %s, type %s:", user_input, requested_record_type)
            show_global_error(self, f"DNS Client Error: {error_message}") # type: ignore
            status_subtitle = f"Client Error: {error_message.splitlines()[0]}"
        else: # Generic Exception
            logger.exception("DNSPage: Unexpected error during DNS lookup for %s, type %s:", user_input, requested_record_type)
            error_message_short = f"An unexpected error occurred: {error_message.splitlines()[0]}"
            show_global_error(self, error_message_short) # type: ignore
            status_subtitle = error_message_short

        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_subtitle) # type: ignore


    def _perform_lookup(self) -> None:
        """
        Perform the DNS lookup based on user input and selected record type.

        Orchestrates input validation, client interaction, and result/error display.
        """
        self._set_loading_state(True, "Looking up...")
        user_input = self.domain_entry.get_text().strip() # type: ignore
        requested_record_type = self._get_selected_record_type()
        logger.debug(f"DNSPage: Performing DNS lookup for: {user_input}, type: {requested_record_type}")

        if not self._validate_dns_input(user_input):
            self._set_loading_state(False, "Idle - Invalid input.") # Reset status to Idle with specific message
            return

        self._clear_error()

        custom_dns_server = self.settings.get_string("custom-dns-server")
        dns_client = DnsResolverClient(custom_dns_server=custom_dns_server or None)

        try:
            result_data = dns_client.resolve(user_input, requested_record_type)
            self._handle_dns_lookup_success(result_data, user_input, requested_record_type, dns_client)
            # Status is set by _handle_dns_lookup_success
        except Exception as e: # Catch all exceptions here and delegate to the handler
            self._handle_dns_lookup_exception(e, user_input, requested_record_type, dns_client)
            # Status is set by _handle_dns_lookup_exception
        finally:
            # Ensure loading state is always reset (spinner off, controls on),
            # but preserve the status message set by success/error handlers.
            # Call _set_loading_state without a message to achieve this.
            self._set_loading_state(False)


    def _get_selected_record_type(self) -> str:
        """
        Get the currently selected DNS record type from the dropdown.

        :return: The selected record type as a string (e.g., "A", "MX").
        :rtype: str
        """
        model = self.dns_record_type_dropdown.get_model() # type: ignore
        selected_index = self.dns_record_type_dropdown.get_selected() # type: ignore
        return model.get_string(selected_index) # type: ignore

    def _clear_error(self) -> None:
        """
        Clear any existing error messages from the UI.

        This includes hiding the main window's error banner and removing
        the 'error' CSS class from the domain entry row.
        """
        self.domain_entry.remove_css_class("error") # type: ignore
        main_window = self.get_native() # type: ignore
        if main_window and hasattr(main_window, 'hide_error'):
            main_window.hide_error() # type: ignore
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    def _display_result(
        self,
        result_records: List[Dict[str, Any]],
        domain_or_ip: str,
        record_type: str,
        dns_servers: Sequence[Any],
    ) -> None:
        """
        Display the DNS lookup results in the UI.

        Clears previous results and populates the results container with new
        rows based on the lookup outcome.

        :param result_records: A list of parsed DNS record dictionaries.
        :type result_records: List[Dict[str, Any]]
        :param domain_or_ip: The domain or IP that was queried.
        :type domain_or_ip: str
        :param record_type: The record type that was queried.
        :type record_type: str
        :param dns_servers: A sequence of DNS server addresses used for the query.
        :type dns_servers: Sequence[Any]
        """
        logger.debug("Displaying %d results for %s (type %s) using servers %s.", len(result_records), domain_or_ip, record_type, dns_servers)
        logger.info("Query for %s, type %s, using servers %s, returned %d records.", domain_or_ip, record_type, dns_servers, len(result_records))

        while (child := self.dns_results_box_container.get_first_child()): # type: ignore
            self.dns_results_box_container.remove(child) # type: ignore

        query_info_row = Adw.ActionRow(title=f"Query: {domain_or_ip}", subtitle=f"Record type queried: {record_type}")
        query_info_row.set_selectable(False)
        self.dns_results_box_container.append(query_info_row) # type: ignore

        servers_str = ", ".join(map(str, dns_servers)) if dns_servers else "System default"
        servers_info_row = Adw.ActionRow(title="DNS Servers Used", subtitle=servers_str)
        servers_info_row.set_selectable(False)
        self.dns_results_box_container.append(servers_info_row) # type: ignore
        self.dns_results_box_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)) # type: ignore

        if not result_records:
            no_records_message = f"No {record_type} records found for {domain_or_ip}."
            if record_type == "PTR" and is_valid_ip(domain_or_ip):
                no_records_message = f"No PTR records found for IP address {domain_or_ip}."
            elif record_type == "PTR": # For domain name PTR queries (less common but possible)
                no_records_message = f"No PTR records found for {domain_or_ip}."
            no_records_row = Adw.ActionRow(title=no_records_message)
            no_records_row.set_selectable(False)
            self.dns_results_box_container.append(no_records_row) # type: ignore
            # Update button sensitivity: No results, so disable
            if self.dns_clear_results_button:
                self.dns_clear_results_button.set_sensitive(False)
            if self.dns_copy_all_results_button:
                self.dns_copy_all_results_button.set_sensitive(False)
            return

        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row:
                self.dns_results_box_container.append(row) # type: ignore

        # Update button sensitivity: Results are present, so enable
        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(True)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(True)


    # --- Helper methods for building record rows ---

    def _create_copy_button(self, text_to_copy: str, tooltip_text: str, widget_for_clipboard: Gtk.Widget) -> Gtk.Button:
        """
        Create a Gtk.Button for copying text.

        :param text_to_copy: The text to be copied when the button is clicked.
        :type text_to_copy: str
        :param tooltip_text: The tooltip text for the button.
        :type tooltip_text: str
        :param widget_for_clipboard: The widget from which to get the clipboard.
        :type widget_for_clipboard: Gtk.Widget
        :return: A new Gtk.Button configured for copying.
        :rtype: Gtk.Button
        """
        button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip_text)
        button.connect("clicked", lambda _btn, text=text_to_copy, w=widget_for_clipboard: DNSPage._copy_to_clipboard(text, w))
        return button

    def _create_base_action_row(self, name: str, record_type_label: str, base_subtitle_text: str, icon_name: Optional[str]) -> Adw.ActionRow:
        """
        Create a basic Adw.ActionRow with title, subtitle, and optional icon.

        :param name: The title for the ActionRow, typically the record name.
        :type name: str
        :param record_type_label: The string representation of the record type (e.g., "A", "MX").
        :type record_type_label: str
        :param base_subtitle_text: Base text for the subtitle (e.g., class and TTL info).
        :type base_subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :return: A new Adw.ActionRow.
        :rtype: Adw.ActionRow
        """
        row = Adw.ActionRow(title=name, subtitle=f"Type: {record_type_label}, {base_subtitle_text}") # type: ignore
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name)) # type: ignore
        row.set_selectable(False)
        return row

    def _add_standard_suffix_box_to_row(
        self,
        row: Adw.ActionRow,
        main_value_text: str,
        main_value_tooltip_prefix: str,
        full_summary_text: str
    ) -> None:
        """
        Add a standard suffix box (label, copy value button, copy summary button) to an ActionRow.

        :param row: The Adw.ActionRow to add suffixes to.
        :type row: Adw.ActionRow
        :param main_value_text: The main value to display as a label and for the value copy button.
        :type main_value_text: str
        :param main_value_tooltip_prefix: The prefix for the tooltip of the value copy button.
        :type main_value_tooltip_prefix: str
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        """
        value_label = Gtk.Label(label=main_value_text, halign=Gtk.Align.START, selectable=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        # suffix_box is removed. Widgets will be added directly to the row.

        copy_value_button = self._create_copy_button(main_value_text, f"{main_value_tooltip_prefix}: {main_value_text}", row)
        copy_full_summary_button = self._create_copy_button(full_summary_text, "Copy Full Record Summary", row)

        row.add_suffix(value_label) # type: ignore
        row.add_suffix(copy_value_button) # type: ignore
        row.add_suffix(copy_full_summary_button) # type: ignore

    def _create_base_expander_row(self, name: str, subtitle_text: str, icon_name: Optional[str], full_summary_text: str) -> Adw.ExpanderRow:
        """
        Create a basic Adw.ExpanderRow with title, subtitle, icon, and a full summary copy button.

        :param name: The title for the ExpanderRow.
        :type name: str
        :param subtitle_text: The subtitle for the ExpanderRow.
        :type subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        :return: A new Adw.ExpanderRow.
        :rtype: Adw.ExpanderRow
        """
        row = Adw.ExpanderRow(title=name, subtitle=subtitle_text) # type: ignore
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name)) # type: ignore

        copy_full_button = self._create_copy_button(full_summary_text, "Copy Full Record Summary", row)
        row.add_suffix(copy_full_button) # type: ignore
        # row.set_expanded(True) # Decided by caller, as TXT/SOA might be empty initially
        return row

    def _add_expander_detail_row(
            self,
            expander_row: Adw.ExpanderRow,
            title: Optional[str],
            value_text: str,
            copy_tooltip_prefix: str,
            is_value_primary_content: bool = False
        ):
        """
        Add a detail row (Adw.ActionRow) to an Adw.ExpanderRow.

        :param expander_row: The Adw.ExpanderRow to add the detail row to.
        :type expander_row: Adw.ExpanderRow
        :param title: Optional title for the detail ActionRow.
        :type title: Optional[str]
        :param value_text: The value text to display in the detail row.
        :type value_text: str
        :param copy_tooltip_prefix: The prefix for the tooltip of the copy button for the value.
        :type copy_tooltip_prefix: str
        :param is_value_primary_content: If True, value_text is the primary content (e.g., TXT segments).
                                         Otherwise, it's a suffix to the title (e.g., SOA fields).
        :type is_value_primary_content: bool
        """
        detail_row = Adw.ActionRow(title=title if title else None) # type: ignore

        value_label = Gtk.Label(label=value_text, halign=Gtk.Align.START, selectable=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        copy_button = self._create_copy_button(value_text, f"{copy_tooltip_prefix}: {value_text}", expander_row)

        # content_box is removed. Widgets will be added directly to the detail_row.
        if is_value_primary_content : # For TXT segments where the value is the main content of the row
            detail_row.add_prefix(value_label) # type: ignore
            detail_row.add_prefix(copy_button) # type: ignore
        else: # For SOA fields where title is present and value is a suffix
            detail_row.add_suffix(value_label) # type: ignore
            detail_row.add_suffix(copy_button) # type: ignore

        detail_row.set_selectable(False)
        expander_row.add_row(detail_row) # type: ignore

    # --- Modified _build_*_record_row methods ---

    def _build_address_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """
        Build a UI row for an A or AAAA DNS record.

        :param record_data: Parsed record data.
        :type record_data: Dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("A" or "AAAA").
        :type record_type: str
        :return: An Adw.ActionRow for the record.
        :rtype: Adw.ActionRow
        """
        row = self._create_base_action_row(name, record_type, base_subtitle, "network-wired-symbolic")
        address_value = str(record_data.get('address', 'N/A'))
        summary_text = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {address_value}"
        self._add_standard_suffix_box_to_row(row, address_value, "Copy Address", summary_text)
        return row

    def _build_cname_ns_ptr_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """
        Build a UI row for CNAME, NS, or PTR DNS records.

        :param record_data: Parsed record data.
        :type record_data: Dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("CNAME", "NS", "PTR").
        :type record_type: str
        :return: An Adw.ActionRow for the record.
        :rtype: Adw.ActionRow
        """
        icon_name = "emblem-shared-symbolic" # Default for CNAME
        if record_type == "NS":
            icon_name = "network-server-symbolic"
        elif record_type == "PTR":
            icon_name = "system-search-symbolic"

        row = self._create_base_action_row(name, record_type, base_subtitle, icon_name)
        target_value = str(record_data.get('target', 'N/A'))
        summary_text = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {target_value}"
        self._add_standard_suffix_box_to_row(row, target_value, "Copy Target", summary_text)
        return row

    def _build_generic_data_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """
        Build a UI row for generic DNS records that have a 'data' field.

        :param record_data: Parsed record data.
        :type record_data: Dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string.
        :type record_type: str
        :return: An Adw.ActionRow for the record.
        :rtype: Adw.ActionRow
        """
        row = self._create_base_action_row(name, record_type, base_subtitle, "help-question-symbolic")
        data_value = str(record_data.get('data', 'N/A'))
        summary_text = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {data_value}"
        self._add_standard_suffix_box_to_row(row, data_value, "Copy Data", summary_text)
        return row

    def _build_mx_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow: # record_type is "MX"
        """
        Build a UI row for an MX DNS record.

        :param record_data: Parsed record data.
        :type record_data: Dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("MX").
        :type record_type: str
        :return: An Adw.ExpanderRow for the record.
        :rtype: Adw.ExpanderRow
        """
        exchange_value = str(record_data.get('exchange', 'N/A'))
        preference_value = str(record_data.get('preference', 'N/A'))
        summary_mx = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} MX {preference_value} {exchange_value}"

        row = self._create_base_expander_row(name, f"MX Record ({base_subtitle})", "mail-send-receive-symbolic", summary_mx)

        # MX detail row is specific
        exchange_value_label = Gtk.Label(label=exchange_value, halign=Gtk.Align.FILL, hexpand=True, selectable=True, wrap=False, lines=1, ellipsize=Pango.EllipsizeMode.END)
        copy_button_exchange = self._create_copy_button(exchange_value, f"Copy Exchange: {exchange_value}", row)
        # mx_detail_row_title_box removed

        mx_detail_row = Adw.ActionRow(subtitle=f"Preference: {preference_value}") # type: ignore
        mx_detail_row.add_prefix(exchange_value_label) # type: ignore
        mx_detail_row.add_prefix(copy_button_exchange) # type: ignore
        mx_detail_row.set_selectable(True) # Allow selecting to copy preference if needed, though not directly copyable via button
        row.add_row(mx_detail_row) # type: ignore
        row.set_expanded(True)
        return row

    def _build_txt_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow: # record_type is "TXT"
        """
        Build a UI row for a TXT DNS record.

        :param record_data: Parsed record data.
        :type record_data: Dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("TXT").
        :type record_type: str
        :return: An Adw.ExpanderRow for the record.
        :rtype: Adw.ExpanderRow
        """
        texts = record_data.get('texts', [])
        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} TXT {texts_str_summary}"

        row = self._create_base_expander_row(name, f"TXT Records ({base_subtitle})", "document-properties-symbolic", summary_txt)

        if not texts:
            no_text_row = Adw.ActionRow(title="No text data.", selectable=False) #type: ignore
            row.add_row(no_text_row) # type: ignore
        else:
            for text_string in texts:
                self._add_expander_detail_row(row, None, text_string, "Copy Text Segment", is_value_primary_content=True)
        row.set_expanded(bool(texts))
        return row

    def _build_soa_record_row(self, record_data: Dict[str, Any], name: str, base_subtitle: str, record_type: str) -> Adw.ExpanderRow: # record_type is "SOA"
        """
        Build a UI row for an SOA DNS record.

        :param record_data: Parsed record data.
        :type record_data: Dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("SOA").
        :type record_type: str
        :return: An Adw.ExpanderRow for the record.
        :rtype: Adw.ExpanderRow
        """
        mname_val = str(record_data.get('mname', 'N/A'))
        rname_val = str(record_data.get('rname', 'N/A'))
        serial_val = str(record_data.get('serial', 'N/A'))
        refresh_val = str(record_data.get('refresh', 'N/A'))
        retry_val = str(record_data.get('retry', 'N/A'))
        expire_val = str(record_data.get('expire', 'N/A'))
        minimum_val = str(record_data.get('minimum', 'N/A'))

        summary_soa = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} SOA {mname_val} {rname_val} {serial_val} {refresh_val} {retry_val} {expire_val} {minimum_val}"
        row = self._create_base_expander_row(name, f"SOA Record ({base_subtitle})", "document-settings-symbolic", summary_soa)

        soa_fields = [
            ("MNAME", mname_val), ("RNAME", rname_val), ("Serial", serial_val),
            ("Refresh", refresh_val), ("Retry", retry_val), ("Expire", expire_val),
            ("Minimum TTL", minimum_val)
        ]
        for field_name_str, field_value in soa_fields:
            self._add_expander_detail_row(row, field_name_str, field_value, f"Copy {field_name_str}")
        row.set_expanded(True)
        return row

    def _create_record_row(self, record_data: Dict[str, Any]) -> Optional[Gtk.Widget]:
        """
        Create a UI row for a single DNS record dictionary.

        Delegates to specific `_build_*_record_row` methods based on record type.

        :param record_data: A dictionary containing the parsed data for one DNS record.
        :type record_data: Dict[str, Any]
        :return: A :class:`Gtk.Widget` (typically an :class:`Adw.ActionRow` or
                 :class:`Adw.ExpanderRow`) representing the record, or ``None`` if
                 the record type is unknown or cannot be displayed.
        :rtype: Optional[Gtk.Widget]
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
            return self._build_generic_data_record_row(record_data, name, base_subtitle, record_type) # Renamed
        else:
            logger.warning("Could not create row for unknown record_data type or missing data field: %s (Type: %s)", record_data, record_type)
            return None

    def trigger_lookup(self) -> None:
        """
        Programmatically trigger the DNS 'Lookup' action.

        This is typically called via a keyboard shortcut. It simulates a click
        on the 'Lookup' button if it's available and sensitive.
        """
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive(): # type: ignore
            self.dns_apply_button.clicked() # type: ignore
        else:
            logger.warning("DNS lookup button not available or not sensitive, cannot trigger lookup.")
