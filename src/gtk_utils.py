"""
GTK Utility Functions for the Woes application.

This module provides helper functions for creating common GTK widgets
and performing related tasks, aiming to reduce code duplication across
different pages of the application.
"""

import logging
from typing import Optional, List

from gi.repository import Gtk, Pango, Adw, Gdk

logger = logging.getLogger(__name__)


def _copy_to_clipboard(text: str, widget: Gtk.Widget) -> None:
    """
    Copy the given text to the clipboard.

    :param text: The text to copy.
    :type text: str
    :param widget: The Gtk.Widget from which to get the clipboard.
                   Needed for context, e.g., to get the display.
    :type widget: Gtk.Widget
    """
    try:
        clipboard = Gdk.Display.get_default().get_clipboard()
        if clipboard:
            clipboard.set_text(text)
            logger.info("Copied to clipboard: %s", text[:100] + "..." if len(text) > 100 else text)
        else:
            logger.warning("Could not get default clipboard.")
    except Exception as e:
        logger.exception(f"Error copying to clipboard: {e}")


def create_copy_button(text_to_copy: str, tooltip_text: str, widget_for_clipboard: Gtk.Widget) -> Gtk.Button:
    """
    Create a Gtk.Button for copying text.

    :param text_to_copy: The text to be copied when the button is clicked.
    :type text_to_copy: str
    :param tooltip_text: The tooltip text for the button.
    :type tooltip_text: str
    :param widget_for_clipboard: The Gtk.Widget that provides context for clipboard operations.
    :type widget_for_clipboard: Gtk.Widget
    :return: A new Gtk.Button configured for copying.
    :rtype: Gtk.Button
    """
    button = Gtk.Button.new_from_icon_name("content-copy-symbolic")
    button.set_valign(Gtk.Align.CENTER)
    button.set_tooltip_text(tooltip_text)
    button.connect("clicked", lambda _btn: _copy_to_clipboard(text_to_copy, widget_for_clipboard))
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
    """
    Create a Gtk.Label with specified styling and properties.
    """
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
        if lines == 1 and ellipsize is None:
            label.set_ellipsize(Pango.EllipsizeMode.END)
    return label


def create_detail_action_row(
    title: str,
    subtitle: Optional[str] = None,
    icon_name: Optional[str] = None,
    value_text: Optional[str] = None,
    value_font_desc: Optional[Pango.FontDescription] = None,
    copy_value_tooltip: Optional[str] = None,
    full_summary_text_for_copy: Optional[str] = None,
    parent_widget_for_clipboard: Gtk.Widget
) -> Adw.ActionRow:
    """
    Create an Adw.ActionRow with standardized title, subtitle, icon, value display, and copy buttons.
    """
    row = Adw.ActionRow(title=title, subtitle=subtitle if subtitle else None)
    row.set_selectable(False)

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
                lines=-1
            )
        else:
            value_label = Gtk.Label(
                label=value_text,
                halign=Gtk.Align.START,
                hexpand=False,
                selectable=True,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                ellipsize=Pango.EllipsizeMode.END,
                lines=-1
            )
        row.add_suffix(value_label)

        if copy_value_tooltip:
            copy_value_button = create_copy_button(
                text_to_copy=value_text,
                tooltip_text=copy_value_tooltip,
                widget_for_clipboard=parent_widget_for_clipboard
            )
            row.add_suffix(copy_value_button)

    if full_summary_text_for_copy:
        copy_summary_button = create_copy_button(
            text_to_copy=full_summary_text_for_copy,
            tooltip_text="Copy Full Record Summary",
            widget_for_clipboard=parent_widget_for_clipboard
        )
        row.add_suffix(copy_summary_button)

    return row


def create_expander_row(
    title: str,
    subtitle: Optional[str] = None,
    icon_name: Optional[str] = None,
    header_suffixes: Optional[List[Gtk.Widget]] = None,
    initially_expanded: bool = True
) -> Adw.ExpanderRow:
    """
    Create an Adw.ExpanderRow with standardized title, subtitle, icon, and optional header suffixes.
    """
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
    title: Optional[str],
    value_text: str,
    copy_tooltip_prefix: str,
    parent_widget_for_clipboard: Gtk.Widget,
    value_font_desc: Optional[Pango.FontDescription] = None,
    is_value_primary_content: bool = False
) -> None:
    """
    Adds a standardized detail row (Adw.ActionRow) to a given Adw.ExpanderRow.
    """
    detail_action_row = Adw.ActionRow(title=title if title and not is_value_primary_content else None)
    detail_action_row.set_selectable(False)

    label_value: Gtk.Label
    if value_font_desc:
        label_value = create_styled_label(
            text=value_text,
            font_desc=value_font_desc,
            halign=Gtk.Align.START,
            hexpand=not is_value_primary_content,
            selectable=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            ellipsize=Pango.EllipsizeMode.END,
            lines=-1
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
            lines=-1
        )

    copy_button_for_value = create_copy_button(
        text_to_copy=value_text,
        tooltip_text=f"{copy_tooltip_prefix}: {value_text}",
        widget_for_clipboard=parent_widget_for_clipboard
    )

    if is_value_primary_content:
        detail_action_row.add_prefix(label_value)
        detail_action_row.add_prefix(copy_button_for_value)
    else:
        detail_action_row.add_suffix(label_value)
        detail_action_row.add_suffix(copy_button_for_value)

    expander_row.add_row(detail_action_row)
