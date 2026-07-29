project('woes',
    version: '0.99.0',
    meson_version: '>= 1.5.0',
    default_options: ['warning_level=2', 'werror=false'],
)

# Imports
i18n = import('i18n')
gnome = import('gnome')
python = import('python')
fs = import('fs')

# Find Python installation
python_bin = python.find_installation('python3')

if not python_bin.found()
    error('No valid Python installation found!')
endif

# Ensure Python 3.10 or newer is used
if not python_bin.language_version().version_compare('>= 3.10')
    error('Python 3.10 or newer is required.')
endif

# Installation directories
prefix = get_option('prefix')

if host_machine.system() == 'darwin'
    message('Building for macOS (' + host_machine.cpu_family() + '), using prefix: ' + prefix)
endif

# Declare runtime dependencies
dependencies = [
    dependency('glib-2.0', version: '>= 2.78.0'),
    dependency('gtk4', version: '>= 4.18.0'),
    dependency('libadwaita-1', version: '>= 1.7.0'),
    dependency('pygobject-3.0', version: '>= 3.46.0'),
]

# Verify Python module dependencies via Python import checks
# Format: [module_import_name, package_name]
python_modules = [
    ['requests', 'requests'],
    ['nmap', 'python-nmap'],
    ['yaml', 'PyYAML'],
    ['dns', 'dnspython'],
]

foreach module : python_modules
    import_name = module[0]
    pkg_name = module[1]
    dep_check = run_command(python_bin, ['-c', 'import ' + import_name], check: false)
    if dep_check.returncode() != 0
        warning('Python module \'' + import_name + '\' (package \'' + pkg_name + '\') not found by ' + python_bin.full_path() + '. Please ensure it is installed for runtime.')
    endif
endforeach

modulesdir = python_bin.get_install_dir() # Modules directory for post-install
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
libexecdir = prefix / get_option('libexecdir')
localedir = prefix / get_option('localedir')
pkgdatadir = datadir / meson.project_name()
icondir = datadir / 'icons' / 'hicolor'
schemadir = datadir / 'glib-2.0' / 'schemas'

# Application metadata and directories
default_pkgappid = 'com.github.mclellac.WebOpsEvaluationSuite'
pkgappid = default_pkgappid
appdatadir = datadir / 'metainfo'
desktopdir = datadir / 'applications'

# Subdirectories
subdir('src')
subdir('data')
subdir('po')

# Post-install tasks
gnome.post_install(
    glib_compile_schemas: true,
    gtk_update_icon_cache: true,
    update_desktop_database: host_machine.system() == 'linux',
)

# Pass the modulesdir to the post-install script
# meson.add_install_script('meson-postinstall.py', modulesdir) # This line is removed
