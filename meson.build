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
python_bin = python.find_installation('python3')

if not python_bin.found()
    error('No valid Python installation found!')
endif

# Ensure Python 3.12 or newer is used
if not python_bin.language_version().version_compare('>= 3.12')
    error('Python 3.12 or newer is required.')
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
    dependency('pygobject-3.0')
]

# Python-based check for PyGObject
# pygobject_check_script = '''#!/usr/bin/env python3
# import gi; print(gi.__version__)'''
# pygobject_check = run_command(python_bin, '-c', pygobject_check_script, check: false)

# if pygobject_check.returncode() != 0
#   warning_message = 'PyGObject (gi module) not found or not importable by Python3. stdout: ' + pygobject_check.stdout().strip() + ' stderr: ' + pygobject_check.stderr().strip()
#   error('Failed to import PyGObject (gi). ' + warning_message)
# else
#   message('Found PyGObject version (via Python import check): ' + pygobject_check.stdout().strip())
# endif
# The 'pygobject-3.0' Meson C-style dependency is no longer added to the 'dependencies' list.
# This assumes that build steps needing PyGObject (like processing Python files or resources)
# will function correctly as long as the Python environment itself has 'gi' available.

# Verify and install Python dependencies
python_modules = [
    'requests',
    'python-nmap',
    'PyYAML',
    'dnspython'
]

foreach module : python_modules
    dep_check = run_command(python_bin, ['-m', 'pip', 'show', module], check: false)
    if dep_check.returncode() != 0
        error('Python module \'' + module + '\' not found. Please install it using pip.')
    endif
endforeach

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
default_pkgappid = 'com.github.mclellac.WebOpsEvaluationSuite'
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
