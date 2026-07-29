# Determine if we're in a Flatpak build
is_flatpak_build = get_option('prefix') == '/app'
message('Is Flatpak build: ' + is_flatpak_build.to_string())
message('Prefix: ' + get_option('prefix'))

# Get the install directory for Python (version specific)
python_purelib = python_bin.get_path('purelib')
python_install_dir = python_purelib # Use for messages and consistency
message('Python install dir (purelib): ' + python_install_dir)

# Determine the site-packages directory (version specific)
site_packages_dir = join_paths(python_purelib, 'woes')
message('Site packages dir (woes subdir): ' + site_packages_dir)

# Create a new configuration variable for the site-packages directory (version specific)
python_site_packages_dir = python_purelib

# Define paths
if host_machine.system() == 'darwin'
    localedir = '/usr/local/share/locale'
    datadir_option = '/usr/local/share'
else
    opt_localedir = get_option('localedir')
    localedir = opt_localedir.startswith('/') ? opt_localedir : get_option('prefix') / opt_localedir

    opt_datadir = get_option('datadir')
    datadir_option = opt_datadir.startswith('/') ? opt_datadir : get_option('prefix') / opt_datadir
endif

pkgdatadir = datadir_option / 'woes'
pkgschemadir = datadir_option / meson.project_name()
gsettings_schemadir = datadir_option / 'glib-2.0' / 'schemas'

# Prepare configuration data
conf = configuration_data()
conf.set('PYTHON', python_bin.full_path())
conf.set('VERSION', meson.project_version())
conf.set('LOCALEDIR', localedir)
conf.set('WOESDIR', site_packages_dir)
conf.set('PYTHON_SITE_PACKAGES_DIR', python_site_packages_dir)
conf.set('APPID', meson.project_name())
conf.set('GSETTINGSSCHEMADIR', gsettings_schemadir) # Add this line

# Set paths conditionally based on whether it is a Flatpak build
if is_flatpak_build
  conf.set('DATADIR', get_option('prefix') / 'share')
  conf.set(
    'PKGDATADIR',
    get_option('prefix') / 'share' / meson.project_name(),
  )
  resource_path = pkgdatadir / 'woes.gresource'
else
  conf.set('DATADIR', datadir_option)
  conf.set('PKGDATADIR', datadir_option / meson.project_name())
  resource_path = datadir_option / 'woes' / 'woes.gresource'
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
gnome.compile_resources(
  'woes',
  'woes.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: conf.get('PKGDATADIR'),
)

# List of source files
woes_sources = files(
  '__init__.py',
  'constants.py',
  'custom_dns_adapter.py',
  'dns_client.py',
  'dns_page.py',
  'helper.py',
  'http_client.py',
  'http_page.py',
  'main.py',
  'nmap_page.py',
  'nmap_scanner.py',
  'preferences.py',
  'style_utils.py',
  'utils.py',
  'window.py',
  'webscan_page.py',
)

# Install the Python source files
python_bin.install_sources(woes_sources, subdir: 'woes')
