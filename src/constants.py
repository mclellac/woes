"""
Global constants for the Woes application.

This module defines various global constants used throughout the Woes application.
These include:

- Application identifiers (e.g., ``APP_ID``).
- Resource paths (e.g., ``RESOURCE_PREFIX``).
- Theme names (e.g., ``THEME_LIGHT``, ``THEME_DARK``).
- URLs for website, issues, etc.
- Build-time configured values like ``VERSION`` and ``PKGDATADIR``,
  with fallbacks for development/uninstalled scenarios.
- Predefined User-Agent strings for HTTP requests.
- Keys for GSettings.

The ``VERSION`` and ``PKGDATADIR`` constants are loaded from a Meson-generated
``_config.py`` file during runtime. If this file is not found (e.g., when
running from source without a build or during tests), fallback values are used.
The ``CONFIG_AVAILABLE`` flag indicates whether ``_config.py`` was successfully loaded.
"""

import os
import logging
from typing import List, Dict, Final # Added Final and Union for type hints

from gi.repository import Gtk

logger = logging.getLogger(__name__)

# --- Application Metadata ---
APP_ID: Final[str] = "com.github.mclellac.woes"
APP_WEBSITE_URL: Final[str] = "https://github.com/mclellac/woes"
APP_LICENSE_TYPE: Final[Gtk.License] = Gtk.License.MIT_X11
APP_DESCRIPTION: Final[str] = "A simple toolkit for web, nmap, and DNS scans."
APP_ISSUES_URL: Final[str] = "https://github.com/mclellac/woes/issues"

# --- Resource Paths and Themes ---
RESOURCE_PREFIX: Final[str] = "/com/github/mclellac/woes/gtk"
THEME_LIGHT: Final[str] = "style.css"
THEME_DARK: Final[str] = "style-dark.css"

# --- Build-time Configuration with Fallbacks ---
# These will be populated by the try-except block below.
PKGDATADIR: str
VERSION: str
CONFIG_AVAILABLE: bool

try:
    from . import _config  # Assuming Meson generates src/_config.py

    PKGDATADIR = _config.PKGDATADIR
    VERSION = _config.VERSION
    CONFIG_AVAILABLE = True
    logger.info("Loaded PKGDATADIR ('%s') and VERSION ('%s') from src._config.", PKGDATADIR, VERSION)
except ImportError:
    logger.warning(
        "src._config not found. Using fallback constants. "
        "This is expected if running uninstalled or if Meson's configure_file step hasn't run."
    )
    _project_root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    # Fallback PKGDATADIR for data files when running uninstalled
    # (e.g., from project_root/data or project_root/build/data)
    PKGDATADIR = os.path.join(_project_root, "data")
    if not os.path.isdir(PKGDATADIR):
        # Common alternative for Meson build directory when running from source tree root
        PKGDATADIR = os.path.join(_project_root, "build", "data", "woes")
        if not os.path.isdir(PKGDATADIR): # If build/data/woes also doesn't exist
             PKGDATADIR = os.path.join(_project_root, "build", "data") # Try build/data
             if not os.path.isdir(PKGDATADIR): # Final fallback if build/data also doesn't exist
                  PKGDATADIR = os.path.join(_project_root, "data") # Revert to initial data path

    VERSION = "0.0.0-dev"  # Fallback version for development
    CONFIG_AVAILABLE = False
    logger.info(
        "Using fallback PKGDATADIR ('%s') and VERSION ('%s').", PKGDATADIR, VERSION
    )


# --- GSettings Keys ---
GNOME_INTERFACE_SCHEMA: Final[str] = "org.gnome.desktop.interface"
FONT_NAME_KEY: Final[str] = "font-name"
TEXT_SCALING_FACTOR_KEY: Final[str] = "text-scaling-factor"

# --- Predefined User-Agent Strings ---
# List of dictionaries, where each dictionary has "title" and "value" keys.
USER_AGENTS: Final[List[Dict[str, str]]] = [
    {
        "title": "Chrome (Linux)",
        "value": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    },
    {
        "title": "Firefox (Linux)",
        "value": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    },
    {
        "title": "Chrome (Windows)",
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
    {
        "title": "Firefox (Windows)",
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    },
    {
        "title": "Safari (macOS)",
        "value": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    },
    {
        "title": "Edge (Windows)",
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    },
    {
        "title": "OpenAI GPTBot",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot",
    },
    {
        "title": "OpenAI SearchBot",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    },
    {
        "title": "OpenAI ChatGPT-User (Legacy)",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    },
    {
        "title": "OpenAI ChatGPT-User",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/2.0; +https://openai.com/bot",
    },
    {
        "title": "Google-Extended",
        "value": "Mozilla/5.0 (compatible; Google-Extended/1.0; +http://www.google.com/bot.html)",
    },
    {
        "title": "Googlebot (Smartphone)",
        "value": "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    },
    {
        "title": "Anthropic ClaudeBot (Chat)",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ClaudeBot/1.0; +claudebot@anthropic.com",
    },
    {
        "title": "Anthropic AI (Generic)",
        "value": "Mozilla/5.0 (compatible; anthropic-ai/1.0; +http://www.anthropic.com/bot.html)",
    },
    {
        "title": "Anthropic Claude-Web",
        "value": "Mozilla/5.0 (compatible; claude-web/1.0; +http://www.anthropic.com/bot.html)",
    },
    {"title": "Microsoft Bingbot", "value": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"},
    {
        "title": "AppleBot (Specific)",
        "value": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15 (AppleBot/0.1)",
    },
    {"title": "AppleBot (Generic)", "value": "Mozilla/5.0 (compatible; Applebot/1.0; +http://www.apple.com/bot.html)"},
    {
        "title": "Applebot-Extended",
        "value": "Mozilla/5.0 (compatible; Applebot-Extended/1.0; +http://www.apple.com/bot.html)",
    },
    {
        "title": "PerplexityAI Bot",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot",
    },
    {
        "title": "PerplexityAI User",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Perplexity-User/1.0; +https://www.perplexity.ai/useragent",
    },
    {
        "title": "Amazonbot",
        "value": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/600.2.5 (KHTML, like Gecko) Version/8.0.2 Safari/600.2.5 (Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)",
    },
    {
        "title": "Meta FacebookBot",
        "value": "Mozilla/5.0 (compatible; FacebookBot/1.0; +http://www.facebook.com/bot.html)",
    },
    {
        "title": "Meta External Agent",
        "value": "Mozilla/5.0 (compatible; meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler))",
    },
    {
        "title": "LinkedInBot",
        "value": "LinkedInBot/1.0 (compatible; Mozilla/5.0; Jakarta Commons-HttpClient/3.1 +http://www.linkedin.com)",
    },
    {
        "title": "ByteDance Bytespider",
        "value": "Mozilla/5.0 (compatible; Bytespider/1.0; +http://www.bytedance.com/bot.html)",
    },
    {
        "title": "DuckDuckGo DuckAssistBot",
        "value": "Mozilla/5.0 (compatible; DuckAssistBot/1.0; +http://www.duckduckgo.com/bot.html)",
    },
    {"title": "Cohere AI Bot", "value": "Mozilla/5.0 (compatible; cohere-ai/1.0; +http://www.cohere.ai/bot.html)"},
    {
        "title": "Allen Institute AI2Bot",
        "value": "Mozilla/5.0 (compatible; AI2Bot/1.0; +http://www.allenai.org/crawler)",
    },
    {
        "title": "Common Crawl CCBot",
        "value": "Mozilla/5.0 (compatible; CCBot/1.0; +http://www.commoncrawl.org/bot.html)",
    },
    {
        "title": "Diffbot",
        "value": "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.1.2) Gecko/20090729 Firefox/3.5.2 (.NET CLR 3.5.30729; Diffbot/0.1; +http://www.diffbot.com)",
    },
    {"title": "Omgili Bot", "value": "Mozilla/5.0 (compatible; omgili/1.0; +http://www.omgili.com/bot.html)"},
    {"title": "TimpiBot", "value": "Timpibot/0.8 (+http://www.timpi.io)"},
    {"title": "You.com YouBot", "value": "Mozilla/5.0 (compatible; YouBot (+http://www.you.com))"},
    {"title": "MistralAI User", "value": "Mozilla/5.0 (compatible; MistralAI-User/1.0; +https://mistral.ai/bot)"},
]
