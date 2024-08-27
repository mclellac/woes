# __init__.py
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

import logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# Main application pages and modules
from .http_page import HttpPage
from .dns_page import DNSPage
from .nmap_page import NmapPage
from .helper import Helper
from .preferences import Preferences
from .window import WoesWindow

__all__ = [
    "HttpPage",
    "NmapPage",
    "DNSPage",
    "Helper",
    "Preferences",
    "WoesWindow",
]
