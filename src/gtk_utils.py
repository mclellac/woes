"""
GTK Utility Functions for the Woes application.

This module provides helper functions for creating common GTK widgets
and performing related tasks, aiming to reduce code duplication across
different pages of the application. These utilities include functions for
copying text to the clipboard, creating styled labels, and constructing
standardized :class:`Adw.ActionRow` and :class:`Adw.ExpanderRow` widgets
for displaying details.
"""

import logging
from typing import Optional, list # Using Python's built-in list for hinting

from gi.repository import Gtk, Pango, Adw, Gdk

logger = logging.getLogger(__name__)


def _copy_to_clipboard(text: str) -> None:
    """
    Copy the given text to the clipboard.

    This function uses the default GDK display to access the clipboard.

    :param text: The text to copy.
    """
    try:
        clipboard = Gdk.Display.get_default().get_clipboard()
        if clipboard:
            clipboard.set_text(text)
            logger.info("Copied to clipboard: %s", text[:100] + "..." if len(text) > 100 else text)
        else:
            logger.warning("Could not get default clipboard.")
    except Exception as e:
        logger.exception("Error copying text to clipboard: %s", e)


def create_copy_button(text_to_copy: str, tooltip_text: str) -> Gtk.Button:
    """
    Create a Gtk.Button configured for copying the provided text to the clipboard.

    The button uses the "content-copy-symbolic" icon and has a "flat" style.

    :param text_to_copy: The text that will be copied when the button is clicked.
    :param tooltip_text: The text to display as a tooltip for the button.
    :return: A new :class:`Gtk.Button` for copying text.
    """
    button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
    button.set_valign(Gtk.Align.CENTER)
    button.set_tooltip_text(tooltip_text)
    # No longer need widget_for_clipboard for _copy_to_clipboard
    button.connect("clicked", lambda _btn: _copy_to_clipboard(text_to_copy))
    button.get_style_context().add_class("flat")
    return button


def create_styled_label(
    text: str,
    font_desc: Pango.FontDescription,
    wrap_mode: Optional[Pango.WrapMode] = None,
    ellipsize: Optional[Pango.EllipsizeMode] = None,
    lines: Optional[int] = None,
    halign: Gtk.Align = Gtk.Align.FILL,
    hexpand: bool = True,
    selectable: bool = True,
    xalign: float = 0.0,
) -> Gtk.Label:
    """Create a Gtk.Label with specified styling and properties."""
    label = Gtk.Label(
        label=text,
        halign=halign,
        hexpand=hexpand,
        selectable=selectable,
        xalign=xalign,
    )
    label.override_font(font_desc)

    if wrap_mode is not None:
        label.set_wrap(True)
        label.set_wrap_mode(wrap_mode)

    if ellipsize is not None:
        label.set_ellipsize(ellipsize)

    if lines is not None:
        label.set_lines(lines)
        if lines == 1 and ellipsize is None: # Auto-ellipsize for single line if not specified
            label.set_ellipsize(Pango.EllipsizeMode.END)
    return label


def create_detail_action_row(
    title: str,
    # parent_widget_for_clipboard: Gtk.Widget, # No longer needed
    subtitle: Optional[str] = None,
    icon_name: Optional[str] = None,
    value_text: Optional[str] = None,
    value_font_desc: Optional[Pango.FontDescription] = None,
    copy_value_tooltip: Optional[str] = None,
    full_summary_text_for_copy: Optional[str] = None,
) -> Adw.ActionRow:
    """Create an Adw.ActionRow with title, subtitle, icon, value, and copy buttons."""
    row = Adw.ActionRow(title=title, subtitle=subtitle if subtitle else None)
    row.set_selectable(False) # Generally, action rows themselves aren't selectable if they have interactive suffixes

    if icon_name:
        row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))

    if value_text is not None:
        value_label: Gtk.Label
        if value_font_desc:
            value_label = create_styled_label(
                text=value_text,
                font_desc=value_font_desc,
                halign=Gtk.Align.START,
                hexpand=False,
                selectable=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                ellipsize=Pango.EllipsizeMode.END,
                lines=-1, # Allow multi-line for value text
            )
        else:
            value_label = Gtk.Label(
                label=value_text,
                halign=Gtk.Align.START,
                hexpand=False,
                selectable=True,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                ellipsize=Pango.EllipsizeMode.END, # Ellipsize if it exceeds available space
                lines=-1, # Allow multi-line
            )
        row.add_suffix(value_label)

        if copy_value_tooltip:
            copy_value_button = create_copy_button(
                text_to_copy=value_text, # Text to copy is the value itself
                tooltip_text=copy_value_tooltip,
                # widget_for_clipboard is no longer needed by create_copy_button
            )
            row.add_suffix(copy_value_button)

    if full_summary_text_for_copy: # This implies a different text to copy than just value_text
        copy_summary_button = create_copy_button(
            text_to_copy=full_summary_text_for_copy,
            tooltip_text="Copy Full Record Summary",
        )
        row.add_suffix(copy_summary_button)

    return row


def create_expander_row(
    title: str,
    subtitle: Optional[str] = None,
    icon_name: Optional[str] = None,
    header_suffixes: Optional[list[Gtk.Widget]] = None, # Changed from List to list
    initially_expanded: bool = True,
) -> Adw.ExpanderRow:
    """Create an Adw.ExpanderRow with title, subtitle, icon, and optional header suffixes."""
    expander = Adw.ExpanderRow(title=title, subtitle=subtitle if subtitle else None)
    expander.set_expanded(initially_expanded)

    if icon_name:
        expander.add_prefix(Gtk.Image.new_from_icon_name(icon_name))

    if header_suffixes:
        for suffix_widget in header_suffixes:
            expander.add_suffix(suffix_widget)

    return expander


def add_detail_to_expander(
    expander_row: Adw.ExpanderRow,
    title: Optional[str], # Title for the detail row itself
    value_text: str,
    copy_tooltip_prefix: str, # Used to build the tooltip for copying the value_text
    # parent_widget_for_clipboard: Gtk.Widget, # No longer needed
    value_font_desc: Optional[Pango.FontDescription] = None,
    is_value_primary_content: bool = False, # If true, value_text is placed in title area (prefix)
) -> None:
    """Add a standardized detail row (Adw.ActionRow) to a given Adw.ExpanderRow."""
    detail_action_row = Adw.ActionRow(title=title if title and not is_value_primary_content else None)
    detail_action_row.set_selectable(False)

    label_value: Gtk.Label
    if value_font_desc:
        label_value = create_styled_label(
            text=value_text,
            font_desc=value_font_desc,
            halign=Gtk.Align.START,
            hexpand=not is_value_primary_content, # Only expand if it's suffix content
            selectable=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            ellipsize=Pango.EllipsizeMode.END,
            lines=-1,
        )
    else:
        label_value = Gtk.Label(
            label=value_text,
            halign=Gtk.Align.START,
            hexpand=not is_value_primary_content,
            selectable=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            ellipsize=Pango.EllipsizeMode.END,
            lines=-1,
        )

    # Tooltip for copying just this specific value
    specific_copy_tooltip = f"{copy_tooltip_prefix}: {value_text[:30]}{'...' if len(value_text) > 30 else ''}"
    copy_button_for_value = create_copy_button(
        text_to_copy=value_text,
        tooltip_text=specific_copy_tooltip,
    )

    if is_value_primary_content:
        # For primary content, title in Adw.ActionRow is usually None or a generic prefix
        # and the main content (label_value) is added as a prefix.
        detail_action_row.add_prefix(label_value)
        detail_action_row.add_suffix(copy_button_for_value) # Copy button still as suffix
    else:
        # For key-value style, title is set, value_label is a suffix.
        detail_action_row.add_suffix(label_value)
        detail_action_row.add_suffix(copy_button_for_value)

    expander_row.add_row(detail_action_row)
