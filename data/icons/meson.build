application_id = 'com.github.mclellac.WebOpsEvaluationSuite'

# Install the scalable application icon
scalable_dir = 'hicolor' / 'scalable' / 'apps'
install_data(
  scalable_dir / ('@0@.svg').format(application_id),
  install_dir: get_option('datadir') / 'icons' / scalable_dir
)

# Install the symbolic application icon
symbolic_dir = 'hicolor' / 'symbolic' / 'apps'
install_data(
  symbolic_dir / ('@0@-symbolic.svg').format(application_id),
  install_dir: get_option('datadir') / 'icons' / symbolic_dir
)

# Install the emote icons
emotes = [
  'face-angel-symbolic.svg',
  'face-angry-symbolic.svg',
  'face-cool-symbolic.svg',
  'face-devilish-symbolic.svg',
  'face-glasses-symbolic.svg',
  'face-shutmouth-symbolic.svg'
]

foreach icon : emotes
  install_data(
    'hicolor/symbolic/emotes/' / icon,
    install_dir: get_option('datadir') / 'icons/hicolor/symbolic/emotes'
  )
endforeach

# Install the status icons
status_icons = [
  'network-receive-symbolic.svg',
  'security-high-symbolic.svg',
  'security-low-symbolic.svg',
  'security-medium-rtl-symbolic.svg',
  'security-medium-symbolic-rtl.svg',
  'security-medium-symbolic.svg'
]

foreach icon : status_icons
  install_data(
    'hicolor/symbolic/status/' / icon,
    install_dir: get_option('datadir') / 'icons/hicolor/symbolic/status'
  )
endforeach

# Install the action icons
action_icons = [
  'globe-symbolic.svg',
  'globe-alt-symbolic.svg'
]

foreach icon : action_icons
  install_data(
    'hicolor/symbolic/actions/' / icon,
    install_dir: get_option('datadir') / 'icons/hicolor/symbolic/actions'
  )
endforeach
