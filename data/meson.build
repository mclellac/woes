desktop_file = i18n.merge_file(
    input: 'com.github.mclellac.WebOpsEvaluationSuite.desktop.in',
    output: 'com.github.mclellac.WebOpsEvaluationSuite.desktop',
    type: 'desktop',
    po_dir: '../po',
    install: true,
    install_dir: get_option('datadir') / 'applications'
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
    test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

appstream_file = i18n.merge_file(
    input: 'com.github.mclellac.WebOpsEvaluationSuite.metainfo.xml.in',
    output: 'com.github.mclellac.WebOpsEvaluationSuite.metainfo.xml',
    po_dir: '../po',
    install: true,
    install_dir: get_option('datadir') / 'metainfo'
)

appstreamcli = find_program('appstreamcli', required: false, disabler: true)
test('Validate appstream file', appstreamcli,
     args: ['validate', '--no-net', '--explain', appstream_file])

# Install the schema file
install_data(
    'com.github.mclellac.WebOpsEvaluationSuite.gschema.xml',
    install_dir: get_option('datadir') / 'glib-2.0' / 'schemas'
)

# Compile the schemas
compile_schemas = find_program('glib-compile-schemas', required: false, disabler: true)
if compile_schemas.found()
    test('Validate schema file',
        compile_schemas,
        args: ['--strict', '--dry-run', get_option('datadir') / 'glib-2.0' / 'schemas']
    )
endif

subdir('icons')
