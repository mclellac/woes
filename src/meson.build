# Determine if we're in a Flatpak build
is_flatpak_build = get_option('prefix') == '/app'
message('Is Flatpak build: ' + is_flatpak_build.to_string())
message('Prefix: ' + get_option('prefix'))

# Get the install directory for Python
python_install_dir = python3.get_install_dir()
message('Python install dir: ' + python_install_dir)

# Determine the site-packages directory
site_packages_dir = python3.get_install_dir(subdir: 'woes')
message('Site packages dir: ' + site_packages_dir)

# Define paths
localedir = join_paths(get_option('prefix'), 'share', 'locale')
pkgdatadir = join_paths(get_option('prefix'), 'share', 'woes')
datadir_option = join_paths(get_option('prefix'), get_option('datadir'))
pkgschemadir = join_paths(get_option('prefix'), 'share', meson.project_name())

# Prepare configuration data
conf = configuration_data()
conf.set('PYTHON', python3.full_path())
conf.set('VERSION', meson.project_version())
conf.set('LOCALEDIR', localedir)
conf.set('WOESDIR', site_packages_dir)
conf.set('APPID', meson.project_name())

# Set paths conditionally based on whether it is a Flatpak build
if is_flatpak_build
    conf.set('DATADIR', join_paths(get_option('prefix'), 'share'))
    conf.set('PKGDATADIR', join_paths(get_option('prefix'), 'share', meson.project_name()))
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

# Compile GNOME resources
gnome.compile_resources('woes',
  'woes.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: conf.get('PKGDATADIR'),
)

# List of source files
woes_sources = files(
  '__init__.py',
  'main.py',
  'window.py',
  'preferences.py',
  'http_util.py',
)

# Install the Python source files
python3.install_sources(woes_sources, subdir: conf.get('WOESDIR'))
