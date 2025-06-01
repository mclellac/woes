import logging
import re
from datetime import datetime

import dns.resolver
import dns.reversename
import gi

# GTK version requirements (must be before gi.repository imports)
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')

# Now import GTK libraries and other dependencies
# pylint: disable=wrong-import-position
from gi.repository import Adw, Gio, Gtk, GtkSource, Pango, GLib
# pylint: disable=wrong-import-position
from .constants import APP_ID, RESOURCE_PREFIX
# pylint: disable=wrong-import-position
from .style_utils import apply_source_style_scheme
# pylint: disable=wrong-import-position
from .utils import create_source_view

# Initialize logger after all imports
logger = logging.getLogger(__name__)


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Adw.PreferencesPage):
    __gtype_name__ = "DNSPage"

    dns_ip_entryrow = Gtk.Template.Child("dns_ip_entryrow")
    dns_record_type_dropdown = Gtk.Template.Child("dns_record_type_dropdown")
    dns_results_scrolled_window = Gtk.Template.Child("dns_results_scrolled_window")
    error_banner = Gtk.Template.Child("error_banner")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.header_tag = None  # Initialize to prevent W0201 if _display_result isn't called early
        self._connect_signals()
        # Ensure create_source_view is defined and accessible
        self.source_view, self.source_buffer = create_source_view(language_name=None)
        self.dns_results_scrolled_window.set_child(self.source_view)
        self.settings = Gio.Settings.new(APP_ID)  # Ensure settings is initialized before use
        self._apply_source_view_style()  # Initial style application
        self.settings.connect(
            "changed::source-style-scheme",
            self._on_source_style_scheme_setting_changed
        )

        try:
            self.bold_tag = self.source_buffer.create_tag(
                "bold", weight=Pango.Weight.BOLD
            )
            self.domain_color_tag = self.source_buffer.create_tag(
                "domain_color", foreground="#3465a4"
            )
            self.record_type_color_tag = self.source_buffer.create_tag(
                "record_type_color", foreground="#cc0000"
            )
            self.value_color_tag = self.source_buffer.create_tag(
                "value_color", foreground="#73d216"
            )
            # self.ttl_color_tag = self.source_buffer.create_tag(  # TTL not currently parsed/used
            # "ttl_color", foreground="#fce94f"
            # )
            self.class_color_tag = self.source_buffer.create_tag(
                "class_color", foreground="#75507b"
            )
        except GLib.Error as e:  # Pango related errors can be GLib.Error
            logger.error("Error creating Pango text tags: %s", e, exc_info=True)
        except Exception:  # Fallback for other unexpected errors
            logger.exception("Unexpected error creating text tags.")

    def _connect_signals(self) -> None:
        """Connect signals for UI elements."""
        self.dns_ip_entryrow.connect("apply", self._on_entry_activated)
        self.dns_record_type_dropdown.connect(
            "notify::selected", self._on_record_type_changed
        )
        # The error_banner signal <signal name="button-clicked" handler="_on_error_banner_dismiss"/>
        # is connected in the UI file, so no explicit @Gtk.Template.Callback() is needed here.

    def _on_source_style_scheme_setting_changed(self, _settings, key):
        """Handle changes to the source-style-scheme setting."""
        logger.debug("DNSPage: '%s' setting changed, applying new source view style.", key)
        self._apply_source_view_style()

    def _apply_source_view_style(self):
        """Apply the style scheme to the GtkSourceView."""
        source_style_scheme = self.settings.get_string("source-style-scheme")
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),  # GtkSource.StyleSchemeManager() is a singleton.
            self.source_buffer,
            source_style_scheme,
        )
        self.source_view.set_editable(False)  # Ensure view is not editable.

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
        self._perform_lookup()

    def _on_record_type_changed(self, _dropdown: Gtk.Widget, _param):
        """Handle the record type dropdown change event."""
        self._perform_lookup()

    def _on_error_banner_dismiss(self, _banner: Adw.Banner, *_args):
        """Handle the error banner dismiss button click."""
        self._clear_error()

    def _configure_resolver(self) -> dns.resolver.Resolver:
        """Configures and returns a DNS resolver based on settings."""
        resolver = dns.resolver.Resolver()
        custom_dns_server = self.settings.get_string("custom-dns-server")
        if custom_dns_server:
            logger.debug("Using custom DNS server: %s", custom_dns_server)
            resolver.nameservers = [custom_dns_server]
        else:
            logger.debug("Using system default DNS servers.")
        return resolver

    def _handle_ip_address_lookup(self, ip_address: str) -> str:
        """
        Handles logic specific to IP address (reverse) lookups.
        Sets the dropdown to PTR and returns "PTR".
        """
        # If it's an IP, always use PTR. Find PTR in the model and set it.
        model = self.dns_record_type_dropdown.get_model()
        for i in range(model.get_n_items()):
            if model.get_string(i) == "PTR":
                self.dns_record_type_dropdown.set_selected(i)
                logger.debug("Input is IP ('%s'), selected PTR record type in dropdown.", ip_address)
                break
        return "PTR"  # This is the record type to be used for lookup

    def _perform_lookup(self):
        """Perform the DNS lookup based on the user input and selected record type."""
        user_input = self.dns_ip_entryrow.get_text().strip()
        if not user_input:
            self._show_error("Input cannot be empty.")
            return

        self._clear_error()  # Clear previous errors first

        if not self._is_valid_ip_or_domain(user_input):
            self._show_error("Invalid IP address or domain name.")
            return

        # Get initially selected type, but it might be overridden for IP lookups
        lookup_record_type = self._get_selected_record_type()

        try:
            resolver = self._configure_resolver()

            if self._is_ip_address(user_input):
                lookup_record_type = self._handle_ip_address_lookup(user_input)

            logger.info("Performing DNS lookup for %s, type %s", user_input, lookup_record_type)
            result = self._lookup_record(user_input, lookup_record_type, resolver)
            self._display_result(result, user_input, lookup_record_type, resolver.nameservers)

        except dns.exception.DNSException as e:  # Specific DNS exceptions.
            logger.error("DNS lookup failed for %s (%s): %s", user_input, lookup_record_type, e, exc_info=True)
            self._show_error(f"DNS Error: {e}")  # User-friendly, e often has good info.
        except ValueError as e:  # For invalid input not caught by initial validation.
            logger.error("Invalid input for DNS lookup: %s", e, exc_info=True)
            self._show_error(f"Invalid Input: {e}")
        except Exception:  # General fallback for other unexpected errors.
            logger.exception("Unexpected error during DNS lookup for %s (%s).", user_input, lookup_record_type)
            self._show_error("An unexpected error occurred during lookup.")

    def _get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown."""
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        return model.get_string(selected_index)

    def _show_error(self, message: str):
        """Display an error message in the UI."""
        self.dns_ip_entryrow.add_css_class("error")
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)

    def _clear_error(self):
        """Clear any existing error messages from the UI."""
        self.dns_ip_entryrow.remove_css_class("error")
        self.error_banner.set_revealed(False)
        self.error_banner.set_title("")

    @staticmethod
    def _lookup_record(domain_or_ip: str, record_type: str, resolver: dns.resolver.Resolver) -> str:
        try:
            if record_type == "PTR":
                rev_name = dns.reversename.from_address(domain_or_ip)
                result = resolver.resolve(rev_name, record_type)
            else:
                result = resolver.resolve(domain_or_ip, record_type)

            return "\n".join(
                [f"{domain_or_ip}. IN {record_type} {r.to_text()}" for r in result]
            )
        except dns.exception.DNSException as e:
            return f"{record_type} record lookup failed for {domain_or_ip}: {e}"

    def _display_result(self, result: str, domain_or_ip: str, record_type: str, dns_servers: list):
        """Display the DNS lookup results in the source buffer with enhanced formatting."""
        self.source_buffer.set_text("")  # Clear previous results

        # Check if the header tag already exists in the tag table
        self.header_tag = self.source_buffer.get_tag_table().lookup("header")
        if not self.header_tag:
            try:
                self.header_tag = self.source_buffer.create_tag(
                    "header", weight=Pango.Weight.BOLD, size_points=12
                )
            except GLib.Error as e:  # Pango related errors
                logger.error("Error creating Pango header tag: %s", e, exc_info=True)
            except Exception:  # Fallback
                logger.exception("Unexpected error creating header tag.")

        dns_server_info = f"DNS server used: {', '.join(dns_servers) if dns_servers else 'System default'}\n"

        header_text = (
            f"DNS Lookup Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"{dns_server_info}\n"
        )
        self.source_buffer.insert_with_tags(
            self.source_buffer.get_end_iter(), header_text, self.header_tag
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
        """Format the DNS result string by separating fields with tabs and applying color."""
        lines = result.splitlines()

        for line in lines:
            # Improved regex to handle potential extra spaces and variations in DNS output
            match = re.match(r"^\s*([\w.-]+)\s+\d*\s*(IN)\s+([A-Z]+)\s+(.*)$", line, re.IGNORECASE)
            if match:
                domain, record_class, record_type_str, value = match.groups()

                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    domain + "\t",
                    self.domain_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    record_class.upper() + "\t",  # Ensure IN is uppercase.
                    self.class_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    record_type_str.upper() + "\t",  # Ensure record type is uppercase.
                    self.record_type_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    value.strip() + "\n",  # Strip trailing spaces from value.
                    self.value_color_tag,
                )
            else:
                # If line doesn't match expected format, insert it as is.
                self.source_buffer.insert(
                    self.source_buffer.get_end_iter(), line + "\n"
                )
