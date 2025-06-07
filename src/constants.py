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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/109.0",
]
