"""Global constants for the Woes application."""
import os

APP_ID = "com.github.mclellac.woes"
RESOURCE_PREFIX = "/com/github/mclellac/woes/gtk"
THEME_LIGHT = "style.css"
THEME_DARK = "style-dark.css"
VERSION = "0.2.0"

# PKGDATADIR would typically be set by the build system (e.g., Meson, Autotools)
# This should match where Meson installs woes.gresource, which is typically
# {prefix}/share/{project_name}
_default_pkgdatadir = "/usr/local/share/woes"
PKGDATADIR = os.environ.get("WOES_PKGDATADIR", _default_pkgdatadir)
# This allows overriding with an environment variable for testing or different installations.

USER_AGENTS = [
    {
        "title": "Chrome (Windows)",
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    },
    {
        "title": "Firefox (Windows)",
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
    },
    {
        "title": "Safari (macOS)",
        "value": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    },
    {
        "title": "Edge (Windows)",
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    },
    {
        "title": "OpenAI GPTBot",
        "value": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.0; +https://openai.com/gptbot"
    },
    {
        "title": "Googlebot (Generic)",
        "value": "Googlebot/2.1 (+http://www.google.com/bot.html)"
    },
    {
        "title": "Googlebot (Smartphone)",
        "value": "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    },
    {
        "title": "Microsoft Bingbot",
        "value": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
    },
    {
        "title": "AppleBot",
        "value": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15 (AppleBot/0.1)"
    },
    {
        "title": "ClaudeBot (General)",
        "value": "ClaudeBot/1.0 (+https://anthropic.com/claude-bot)"
    }
]

GNOME_INTERFACE_SCHEMA = "org.gnome.desktop.interface"
FONT_NAME_KEY = "font-name"
TEXT_SCALING_FACTOR_KEY = "text-scaling-factor"
