project('woes',
    version: '0.2.0',
    meson_version: '>= 1.3.0',
    default_options: ['warning_level=2', 'werror=false'],
)

# Imports
i18n = import('i18n')
gnome = import('gnome')
python = import('python')

# Find Python installation
python_bin = python.find_installation('python3.12') # Changed to python3.12

if not python_bin.found()
    error('No valid Python 3.12 installation found!') # Updated error message
endif

# Ensure Python 3.12 or newer is used
if not python_bin.language_version().version_compare('>= 3.12') # Changed to 3.12
    error('Python 3.12 or newer is required.') # Updated error message
endif

# macOS-specific prefix correction
if host_machine.system() == 'darwin'
    prefix = '/usr/local'
endif

# Declare runtime dependencies
dependencies = [
    dependency('glib-2.0', version: '>= 2.78.0'),
    dependency('gtk4', version: '>= 4.14.0'),
    dependency('libadwaita-1', version: '>= 1.5.0'),
    dependency('pygobject-3.0', version: '>= 3.46.0'),
    dependency('gtksourceview-5', version: '>= 5.0.0'),
]

# Installation directories
prefix = get_option('prefix')
modulesdir = python_bin.get_install_dir() # Modules directory for post-install
bindir = join_paths(prefix, get_option('bindir'))
datadir = join_paths(prefix, get_option('datadir'))
libexecdir = join_paths(prefix, get_option('libexecdir'))
localedir = join_paths(prefix, get_option('localedir'))
pkgdatadir = join_paths(datadir, meson.project_name())
icondir = join_paths(datadir, 'icons', 'hicolor')
schemadir = join_paths(datadir, 'glib-2.0', 'schemas')

# Application metadata and directories
default_pkgappid = 'com.github.mclellac.woes'
pkgappid = default_pkgappid
appdatadir = join_paths(datadir, 'metainfo')
desktopdir = join_paths(datadir, 'applications')

# Subdirectories
subdir('src')
subdir('data')
subdir('po')

# Post-install tasks
gnome.post_install(
    glib_compile_schemas: true,
    gtk_update_icon_cache: true,
    update_desktop_database: true,
)

# Pass the modulesdir to the post-install script
meson.add_install_script('meson-postinstall.py', modulesdir)
