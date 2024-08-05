# Determine if we're in a Flatpak build
is_flatpak_build = get_option('prefix') == '/app'
message('Is Flatpak build: ' + is_flatpak_build.to_string())
message('Prefix: ' + get_option('prefix'))

conf = configuration_data()
conf.set('PYTHON', python3.full_path())
conf.set('VERSION', meson.project_version())

# Get the install directory for Python
python_install_dir = python3.get_install_dir()
message('Python install dir: ' + python_install_dir)

# Use the correct method to get site-packages directory
site_packages_dir = python3.get_install_dir(subdir: 'woes')
message('Site packages dir: ' + site_packages_dir)

localedir = join_paths(get_option('prefix'), 'share', 'locale')
pkgdatadir = join_paths(get_option('prefix'), 'share', 'woes')

if is_flatpak_build
    conf.set('DATADIR', join_paths(get_option('prefix'), 'share'))
    conf.set('WOESDIR', site_packages_dir)
    conf.set('PKGDATADIR', join_paths(get_option('prefix'), 'share', meson.project_name()))
    resource_path = join_paths(get_option('prefix'), 'share', 'woes', 'woes.gresource')
else
    conf.set('DATADIR', join_paths(get_option('prefix'), get_option('datadir')))
    conf.set('WOESDIR', site_packages_dir)
    conf.set('PKGDATADIR', join_paths(get_option('prefix'), get_option('datadir'), meson.project_name()))
    resource_path = join_paths(get_option('prefix'), get_option('datadir'), 'woes', 'woes.gresource')
endif

conf.set('RESOURCE_PATH', resource_path)
conf.set('APPID', meson.project_name())

configure_file(
  input: 'woes.in',
  output: 'woes',
  configuration: {
    'localedir': localedir,
    'pkgdatadir': pkgdatadir,
    'PYTHON': python3.full_path(),
    'VERSION': conf.get('VERSION')

  },
  install: true,
  install_dir: bindir,
  install_mode: 'r-xr-xr-x',
)

gnome = import('gnome')

# Compile GNOME resources from an XML file. This bundles resources into a single binary file.
gnome.compile_resources('woes',
  'woes.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: conf.get('PKGDATADIR')
)

# Define the list of source files for the 'woes' project.
woes_sources = files(
  '__init__.py',
  'main.py',
  'window.py',
  'preferences.py',
  'http_util.py',
)

# Use the python3.install_sources method to install the Python source files.
python3.install_sources(woes_sources, subdir: conf.get('WOESDIR'))