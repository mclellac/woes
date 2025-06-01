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
_installed_pkgdatadir = os.path.join(os.path.dirname(__file__), "gtk") # e.g. src/gtk
PKGDATADIR = os.environ.get("WOES_PKGDATADIR", _installed_pkgdatadir)
# This allows overriding with an environment variable for installed scenarios if needed,
# otherwise defaults to a path relative to this constants.py file (src/gtk).
