"""Provides the user interface for DNS lookups within the Woes application.

This module defines the :class:`DNSPage` class, which is an Adw.PreferencesPage
subclass. It handles user input for domain names/IPs and record types,
initiates DNS queries using :class:`~.dns_client.DnsResolverClient`,
and displays the results in a structured format.
"""
from .dns_client import (
    DnsResolverClient,
    DnsClientError,
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

# dns.resolver, etc., are now primarily in dns_client.py

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Adw.PreferencesPage):
    """Manages the DNS lookup page and its associated UI elements and logic.

    This class provides the user interface for performing DNS queries. Users can
    input a domain name or IP address, select a DNS record type from a dropdown,
    and initiate a lookup. Results are displayed in a dynamically generated list
    of expandable rows, each representing a DNS record. It utilizes the
    :class:`~.dns_client.DnsResolverClient` for performing the actual DNS lookups
    and can use a custom DNS server configured in the application's settings.
    Error handling and UI updates for loading states are also managed here.
    """

    __gtype_name__ = "DNSPage"

    domain_entry = Gtk.Template.Child() # type: ignore
    dns_apply_button = Gtk.Template.Child() # type: ignore
    dns_record_type_dropdown = Gtk.Template.Child() # type: ignore
    dns_results_box_container = Gtk.Template.Child() # type: ignore
    dns_clear_results_button = Gtk.Template.Child() # type: ignore
    dns_copy_all_results_button = Gtk.Template.Child() # type: ignore
    dns_status_row = Gtk.Template.Child() # type: ignore
    dns_status_spinner = Gtk.Template.Child() # type: ignore

    def __init__(self, **kwargs: Any):
        """Initialize the DNSPage.

        Sets up UI elements from the Gtk.Template, connects signal handlers
        for user interactions, and initializes access to application settings.

        :param kwargs: Keyword arguments passed to the :class:`Adw.PreferencesPage`
                       constructor.
        :type kwargs: Any
        """
        super().__init__(**kwargs)
        logger.debug("DNSPage initialized.")
        self._connect_signals()
        self.settings = Gio.Settings.new(APP_ID)

    def _connect_signals(self) -> None:
        """Connects Gtk signals for various UI elements to their respective handlers."""
        self.domain_entry.connect(
            "activate", self._on_entry_activated)
        self.dns_apply_button.connect(
            "clicked", self._on_entry_activated)
        self.dns_record_type_dropdown.connect(
            "notify::selected", self._on_record_type_changed)
        if self.dns_clear_results_button:
            self.dns_clear_results_button.connect(
                "clicked", self._on_clear_results_clicked)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.connect(
                "clicked", self._on_copy_all_results_clicked)

    def _on_clear_results_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Clear Results' button.

        Removes all dynamically added rows from the results container and
        disables the 'Clear Results' and 'Copy All Results' buttons.

        :param _button: The Gtk.Button that was clicked.
        :type _button: Gtk.Button
        """
        logger.info("Clearing DNS results.")
        # Remove all children (result rows) from the container.
        while (child := self.dns_results_box_container.get_first_child()):
            self.dns_results_box_container.remove(child)

        # Disable buttons as there are no results to clear or copy.
        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(False)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(False)

    def _on_copy_all_results_clicked(self, _button: Gtk.Button) -> None:
        """Handles the 'clicked' signal for the 'Copy All Results' button.

        Aggregates the text content (title and subtitle) from all result rows
        and copies it to the clipboard.

        :param _button: The Gtk.Button that was clicked.
        :type _button: Gtk.Button
        """
        logger.info("Copying all DNS results to clipboard.")
        all_results_text: List[str] = []
        child = self.dns_results_box_container.get_first_child()
        while child:
            text_parts_for_child: List[str] = []
            # Extract text from Adw.ActionRow or Adw.ExpanderRow
            if isinstance(child, (Adw.ActionRow, Adw.ExpanderRow)):
                title = child.get_title()
                subtitle = child.get_subtitle()
                if title:
                    text_parts_for_child.append(title)
                if subtitle: # Subtitles can be None or empty
                    text_parts_for_child.append(subtitle)

            if text_parts_for_child:
                all_results_text.append(" - ".join(text_parts_for_child))
            child = child.get_next_sibling()

        if not all_results_text:
            show_global_toast(self, "No results to copy.")
            return

        final_text_to_copy = "\n".join(all_results_text)
        DNSPage._copy_to_clipboard(final_text_to_copy, self)
        show_global_toast(self, "All results copied to clipboard.")

    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        """Copies the provided text to the system clipboard.

        :param text: The text to be copied.
        :type text: str
        :param widget: A Gtk.Widget instance used to access the clipboard.
        :type widget: Gtk.Widget
        """
        try:
            clipboard = widget.get_clipboard()
            if clipboard:
                clipboard.set(text)
                logger.info("Copied to clipboard: %s", text[:100] + "..." if len(text) > 100 else text)
            else:
                logger.warning(
                    "Could not get clipboard from widget: %s", widget)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error copying to clipboard:")

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """Validates if the input string is a syntactically valid IP address or domain name.

        :param input_str: The string to validate.
        :type input_str: str
        :return: True if valid, False otherwise.
        :rtype: bool
        """
        if not input_str:
            return False
        # Uses helper functions for actual validation logic.
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget) -> None:
        """Handles activation of the domain entry (e.g., Enter key press) or
        click of the 'Lookup' button. Triggers the DNS lookup process.

        :param _widget: The Gtk.Widget that triggered the activation.
        :type _widget: Gtk.Widget
        """
        logger.debug(f"_on_entry_activated called by widget: {_widget}")
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GObject.ParamSpec) -> None:
        """Handles changes in the selected DNS record type from the dropdown.
        Triggers a new DNS lookup with the new record type.

        :param _dropdown: The Gtk.DropDown whose selection changed.
        :type _dropdown: Gtk.DropDown
        :param _param_spec: The GObject.ParamSpec of the property that changed (unused).
        :type _param_spec: GObject.ParamSpec
        """
        self._perform_lookup()

    def _create_copy_button(
            self, text_to_copy: str, tooltip_text: str,
            widget_for_clipboard: Gtk.Widget) -> Gtk.Button:
        """Creates a Gtk.Button configured for copying the provided text.

        :param text_to_copy: The text that this button will copy.
        :type text_to_copy: str
        :param tooltip_text: The tooltip to display for this button.
        :type tooltip_text: str
        :param widget_for_clipboard: The widget used to access the clipboard.
        :type widget_for_clipboard: Gtk.Widget
        :return: A configured Gtk.Button for copying text.
        :rtype: Gtk.Button
        """
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
        """Creates a base Adw.ActionRow with a title, subtitle, and an optional icon.

        This is a helper for constructing consistent looking rows for DNS records.

        :param name: The primary name/title for the row (e.g., domain name).
        :type name: str
        :param record_type_label: The label for the DNS record type (e.g., "A", "MX").
        :type record_type_label: str
        :param base_subtitle_text: Base text for the subtitle (e.g., TTL, Class).
        :type base_subtitle_text: str
        :param icon_name: Optional Gtk icon name to display as a prefix.
        :type icon_name: Optional[str]
        :return: A configured Adw.ActionRow.
        :rtype: Adw.ActionRow
        """
        row = Adw.ActionRow(
            title=name,
            subtitle=f"Type: {record_type_label}, {base_subtitle_text}")
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name))
        row.set_selectable(False) # Row itself is not selectable, actions are on buttons.
        return row

    def _add_standard_suffix_box_to_row(
        self,
        row: Adw.ActionRow,
        main_value_text: str,
        main_value_tooltip_prefix: str,
        full_summary_text: str
    ) -> None:
        """Adds a standard set of suffix widgets (value label, copy buttons) to an Adw.ActionRow.

        This typically includes the primary data of the DNS record and buttons to copy
        that data or the full record summary.

        :param row: The Adw.ActionRow to add suffixes to.
        :type row: Adw.ActionRow
        :param main_value_text: The main data value of the record (e.g., IP address, target).
        :type main_value_text: str
        :param main_value_tooltip_prefix: Tooltip prefix for copying the main value.
        :type main_value_tooltip_prefix: str
        :param full_summary_text: The full text summary of the record for copying.
        :type full_summary_text: str
        """
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
        """Creates a base Adw.ExpanderRow with title, subtitle, icon, and a copy button for the full summary.

        :param name: The title for the expander row.
        :type name: str
        :param subtitle_text: The subtitle for the expander row.
        :type subtitle_text: str
        :param icon_name: Optional Gtk icon name for the prefix.
        :type icon_name: Optional[str]
        :param full_summary_text: The full text summary to be copied by the suffix button.
        :type full_summary_text: str
        :return: A configured Adw.ExpanderRow.
        :rtype: Adw.ExpanderRow
        """
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
    ) -> None:
        """Adds a detail row (as an Adw.ActionRow) inside an Adw.ExpanderRow.

        Each detail row typically displays a piece of data associated with the
        expanded record (e.g., a TXT string segment, an SOA record field).

        :param expander_row: The parent Adw.ExpanderRow.
        :type expander_row: Adw.ExpanderRow
        :param title: Optional title for this detail row. If None, value is primary.
        :type title: Optional[str]
        :param value_text: The main text value for this detail row.
        :type value_text: str
        :param copy_tooltip_prefix: Tooltip prefix for the copy button.
        :type copy_tooltip_prefix: str
        :param is_value_primary_content: If True, value and copy button are added as prefixes.
                                         Otherwise, as suffixes (for title/value pairs).
        :type is_value_primary_content: bool
        """
        detail_row = Adw.ActionRow(title=title if title else None)
        value_label = Gtk.Label(
            label=value_text, halign=Gtk.Align.FILL, hexpand=True,
            selectable=True, wrap=False, lines=1,
            ellipsize=Pango.EllipsizeMode.END)
        copy_button = self._create_copy_button(
            value_text, f"{copy_tooltip_prefix}: {value_text}", expander_row)

        if is_value_primary_content: # Typically for lists like TXT records
            detail_row.add_prefix(value_label)
            detail_row.add_prefix(copy_button)
        else: # For title/value pairs like SOA fields
            detail_row.add_suffix(value_label)
            detail_row.add_suffix(copy_button)
        detail_row.set_selectable(False)
        expander_row.add_row(detail_row)

    def _set_loading_state(self, active: bool,
                           message: Optional[str] = None) -> None:
        """Updates the UI to reflect a loading or idle state.

        Manages the visibility and animation of a spinner, updates a status message,
        and adjusts the sensitivity of input controls.

        :param active: True if loading state should be activated, False otherwise.
        :type active: bool
        :param message: Optional message to display in the status row.
                        If None, a default "Looking up..." or "Idle" is used.
        :type message: Optional[str]
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
            elif active and current_subtitle != "Looking up...": # Avoid redundant sets
                self.dns_status_row.set_subtitle("Looking up...")
            elif not active and not message: # Default to Idle if no specific message
                self.dns_status_row.set_subtitle("Idle")

        # Disable input fields during active lookup
        if self.domain_entry:
            self.domain_entry.set_sensitive(not active)
        if self.dns_apply_button:
            self.dns_apply_button.set_sensitive(not active)
        if self.dns_record_type_dropdown:
            self.dns_record_type_dropdown.set_sensitive(not active)

    def _validate_dns_input(self, user_input: str) -> bool:
        """Validates the user-provided DNS input (domain or IP).

        Shows a global toast/error message if validation fails.

        :param user_input: The string input by the user.
        :type user_input: str
        :return: True if the input is valid, False otherwise.
        :rtype: bool
        """
        if not user_input:
            show_global_toast(self, "Input cannot be empty.")
            # Fallback error if toast is not available (e.g. no main window context)
            if not (self.get_native() and hasattr(self.get_native(), 'show_toast')):
                show_global_error(self, "Input cannot be empty.")
            return False

        if not self._is_valid_ip_or_domain(user_input):
            show_global_toast(self, "Invalid IP address or domain name.")
            if not (self.get_native() and hasattr(self.get_native(), 'show_toast')):
                show_global_error(self, "Invalid IP address or domain name.")
            return False
        return True

    def _update_ptr_dropdown(self, user_input: str,
                             requested_record_type: str) -> str:
        """Adjusts the record type dropdown to "PTR" if an IP address was entered
        and "PTR" was the requested type (or implicitly desired).

        This ensures the UI reflects the actual lookup type if an IP-to-PTR
        conversion occurs.

        :param user_input: The user's input string (domain or IP).
        :type user_input: str
        :param requested_record_type: The initially requested record type.
        :type requested_record_type: str
        :return: The record type that is effectively used or set in the UI.
        :rtype: str
        """
        actual_record_type_used = requested_record_type
        # If input is an IP and user selected PTR, ensure dropdown shows PTR.
        if is_valid_ip(user_input) and requested_record_type.upper() == "PTR":
            model = self.dns_record_type_dropdown.get_model()
            if model: # Gtk.StringList
                for i in range(model.get_n_items()): # type: ignore
                    if model.get_string(i) == "PTR": # type: ignore
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
        """Processes and displays successful DNS lookup results.

        Updates the record type dropdown if an IP to PTR lookup occurred,
        displays the results, and sets the status message.

        :param result_data: The list of DNS records from the lookup.
        :type result_data: List[Dict[str, Any]]
        :param user_input: The original user input (domain or IP).
        :type user_input: str
        :param requested_record_type: The record type requested by the user.
        :type requested_record_type: str
        :param dns_client: The DnsResolverClient instance used for the lookup.
        :type dns_client: DnsResolverClient
        """
        actual_record_type_displayed = requested_record_type
        # If an IP was entered and PTR was requested, ensure dropdown reflects PTR.
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
        dns_client: DnsResolverClient # Included for potential future use (e.g. logging server info)
    ) -> None:
        """Handles exceptions raised during DNS lookups and updates the UI.

        Displays appropriate error messages to the user via global error/toast
        and updates the status row.

        :param error: The exception that occurred.
        :type error: Exception
        :param user_input: The original user input.
        :type user_input: str
        :param requested_record_type: The record type requested.
        :type requested_record_type: str
        :param dns_client: The DnsResolverClient instance used.
        :type dns_client: DnsResolverClient
        """
        error_message = str(error)
        status_subtitle = f"Error: {error_message.splitlines()[0]}" # Default short status

        if isinstance(error, DnsNxDomainError):
            show_global_error(self, error_message)
            status_subtitle = f"NXDOMAIN: Domain '{user_input}' not found."
        elif isinstance(error, DnsNoAnswerError):
            logger.info("DNSPage: %s", error_message) # Log NoAnswer as info, not error
            nameservers_used = dns_client.resolver.nameservers
            # Display an empty result set for NoAnswer, as it's not a "domain not found" error
            self._display_result(
                [], user_input, requested_record_type, nameservers_used)
            status_subtitle = (f"No {requested_record_type} records found for "
                               f"{user_input} (No Answer).")
        elif isinstance(error, DnsResolutionTimeoutError):
            show_global_error(self, error_message)
            status_subtitle = f"Timeout: Could not resolve {user_input}."
        elif isinstance(error, DnsGenericError):
            logger.warning( # Log generic DNS errors as warnings
                "DNSPage: DNS lookup failed for %s, type %s "
                "(DnsGenericError): %s", user_input, requested_record_type, error)
            show_global_error(self, error_message)
            status_subtitle = f"DNS Error: {error_message.splitlines()[0]}"
        elif isinstance(error, DnsClientError): # Other client-defined errors
            logger.error( # Log other DnsClientErrors as errors
                "DNSPage: Unexpected DnsClientError for %s, type %s: %s",
                user_input, requested_record_type, error, exc_info=True)
            show_global_error(self, f"DNS Client Error: {error_message}")
            status_subtitle = f"Client Error: {error_message.splitlines()[0]}"
        else: # Unexpected Python exceptions
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
        """Orchestrates the DNS lookup process.

        This method is typically called when the user activates the domain entry
        or changes the record type. It validates the input, retrieves necessary
        settings (like a custom DNS server), instantiates :class:`DnsResolverClient`,
        calls its `resolve` method, and then delegates to helper methods to
        display results or handle errors. It also manages the UI loading state.
        The C901 noqa is due to the branching logic for error handling and UI updates.
        """
        self._set_loading_state(True, "Looking up...")
        user_input = self.domain_entry.get_text().strip()
        requested_record_type = self._get_selected_record_type()
        logger.debug(
            f"DNSPage: Performing DNS lookup for: {user_input}, type: {requested_record_type}")

        if not self._validate_dns_input(user_input):
            self._set_loading_state(False, "Idle - Invalid input.")
            return

        self._clear_error() # Clear previous errors from banner/entry

        custom_dns_server = self.settings.get_string("custom-dns-server")
        dns_client = DnsResolverClient(
            custom_dns_server=custom_dns_server if custom_dns_server else None)

        try:
            result_data = dns_client.resolve(user_input, requested_record_type)
            self._handle_dns_lookup_success(
                result_data, user_input, requested_record_type, dns_client)
        except Exception as e: # Catch all exceptions from resolve or success handler
            self._handle_dns_lookup_exception(
                e, user_input, requested_record_type, dns_client)
        finally:
            self._set_loading_state(False) # Ensure loading state is reset

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown.

        :return: The selected record type as a string (e.g., "A", "MX").
        :rtype: str
        """
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        if model and selected_index < model.get_n_items(): # type: ignore
            return model.get_string(selected_index) # type: ignore
        logger.warning("Could not determine selected record type, defaulting to A.")
        return "A" # Default fallback

    def _clear_error(self) -> None:
        """Clears any existing error messages from the UI.

        This includes hiding the main window's error banner (if available)
        and removing the 'error' CSS class from the domain entry row.
        """
        if self.domain_entry:
            self.domain_entry.remove_css_class("error")
        main_window = self.get_native() # Adw.PreferencesPage.get_native()
        if main_window and hasattr(main_window, 'hide_error'):
            main_window.hide_error() # type: ignore
        else:
            logger.debug( # Changed to debug as this can be normal if not in a window
                "DNSPage: Could not find main_window or hide_error method to clear error.")

    def _display_result(
        self,
        result_records: List[Dict[str, Any]],
        domain_or_ip: str,
        record_type: str,
        dns_servers: Sequence[Any],
    ) -> None:
        """Displays the DNS lookup results or a 'no records found' message in the UI.

        Clears previous results and populates the results container with new
        rows representing the query info, servers used, and each DNS record.
        Manages sensitivity of clear/copy buttons.

        :param result_records: A list of dictionaries, where each dictionary
                               represents a parsed DNS record.
        :type result_records: List[Dict[str, Any]]
        :param domain_or_ip: The domain name or IP address that was queried.
        :type domain_or_ip: str
        :param record_type: The DNS record type that was queried.
        :type record_type: str
        :param dns_servers: A sequence of DNS server IP addresses used for the query,
                            or an empty sequence if system defaults were used.
        :type dns_servers: Sequence[Any]
        """
        logger.debug(
            "Displaying %d results for %s (type %s) using servers %s.",
            len(result_records), domain_or_ip, record_type, dns_servers)

        # Clear previous results
        while (child := self.dns_results_box_container.get_first_child()):
            self.dns_results_box_container.remove(child)

        # Display general query information
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

        # Separator before results or "no records" message
        self.dns_results_box_container.append(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        if not result_records:
            no_records_message = (f"No {record_type} records found for "
                                  f"{domain_or_ip}.")
            # More specific messages for PTR queries
            if record_type == "PTR" and is_valid_ip(domain_or_ip):
                no_records_message = (f"No PTR records found for IP address "
                                      f"{domain_or_ip}.")
            elif record_type == "PTR": # PTR for a non-IP name
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

        # Populate with new result rows
        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row:
                self.dns_results_box_container.append(row)

        # Enable clear/copy buttons if there are results
        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(True)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(True)

    def _build_address_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ActionRow:
        """Builds a UI row for an A or AAAA DNS record.

        :param record_data: Dictionary containing data for the record.
        :type record_data: Dict[str, Any]
        :param name: The record name (qname).
        :type name: str
        :param base_subtitle: Pre-formatted base subtitle (TTL, Class).
        :type base_subtitle: str
        :param record_type: The record type ("A" or "AAAA").
        :type record_type: str
        :return: An Adw.ActionRow representing the record.
        :rtype: Adw.ActionRow
        """
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
        """Builds a UI row for CNAME, NS, or PTR DNS records.

        :param record_data: Dictionary containing data for the record.
        :type record_data: Dict[str, Any]
        :param name: The record name (qname).
        :type name: str
        :param base_subtitle: Pre-formatted base subtitle (TTL, Class).
        :type base_subtitle: str
        :param record_type: The record type ("CNAME", "NS", "PTR").
        :type record_type: str
        :return: An Adw.ActionRow representing the record.
        :rtype: Adw.ActionRow
        """
        icon_name = "emblem-shared-symbolic"  # Default for CNAME
        if record_type == "NS":
            icon_name = "network-server-symbolic"
        elif record_type == "PTR":
            icon_name = "system-search-symbolic" # More indicative of reverse lookup

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
        """Builds a UI row for generic DNS records that have a 'data' field.

        Used as a fallback for record types not explicitly handled.

        :param record_data: Dictionary containing data for the record.
        :type record_data: Dict[str, Any]
        :param name: The record name (qname).
        :type name: str
        :param base_subtitle: Pre-formatted base subtitle (TTL, Class).
        :type base_subtitle: str
        :param record_type: The record type string.
        :type record_type: str
        :return: An Adw.ActionRow representing the record.
        :rtype: Adw.ActionRow
        """
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
        """Builds an Adw.ExpanderRow for an MX DNS record.

        Displays preference and exchange, with individual copy buttons.

        :param record_data: Dictionary containing data for the MX record.
        :type record_data: Dict[str, Any]
        :param name: The record name (qname).
        :type name: str
        :param base_subtitle: Pre-formatted base subtitle (TTL, Class).
        :type base_subtitle: str
        :param record_type: The record type ("MX").
        :type record_type: str
        :return: An Adw.ExpanderRow representing the MX record.
        :rtype: Adw.ExpanderRow
        """
        exchange_value = str(record_data.get('exchange', 'N/A'))
        preference_value = str(record_data.get('preference', 'N/A'))
        summary_mx = (f"{name} {record_data.get('ttl', '')} "
                      f"{record_data.get('class', '')} {record_type} {preference_value} "
                      f"{exchange_value}")

        # Main title for the expander includes the exchange for quick identification
        row_title = f"{name} (MX Preference: {preference_value})"
        row_subtitle = f"Exchange: {exchange_value} ({base_subtitle})"

        row = self._create_base_expander_row(
            row_title, row_subtitle,
            "mail-send-receive-symbolic", summary_mx)

        # Detail row for Preference
        self._add_expander_detail_row(
            row, "Preference", preference_value, "Copy Preference")
        # Detail row for Exchange
        self._add_expander_detail_row(
            row, "Exchange", exchange_value, "Copy Exchange")

        row.set_expanded(True) # MX records are usually expanded by default
        return row

    def _build_txt_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Builds an Adw.ExpanderRow for a TXT DNS record.

        Displays each text segment as a separate detail row.

        :param record_data: Dictionary containing data for the TXT record.
        :type record_data: Dict[str, Any]
        :param name: The record name (qname).
        :type name: str
        :param base_subtitle: Pre-formatted base subtitle (TTL, Class).
        :type base_subtitle: str
        :param record_type: The record type ("TXT").
        :type record_type: str
        :return: An Adw.ExpanderRow representing the TXT record.
        :rtype: Adw.ExpanderRow
        """
        texts = record_data.get('texts', [])
        # For summary, join texts; for display, list them.
        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = (f"{name} {record_data.get('ttl', '')} "
                       f"{record_data.get('class', '')} {record_type} "
                       f"{texts_str_summary}")
        # Show number of segments in subtitle for quick info
        num_segments = len(texts)
        row_subtitle = f"{num_segments} text segment(s) ({base_subtitle})"

        row = self._create_base_expander_row(
            name, row_subtitle,
            "document-properties-symbolic", summary_txt)

        if not texts:
            no_text_row = Adw.ActionRow(title="No text data.",
                                        selectable=False)
            row.add_row(no_text_row)
        else:
            for i, text_string in enumerate(texts):
                # No title for TXT segments, just the value.
                self._add_expander_detail_row(
                    row, f"Segment {i+1}", text_string, "Copy Text Segment",
                    is_value_primary_content=False) # Show title "Segment X"
        row.set_expanded(bool(texts)) # Expand if there are texts
        return row

    def _build_soa_record_row(
            self, record_data: Dict[str, Any], name: str,
            base_subtitle: str, record_type: str) -> Adw.ExpanderRow:
        """Builds an Adw.ExpanderRow for an SOA DNS record.

        Displays all SOA fields (MNAME, RNAME, Serial, etc.) as detail rows.

        :param record_data: Dictionary containing data for the SOA record.
        :type record_data: Dict[str, Any]
        :param name: The record name (qname).
        :type name: str
        :param base_subtitle: Pre-formatted base subtitle (TTL, Class).
        :type base_subtitle: str
        :param record_type: The record type ("SOA").
        :type record_type: str
        :return: An Adw.ExpanderRow representing the SOA record.
        :rtype: Adw.ExpanderRow
        """
        mname_val = str(record_data.get('mname', 'N/A'))
        rname_val = str(record_data.get('rname', 'N/A'))
        serial_val = str(record_data.get('serial', 'N/A'))
        refresh_val = str(record_data.get('refresh', 'N/A'))
        retry_val = str(record_data.get('retry', 'N/A'))
        expire_val = str(record_data.get('expire', 'N/A'))
        minimum_val = str(record_data.get('minimum', 'N/A'))

        summary_soa = (f"{name} {record_data.get('ttl', '')} "
                       f"{record_data.get('class', '')} {record_type} {mname_val} "
                       f"{rname_val} {serial_val} {refresh_val} {retry_val} "
                       f"{expire_val} {minimum_val}")
        # Subtitle includes primary master and serial for quick view
        row_subtitle = f"MNAME: {mname_val}, Serial: {serial_val} ({base_subtitle})"
        row = self._create_base_expander_row(
            name, row_subtitle,
            "document-settings-symbolic", summary_soa)

        soa_fields = [
            ("Primary Master (MNAME)", mname_val),
            ("Responsible Mail (RNAME)", rname_val),
            ("Serial Number", serial_val),
            ("Refresh Interval", refresh_val),
            ("Retry Interval", retry_val),
            ("Expire Limit", expire_val),
            ("Minimum TTL", minimum_val)
        ]
        for field_name_str, field_value in soa_fields:
            self._add_expander_detail_row(
                row, field_name_str, field_value, f"Copy {field_name_str}")
        row.set_expanded(True) # SOA records are usually important, expand by default
        return row

    def _create_record_row(self, record_data: Dict[str, Any]) -> Optional[Gtk.Widget]:
        """Creates a Gtk.Widget (usually Adw.ActionRow or Adw.ExpanderRow)
        to display a single DNS record.

        This method acts as a dispatcher, calling specific `_build_*_record_row`
        methods based on the `record_type` found in `record_data`.

        :param record_data: A dictionary containing the parsed data for one DNS record.
        :type record_data: Dict[str, Any]
        :return: A Gtk.Widget to display the record, or None if the record type
                 is unknown or cannot be displayed.
        :rtype: Optional[Gtk.Widget]
        """
        record_type = record_data.get('type', '').upper()
        name = record_data.get('name', 'N/A')
        ttl = record_data.get('ttl', 'N/A') # Provide N/A if missing
        rd_class_str = record_data.get('class', 'N/A') # Provide N/A if missing
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
        elif 'data' in record_data: # Fallback for other types with a 'data' field
            return self._build_generic_data_record_row(record_data, name, base_subtitle, record_type)
        else:
            logger.warning(
                "Could not create row for unknown record_data type or missing data field: %s (Type: %s)",
                record_data, record_type)
            # Create a simple row for unknown types if they have some text representation
            unknown_data_str = record_data.get('rdata_text', str(record_data))
            return Adw.ActionRow(title=name, subtitle=f"Type: {record_type}, Data: {unknown_data_str[:100]}")


    def trigger_lookup(self) -> None:
        """Programmatically triggers the DNS 'Lookup' action.

        Useful for invoking a DNS lookup via a shortcut or another UI event.
        Checks if the lookup button is available and sensitive before clicking it.
        """
        logger.debug("DNS lookup triggered by shortcut or external action.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive():
            self.dns_apply_button.clicked()
        else:
            logger.warning(
                "DNS lookup button not available or not sensitive, cannot trigger lookup.")
