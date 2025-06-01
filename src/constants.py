import os

APP_ID = "com.github.mclellac.woes"
RESOURCE_PREFIX = "/com/github/mclellac/woes/gtk"
THEME_LIGHT = "style.css"
THEME_DARK = "style-dark.css"
VERSION = "0.2.0"

_default_pkgdatadir = "/usr/local/share/woes"
PKGDATADIR = os.environ.get("WOES_PKGDATADIR", _default_pkgdatadir)
