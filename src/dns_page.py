import logging
import re
from datetime import datetime

import dns.resolver
import dns.reversename

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gio, Gtk, GtkSource, Pango, GLib

from .constants import APP_ID, RESOURCE_PREFIX
from .style_utils import apply_source_style_scheme
from .utils import create_source_view

logger = logging.getLogger(__name__)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Gtk.Box):
    __gtype_name__ = "DNSPage"

    dns_ip_entryrow = Gtk.Template.Child("dns_ip_entryrow")
    dns_record_type_dropdown = Gtk.Template.Child("dns_record_type_dropdown")
    dns_results_scrolled_window = Gtk.Template.Child("dns_results_scrolled_window")
    dns_error_banner = Gtk.Template.Child("dns_error_banner")

    def __init__(self, **kwargs):
        logger.debug("DNSPage.__init__: Starting")
        super().__init__(**kwargs)
        self.settings = Gio.Settings.new(APP_ID)
        logger.debug(f"DNSPage.__init__: self.settings initialized: {self.settings}")
        self.header_tag = None
        logger.debug(f"DNSPage.__init__: self.header_tag initialized to {self.header_tag}")
        self._connect_signals()
        logger.debug("DNSPage.__init__: Calling create_source_view()")
        self.source_view, self.source_buffer = create_source_view(language_name=None)
        logger.debug(
            f"DNSPage.__init__: create_source_view() returned source_view: {self.source_view}, "
            f"source_buffer: {self.source_buffer}"
        )
        if self.dns_results_scrolled_window is None:
            logger.critical(
                "DNSPage.__init__: Gtk.Template.Child 'dns_results_scrolled_window' not found. "
                "UI will be broken."
            )
        else:
            self.dns_results_scrolled_window.set_child(self.source_view)
            logger.debug("DNSPage.__init__: dns_results_scrolled_window child set to source_view.")
        self._apply_source_view_style()
        logger.debug("DNSPage.__init__: Before self.settings.connect('changed::source-style-scheme')")
        self.settings.connect(
            "changed::source-style-scheme",
            self._on_source_style_scheme_setting_changed
        )
        logger.debug("DNSPage.__init__: After self.settings.connect('changed::source-style-scheme')")
        logger.debug("DNSPage.__init__: Initializing Pango tags.")
        try:
            self.bold_tag = self.source_buffer.create_tag(
                "bold", weight=Pango.Weight.BOLD
            )
            logger.debug(f"DNSPage.__init__: self.bold_tag created: {self.bold_tag}")
            self.domain_color_tag = self.source_buffer.create_tag(
                "domain_color", foreground="#3465a4"
            )
            logger.debug(f"DNSPage.__init__: self.domain_color_tag created: {self.domain_color_tag}")
            self.record_type_color_tag = self.source_buffer.create_tag(
                "record_type_color", foreground="#cc0000"
            )
            logger.debug(f"DNSPage.__init__: self.record_type_color_tag created: {self.record_type_color_tag}")
            self.value_color_tag = self.source_buffer.create_tag(
                "value_color", foreground="#73d216"
            )
            logger.debug(f"DNSPage.__init__: self.value_color_tag created: {self.value_color_tag}")
            self.class_color_tag = self.source_buffer.create_tag(
                "class_color", foreground="#75507b"
            )
            logger.debug(f"DNSPage.__init__: self.class_color_tag created: {self.class_color_tag}")
        except GLib.Error as e:
            logger.error("DNSPage.__init__: Error creating Pango text tags: %s", e, exc_info=True)
        except Exception:
            logger.exception("DNSPage.__init__: Unexpected error creating text tags.")
        self.dns_error_banner.set_revealed(False)
        logger.debug("DNSPage.__init__: dns_error_banner revealed set to False.")
        logger.debug("DNSPage.__init__: Finished")

    def _connect_signals(self) -> None:
        """Connect signals for UI elements."""
        logger.debug("DNSPage._connect_signals: Connecting signals.")
        self.dns_ip_entryrow.connect("apply", self._on_entry_activated)
        logger.debug("DNSPage._connect_signals: Connected 'apply' for dns_ip_entryrow.")
        self.dns_record_type_dropdown.connect(
            "notify::selected", self._on_record_type_changed
        )
        logger.debug("DNSPage._connect_signals: Connected 'notify::selected' for dns_record_type_dropdown.")
        self.dns_error_banner.connect("button-clicked", self._on_error_banner_dismiss)
        logger.debug("DNSPage._connect_signals: Connected 'button-clicked' for dns_error_banner.")
        logger.debug("DNSPage._connect_signals: Finished connecting signals.")

    def _on_source_style_scheme_setting_changed(self, _settings, key):
        """Handle changes to the source-style-scheme setting."""
        logger.debug(f"DNSPage._on_source_style_scheme_setting_changed: Triggered for key: '{key}'")
        logger.debug("DNSPage._on_source_style_scheme_setting_changed: Before self._apply_source_view_style()")
        self._apply_source_view_style()
        logger.debug("DNSPage._on_source_style_scheme_setting_changed: After self._apply_source_view_style()")
        logger.debug(f"DNSPage._on_source_style_scheme_setting_changed: Finished for key: '{key}'")

    def _apply_source_view_style(self):
        """Apply the style scheme to the GtkSourceView."""
        logger.debug("DNSPage._apply_source_view_style: Starting.")
        source_style_scheme = self.settings.get_string("source-style-scheme")
        logger.debug(f"DNSPage._apply_source_view_style: Retrieved source-style-scheme: '{source_style_scheme}'")
        logger.debug("DNSPage._apply_source_view_style: Before apply_source_style_scheme()")
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            self.source_buffer,
            source_style_scheme,
        )
        logger.debug("DNSPage._apply_source_view_style: After apply_source_style_scheme()")
        self.source_view.set_editable(False)
        logger.debug("DNSPage._apply_source_view_style: source_view editable set to False.")
        logger.debug("DNSPage._apply_source_view_style: Finished.")

    def _is_ip_address(self, input_str: str) -> bool:
        """Check if the input string is a valid IP address."""
        try:
            dns.reversename.from_address(input_str)
            return True
        except dns.exception.SyntaxError:
            return False

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """Validate whether the input string is a valid IP address or domain."""
        ip_pattern = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
        domain_pattern = re.compile(
            r"^(?=.{1,253}$)(?!-)([A-Za-z0-9-]{1,63}(?<!-)\.)+[A-Za-z]{2,63}$"
        )
        is_ip = bool(ip_pattern.match(input_str))
        is_domain = bool(domain_pattern.match(input_str))
        return is_ip or is_domain

    def _on_entry_activated(self, _entryrow: Gtk.Widget):
        """Handle DNS entry activation event."""
        logger.debug(f"DNSPage._on_entry_activated: Triggered for entryrow: {_entryrow}")
        logger.debug("DNSPage._on_entry_activated: Before self._perform_lookup()")
        self._perform_lookup() # Logs itself
        logger.debug("DNSPage._on_entry_activated: After self._perform_lookup()")
        logger.debug("DNSPage._on_entry_activated: Finished.")

    def _on_record_type_changed(self, _dropdown: Gtk.Widget, _param):
        """Handle the record type dropdown change event."""
        logger.debug(f"DNSPage._on_record_type_changed: Triggered for dropdown: {_dropdown}, param: {_param}")
        logger.debug("DNSPage._on_record_type_changed: Before self._perform_lookup()")
        self._perform_lookup() # Logs itself
        logger.debug("DNSPage._on_record_type_changed: After self._perform_lookup()")
        logger.debug("DNSPage._on_record_type_changed: Finished.")

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        """Handle the error banner dismiss button click."""
        logger.debug(f"DNSPage._on_error_banner_dismiss: Triggered for banner: {_banner}")
        logger.debug("DNSPage._on_error_banner_dismiss: Before self._clear_error()")
        self._clear_error() # Logs itself
        logger.debug("DNSPage._on_error_banner_dismiss: After self._clear_error()")
        logger.debug("DNSPage._on_error_banner_dismiss: Finished.")

    def _configure_resolver(self) -> dns.resolver.Resolver:
        """Configures and returns a DNS resolver based on settings."""
        logger.debug("DNSPage._configure_resolver: Starting.")
        resolver = dns.resolver.Resolver()
        logger.debug(f"DNSPage._configure_resolver: dns.resolver.Resolver() created: {resolver}")
        custom_dns_server = self.settings.get_string("custom-dns-server")
        logger.debug(f"DNSPage._configure_resolver: custom_dns_server from settings: '{custom_dns_server}'")
        if custom_dns_server:
            logger.debug(f"DNSPage._configure_resolver: Using custom DNS server: {custom_dns_server}") # Existing
            resolver.nameservers = [custom_dns_server]
            logger.debug(f"DNSPage._configure_resolver: resolver.nameservers set to: {resolver.nameservers}")
        else:
            logger.debug("DNSPage._configure_resolver: Using system default DNS servers.") # Existing
        logger.debug(f"DNSPage._configure_resolver: Returning resolver: {resolver}")
        return resolver

    def _handle_ip_address_lookup(self, ip_address: str) -> str:
        """
        Handles logic specific to IP address (reverse) lookups.
        Sets the dropdown to PTR and returns "PTR".
        """
        logger.debug(f"DNSPage._handle_ip_address_lookup: Starting for ip_address: '{ip_address}'")
        model = self.dns_record_type_dropdown.get_model()
        logger.debug(f"DNSPage._handle_ip_address_lookup: Model for dropdown: {model}")
        for i in range(model.get_n_items()):
            if model.get_string(i) == "PTR":
                self.dns_record_type_dropdown.set_selected(i)
                logger.debug(
                    f"DNSPage._handle_ip_address_lookup: Input is IP ('{ip_address}'), "
                    f"selected PTR record type (index {i}) in dropdown."
                )
                break
        logger.debug("DNSPage._handle_ip_address_lookup: Returning 'PTR'.")
        return "PTR"

    def _perform_lookup(self):
        """Perform the DNS lookup based on the user input and selected record type."""
        logger.debug("DNSPage._perform_lookup: Starting.")
        user_input = self.dns_ip_entryrow.get_text().strip()
        logger.debug(f"DNSPage._perform_lookup: user_input: '{user_input}'")
        if not user_input:
            logger.debug("DNSPage._perform_lookup: User input is empty.")
            self._show_error("Input cannot be empty.")
            logger.debug("DNSPage._perform_lookup: Finished due to empty input.")
            return

        self._clear_error()

        is_valid_input = self._is_valid_ip_or_domain(user_input)
        logger.debug(f"DNSPage._perform_lookup: Input validation status for '{user_input}': {is_valid_input}")
        if not is_valid_input:
            self._show_error("Invalid IP address or domain name.")
            logger.debug("DNSPage._perform_lookup: Finished due to invalid input.")
            return

        lookup_record_type = self._get_selected_record_type()
        logger.debug(f"DNSPage._perform_lookup: Initial lookup_record_type: '{lookup_record_type}'")

        try:
            logger.debug("DNSPage._perform_lookup: Before self._configure_resolver()")
            resolver = self._configure_resolver()
            logger.debug(f"DNSPage._perform_lookup: After self._configure_resolver(), resolver: {resolver}")

            if self._is_ip_address(user_input):
                logger.debug(
                    f"DNSPage._perform_lookup: Input '{user_input}' is an IP address. "
                    "Calling _handle_ip_address_lookup."
                )
                logger.debug("DNSPage._perform_lookup: Before self._handle_ip_address_lookup()")
                lookup_record_type = self._handle_ip_address_lookup(user_input)
                logger.debug(
                    f"DNSPage._perform_lookup: After self._handle_ip_address_lookup(), "
                    f"lookup_record_type is now: '{lookup_record_type}'"
                )

            logger.info(
                "DNSPage._perform_lookup: Performing DNS lookup for %s, type %s",
                user_input, lookup_record_type
            )
            logger.debug("DNSPage._perform_lookup: Before self._lookup_record()")
            result = self._lookup_record(user_input, lookup_record_type, resolver)
            logger.debug(f"DNSPage._perform_lookup: After self._lookup_record(), result: '{result}'")
            self._display_result(result, user_input, lookup_record_type, resolver.nameservers)

        except dns.exception.DNSException as e:
            logger.error(
                f"DNSPage._perform_lookup: DNS lookup failed for {user_input} ({lookup_record_type}): {e}",
                exc_info=True
            )
            self._show_error(f"DNS Error: {e}")
        except ValueError as e:
            logger.error(f"DNSPage._perform_lookup: Invalid input for DNS lookup: {e}", exc_info=True)
            self._show_error(f"Invalid Input: {e}")
        except Exception:
            logger.exception(
                f"DNSPage._perform_lookup: Unexpected error during DNS lookup for {user_input} "
                f"({lookup_record_type})."
            )
            self._show_error("An unexpected error occurred during lookup.")
        logger.debug("DNSPage._perform_lookup: Finished.")

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown."""
        logger.debug("DNSPage._get_selected_record_type: Starting.")
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        logger.debug(f"DNSPage._get_selected_record_type: Selected index: {selected_index}")
        record_type = model.get_string(selected_index)
        logger.debug(f"DNSPage._get_selected_record_type: Returning record_type: '{record_type}'")
        return record_type

    def _show_error(self, message: str):
        """Display an error message in the UI."""
        logger.debug(f"DNSPage._show_error: Called with message: '{message}'")
        self.dns_ip_entryrow.add_css_class("error")
        logger.debug("DNSPage._show_error: 'error' CSS class added to dns_ip_entryrow.")
        self.dns_error_banner.set_title(message)
        logger.debug("DNSPage._show_error: dns_error_banner title set.")
        self.dns_error_banner.set_revealed(True)
        logger.debug("DNSPage._show_error: dns_error_banner revealed set to True.")
        logger.debug("DNSPage._show_error: Finished.")

    def _clear_error(self):
        """Clear any existing error messages from the UI."""
        logger.debug("DNSPage._clear_error: Called.")
        self.dns_ip_entryrow.remove_css_class("error")
        logger.debug("DNSPage._clear_error: 'error' CSS class removed from dns_ip_entryrow.")
        self.dns_error_banner.set_revealed(False)
        logger.debug("DNSPage._clear_error: dns_error_banner revealed set to False.")
        self.dns_error_banner.set_title("")
        logger.debug("DNSPage._clear_error: dns_error_banner title set to empty string.")
        logger.debug("DNSPage._clear_error: Finished.")

    @staticmethod
    def _lookup_record(domain_or_ip: str, record_type: str, resolver: dns.resolver.Resolver) -> str:
        logger.debug(
            f"DNSPage._lookup_record: Starting for domain_or_ip: '{domain_or_ip}', "
            f"record_type: '{record_type}', resolver: {resolver}"
        )
        try:
            if record_type == "PTR":
                rev_name = dns.reversename.from_address(domain_or_ip)
                logger.debug(f"DNSPage._lookup_record: PTR lookup, rev_name: {rev_name}")
                logger.debug(f"DNSPage._lookup_record: Before resolver.resolve({rev_name}, {record_type})")
                result = resolver.resolve(rev_name, record_type)
                logger.debug(f"DNSPage._lookup_record: After resolver.resolve(), result: {result}")
            else:
                logger.debug(f"DNSPage._lookup_record: Before resolver.resolve({domain_or_ip}, {record_type})")
                result = resolver.resolve(domain_or_ip, record_type)
                logger.debug(f"DNSPage._lookup_record: After resolver.resolve(), result: {result}")

            formatted_result = "\n".join(
                [f"{domain_or_ip}. IN {record_type} {r.to_text()}" for r in result]
            )
            logger.debug(f"DNSPage._lookup_record: Formatted result: '{formatted_result}'")
            return formatted_result
        except dns.exception.DNSException as e:
            logger.error(
                f"DNSPage._lookup_record: DNSException for {domain_or_ip} ({record_type}): {e}",
                exc_info=True
            )
            error_message = f"{record_type} record lookup failed for {domain_or_ip}: {e}"
            logger.debug(f"DNSPage._lookup_record: Returning error message: '{error_message}'")
            return error_message
        except Exception as e:
            logger.error(
                f"DNSPage._lookup_record: Unexpected exception for {domain_or_ip} ({record_type}): {e}",
                exc_info=True
            )
            error_message = f"Unexpected error during {record_type} lookup for {domain_or_ip}: {e}"
            logger.debug(f"DNSPage._lookup_record: Returning unexpected error message: '{error_message}'")
            return error_message

    def _display_result(self, result: str, domain_or_ip: str, record_type: str, dns_servers: list):
        """Display the DNS lookup results in the source buffer with enhanced formatting."""
        logger.debug(
            f"DNSPage._display_result: Starting for result: '{result}', domain_or_ip: '{domain_or_ip}', "
            f"record_type: '{record_type}', dns_servers: {dns_servers}"
        )
        logger.debug("DNSPage._display_result: Before self.source_buffer.set_text('')")
        self.source_buffer.set_text("")
        logger.debug("DNSPage._display_result: After self.source_buffer.set_text('')")

        self.header_tag = self.source_buffer.get_tag_table().lookup("header")
        logger.debug(f"DNSPage._display_result: Looked up 'header' tag: {self.header_tag}")
        if not self.header_tag:
            try:
                logger.debug("DNSPage._display_result: 'header' tag not found, creating.")
                self.header_tag = self.source_buffer.create_tag(
                    "header", weight=Pango.Weight.BOLD, size_points=12
                )
                logger.debug(f"DNSPage._display_result: Created 'header' tag: {self.header_tag}")
            except GLib.Error as e:
                logger.error("DNSPage._display_result: Error creating Pango header tag: %s", e, exc_info=True)
            except Exception:
                logger.exception("DNSPage._display_result: Unexpected error creating header tag.")

        dns_server_info = f"DNS server used: {', '.join(dns_servers) if dns_servers else 'System default'}\n"
        logger.debug(f"DNSPage._display_result: dns_server_info: '{dns_server_info.strip()}'")

        header_text = (
            f"DNS Lookup Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"{dns_server_info}\n"
        )
        logger.debug(f"DNSPage._display_result: Prepared header_text (first 30 chars): '{header_text[:30]}...'")
        logger.debug("DNSPage._display_result: Before inserting header_text with tags.")
        self.source_buffer.insert_with_tags(
            self.source_buffer.get_end_iter(), header_text, self.header_tag
        )
        logger.debug("DNSPage._display_result: After inserting header_text with tags.")

        logger.debug("DNSPage._display_result: Before inserting domain_or_ip with bold_tag.")
        self.source_buffer.insert_with_tags(
            self.source_buffer.get_end_iter(), domain_or_ip, self.bold_tag
        )
        logger.debug("DNSPage._display_result: After inserting domain_or_ip with bold_tag.")
        self.source_buffer.insert(self.source_buffer.get_end_iter(), "\t")
        logger.debug("DNSPage._display_result: Before inserting record_type with bold_tag.")
        self.source_buffer.insert_with_tags(
            self.source_buffer.get_end_iter(), record_type, self.bold_tag
        )
        logger.debug("DNSPage._display_result: After inserting record_type with bold_tag.")
        self.source_buffer.insert(self.source_buffer.get_end_iter(), "\n\n")

        logger.debug("DNSPage._display_result: Before self._format_result_in_buffer()")
        self._format_result_in_buffer(result)
        logger.debug("DNSPage._display_result: After self._format_result_in_buffer()")
        logger.debug("DNSPage._display_result: Finished.")

    def _format_result_in_buffer(self, result: str):
        """Format the DNS result string by separating fields with tabs and applying color."""
        logger.debug(f"DNSPage._format_result_in_buffer: Starting with result: '{result}'")
        lines = result.splitlines()
        logger.debug(f"DNSPage._format_result_in_buffer: Processing {len(lines)} lines.")

        for i, line in enumerate(lines):
            match = re.match(r"^\s*([\w.-]+)\s+\d*\s*(IN)\s+([A-Z]+)\s+(.*)$", line, re.IGNORECASE)
            if match:
                domain, record_class, record_type_str, value = match.groups()
                logger.debug(
                    f"DNSPage._format_result_in_buffer: Line {i+1} matched. Domain: '{domain}', "
                    f"Class: '{record_class}', Type: '{record_type_str}', Value: '{value[:30]}...'"
                )

                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    domain + "\t",
                    self.domain_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    record_class.upper() + "\t",
                    self.class_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    record_type_str.upper() + "\t",
                    self.record_type_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    value.strip() + "\n",
                    self.value_color_tag,
                )
            else:
                logger.debug(
                    f"DNSPage._format_result_in_buffer: Line {i+1} ('{line}') "
                    "did not match expected format, inserting as is."
                )
                self.source_buffer.insert(
                    self.source_buffer.get_end_iter(), line + "\n"
                )
        logger.debug("DNSPage._format_result_in_buffer: Finished.")
