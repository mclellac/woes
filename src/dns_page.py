"""
Defines the DNS lookup page for the Woes application.

This module contains the :class:`.DNSPage` class, which provides UI and
functionality for performing DNS lookups.
"""

import logging

logger = logging.getLogger(__name__)

import gi
from gi.repository import Adw, Gio, Gtk, Pango, GObject, GLib
from typing import Optional, Sequence, Any  # list and dict will be used directly
from enum import Enum

from .constants import APP_ID, RESOURCE_PREFIX
from .utils import show_global_error, show_global_toast, is_valid_ip, is_valid_domain
from .dns_client import (
    DnsResolverClient,
    DnsClientError,  # Base for catching all client errors
    DnsResolutionTimeoutError,
    DnsNxDomainError,
    DnsNoAnswerError,
    DnsGenericError,
    # DnsCancelledError, # This was speculative and not implemented in dns_client.py
)

DNS_LOOKUP_ERROR_DOMAIN = "dns-lookup-error-domain"

class DnsLookupErrorType(int, Enum):
    """Enumeration of DNS Lookup error types for Gio.Task error reporting."""

    CANCELLED = 0
    # Other specific DNS errors could be added if needed for task error reporting,
    # but DnsClientError subtypes are usually handled directly.

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
    dns_cancel_button = Gtk.Template.Child() # Bind the button from UI

    def __init__(self, **kwargs: Any):
        """
        Initialize the DNSPage.

        Sets up UI elements, connects signals, and initializes GSettings.

        :param kwargs: Keyword arguments passed to the :class:`Gtk.Box` constructor.
        :type kwargs: Any
        """
        super().__init__(**kwargs)
        logger.debug("DNSPage initialized.")
        self.settings = Gio.Settings.new(APP_ID)

        # Global Font setting
        self._output_font_gsettings_key = "output-font"
        output_font_str = self.settings.get_string(self._output_font_gsettings_key)
        self._output_font_desc = Pango.FontDescription.from_string(output_font_str if output_font_str else "Sans 10")

        # Stored results for refresh
        self._current_result_records: Optional[list[dict[str, Any]]] = None
        self._current_user_input: Optional[str] = None
        self._current_requested_record_type: Optional[str] = None
        self._current_dns_servers: Optional[Sequence[Any]] = None

        self.current_dns_task: Optional[Gio.Task] = None
        self.current_dns_cancellable: Optional[Gio.Cancellable] = None

        self._connect_signals()

        # Initialize new status row and spinner
        # Initially disable clear/copy buttons as there are no results
        # Also ensure cancel button is initially in the correct state
        if self.dns_cancel_button:
            self.dns_cancel_button.set_visible(False)
            self.dns_cancel_button.set_sensitive(False)

    def _connect_signals(self) -> None:
        """Connect signals for UI elements to their respective handlers."""
        self.domain_entry.connect("activate", self._on_entry_activated)
        self.domain_entry.connect("changed", self._on_domain_entry_changed) # Clear error on type
        self.dns_apply_button.connect("clicked", self._on_entry_activated)
        self.dns_record_type_dropdown.connect("notify::selected", self._on_record_type_changed)
        if self.dns_clear_results_button:
            self.dns_clear_results_button.connect("clicked", self._on_clear_results_clicked)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.connect("clicked", self._on_copy_all_results_clicked)

        if self.dns_cancel_button: # Now it should be bound by Gtk.Template
            self.dns_cancel_button.connect("clicked", self._on_cancel_lookup_clicked)
        else:
            logger.warning("DNSPage: dns_cancel_button was not bound from UI file.")


        # Connect GSettings change for global font
        self.settings.connect(f"changed::{self._output_font_gsettings_key}", self._on_global_output_font_changed)

    # Removed _find_cancel_button method

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
        """
        Clear all DNS lookup results from the UI.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        """
        logger.info("Clearing DNS results.")
        while child := self.dns_results_box_container.get_first_child():
            self.dns_results_box_container.remove(child)

        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(False)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(False)

        # Clear stored results
        self._current_result_records = None
        self._current_user_input = None
        self._current_requested_record_type = None
        self._current_dns_servers = None

    def _on_copy_all_results_clicked(self, _button: Gtk.Button) -> None:
        """
        Copy all displayed DNS results to the clipboard.

        :param _button: The :class:`Gtk.Button` that was clicked (unused).
        :type _button: Gtk.Button
        """
        logger.info("Copying all DNS results to clipboard.")
        all_results_text_parts = []

        def get_widget_text(widget: Gtk.Widget) -> Optional[str]:
            """Extracts text from known text-holding widgets."""
            if isinstance(widget, Gtk.Label):
                return widget.get_label()
            if hasattr(widget, "get_title") and callable(widget.get_title):
                title = widget.get_title()
                if title: return title
            if hasattr(widget, "get_subtitle") and callable(widget.get_subtitle):
                subtitle = widget.get_subtitle()
                if subtitle: return subtitle
            return None

        def extract_text_from_action_row_children(action_row: Adw.ActionRow, indent: str) -> list[str]:
            """
            Extracts text from Gtk.Label children of an Adw.ActionRow,
            including those potentially nested in Gtk.Box (common for prefixes/suffixes).
            """
            extracted_texts = []
            # Adw.ActionRow typically has a Gtk.Box as its first child (the "content area")
            # Prefixes are added before this box, suffixes after, or sometimes within complex structures.
            # We need to iterate all children of the ActionRow itself.
            child = action_row.get_first_child()
            processed_labels_in_content = set() # To avoid double counting if label is title/subtitle

            title = action_row.get_title()
            subtitle = action_row.get_subtitle()

            while child:
                if isinstance(child, Gtk.Label):
                    label_text = child.get_label()
                    # Avoid duplicating title/subtitle if they are also direct label children
                    if label_text and label_text != title and label_text != subtitle:
                        extracted_texts.append(f"{indent}  Value: {label_text}")
                        processed_labels_in_content.add(label_text)
                elif isinstance(child, Gtk.Box): # Common for suffix/prefix containers
                    box_child = child.get_first_child()
                    while box_child:
                        if isinstance(box_child, Gtk.Label):
                            label_text = box_child.get_label()
                            if label_text and label_text != title and label_text != subtitle:
                                extracted_texts.append(f"{indent}  Value: {label_text}")
                                processed_labels_in_content.add(label_text)
                        box_child = box_child.get_next_sibling()
                child = child.get_next_sibling()

            # The _add_standard_suffix_box_to_row and _add_expander_detail_row
            # add a Gtk.Label directly as a suffix.
            # Adw.ActionRow stores suffixes in a way that they might not be simple children.
            # However, Gtk.Widget.get_last_child() could point to the last suffix if it's simple.
            # This part is still heuristic due to GTK's complex layout.
            # Let's assume the iteration above catches most labels.
            # If specific labels (like the main value_label) are missed, a more targeted approach
            # for Adw.ActionRow's suffix area might be needed.

            return extracted_texts

        def extract_text_from_row(row: Gtk.Widget, level: int = 0) -> None:
            """Recursively extracts text from a row and its children."""
            indent = "  " * level
            current_row_texts = []

            title = getattr(row, "get_title", lambda: None)()
            subtitle = getattr(row, "get_subtitle", lambda: None)()

            if title:
                current_row_texts.append(f"{indent}{title}")
            if subtitle:
                # If title was present, make subtitle clearly associated
                prefix = f"{indent}  " if title else indent
                current_row_texts.append(f"{prefix}└─ {subtitle}")


            if isinstance(row, Adw.ActionRow):
                # For ActionRows, try to get labels from its children (prefixes/suffixes)
                # This is important for rows created by _add_expander_detail_row or _add_standard_suffix_box_to_row
                # where the main data is in a Gtk.Label added as a prefix or suffix.
                action_row_children_texts = extract_text_from_action_row_children(row, indent + ("  " if title or subtitle else ""))
                current_row_texts.extend(action_row_children_texts)

            # Add collected texts for the current row to the main list
            if current_row_texts:
                all_results_text_parts.extend(current_row_texts)

            # If it's an ExpanderRow, recurse for its children rows
            if isinstance(row, Adw.ExpanderRow) and row.get_expanded():
                _child_row = row.get_first_child() # This gets the header area of expander
                # We need to iterate the actual added rows using add_row
                # This requires a different approach, as get_first_child on ExpanderRow
                # does not give the Gtk.ListBox that holds the rows.
                # Instead, we assume children added with `add_row` are in a Gtk.ListBox
                # which is a child of the ExpanderRow.

                # Let's find the Gtk.ListBox among children of Adw.ExpanderRow
                expander_child = row.get_first_child()
                _list_box_container = None
                while expander_child:
                    # The list box is usually the last complex child before any internal actionables
                    # This is heuristic. A more robust way would be to know the exact structure.
                    # Often, it's a Gtk.Box containing a Gtk.ListBox or directly a Gtk.ListBox.
                    # For Adw.ExpanderRow, rows are added to an internal Gtk.ListBox.
                    # We need to find this list box.
                    # A common structure is ExpanderRow -> Gtk.Box -> Gtk.ListBox (for rows)
                    # Or ExpanderRow -> Gtk.ListBox

                    # Simplified: Iterate all children and if it is a ListBox, use it.
                    # Or, if it's a row type we expect inside, process it.
                    # This is still not perfect.
                    # A better way: Adw.ExpanderRow has a `get_rows()` method in some GTK versions or
                    # a known child structure. If not directly available, this remains heuristic.
                    # For now, let's assume `add_row` adds to a child that can be iterated.
                    # This part is complex due to Gtk/Adw internal structures.

                    # Fallback: Iterate all children of the expander. If a child is an ActionRow, process it.
                    # This is what the original code was missing.
                    # The children of an Adw.ExpanderRow are complex.
                    # The rows added via `add_row` are typically in a Gtk.ListBox.

                    # Let's try to find the list box that holds the rows.
                    # AdwExpanderRow -> GtkBox -> AdwPreferencesGroup (if rows are added) -> GtkListBox -> AdwActionRow
                    # This structure can be deep.
                    # A simpler assumption for now: look for Adw.ActionRow as direct children or children of children.

                    # Let's refine the iteration for ExpanderRow children
                    # The actual rows are added to a Gtk.ListBox which is a child of the Adw.ExpanderRow.
                    # This ListBox is usually found as a child of a Gtk.Box, which itself is a child of Adw.ExpanderRow.
                    # Or, in simpler cases, it might be a direct child.
                    list_box_found = None

                    # Common structure: ExpanderRow -> Gtk.Box (child) -> Gtk.ListBox (grandchild)
                    # Or ExpanderRow -> Gtk.ListBox (child)

                    iter_child = row.get_first_child()
                    while iter_child:
                        if isinstance(iter_child, Gtk.ListBox):
                            list_box_found = iter_child
                            break
                        # Check if this child is a Gtk.Box that contains a Gtk.ListBox
                        if hasattr(iter_child, "get_first_child"): # Check if it's a container
                            potential_list_box = iter_child.get_first_child()
                            if isinstance(potential_list_box, Gtk.ListBox):
                                list_box_found = potential_list_box
                                break
                        iter_child = iter_child.get_next_sibling()

                    if list_box_found:
                        actual_row_child = list_box_found.get_first_child()
                        while actual_row_child:
                            # Ensure we are processing an actual row widget, not just any child of the ListBox
                            if isinstance(actual_row_child, (Adw.ActionRow, Adw.ExpanderRow, Adw.PreferencesRow)):
                                extract_text_from_row(actual_row_child, level + 1)
                            actual_row_child = actual_row_child.get_next_sibling()
                    else:
                        # Fallback if the specific ListBox structure isn't found
                        # This might grab more than just the 'rows' but is better than nothing
                        logger.warning("Could not find Gtk.ListBox in Adw.ExpanderRow, using fallback child iteration.")
                        expander_child_fallback = row.get_first_child()
                        while expander_child_fallback:
                            # Avoid processing the expander's own header/title widget or non-row widgets
                            if expander_child_fallback != row.get_title_widget() and \
                               isinstance(expander_child_fallback, (Adw.ActionRow, Adw.ExpanderRow, Adw.PreferencesRow)):
                                extract_text_from_row(expander_child_fallback, level + 1)
                            expander_child_fallback = expander_child_fallback.get_next_sibling()


        # Iterate through children of dns_results_box_container
        child = self.dns_results_box_container.get_first_child()
        is_first_separator = True
        while child:
            if isinstance(child, Gtk.Separator):
                if not is_first_separator: # Add a visual separator for multiple records
                    all_results_text_parts.append("---")
                is_first_separator = False # Skip adding "---" for the first separator after query info
            elif isinstance(child, (Adw.ActionRow, Adw.ExpanderRow)):
                extract_text_from_row(child)
            child = child.get_next_sibling()

        if not all_results_text_parts:
            show_global_toast(self, "No results to copy.")
            return

        final_text_to_copy = "\n".join(all_results_text_parts)
        DNSPage._copy_to_clipboard(final_text_to_copy, self)
        show_global_toast(self, "All results copied to clipboard.")

    @staticmethod
    def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
        """
        Copy the given text to the clipboard.

        :param text: The text to copy.
        :type text: str
        :param widget: The :class:`Gtk.Widget` from which to get the clipboard.
        :type widget: Gtk.Widget
        """
        try:
            # display = widget.get_display() # No longer needed
            # clipboard = Gtk.Clipboard.get_default(display) # Old failing line
            clipboard = widget.get_clipboard() # New approach
            if clipboard: # Gtk.Clipboard might be None if not available
                clipboard.set_text(text) # set_text does not take a length argument in GTK4
                logger.info("Copied to clipboard: %s", text[:100] + "..." if len(text) > 100 else text)
                # show_global_toast(widget, "Text copied to clipboard.") # Caller handles success toast
            else:
                logger.warning("Could not get clipboard from widget: %s", widget)
                show_global_toast(widget.get_native(), "Failed to access clipboard.") # type: ignore
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error copying to clipboard:")
            show_global_toast(widget.get_native(), "Error copying to clipboard.") # type: ignore

    def _is_valid_ip_or_domain(self, input_str: str) -> bool:
        """
        Validate if the input is a syntactically valid IP or domain.

        :param input_str: The string to validate.
        :type input_str: str
        :return: ``True`` if the input is a valid IP address or domain name, ``False`` otherwise.
        :rtype: bool
        """
        if not input_str:
            return False
        return is_valid_ip(input_str) or is_valid_domain(input_str)

    def _on_entry_activated(self, _widget: Gtk.Widget) -> None:
        """
        Handle activation of the domain entry or click of the 'Lookup' button.

        Triggers the DNS lookup process.

        :param _widget: The :class:`Gtk.Widget` that triggered the action (unused).
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
        Create a :class:`Gtk.Button` for copying text.

        :param text_to_copy: The text to be copied when the button is clicked.
        :type text_to_copy: str
        :param tooltip_text: The tooltip text for the button.
        :type tooltip_text: str
        :param widget_for_clipboard: The :class:`Gtk.Widget` from which to get the clipboard.
        :type widget_for_clipboard: Gtk.Widget
        :return: A new :class:`Gtk.Button` configured for copying.
        :rtype: Gtk.Button
        """
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
        """
        Create a basic :class:`Adw.ActionRow` with title, subtitle, and optional icon.

        :param name: The title for the ActionRow, typically the record name.
        :type name: str
        :param record_type_label: The string representation of the record type (e.g., "A", "MX").
        :type record_type_label: str
        :param base_subtitle_text: Base text for the subtitle (e.g., class and TTL info).
        :type base_subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :return: A new :class:`Adw.ActionRow`.
        :rtype: Adw.ActionRow
        """
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
        """
        Add a standard suffix box (label, copy value button, copy summary button) to an :class:`Adw.ActionRow`.

        :param row: The :class:`Adw.ActionRow` to add suffixes to.
        :type row: Adw.ActionRow
        :param main_value_text: The main value to display as a label and for the value copy button.
        :type main_value_text: str
        :param main_value_tooltip_prefix: The prefix for the tooltip of the value copy button.
        :type main_value_tooltip_prefix: str
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        """
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
        # suffix_box removed
        row.add_suffix(value_label)  # type: ignore
        row.add_suffix(
            self._create_copy_button(main_value_text, f"{main_value_tooltip_prefix}: {main_value_text}", row)
        )  # type: ignore
        row.add_suffix(self._create_copy_button(full_summary_text, "Copy Full Record Summary", row))  # type: ignore

    def _create_base_expander_row(
        self, name: str, subtitle_text: str, icon_name: Optional[str], full_summary_text: str
    ) -> Adw.ExpanderRow:
        """
        Create a basic :class:`Adw.ExpanderRow` with title, subtitle, icon, and a full summary copy button.

        :param name: The title for the ExpanderRow.
        :type name: str
        :param subtitle_text: The subtitle for the ExpanderRow.
        :type subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        :return: A new :class:`Adw.ExpanderRow`.
        :rtype: Adw.ExpanderRow
        """
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
        """
        Add a detail row (:class:`Adw.ActionRow`) to an :class:`Adw.ExpanderRow`.

        :param expander_row: The :class:`Adw.ExpanderRow` to add the detail row to.
        :type expander_row: Adw.ExpanderRow
        :param title: Optional title for the detail :class:`Adw.ActionRow`.
        :type title: Optional[str]
        :param value_text: The value text to display in the detail row.
        :type value_text: str
        :param copy_tooltip_prefix: The prefix for the tooltip of the copy button for the value.
        :type copy_tooltip_prefix: str
        :param is_value_primary_content: If ``True``, ``value_text`` is the primary content (e.g., TXT segments).
                                         Otherwise, it's a suffix to the title (e.g., SOA fields).
        :type is_value_primary_content: bool
        """
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

        # content_box removed
        if is_value_primary_content:  # For TXT segments where the value is the main content of the row
            detail_row.add_prefix(value_label)  # type: ignore
            detail_row.add_prefix(copy_button)  # type: ignore
        else:  # For SOA fields where title is present and value is a suffix
            detail_row.add_suffix(value_label)  # type: ignore
            detail_row.add_suffix(copy_button)  # type: ignore

        detail_row.set_selectable(False)
        expander_row.add_row(detail_row)  # type: ignore

    def _set_loading_state(self, active: bool, message: Optional[str] = None) -> None:
        """
        Set the UI loading state (spinner, status message, sensitivity).

        :param active: ``True`` to set loading state, ``False`` to unset.
        :type active: bool
        :param message: Optional message to display in the status row.
        :type message: Optional[str]
        """
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
            elif active and current_subtitle != "Looking up...":  # Default message when starting an operation
                self.dns_status_row.set_subtitle("Looking up...")  # type: ignore
            elif not active and not message:  # Default message when stopping (idle) and no specific message given
                self.dns_status_row.set_subtitle("Idle")  # type: ignore
            # If not active and a message is present (e.g. error or success), it will be set by the caller.

        sensitive = not active
        if self.domain_entry:
            self.domain_entry.set_sensitive(sensitive)  # type: ignore
        if self.dns_apply_button:
            self.dns_apply_button.set_sensitive(sensitive)  # type: ignore
        if self.dns_record_type_dropdown:
            self.dns_record_type_dropdown.set_sensitive(sensitive)  # type: ignore

        if self.dns_cancel_button:
            self.dns_cancel_button.set_visible(active)
            self.dns_cancel_button.set_sensitive(active)

    def _on_cancel_lookup_clicked(self, _button: Gtk.Button) -> None:
        """Handle click on the 'Cancel Lookup' button."""
        logger.info("DNS lookup cancellation requested.")
        if self.current_dns_cancellable and not self.current_dns_cancellable.is_cancelled():
            self.current_dns_cancellable.cancel()
            if self.dns_cancel_button:
                self.dns_cancel_button.set_sensitive(False)
            if self.dns_status_row:
                self.dns_status_row.set_subtitle("Cancelling lookup...") # type: ignore
        else:
            logger.warning("No active DNS lookup cancellable to cancel.")


    def _on_domain_entry_changed(self, editable: Adw.EntryRow) -> None:
        """
        Handle the 'changed' signal for the domain entry row.
        Clears the 'error' CSS class and any specific global error message.
        """
        if editable.has_css_class("error"):
            editable.remove_css_class("error")
            main_window = self.get_native()
            if main_window and hasattr(main_window, "hide_error_if_message_matches"):
                # Try to hide specific messages if that method exists
                main_window.hide_error_if_message_matches("Invalid input for PTR record. Please enter a valid IP address.") # type: ignore[attr-defined]
                main_window.hide_error_if_message_matches("Invalid input. Please enter a valid domain name or IP address.") # type: ignore[attr-defined]
            # Fallback or if the specific message isn't the current one,
            # the error banner might persist until next validation or _clear_error().

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
        result_data: list[dict[str, Any]],
        user_input: str,
        requested_record_type: str,  # The type initially selected by user
        dns_client: DnsResolverClient,
    ) -> None:
        """
        Handle successful DNS lookup results.

        :param result_data: The list of DNS records obtained from the lookup.
        :type result_data: list[dict[str, Any]]
        :param user_input: The domain or IP address that was queried.
        :type user_input: str
        :param requested_record_type: The DNS record type that was requested.
        :type requested_record_type: str
        :param dns_client: The :class:`.dns_client.DnsResolverClient` instance used for the lookup.
        :type dns_client: .dns_client.DnsResolverClient
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

        # Store results for potential refresh
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
        # show_global_toast is good for transient notifications, status row is persistent.
        show_global_toast(self, status_message)

    def _handle_dns_lookup_exception(
        self,
        error: Exception,
        user_input: str,
        requested_record_type: str,
        dns_client: DnsResolverClient,  # Pass client to get nameservers for NoAnswer
    ) -> None:
        """
        Handle exceptions from :class:`.dns_client.DnsResolverClient`.

        :param error: The exception object that was raised.
        :type error: Exception
        :param user_input: The domain or IP address that was queried.
        :type user_input: str
        :param requested_record_type: The DNS record type that was requested.
        :type requested_record_type: str
        :param dns_client: The :class:`.dns_client.DnsResolverClient` instance used for the lookup.
        :type dns_client: .dns_client.DnsResolverClient
        """
        error_message = str(error)  # Original full error message
        status_subtitle = f"Error: {error_message.splitlines()[0]}"  # Default status: first line of error

        if isinstance(error, DnsNxDomainError):
            show_global_error(self, error_message)
            status_subtitle = f"NXDOMAIN: Domain '{user_input}' not found."
            self._current_result_records = None  # No valid results to refresh
        elif isinstance(error, DnsNoAnswerError):
            logger.info("DNSPage: %s", error_message)
            nameservers_used = dns_client.resolver.nameservers
            # Store parameters for refresh, as _display_result will show "No records"
            self._current_result_records = []
            self._current_user_input = user_input
            self._current_requested_record_type = requested_record_type
            self._current_dns_servers = nameservers_used
            self._display_result(
                [], user_input, requested_record_type, nameservers_used
            )  # Shows "No records found" in results area
            # Override the "No records found" status from _display_result with the actual error for clarity in status row
            status_subtitle = f"No {requested_record_type} records found for {user_input} (No Answer)."
        elif isinstance(error, DnsResolutionTimeoutError):
            show_global_error(self, error_message)  # type: ignore
            status_subtitle = f"Timeout: Could not resolve {user_input}."
            self._current_result_records = None  # No valid results to refresh
        elif isinstance(error, DnsGenericError):
            logger.exception(
                "DNSPage: DNS lookup failed for %s, type %s (DnsGenericError):",
                user_input,
                requested_record_type,
            )
            show_global_error(self, error_message)
            status_subtitle = f"DNS Error: {error_message.splitlines()[0]}"
        elif isinstance(error, DnsClientError):  # Base client error
            logger.exception(
                "DNSPage: Unexpected DnsClientError for %s, type %s:",
                user_input,
                requested_record_type,
            )
            show_global_error(self, f"DNS Client Error: {error_message}")
            status_subtitle = f"Client Error: {error_message.splitlines()[0]}"
            self._current_result_records = None
        else:  # Generic Exception
            logger.exception(
                "DNSPage: Unexpected error during DNS lookup for %s, type %s:",
                user_input,
                requested_record_type,
            )
            error_message_short = f"An unexpected error occurred: {error_message.splitlines()[0]}"
            show_global_error(self, error_message_short)
            status_subtitle = error_message_short
            self._current_result_records = None

        if self.dns_status_row:
            self.dns_status_row.set_subtitle(status_subtitle)

    def _perform_lookup(self) -> None:
        """
        Perform the DNS lookup based on user input and selected record type.

        Orchestrates input validation, client interaction, and result/error display.
        """
        if self.current_dns_task and not self.current_dns_task.is_done():
            if self.current_dns_cancellable and not self.current_dns_cancellable.is_cancelled():
                logger.info("Requesting cancellation of previous DNS lookup task.")
                self.current_dns_cancellable.cancel()
                # UI will be updated by the _dns_lookup_done_cb of the cancelled task
            else: # Task is running but no cancellable, or already cancelled
                logger.warning("Previous DNS lookup task is still running or finalizing cancellation.")
                # Potentially show a toast if user tries to start multiple lookups rapidly
                # For now, we let it proceed to create a new task.
                # The old task, if it completes, might update UI, but new one will override.

        self._set_loading_state(True, "Looking up...")
        user_input = self.domain_entry.get_text().strip()  # type: ignore
        requested_record_type = self._get_selected_record_type()
        logger.debug(f"DNSPage: Performing DNS lookup for: {user_input}, type: {requested_record_type}")

        # Clear previous validation error message if any
        main_window = self.get_native()
        if main_window and hasattr(main_window, "hide_error_if_message_matches"):
            main_window.hide_error_if_message_matches("Invalid input for PTR record. Please enter a valid IP address.") # type: ignore[attr-defined]
            main_window.hide_error_if_message_matches("Invalid input. Please enter a valid domain name or IP address.") # type: ignore[attr-defined]

        validation_passed = False
        error_message_to_show = ""

        if not user_input:
            error_message_to_show = "Input cannot be empty."
        elif requested_record_type.upper() == "PTR":
            if not is_valid_ip(user_input):
                error_message_to_show = "Invalid input for PTR record. Please enter a valid IP address."
            else:
                validation_passed = True
        else: # For other record types
            if not (is_valid_ip(user_input) or is_valid_domain(user_input)):
                error_message_to_show = "Invalid input. Please enter a valid domain name or IP address."
            else:
                validation_passed = True

        if not validation_passed:
            self.domain_entry.add_css_class("error") # type: ignore[attr-defined]
            if error_message_to_show:
                 show_global_error(self, error_message_to_show)
            self._set_loading_state(False, f"Idle - {error_message_to_show.split('.')[0]}.")
            return

        self.domain_entry.remove_css_class("error") # type: ignore[attr-defined]
        self._clear_error()

        self.current_dns_cancellable = Gio.Cancellable()
        task = Gio.Task.new(self, self.current_dns_cancellable, self._dns_lookup_done_cb, None)
        self.current_dns_task = task

        custom_dns_server = self.settings.get_string("custom-dns-server")
        # Create client here to pass to thread, or pass server string and let thread create it
        dns_client = DnsResolverClient(custom_dns_server=custom_dns_server or None)

        task_data = {
            "user_input": user_input,
            "requested_record_type": requested_record_type,
            "dns_client": dns_client # Pass the client instance
        }
        task.run_in_thread((lambda t, _, td, c: self._dns_lookup_thread_func(t, td, c)), task_data=task_data) # type: ignore

    def _dns_lookup_thread_func(self, task: Gio.Task, task_data: dict, cancellable: Gio.Cancellable) -> None:
        """Background thread function for DNS lookup."""
        user_input = task_data["user_input"]
        requested_record_type = task_data["requested_record_type"]
        dns_client: DnsResolverClient = task_data["dns_client"]

        try:
            if cancellable.is_cancelled():
                task.return_new_error_literal(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.CANCELLED.value, "Lookup cancelled before execution.")
                return

            # The actual blocking call
            result_data = dns_client.resolve(user_input, requested_record_type)

            if cancellable.is_cancelled():
                task.return_new_error_literal(DNS_LOOKUP_ERROR_DOMAIN, DnsLookupErrorType.CANCELLED.value, "Lookup cancelled after execution.")
                return

            # Store data needed by _handle_dns_lookup_success in the task result
            # along with the actual DNS records.
            task.return_value(GLib.Variant.new_tuple(
                GLib.Variant.new_python(result_data),
                GLib.Variant.new_string(user_input),
                GLib.Variant.new_string(requested_record_type),
                GLib.Variant.new_python(dns_client) # To get nameservers later
            ))

        except DnsClientError as e: # Catch specific DNS client errors
            # These errors are returned as DnsClientError objects directly.
            # The main thread callback will handle them.
            task.return_error(GLib.Error(str(e), DNS_LOOKUP_ERROR_DOMAIN, DnsClientError.quark().to_int())) # type: ignore
            # Using a generic error code for DnsClientError, specific type handling is in _dns_lookup_done_cb
        except Exception as e: # Catch any other unexpected errors
            logger.exception("DNSPage: Unexpected error in _dns_lookup_thread_func")
            # For other exceptions, return a generic GLib.Error
            # It's better to use a specific domain and code if possible.
            # For now, using a generic one.
            generic_error_quark = GLib.quark_from_string("generic-task-error")
            task.return_error(GLib.Error(f"Unexpected error: {e}", generic_error_quark, 0))


    def _dns_lookup_done_cb(self, _source_object: GObject.Object, _result: Gio.AsyncResult, _user_data: Any = None) -> None:
        """Callback for when the DNS lookup task is done."""
        task_being_processed = self.current_dns_task
        self.current_dns_task = None # Clear current task reference

        try:
            propagated_result = task_being_processed.propagate_value() # type: ignore

            # Unpack the GVariant tuple
            result_data_py, user_input, requested_record_type, dns_client_py = propagated_result.unpack()

            # Convert from GVariant back to Python types if necessary (GLib.Variant.new_python helps)
            result_data = result_data_py # Already Python list[dict]
            dns_client = dns_client_py # Already DnsResolverClient instance

            self._handle_dns_lookup_success(result_data, user_input, requested_record_type, dns_client)

        except GLib.Error as e:
            user_input = self._current_user_input or "unknown target" # Fallback if task_data wasn't set yet
            requested_record_type = self._current_requested_record_type or "unknown type" # Fallback
            custom_dns_server = self.settings.get_string("custom-dns-server") # For _handle_dns_lookup_exception
            dns_client_for_error = DnsResolverClient(custom_dns_server=custom_dns_server or None)


            if e.matches(GLib.quark_from_string(DNS_LOOKUP_ERROR_DOMAIN), DnsLookupErrorType.CANCELLED.value):
                logger.info(f"DNS lookup for {user_input} was cancelled.")
                self._set_loading_state(False, f"Lookup for {user_input} cancelled.")
                # Optionally clear results or leave them as they were before cancellation
                # self._clear_results() # If you want to clear on cancel
                # Ensure UI is consistent:
                if self.dns_status_row:
                    self.dns_status_row.set_subtitle(f"Lookup for {user_input} cancelled.") # type: ignore
            elif e.matches(GLib.quark_from_string(DNS_LOOKUP_ERROR_DOMAIN), DnsClientError.quark().to_int()): # type: ignore
                # Reconstruct the original DnsClientError if possible, or handle based on message
                # For simplicity, we pass the GLib.Error message to the handler
                # A more robust way would be to pass serialized error details via the GTask.
                logger.warning(f"DNS lookup failed with DnsClientError: {e.message}")
                # Attempt to map GLib.Error message back to specific DnsClientError type for _handle_dns_lookup_exception
                # This is a simplification. A proper way would involve serializing error types or using distinct error codes.
                if "NXDOMAIN" in e.message:
                    actual_error = DnsNxDomainError(e.message)
                elif "No answer" in e.message:
                    actual_error = DnsNoAnswerError(e.message)
                elif "Timeout" in e.message:
                     actual_error = DnsResolutionTimeoutError(e.message)
                else: # Fallback
                    actual_error = DnsGenericError(e.message)
                self._handle_dns_lookup_exception(actual_error, user_input, requested_record_type, dns_client_for_error)
            else:
                logger.error(f"DNS lookup failed with an unexpected GLib.Error: {e.message}")
                show_global_error(self, f"DNS lookup error: {e.message}")
                self._set_loading_state(False, f"Error: {e.message.splitlines()[0]}")
        except Exception as e_unhandled: # Catch any other Python exceptions from result handling
            logger.exception("DNSPage: Unexpected Python error in _dns_lookup_done_cb")
            show_global_error(self, f"An unexpected error occurred: {e_unhandled}")
            self._set_loading_state(False, "Unexpected error processing results.")
        finally:
            # Final UI state update, ensuring loading is false
            # The specific status message should have been set by success/error handlers
            self._set_loading_state(False)


    def _get_selected_record_type(self) -> str:
        """
        Get the currently selected DNS record type from the dropdown.

        :return: The selected record type as a string (e.g., "A", "MX").
        :rtype: str
        """
        model = self.dns_record_type_dropdown.get_model()  # type: ignore
        selected_index = self.dns_record_type_dropdown.get_selected()  # type: ignore
        return model.get_string(selected_index)  # type: ignore

    def _clear_error(self) -> None:
        """
        Clear any existing error messages from the UI.

        This includes hiding the main window's error banner and removing
        the 'error' CSS class from the domain entry row.
        """
        self.domain_entry.remove_css_class("error")  # type: ignore
        main_window = self.get_native()  # type: ignore
        if main_window and hasattr(main_window, "hide_error"):
            main_window.hide_error()  # type: ignore
        else:
            logger.warning("Could not find main window or hide_error method to clear error.")

    def _display_result(
        self,
        result_records: list[dict[str, Any]],
        domain_or_ip: str,
        record_type: str,
        dns_servers: Sequence[Any],  # Using typing.Sequence
    ) -> None:
        """
        Display the DNS lookup results in the UI.

        Clears previous results and populates the results container with new
        rows based on the lookup outcome.

        :param result_records: A list of parsed DNS record dictionaries.
        :type result_records: list[dict[str, Any]]
        :param domain_or_ip: The domain or IP that was queried.
        :type domain_or_ip: str
        :param record_type: The record type that was queried.
        :type record_type: str
        :param dns_servers: A sequence of DNS server addresses used for the query.
        :type dns_servers: collections.abc.Sequence[Any]
        """
        logger.debug(
            "Displaying %d results for %s (type %s) using servers %s.",
            len(result_records),
            domain_or_ip,
            record_type,
            dns_servers,
        )
        logger.info(
            "Query for %s, type %s, using servers %s, returned %d records.",
            domain_or_ip,
            record_type,
            dns_servers,
            len(result_records),
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
            elif record_type == "PTR":  # For domain name PTR queries (less common but possible)
                no_records_message = f"No PTR records found for {domain_or_ip}."
            no_records_row = Adw.ActionRow(title=no_records_message)
            no_records_row.set_selectable(False)
            self.dns_results_box_container.append(no_records_row)  # type: ignore
            # Update button sensitivity: No results, so disable
            if self.dns_clear_results_button:
                self.dns_clear_results_button.set_sensitive(False)
            if self.dns_copy_all_results_button:
                self.dns_copy_all_results_button.set_sensitive(False)
            return

        for record_data in result_records:
            row = self._create_record_row(record_data)
            if row:
                self.dns_results_box_container.append(row)  # type: ignore

        # Update button sensitivity: Results are present, so enable
        if self.dns_clear_results_button:
            self.dns_clear_results_button.set_sensitive(True)
        if self.dns_copy_all_results_button:
            self.dns_copy_all_results_button.set_sensitive(True)

    # --- Helper methods for building record rows ---

    def _create_copy_button(self, text_to_copy: str, tooltip_text: str, widget_for_clipboard: Gtk.Widget) -> Gtk.Button:
        """
        Create a :class:`Gtk.Button` for copying text.

        :param text_to_copy: The text to be copied when the button is clicked.
        :type text_to_copy: str
        :param tooltip_text: The tooltip text for the button.
        :type tooltip_text: str
        :param widget_for_clipboard: The :class:`Gtk.Widget` from which to get the clipboard.
        :type widget_for_clipboard: Gtk.Widget
        :return: A new :class:`Gtk.Button` configured for copying.
        :rtype: Gtk.Button
        """
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
        """
        Create a basic :class:`Adw.ActionRow` with title, subtitle, and optional icon.

        :param name: The title for the ActionRow, typically the record name.
        :type name: str
        :param record_type_label: The string representation of the record type (e.g., "A", "MX").
        :type record_type_label: str
        :param base_subtitle_text: Base text for the subtitle (e.g., class and TTL info).
        :type base_subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :return: A new :class:`Adw.ActionRow`.
        :rtype: Adw.ActionRow
        """
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
        """
        Add a standard suffix box (label, copy value button, copy summary button) to an :class:`Adw.ActionRow`.

        :param row: The :class:`Adw.ActionRow` to add suffixes to.
        :type row: Adw.ActionRow
        :param main_value_text: The main value to display as a label and for the value copy button.
        :type main_value_text: str
        :param main_value_tooltip_prefix: The prefix for the tooltip of the value copy button.
        :type main_value_tooltip_prefix: str
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        """
        value_label = Gtk.Label(
            label=main_value_text,
            halign=Gtk.Align.START,
            selectable=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        # suffix_box is removed. Widgets will be added directly to the row.

        copy_value_button = self._create_copy_button(
            main_value_text, f"{main_value_tooltip_prefix}: {main_value_text}", row
        )
        copy_full_summary_button = self._create_copy_button(full_summary_text, "Copy Full Record Summary", row)

        row.add_suffix(value_label)  # type: ignore
        row.add_suffix(copy_value_button)  # type: ignore
        row.add_suffix(copy_full_summary_button)  # type: ignore

    def _create_base_expander_row(
        self, name: str, subtitle_text: str, icon_name: Optional[str], full_summary_text: str
    ) -> Adw.ExpanderRow:
        """
        Create a basic :class:`Adw.ExpanderRow` with title, subtitle, icon, and a full summary copy button.

        :param name: The title for the ExpanderRow.
        :type name: str
        :param subtitle_text: The subtitle for the ExpanderRow.
        :type subtitle_text: str
        :param icon_name: Optional icon name for the prefix of the row.
        :type icon_name: Optional[str]
        :param full_summary_text: The full summary text for the summary copy button.
        :type full_summary_text: str
        :return: A new :class:`Adw.ExpanderRow`.
        :rtype: Adw.ExpanderRow
        """
        row = Adw.ExpanderRow(title=name, subtitle=subtitle_text)  # type: ignore
        if icon_name:
            row.add_prefix(Gtk.Image(icon_name=icon_name))  # type: ignore

        copy_full_button = self._create_copy_button(full_summary_text, "Copy Full Record Summary", row)
        row.add_suffix(copy_full_button)  # type: ignore
        # row.set_expanded(True) # Decided by caller, as TXT/SOA might be empty initially
        return row

    def _add_expander_detail_row(
        self,
        expander_row: Adw.ExpanderRow,
        title: Optional[str],
        value_text: str,
        copy_tooltip_prefix: str,
        is_value_primary_content: bool = False,
    ):
        """
        Add a detail row (:class:`Adw.ActionRow`) to an :class:`Adw.ExpanderRow`.

        :param expander_row: The :class:`Adw.ExpanderRow` to add the detail row to.
        :type expander_row: Adw.ExpanderRow
        :param title: Optional title for the detail :class:`Adw.ActionRow`.
        :type title: Optional[str]
        :param value_text: The value text to display in the detail row.
        :type value_text: str
        :param copy_tooltip_prefix: The prefix for the tooltip of the copy button for the value.
        :type copy_tooltip_prefix: str
        :param is_value_primary_content: If ``True``, ``value_text`` is the primary content (e.g., TXT segments).
                                         Otherwise, it's a suffix to the title (e.g., SOA fields).
        :type is_value_primary_content: bool
        """
        detail_row = Adw.ActionRow(title=title if title else None)  # type: ignore

        value_label = Gtk.Label(
            label=value_text,
            halign=Gtk.Align.START,
            selectable=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        copy_button = self._create_copy_button(value_text, f"{copy_tooltip_prefix}: {value_text}", expander_row)

        # content_box is removed. Widgets will be added directly to the detail_row.
        if is_value_primary_content:  # For TXT segments where the value is the main content of the row
            detail_row.add_prefix(value_label)  # type: ignore
            detail_row.add_prefix(copy_button)  # type: ignore
        else:  # For SOA fields where title is present and value is a suffix
            detail_row.add_suffix(value_label)  # type: ignore
            detail_row.add_suffix(copy_button)  # type: ignore

        detail_row.set_selectable(False)
        expander_row.add_row(detail_row)  # type: ignore

    # --- Modified _build_*_record_row methods ---

    def _build_address_record_row(  # type: ignore[type-arg]
        self, record_data: dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        """
        Build a UI row for an A or AAAA DNS record.

        :param record_data: Parsed record data.
        :type record_data: dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("A" or "AAAA").
        :type record_type: str
        :return: An :class:`Adw.ActionRow` for the record.
        :rtype: Adw.ActionRow
        """
        row = self._create_base_action_row(name, record_type, base_subtitle, "network-wired-symbolic")
        address_value = str(record_data.get("address", "N/A"))
        summary_text = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {address_value}"
        )
        self._add_standard_suffix_box_to_row(row, address_value, "Copy Address", summary_text)
        return row

    def _build_cname_ns_ptr_record_row(  # type: ignore[type-arg]
        self, record_data: dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        """
        Build a UI row for CNAME, NS, or PTR DNS records.

        :param record_data: Parsed record data.
        :type record_data: dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("CNAME", "NS", "PTR").
        :type record_type: str
        :return: An :class:`Adw.ActionRow` for the record.
        :rtype: Adw.ActionRow
        """
        icon_name = "emblem-shared-symbolic"  # Default for CNAME
        if record_type == "NS":
            icon_name = "network-server-symbolic"
        elif record_type == "PTR":
            icon_name = "system-search-symbolic"

        row = self._create_base_action_row(name, record_type, base_subtitle, icon_name)
        target_value = str(record_data.get("target", "N/A"))
        summary_text = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {target_value}"
        )
        self._add_standard_suffix_box_to_row(row, target_value, "Copy Target", summary_text)
        return row

    def _build_generic_data_record_row(  # type: ignore[type-arg]
        self, record_data: dict[str, Any], name: str, base_subtitle: str, record_type: str
    ) -> Adw.ActionRow:
        """
        Build a UI row for generic DNS records that have a 'data' field.

        :param record_data: Parsed record data.
        :type record_data: dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string.
        :type record_type: str
        :return: An :class:`Adw.ActionRow` for the record.
        :rtype: Adw.ActionRow
        """
        row = self._create_base_action_row(name, record_type, base_subtitle, "help-question-symbolic")
        data_value = str(record_data.get("data", "N/A"))
        summary_text = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} {record_type} {data_value}"
        self._add_standard_suffix_box_to_row(row, data_value, "Copy Data", summary_text)
        return row

    def _build_mx_record_row(  # type: ignore[type-arg]
        self, record_data: dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:  # record_type is "MX"
        """
        Build a UI row for an MX DNS record.

        :param record_data: Parsed record data.
        :type record_data: dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("MX").
        :type record_type: str
        :return: An :class:`Adw.ExpanderRow` for the record.
        :rtype: Adw.ExpanderRow
        """
        exchange_value = str(record_data.get("exchange", "N/A"))
        preference_value = str(record_data.get("preference", "N/A"))
        summary_mx = (
            f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} MX {preference_value} {exchange_value}"
        )

        row = self._create_base_expander_row(
            name, f"MX Record ({base_subtitle})", "mail-send-receive-symbolic", summary_mx
        )

        # MX detail row is specific
        exchange_value_label = Gtk.Label(
            label=exchange_value,
            halign=Gtk.Align.FILL,
            hexpand=True,
            selectable=True,
            wrap=False,
            lines=1,
            ellipsize=Pango.EllipsizeMode.END,
        )
        copy_button_exchange = self._create_copy_button(exchange_value, f"Copy Exchange: {exchange_value}", row)
        # mx_detail_row_title_box removed

        mx_detail_row = Adw.ActionRow(subtitle=f"Preference: {preference_value}")  # type: ignore
        mx_detail_row.add_prefix(exchange_value_label)  # type: ignore
        mx_detail_row.add_prefix(copy_button_exchange)  # type: ignore
        mx_detail_row.set_selectable(
            True
        )  # Allow selecting to copy preference if needed, though not directly copyable via button
        row.add_row(mx_detail_row)  # type: ignore
        row.set_expanded(True)
        return row

    def _build_txt_record_row(  # type: ignore[type-arg]
        self, record_data: dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:  # record_type is "TXT"
        """
        Build a UI row for a TXT DNS record.

        :param record_data: Parsed record data.
        :type record_data: dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("TXT").
        :type record_type: str
        :return: An :class:`Adw.ExpanderRow` for the record.
        :rtype: Adw.ExpanderRow
        """
        texts = record_data.get("texts", [])
        texts_str_summary = " ".join([f'"{s}"' for s in texts])
        summary_txt = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} TXT {texts_str_summary}"

        row = self._create_base_expander_row(
            name, f"TXT Records ({base_subtitle})", "document-properties-symbolic", summary_txt
        )

        if not texts:
            no_text_row = Adw.ActionRow(title="No text data.", selectable=False)  # type: ignore
            row.add_row(no_text_row)  # type: ignore
        else:
            for text_string in texts:
                self._add_expander_detail_row(
                    row, None, text_string, "Copy Text Segment", is_value_primary_content=True
                )
        row.set_expanded(bool(texts))
        return row

    def _build_soa_record_row(  # type: ignore[type-arg]
        self, record_data: dict[str, Any], name: str, base_subtitle: str
    ) -> Adw.ExpanderRow:  # record_type is "SOA"
        """
        Build a UI row for an SOA DNS record.

        :param record_data: Parsed record data.
        :type record_data: dict[str, Any]
        :param name: Record name.
        :type name: str
        :param base_subtitle: Base subtitle string (class, TTL).
        :type base_subtitle: str
        :param record_type: Record type string ("SOA").
        :type record_type: str
        :return: An :class:`Adw.ExpanderRow` for the record.
        :rtype: Adw.ExpanderRow
        """
        mname_val = str(record_data.get("mname", "N/A"))
        rname_val = str(record_data.get("rname", "N/A"))
        serial_val = str(record_data.get("serial", "N/A"))
        refresh_val = str(record_data.get("refresh", "N/A"))
        retry_val = str(record_data.get("retry", "N/A"))
        expire_val = str(record_data.get("expire", "N/A"))
        minimum_val = str(record_data.get("minimum", "N/A"))

        summary_soa = f"{name} {record_data.get('ttl', '')} {record_data.get('class', '')} SOA {mname_val} {rname_val} {serial_val} {refresh_val} {retry_val} {expire_val} {minimum_val}"
        row = self._create_base_expander_row(
            name, f"SOA Record ({base_subtitle})", "document-settings-symbolic", summary_soa
        )

        soa_fields = [
            ("MNAME", mname_val),
            ("RNAME", rname_val),
            ("Serial", serial_val),
            ("Refresh", refresh_val),
            ("Retry", retry_val),
            ("Expire", expire_val),
            ("Minimum TTL", minimum_val),
        ]
        for field_name_str, field_value in soa_fields:
            self._add_expander_detail_row(row, field_name_str, field_value, f"Copy {field_name_str}")
        row.set_expanded(True)
        return row

    def _create_record_row(self, record_data: dict[str, Any]) -> Optional[Gtk.Widget]:
        """
        Create a UI row for a single DNS record dictionary.

        Delegates to specific ``_build_*_record_row`` methods based on record type.

        :param record_data: A dictionary containing the parsed data for one DNS record.
        :type record_data: dict[str, Any]
        :return: A :class:`Gtk.Widget` (typically an :class:`Adw.ActionRow` or \
                 :class:`Adw.ExpanderRow`) representing the record, or ``None`` if \
                 the record type is unknown or cannot be displayed.
        :rtype: Optional[Gtk.Widget]
        """
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
            return self._build_generic_data_record_row(record_data, name, base_subtitle, record_type)  # Renamed
        else:
            logger.warning(
                "Could not create row for unknown record_data type or missing data field: %s (Type: %s)",
                record_data,
                record_type,
            )
            return None

    def trigger_lookup(self) -> None:
        """
        Programmatically trigger the DNS 'Lookup' action.

        This is typically called via a keyboard shortcut. It simulates a click
        on the 'Lookup' button if it's available and sensitive.
        """
        logger.debug("DNS lookup triggered by shortcut.")
        if self.dns_apply_button and self.dns_apply_button.get_sensitive():  # type: ignore
            self.dns_apply_button.activate()  # type: ignore[attr-defined]
        else:
            logger.warning("DNS lookup button not available or not sensitive, cannot trigger lookup.")
