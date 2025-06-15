"""
Global constants for the Woes application.

This module defines various global constants used throughout the Woes application,
including application identifiers, resource paths, theme names, version information,
URLs, and predefined User-Agent strings.
"""

import os

from gi.repository import Gtk

APP_ID = "com.github.mclellac.woes"
RESOURCE_PREFIX = "/com/github/mclellac/woes/gtk"
THEME_LIGHT = "style.css"
THEME_DARK = "style-dark.css"
VERSION = "0.2.0"
APP_WEBSITE_URL = "https://github.com/mclellac/woes"
APP_LICENSE_TYPE = Gtk.License.MIT_X11
APP_DESCRIPTION = "A simple toolkit for web, nmap, and DNS scans."
APP_ISSUES_URL = "https://github.com/mclellac/woes/issues"

# PKGDATADIR would typically be set by the build system (e.g., Meson, Autotools)
_default_pkgdatadir = "/usr/local/share/woes"
PKGDATADIR = os.environ.get("WOES_PKGDATADIR", _default_pkgdatadir)
# This allows overriding with an environment variable for testing or different installations.

GNOME_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
FONT_NAME_KEY = "font-name"
TEXT_SCALING_FACTOR_KEY = "text-scaling-factor"

# User Agents to be used as defaults and for selection.
# Format: List of (Title, UserAgentString) tuples.
USER_AGENTS = [
    ("Chrome (Latest) on Linux", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"),
    ("Firefox (Latest) on Linux", "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"),
    ("Safari (Latest) on macOS", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    ("Edge (Latest) on Windows", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"),
    ("Woes HTTP Client", f"Woes/{APP_ID} (github.com/mclellac/woes)"),
]
