"""A collection of general-purpose utility and helper functions for the Woes application.

This module includes functions for tasks such as UI helper functions
(e.g., creating GtkSource.View, showing global notifications), input validation
(IPs, domains, URLs), and potentially other application-wide utilities.
"""
import logging
import ipaddress
import re
from urllib.parse import urlparse
from typing import Tuple, List, Optional, Any # Removed 'Any' as it was not used

import gi
from gi.repository import Gtk, GtkSource, Adw

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")
gi.require_version("Adw", "1")

logger = logging.getLogger(__name__)


def create_source_view(
        language_name: str = "text"
) -> Tuple[GtkSource.View, GtkSource.Buffer]:
    """Creates and configures a :class:`GtkSource.View` with its buffer.

    Sets up common properties for the source view such as visibility of line
    numbers, monospace font, word/character wrap mode, automatic indentation,
    and tab behavior (inserting spaces instead of tabs). It also configures
    syntax highlighting based on the provided language name.

    If the specified language is not found, it logs a warning and falls back
    to a plain buffer without syntax highlighting.

    :param language_name: The language ID for syntax highlighting
                          (e.g., "yaml", "python", "text"). Defaults to "text".
    :type language_name: str
    :return: A tuple containing the configured :class:`GtkSource.View`
             and its associated :class:`GtkSource.Buffer`.
    :rtype: Tuple[GtkSource.View, GtkSource.Buffer]
    """
    language_manager = GtkSource.LanguageManager.get_default()
    # Ensure fallback even if None is explicitly passed or if language_name is empty
    effective_language_name = language_name if language_name else "text"
    language = language_manager.get_language(effective_language_name)

    if language is None:
        logger.warning("GtkSource language '%s' not found. Falling back to a "
                       "plain buffer.", effective_language_name)
        source_buffer = GtkSource.Buffer() # Create a buffer without a language
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
    source_view.set_vexpand(True) # Make the view expand vertically

    return source_view, source_buffer


def show_global_error(widget: Gtk.Widget, message: str) -> None:
    """Displays a global error message using the main window's error banner.

    This function attempts to find the top-level window containing the given
    widget and calls a `show_error` method on it, if available.

    :param widget: A :class:`Gtk.Widget` (typically 'self' from a page object)
                   used to get the native (top-level) window.
    :type widget: Gtk.Widget
    :param message: The error message string to display.
    :type message: str
    :return: None
    :rtype: None
    """
    try:
        main_window = widget.get_native()
        if main_window and hasattr(main_window, 'show_error'):
            main_window.show_error(message) # type: ignore
            logger.error("Global error displayed via main window: %s", message)
        else:
            logger.warning("Could not find main window or show_error method "
                           "to display global error: %s", message)
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("An unexpected error occurred while trying to show "
                         "global error '%s': %s", message, e)


def show_global_toast(
    widget: Gtk.Widget,
    message: str,
    timeout: int = 2,
    priority: Adw.ToastPriority = Adw.ToastPriority.NORMAL
) -> None:
    """Displays a global toast message using the main window's toast overlay.

    Attempts to find the top-level window containing the given widget and
    calls a `show_toast` method on it, if available.

    :param widget: A :class:`Gtk.Widget` (typically 'self' from a page object)
                   used to get the native (top-level) window.
    :type widget: Gtk.Widget
    :param message: The toast message string to display.
    :type message: str
    :param timeout: Duration in seconds for the toast to be visible.
                    Defaults to 2.
    :type timeout: int
    :param priority: The priority of the toast (e.g.,
                     :attr:`Adw.ToastPriority.NORMAL`,
                     :attr:`Adw.ToastPriority.HIGH`).
                     Defaults to :attr:`Adw.ToastPriority.NORMAL`.
    :type priority: Adw.ToastPriority
    :return: None
    :rtype: None
    """
    try:
        main_window = widget.get_native()
        if main_window and hasattr(main_window, 'show_toast'):
            main_window.show_toast( # type: ignore
                message, priority=priority, timeout=timeout)
            logger.info("Global toast shown via main window: %s", message)
        else:
            logger.warning("Could not find main window or show_toast method "
                           "to display global toast: %s", message)
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("An unexpected error occurred while trying to show "
                         "global toast '%s': %s", message, e)


def is_valid_ip(address: str) -> bool:
    """Checks if the given string is a valid IPv4 or IPv6 address.

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
        # This exception is raised by ipaddress.ip_address if the address is not valid.
        return False


def is_valid_domain(domain: str) -> bool:
    """Checks if the given string is a syntactically valid domain name (ASCII-based).

    This validation is based on typical ASCII domain name rules (LDH labels -
    letters, digits, hyphen). It does not perform DNS resolution or check for
    IDN (Internationalized Domain Names) specific rules beyond basic structure.
    The domain must have at least one dot and a TLD of at least two alphabetic characters.

    :param domain: The string to validate.
    :type domain: str
    :return: ``True`` if the string is a syntactically valid domain name,
             ``False`` otherwise.
    :rtype: bool
    """
    if not domain or not isinstance(domain, str) or len(domain) > 253: # Overall length check
        return False
    # Regex for domain names:
    # - Each label (part between dots) is 1-63 chars.
    # - Labels consist of LDH (letters, digits, hyphen).
    # - Labels do not start or end with a hyphen.
    # - The TLD (last label) must be at least 2 chars and all alphabetic.
    # This regex is a common one for ASCII domain names. It allows for
    # subdomains and ensures TLD is alphabetic.
    domain_regex = re.compile(
        r"^(?:[a-zA-Z0-9]"  # First character of a label
            r"(?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)"  # Subsequent chars + dot
            r"+[a-zA-Z]{2,63}$")  # TLD (all letters, 2-63 chars)
    # Using fullmatch to ensure the entire string conforms.
    return bool(domain_regex.fullmatch(domain))


def is_valid_url(url: str, schemes: Optional[List[str]] = None) -> bool:
    """Checks if the given string is a syntactically valid URL and optionally
    validates its scheme against a provided list.

    :param url: The string to validate.
    :type url: str
    :param schemes: A list of allowed schemes (e.g., ``['http', 'https']``).
                    If ``None`` (default), allows ``['http', 'https']``.
                    If an empty list (``[]``) is provided, any scheme is
                    considered valid as long as one is present in the URL and
                    it has a network location.
    :type schemes: Optional[List[str]]
    :return: ``True`` if the string is a valid URL adhering to the scheme
             constraints, ``False`` otherwise.
    :rtype: bool
    """
    if schemes is None:  # Default to http and https if not provided
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
        # If schemes is an empty list, any scheme is allowed, already checked by parsed_url.scheme.
        return True
    except ValueError:  # urlparse can raise ValueError for some malformed URLs.
        return False
