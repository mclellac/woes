project('woes',
    version: '0.2.0',
    meson_version: '>= 1.5.0',
    default_options: ['warning_level=2', 'werror=false'],
)

# Imports
i18n = import('i18n')
gnome = import('gnome')
python = import('python')

# Find Python installation
python_versions_to_try = ['python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3']
found_python = false
# Initialize python_bin to a default or error state if necessary,
# though the loop structure should assign it if a version is found.
# Declaring it here to ensure it's in scope.
python_bin = find_program('false', required : false) # Placeholder

foreach py_version : python_versions_to_try
    _python_bin_candidate = python.find_installation(py_version, required : false)
    if _python_bin_candidate.found()
        python_bin = _python_bin_candidate
        found_python = true
        message('Found Python interpreter: ' + python_bin.full_path())
        break
    endif
endforeach

if not found_python
    error('No suitable Python interpreter found. Tried: ' + ', '.join(python_versions_to_try))
endif

# Ensure Python 3.10 or newer is used
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
    dependency('pygobject-3.0', version: '>= 3.46.0'),
    dependency('gtksourceview-5', version: '>= 5.0'),
]

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
