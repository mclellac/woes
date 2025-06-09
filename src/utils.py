"""General utility functions for the Woes application."""
import logging
import ipaddress
import re
from urllib.parse import urlparse
from typing import Tuple, List, Optional # Added Optional

import gi
from gi.repository import Gtk, GtkSource, Adw

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")
gi.require_version("Adw", "1")

logger = logging.getLogger(__name__)


def create_source_view(language_name: str = "txt") -> Tuple[GtkSource.View, GtkSource.Buffer]:
    """Create and configure a GtkSource.View and its associated GtkSource.Buffer.

    Sets up common properties for the source view like line numbers, monospace font,
    wrap mode, auto-indent, and tab behavior. Configures syntax highlighting
    for the specified language.

    :param language_name: The language ID for syntax highlighting
                          (e.g., "yaml", "python", "txt"). Defaults to "txt".
    :type language_name: str
    :return: A tuple containing the configured :class:`GtkSource.View` and
             :class:`GtkSource.Buffer`.
    :rtype: Tuple[GtkSource.View, GtkSource.Buffer]
    """
    language_manager = GtkSource.LanguageManager.get_default()
    if language_name is None:  # Ensure fallback even if None is explicitly passed
        language_name = "txt"
    language = language_manager.get_language(language_name)

    if language is None:
        logger.warning(
            "GtkSourceView language '%s' not found. Falling back to a plain buffer.",
            language_name,
        )
        source_buffer = GtkSource.Buffer()
    else:
        source_buffer = GtkSource.Buffer.new_with_language(language)
        source_buffer.set_highlight_syntax(True)

    source_view = GtkSource.View.new_with_buffer(source_buffer)
    source_view.set_show_line_numbers(True)
    source_view.set_monospace(True)
    source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    source_view.set_auto_indent(True)
    source_view.set_smart_backspace(True)
    source_view.set_indent_on_tab(True)
    source_view.set_tab_width(4)
    source_view.set_insert_spaces_instead_of_tabs(True)
    source_view.set_highlight_current_line(True)
    source_view.set_vexpand(True)

    return source_view, source_buffer


def show_global_error(widget: Gtk.Widget, message: str):
    """Display a global error message using the main window's error banner.

    :param widget: A :class:`Gtk.Widget` (typically 'self' from a page object)
                   to get the native window.
    :type widget: Gtk.Widget
    :param message: The error message string to display.
    :type message: str
    """
    try:
        main_window = widget.get_native() # type: ignore
        if main_window and hasattr(main_window, 'show_error'):
            main_window.show_error(message)
            logger.error(f"Global error displayed via main window: {message}")
        else:
            logger.warning(
                "Could not find main window or show_error method to display global error: %s",
                message
            )
    except Exception as e: # pylint: disable=broad-except
        logger.exception(
            "An unexpected error occurred while trying to show global error '%s': %s",
            message, e
        )


def show_global_toast(
    widget: Gtk.Widget,
    message: str,
    timeout: int = 2,
    priority: Adw.ToastPriority = Adw.ToastPriority.NORMAL
):
    """Display a global toast message using the main window's toast overlay.

    :param widget: A :class:`Gtk.Widget` (typically 'self' from a page object)
                   to get the native window.
    :type widget: Gtk.Widget
    :param message: The toast message string to display.
    :type message: str
    :param timeout: Duration in seconds for the toast to be visible.
                    Defaults to 2.
    :type timeout: int
    :param priority: The priority of the toast.
                     Defaults to :attr:`Adw.ToastPriority.NORMAL`.
    :type priority: Adw.ToastPriority
    """
    try:
        main_window = widget.get_native() # type: ignore
        if main_window and hasattr(main_window, 'show_toast'):
            main_window.show_toast(message, priority=priority, timeout=timeout)
            logger.info(f"Global toast shown via main window: {message}")
        else:
            logger.warning(
                "Could not find main window or show_toast method to display global toast: %s",
                message
            )
    except Exception as e: # pylint: disable=broad-except
        logger.exception(
            "An unexpected error occurred while trying to show global toast '%s': %s",
            message, e
        )


def is_valid_ip(address: str) -> bool:
    """Check if the given string is a valid IPv4 or IPv6 address.

    :param address: The string to validate.
    :type address: str
    :return: ``True`` if the string is a valid IP address, ``False`` otherwise.
    :rtype: bool
    """
    if not address or not isinstance(address, str):
        return False
    try:
        ipaddress.ip_address(address)
        return True
    except ValueError:
        return False

def is_valid_domain(domain: str) -> bool:
    """Check if the given string is a syntactically valid domain name (ASCII).

    This validation is based on typical ASCII domain name rules (LDH labels).
    It does not perform DNS resolution or check for IDN (Internationalized
    Domain Names) specific rules beyond basic structure.

    :param domain: The string to validate.
    :type domain: str
    :return: ``True`` if the string is a syntactically valid domain name,
             ``False`` otherwise.
    :rtype: bool
    """
    if not domain or not isinstance(domain, str):
        return False
    # Regex for domain names:
    # - Each label (part between dots) is 1-63 chars.
    # - Labels consist of LDH (letters, digits, hyphen).
    # - Labels do not start or end with a hyphen.
    # - The TLD (last label) must be at least 2 chars and all alphabetic.
    # This regex is a common one for ASCII domain names.
    # It allows for subdomains and ensures TLD is alphabetic.
    domain_regex = re.compile(
        r"^(?:[a-zA-Z0-9]"  # First character of a label
        r"(?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)"  # Subsequent characters of a label, then a dot
        r"+[a-zA-Z]{2,63}$"  # TLD (all letters, 2-63 chars)
    )
    # Using fullmatch to ensure the entire string conforms.
    return bool(domain_regex.fullmatch(domain))


def is_valid_url(url: str, schemes: Optional[List[str]] = None) -> bool:
    """Check if the given string is a syntactically valid URL with specific schemes.

    :param url: The string to validate.
    :type url: str
    :param schemes: A list of allowed schemes (e.g., ``['http', 'https']``).
                    If ``None``, defaults to ``['http', 'https']``.
                    If an empty list is provided, any scheme is effectively allowed
                    as long as one is present in the URL.
    :type schemes: Optional[List[str]]
    :return: ``True`` if the string is a valid URL with an allowed scheme
             (or any scheme if ``schemes`` is empty), ``False`` otherwise.
    :rtype: bool
    """
    if schemes is None: # Default to http and https if not provided
        schemes = ['http', 'https']

    if not url or not isinstance(url, str):
        return False
    try:
        parsed_url = urlparse(url)
        # A URL must have a scheme and a network location (netloc) to be valid.
        if not (parsed_url.scheme and parsed_url.netloc):
            return False
        # If a non-empty list of schemes is provided, the URL's scheme must be in it.
        if schemes and parsed_url.scheme not in schemes:
            return False
        return True
    except ValueError: # urlparse can raise ValueError for some malformed URLs, though it's rare
        return False
