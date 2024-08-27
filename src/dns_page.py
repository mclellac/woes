import logging
import dns.resolver
import dns.reversename
import gi
from datetime import datetime
import re

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from gi.repository import Gio, Gtk, GtkSource, Pango
from .constants import RESOURCE_PREFIX
from .style_utils import apply_source_style_scheme


@Gtk.Template(resource_path=f"{RESOURCE_PREFIX}/dns_page.ui")
class DNSPage(Gtk.Box):
    """A class representing the DNS page in the application.

    This class handles DNS lookups and displays the results in a styled GtkSourceView.
    """

    __gtype_name__ = "DNSPage"

    dns_ip_entryrow = Gtk.Template.Child("dns_ip_entryrow")
    dns_record_type_dropdown = Gtk.Template.Child("dns_record_type_dropdown")
    dns_results_scrolled_window = Gtk.Template.Child("dns_results_scrolled_window")
    dns_error_label = Gtk.Template.Child("dns_errors_label")

    def __init__(self, **kwargs):
        """Initialize the DNSPage with UI components and setup necessary styles."""
        super().__init__(**kwargs)
        self.dns_page_init_ui()
        self.source_buffer = self.init_source_buffer()
        self.source_view = self.init_source_view(self.source_buffer)
        self.apply_source_view_style()

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
        except Exception as e:
            logging.error(f"Error creating text tags: {e}")

    def dns_page_init_ui(self) -> None:
        """Initialize the UI elements and connect signals."""
        self.dns_ip_entryrow.connect("apply", self.on_dns_entry_activated)
        self.dns_record_type_dropdown.connect("notify::selected", self.on_record_type_changed)

    def init_source_buffer(self) -> GtkSource.Buffer:
        """Initialize the source buffer for the GtkSourceView."""
        source_buffer = GtkSource.Buffer()
        lang_manager = GtkSource.LanguageManager.get_default()
        text_lang = lang_manager.get_language("txt")

        if text_lang is not None:
            source_buffer.set_language(text_lang)
        else:
            logging.error(
                "Text language definition not found. Continuing without syntax highlighting."
            )

        source_buffer.set_highlight_syntax(True)
        return source_buffer

    def init_source_view(self, source_buffer: GtkSource.Buffer) -> GtkSource.View:
        """Initialize the GtkSourceView with the provided source buffer."""
        source_view = GtkSource.View.new_with_buffer(source_buffer)
        source_view.set_show_line_numbers(False)
        source_view.set_editable(False)
        source_view.set_wrap_mode(Gtk.WrapMode.WORD)
        source_view.set_hexpand(True)
        source_view.set_vexpand(True)
        self.dns_results_scrolled_window.set_child(source_view)
        return source_view

    def apply_source_view_style(self):
        """Apply the style scheme to the GtkSourceView."""
        settings = Gio.Settings.new("com.github.mclellac.WebOpsEvaluationSuite")
        source_style_scheme = settings.get_string("source-style-scheme")
        apply_source_style_scheme(
            GtkSource.StyleSchemeManager.get_default(),
            self.source_buffer,
            source_style_scheme,
        )

    def is_ip_address(self, input_str: str) -> bool:
        """Check if the input string is a valid IP address."""
        try:
            dns.reversename.from_address(input_str)
            return True
        except dns.exception.SyntaxError:
            return False

    def is_valid_ip_or_domain(self, input_str: str) -> bool:
        """Validate whether the input string is a valid IP address or domain."""
        ip_pattern = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
        domain_pattern = re.compile(
            r"^(?=.{1,253}$)(?!-)([A-Za-z0-9-]{1,63}(?<!-)\.)+[A-Za-z]{2,63}$"
        )
        is_ip = bool(ip_pattern.match(input_str))
        is_domain = bool(domain_pattern.match(input_str))
        return is_ip or is_domain

    def on_dns_entry_activated(self, entryrow: Gtk.Widget):
        """Handle DNS entry activation event."""
        self.perform_dns_lookup()

    def on_record_type_changed(self, dropdown: Gtk.Widget, param):
        """Handle the record type dropdown change event."""
        self.perform_dns_lookup()

    def perform_dns_lookup(self):
        """Perform the DNS lookup based on the user input and selected record type."""
        user_input = self.dns_ip_entryrow.get_text().strip()
        if not user_input:
            self.show_error("Input cannot be empty.")
            return

        self.clear_error()

        if not self.is_valid_ip_or_domain(user_input):
            self.show_error("Invalid IP address or domain name.")
            return

        record_type = self.get_selected_record_type()

        try:
            if self.is_ip_address(user_input):
                result = self.dns_lookup(user_input, "PTR")
            else:
                result = self.dns_lookup(user_input, record_type)

            self.display_dns_result(result, user_input, record_type)
        except Exception as e:
            logging.error(f"Error performing DNS lookup: {e}")
            self.show_error(f"Error: {str(e)}")

    def get_selected_record_type(self) -> str:
        """Get the currently selected DNS record type from the dropdown."""
        model = self.dns_record_type_dropdown.get_model()
        selected_index = self.dns_record_type_dropdown.get_selected()
        return model.get_string(selected_index)

    def show_error(self, message: str):
        """Display an error message in the UI."""
        self.dns_ip_entryrow.set_css_classes(["error"])
        self.dns_error_label.set_text(message)
        self.dns_error_label.set_visible(True)

    def clear_error(self):
        """Clear any existing error messages from the UI."""
        self.dns_ip_entryrow.remove_css_class("error")
        self.dns_error_label.set_text("")
        self.dns_error_label.set_visible(False)

    @staticmethod
    def dns_lookup(domain_or_ip: str, record_type: str) -> str:
        """Perform a DNS lookup for the given domain or IP and record type."""
        try:
            if record_type == "PTR":
                rev_name = dns.reversename.from_address(domain_or_ip)
                result = dns.resolver.resolve(rev_name, record_type)
            else:
                result = dns.resolver.resolve(domain_or_ip, record_type)

            return "\n".join([f"{domain_or_ip}. IN {record_type} {r.to_text()}" for r in result])
        except dns.exception.DNSException as e:
            return f"{record_type} record lookup failed for {domain_or_ip}: {e}"

    def display_dns_result(self, result: str, domain_or_ip: str, record_type: str):
        """Display the DNS lookup results in the source buffer with enhanced formatting."""
        self.source_buffer.set_text("")

        # Check if the header tag already exists in the tag table
        self.header_tag = self.source_buffer.get_tag_table().lookup("header")
        if not self.header_tag:
            try:
                self.header_tag = self.source_buffer.create_tag(
                    "header", weight=Pango.Weight.BOLD, size_points=12
                )
            except Exception as e:
                logging.error(f"Error creating header tag: {e}")

        header = f"DNS Lookup Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
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

        self.format_dns_result(result)

    def format_dns_result(self, result: str):
        """Format the DNS result string by separating fields with tabs and applying color."""
        lines = result.splitlines()

        for line in lines:
            match = re.match(r"^(.*?)\s+(IN)\s+([A-Z]+)\s+(.+)$", line)
            if match:
                domain = match.group(1)
                record_class = match.group(2)
                record_type = match.group(3)
                value = match.group(4)

                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(), domain + "\t", self.domain_color_tag
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(), record_class + "\t", self.class_color_tag
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(),
                    record_type + "\t",
                    self.record_type_color_tag,
                )
                self.source_buffer.insert_with_tags(
                    self.source_buffer.get_end_iter(), value + "\n", self.value_color_tag
                )
            else:
                self.source_buffer.insert(self.source_buffer.get_end_iter(), line + "\n")
