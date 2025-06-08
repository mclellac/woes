"""Defines the DNS lookup page for the Woes application."""
import logging
import re
from datetime import datetime

import dns.resolver
import dns.reversename
import gi
from gi.repository import Adw, Gio, Gtk, GtkSource, Pango, GLib
from typing import Tuple, Sequence, Any # Removed Optional as it's not explicitly used

from .constants import APP_ID, RESOURCE_PREFIX
from .style_utils import apply_source_style_scheme
from .utils import create_source_view

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Adw.PreferencesPage):
    """Activity page for performing DNS lookups and displaying results."""

    __gtype_name__ = "DNSPage"

    dns_ip_entryrow = Gtk.Template.Child("dns_ip_entryrow")
    dns_apply_button = Gtk.Template.Child("dns_apply_button")
    dns_record_type_dropdown = Gtk.Template.Child("dns_record_type_dropdown")
    dns_results_scrolled_window = Gtk.Template.Child("dns_results_scrolled_window")
    error_banner = Gtk.Template.Child("error_banner")

    def __init__(self, **kwargs):
        """Initialize the DNSPage."""
        super().__init__(**kwargs)
        self.header_tag = None
        self._connect_signals()
        self.source_view, self.source_buffer = create_source_view() # Rely on default "txt"
        self.dns_results_scrolled_window.set_child(self.source_view)
        self.settings = Gio.Settings.new(APP_ID)
        self._apply_source_view_style()
        self.settings.connect(
            "changed::source-style-scheme", self._on_source_style_scheme_setting_changed
        )
        if self.dns_apply_button:
            self.dns_apply_button.set_use_underline(True)

        try:
            self.bold_tag = self.source_buffer.create_tag("bold", weight=Pango.Weight.BOLD)
            self.domain_color_tag = self.source_buffer.create_tag(
                "domain_color", foreground="#3465a4"
            )
            self.record_type_color_tag = self.source_buffer.create_tag(
                "record_type_color", foreground="#cc0000"
            )
            self.value_color_tag = self.source_buffer.create_tag(
                "value_color", foreground="#73d216"
            )
            self.ttl_color_tag = self.source_buffer.create_tag("ttl_color", foreground="#fce94f")
            self.class_color_tag = self.source_buffer.create_tag(
                "class_color", foreground="#75507b"
            )
        except GLib.Error as e:
            logging.error("Error creating Pango text tags: %s", e)
        except Exception as e:  # pylint: disable=broad-except
            logging.error("Unexpected error creating text tags (%s): %s", type(e).__name__, e)

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.dns_ip_entryrow.connect("entry-activated", self._on_entry_activated)
        self.dns_apply_button.connect("clicked", self._on_entry_activated)
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed)

    def _on_source_style_scheme_setting_changed(self, _settings: Gio.Settings, key: str):
        """Handle changes to the 'source-style-scheme' GSettings key.

        Args:
            _settings: The Gio.Settings object that changed.
            key: The name of the GSettings key that changed.

        """
        logging.debug("DNSPage: '%s' setting changed, applying new source view style.", key)
        self._apply_source_view_style()

    def _apply_source_view_style(self):
        """Apply the current style scheme (from GSettings) to the GtkSourceView."""
        source_style_scheme = self.settings.get_string("source-style-scheme")
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            self.source_buffer,
            source_style_scheme,
        )
        self.source_view.set_editable(False)

    def _is_ip_address(self, input_str: str) -> bool:
        """Check if the input string is a valid IP address (IPv4 or IPv6).

        Args:
            input_str: The string to check.

        Returns:
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
            input_str: The string to validate.

        Returns:
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
            _widget: The widget that emitted the signal.

        """
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.DropDown, _param_spec: GLib.ParamSpec):
        """Handle the record type dropdown change event.

        Args:
            _dropdown: The Gtk.DropDown widget whose selection changed.
            _param_spec: The GLib.ParamSpec of the property that changed.

        """
        self._perform_lookup()

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        """Handle the error banner dismiss button click by clearing the error display."""
        self._clear_error() # Corrected from self._on_error_banner_dismiss()

    def _prepare_resolver(self) -> dns.resolver.Resolver:
        """Prepare a DNS resolver, incorporating custom server settings if configured.

        Returns:
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
    ) -> Tuple[str, str]: # Changed to typing.Tuple
        """Fetch DNS records for the given input.

        Args:
            user_input: The domain name or IP address to query.
            requested_record_type: The DNS record type as a string (e.g., "A", "MX").
            resolver: The DNS resolver instance to use for the query.

        Returns:
            A tuple containing the result string and the actual record type used for the query.

        Raises:
            dns.resolver.NXDOMAIN: If the domain does not exist.
            dns.resolver.NoAnswer: If the query succeeded but no records of the requested type exist.
            dns.resolver.Timeout: If the query timed out.

        """
        actual_record_type = requested_record_type
        if self._is_ip_address(user_input):
            actual_record_type = "PTR"  # Override to PTR for IP addresses

        # _lookup_record will call resolver.resolve() which can raise the exceptions
        result_str = self._lookup_record(user_input, actual_record_type, resolver)
        return result_str, actual_record_type

    def _perform_lookup(self):  # noqa: C901 # Function complexity is high, consider refactoring.
        """Perform the DNS lookup based on user input and selected record type.

        Handles input validation, prepares the resolver, fetches records,
        and updates the UI with results or error messages.
        """
        user_input = self.dns_ip_entryrow.get_text().strip()
        if not user_input:
            self._show_error("Input cannot be empty.")
            return

        self._clear_error()

        if not self._is_valid_ip_or_domain(user_input):
            self._show_error("Invalid IP address or domain name.")
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

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown.

        Returns:
            The selected record type as a string (e.g., "A", "MX").

        """
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        return model.get_string(selected_index)

    def _show_error(self, message: str):
        """Display an error message in the UI banner and style the entry row.

        Args:
            message: The error message to display.

        """
        self.dns_ip_entryrow.add_css_class("error")
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)

    def _clear_error(self):
        """Clear any existing error messages from the UI banner and entry row styling."""
        self.dns_ip_entryrow.remove_css_class("error")
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")

    @staticmethod
    def _lookup_record(domain_or_ip: str, record_type: str, resolver: dns.resolver.Resolver) -> str:
        """Look up DNS records using the provided resolver.

        Args:
            domain_or_ip: The domain name or IP address to query.
            record_type: The DNS record type (e.g., "A", "MX", "PTR").
            resolver: The DNS resolver instance.

        Returns:
            A string containing the formatted DNS records.

        Raises:
            dns.resolver.NXDOMAIN: If the domain does not exist.
            dns.resolver.NoAnswer: If no records of the requested type exist.
            dns.resolver.Timeout: If the query times out.
            dns.exception.DNSException: For other DNS-related errors.

        """
        # Let DNSExceptions propagate
        if record_type == "PTR":
            rev_name = dns.reversename.from_address(domain_or_ip)
            result = resolver.resolve(rev_name, record_type)
        else:
            result = resolver.resolve(domain_or_ip, record_type)

        return "\n".join([f"{domain_or_ip}. IN {record_type} {r.to_text()}" for r in result])

    def _display_result(self, result: str, domain_or_ip: str, record_type: str, dns_servers: Sequence[Any]): # Changed to Sequence[Any]
        """Display the DNS lookup results in the GtkSourceView.

        Formats the output with headers, timestamps, and the DNS server used.

        Args:
            result: The raw DNS result string.
            domain_or_ip: The domain or IP that was queried.
            record_type: The record type used for the query.
            dns_servers: A list of DNS servers that were used.

        """
        self.source_buffer.set_text("")

        self.header_tag = self.source_buffer.get_tag_table().lookup("header")
        if not self.header_tag:
            try:
                self.header_tag = self.source_buffer.create_tag(
                    "header", weight=Pango.Weight.BOLD, size_points=12
                )
            except GLib.Error as e:
                logging.error("Error creating Pango header tag: %s", e)
            except Exception as e:  # pylint: disable=broad-except
                logging.error("Unexpected error creating header tag (%s): %s", type(e).__name__, e)

        # Ensure all elements in dns_servers are strings before joining
        dns_server_info = f"DNS server used: {', '.join(map(str, dns_servers))}\n"

        header = (
            f"DNS Lookup Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"{dns_server_info}\n"
        )
        self.source_buffer.insert_with_tags(
            self.source_buffer.get_end_iter(), header, self.header_tag
        )

        self.source_buffer.insert_with_tags(
            self.source_buffer.get_end_iter(), domain_or_ip, self.bold_tag
        )
        self.source_buffer.insert(self.source_buffer.get_end_iter(), "\t")
        self.source_buffer.insert_with_tags(
            self.source_buffer.get_end_iter(), record_type, self.bold_tag
        )
        self.source_buffer.insert(self.source_buffer.get_end_iter(), "\n\n")

        self._format_result_in_buffer(result)

    def _format_result_in_buffer(self, result: str):
        """Format and insert the raw DNS result string into the GtkSourceBuffer.

        Applies Pango tags for syntax highlighting of different parts of the DNS records.

        Args:
            result: The raw DNS result string, with each record typically on a new line.

        """
        lines = result.splitlines()

        for line in lines:
            match = re.match(r"^(.*?)\s+(IN)\s+([A-Z]+)\s+(.+)$", line)
            if match:
                domain = match.group(1)
                record_class = match.group(2)
                record_type = match.group(3)
                value = match.group(4)

                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    domain + "\t",
                    self.domain_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    record_class + "\t",
                    self.class_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    record_type + "\t",
                    self.record_type_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    value + "\n",
                    self.value_color_tag,
                )
            else:
                self.source_buffer.insert(self.source_buffer.get_end_iter(), line + "\n")
