import os

APP_ID = "com.github.mclellac.woes"
RESOURCE_PREFIX = "/com/github/mclellac/woes/gtk"
THEME_LIGHT = "style.css"
THEME_DARK = "style-dark.css"
VERSION = "0.2.0"

# PKGDATADIR would typically be set by the build system (e.g., Meson, Autotools)
# to the application's shared data directory (e.g., /usr/share/com.github.mclellac.woes/
# or a path relative to the installation prefix).
# For development and if running from the source tree without installation,
# this might point to where the .gresource file is compiled.
# Assuming woes.gresource will be compiled into a 'gtk' subdirectory within 'src'
# or a similar location accessible from the source.
# If installed, this would be different, e.g., os.path.join(sys.prefix, 'share', APP_ID)
# For now, let's assume it's located in a 'gtk' subdirectory relative to the main 'src' package.
# This path is for the DIRECTORY containing woes.gresource.
# This should match where Meson installs woes.gresource, which is typically
# {prefix}/share/{project_name}
# For this project, with default prefix /usr/local, it's /usr/local/share/woes
_default_pkgdatadir = "/usr/local/share/woes"
PKGDATADIR = os.environ.get("WOES_PKGDATADIR", _default_pkgdatadir)
# This allows overriding with an environment variable for testing or different installations,
# otherwise defaults to the standard Meson install path.

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/109.0",
]
