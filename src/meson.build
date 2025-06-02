# Determine if we're in a Flatpak build
is_flatpak_build = get_option('prefix') == '/app'
message('Is Flatpak :build ' + is_flatpak_build.to_string())
message('Prefix: ' + get_option('prefix'))

# Get the install directory for Python
python_install_dir = python_bin.get_install_dir()
message('Python install dir: ' + python_install_dir)

# Determine the site-packages directory
site_packages_dir = python_bin.get_install_dir(subdir: 'woes')
message('Site packages dir: ' + site_packages_dir)

# Define paths
localedir = join_paths(get_option('prefix'), 'share', 'locale')
pkgdatadir = join_paths(get_option('prefix'), 'share', 'woes')
datadir_option = join_paths(get_option('prefix'), get_option('datadir'))
pkgschemadir = join_paths(get_option('prefix'), 'share', meson.project_name())
gsettings_schemadir = join_paths(get_option('prefix'), 'share', 'glib-2.0', 'schemas')

# Prepare configuration data
conf = configuration_data()
conf.set('PYTHON', python_bin.full_path())
conf.set('VERSION', meson.project_version())
conf.set('LOCALEDIR', localedir)
conf.set('WOESDIR', site_packages_dir)
conf.set('APPID', meson.project_name())
conf.set('GSETTINGSSCHEMADIR', gsettings_schemadir) # Add this line

# Set paths conditionally based on whether it is a Flatpak build
if is_flatpak_build
  conf.set('DATADIR', join_paths(get_option('prefix'), 'share'))
  conf.set(
    'PKGDATADIR',
    join_paths(get_option('prefix'), 'share', meson.project_name()),
  )
  resource_path = join_paths(pkgdatadir, 'woes.gresource')
else
  conf.set('DATADIR', datadir_option)
  conf.set('PKGDATADIR', join_paths(datadir_option, meson.project_name()))
  resource_path = join_paths(datadir_option, 'woes', 'woes.gresource')
endif

conf.set('RESOURCE_PATH', resource_path)

# Configure the woes script file
configure_file(
  input: 'woes.in',
  output: 'woes',
  configuration: conf,
  install: true,
  install_dir: bindir,
  install_mode: 'r-xr-xr-x',
)

gnome = import('gnome')
blueprint_compiler = find_program('blueprint-compiler', required: true)

# Define Blueprint source files and prepare for custom targets
gtk_dir_relative = 'gtk' # Relative path from current meson.build (src/) to gtk blueprint files
blp_files = [
  'window.blp',
  'http_page.blp',
  'nmap_page.blp',
  'dns_page.blp',
  'preferences.blp',
  'help-overlay.blp',
  'webscan_page.blp'
]

compiled_ui_outputs = []

foreach blp_file_name : blp_files
  local_ui_file_name = blp_file_name.replace('.blp', '.ui')
  
  # Define the output path for the .ui file. Since this meson.build is in 'src/',
  # an output of 'gtk/file.ui' will result in '<build_dir>/src/gtk/file.ui'.
  # This matches what woes.gresource.xml expects (<file>gtk/file.ui</file>)
  # when gnome.compile_resources (also defined in src/meson.build) resolves paths.
  output_ui_path = join_paths(gtk_dir_relative, local_ui_file_name) # This variable is no longer used for 'output' but comment is kept for context

  compiled_blp_target = custom_target(
    'compile_blueprint_' + blp_file_name.replace('.blp',''),
    input: join_paths(gtk_dir_relative, blp_file_name),
    output: local_ui_file_name,
    command: [
      blueprint_compiler,
      'compile',
      '--output',
      '@OUTPUT@',
      '@INPUT@'
    ]
  )
  compiled_ui_outputs += compiled_blp_target
endforeach

# Compile GNOME resources
gnome.compile_resources(
  'woes',
  'woes.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: conf.get('PKGDATADIR'),
  dependencies: compiled_ui_outputs
)

# List of source files
woes_sources = files(
  '__init__.py',
  'constants.py',
  'dns_page.py',
  'helper.py',
  'http_page.py',
  'main.py',
  'nmap_page.py',
  'nmap_scanner.py',
  'preferences.py',
  'style_utils.py',
  'window.py',
)

# Install the Python source files
python_bin.install_sources(woes_sources, subdir: conf.get('WOESDIR'))
