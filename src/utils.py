"""General utility functions for the Woes application."""

import logging
import ipaddress
import re
from urllib.parse import urlparse
from typing import Optional, List, Tuple, Any

import gi
from gi.repository import Gtk, Adw, Gio, GLib

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

logger = logging.getLogger(__name__)


def show_global_error(widget: Gtk.Widget, message: str):
    """
    Display a global error message using the main window's error banner.

    :param widget: A :class:`Gtk.Widget` (typically 'self' from a page object)
                   to get the native window.
    :type widget: Gtk.Widget
    :param message: The error message string to display.
    :type message: str
    """
    try:
        main_window = widget.get_native()
        if main_window and hasattr(main_window, "show_error"):
            main_window.show_error(message)
            logger.error(f"Global error displayed via main window: {message}")
        else:
            logger.warning("Could not find main window or show_error method to display global error: %s", message)
    except Exception as e:
        logger.exception("An unexpected error occurred while trying to show global error '%s': %s", message, e)


def show_global_toast(
    widget: Gtk.Widget, message: str, timeout: int = 2, priority: Adw.ToastPriority = Adw.ToastPriority.NORMAL
):
    """
    Display a global toast message using the main window's toast overlay.

    :param widget: A :class:`Gtk.Widget` (typically 'self' from a page object)
                   to get the native window.
    :type widget: Gtk.Widget
    :param message: The toast message string to display.
    :type message: str
    :param timeout: Duration in seconds for the toast to be visible.
                    Defaults to 2.
    :type timeout: int
    :param priority: The priority of the toast.
                     Defaults to :const:`Adw.ToastPriority.NORMAL`.
    :type priority: Adw.ToastPriority
    """
    try:
        main_window = widget.get_native()
        if main_window and hasattr(main_window, "show_toast"):
            main_window.show_toast(message, priority=priority, timeout=timeout)
            logger.info(f"Global toast shown via main window: {message}")
        else:
            logger.warning("Could not find main window or show_toast method to display global toast: %s", message)
    except Exception as e:
        logger.exception("An unexpected error occurred while trying to show global toast '%s': %s", message, e)


def is_valid_ip(address: str) -> bool:
    """
    Check if the given string is a valid IPv4 or IPv6 address.

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
    """
    Check if the given string is a syntactically valid domain name (ASCII).

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
    """
    Check if the given string is a syntactically valid URL with specific schemes.

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
    effective_schemes: List[str]
    if schemes is None:  # Default to http and https if not provided
        effective_schemes = ["http", "https"]
    else:
        effective_schemes = schemes

    if not url or not isinstance(url, str):
        return False
    try:
        parsed_url = urlparse(url)
        # A URL must have a scheme and a network location (netloc) to be valid.
        if not (parsed_url.scheme and parsed_url.netloc):
            return False
        # If a non-empty list of schemes is provided, the URL's scheme must be in it.
        if effective_schemes and parsed_url.scheme not in effective_schemes:
            return False
        return True
    except ValueError:  # urlparse can raise ValueError for some malformed URLs, though it's rare
        return False


def process_task_result(
    task: Gio.Task, result: Gio.AsyncResult, page_logger: logging.Logger
) -> Tuple[Optional[Any], Optional[str]]:
    """
    Process the result of a Gio.Task, handling common exceptions.

    :param task: The Gio.Task that was executed.
    :type task: Gio.Task
    :param result: The Gio.AsyncResult from the completed task.
    :type result: Gio.AsyncResult
    :param page_logger: The logger instance from the calling page/module to log errors.
    :type page_logger: logging.Logger
    :return: A tuple containing (propagated_value, error_message).
             If successful, propagated_value is the task's result and error_message is None.
             If an error occurs, propagated_value is None and error_message contains the error string.
    :rtype: Tuple[Optional[Any], Optional[str]]
    """
    try:
        propagated_value = task.propagate_value(result)
        return propagated_value, None
    except GLib.Error as e:
        page_logger.warning(
            "Task failed with GLib.Error (Domain: %s, Code: %d, Message: %s)", e.domain, e.code, e.message
        )
        # Try to return a somewhat user-friendly message from GLib.Error
        error_message = e.message if e.message else "An operation failed or was cancelled."
        # Escape markup just in case, as this might be shown in UI
        if "<b>" in error_message or "<" in error_message:
            error_message = GLib.markup_escape_text(error_message)
        return None, error_message
    except Exception as e_generic:
        page_logger.exception("Task failed with an unexpected Python error:")
        # Return the first line of the generic exception for brevity in UI
        return None, f"An unexpected error occurred: {str(e_generic).splitlines()[0]}"
