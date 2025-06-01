import logging
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')

from gi.repository import Gtk, GtkSource

logger = logging.getLogger(__name__)


def create_source_view(language_name='txt'):
    """
    Creates and configures a GtkSource.View and GtkSource.Buffer.

    Args:
        language_name (str, optional): The language for syntax highlighting.
                                      Defaults to 'txt'.

    Returns:
        tuple: A tuple containing the configured GtkSource.View and
               GtkSource.Buffer.
    """
    logger.debug(f"utils.create_source_view: Starting with language_name: '{language_name}'")
    if language_name is None:
        logger.debug("utils.create_source_view: language_name is None, defaulting to 'txt'.")
        language_name = 'txt'

    language_manager = GtkSource.LanguageManager.get_default()
    logger.debug(f"utils.create_source_view: GtkSource.LanguageManager.get_default(): {language_manager}")
    language = language_manager.get_language(language_name)
    logger.debug(f"utils.create_source_view: Language manager get_language('{language_name}') returned: {language}")

    if language is None:
        logger.warning(f"utils.create_source_view: GtkSourceView language '{language_name}' not found. Falling back to a plain buffer.")
        source_buffer = GtkSource.Buffer()
        logger.debug(f"utils.create_source_view: Created plain GtkSource.Buffer: {source_buffer}")
    else:
        source_buffer = GtkSource.Buffer.new_with_language(language)
        logger.debug(f"utils.create_source_view: Created GtkSource.Buffer with language '{language.get_name()}': {source_buffer}")
        source_buffer.set_highlight_syntax(True)
        logger.debug("utils.create_source_view: Syntax highlighting set to True for source_buffer.")

    source_view = GtkSource.View.new_with_buffer(source_buffer)
    logger.debug(f"utils.create_source_view: GtkSource.View created with buffer: {source_view}")
    source_view.set_show_line_numbers(True)
    logger.debug("utils.create_source_view: Show line numbers set to True.")
    source_view.set_monospace(True)
    logger.debug("utils.create_source_view: Monospace set to True.")
    source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    logger.debug("utils.create_source_view: Wrap mode set to WORD_CHAR.")
    source_view.set_auto_indent(True)
    logger.debug("utils.create_source_view: Auto indent set to True.")
    source_view.set_smart_backspace(True)
    logger.debug("utils.create_source_view: Smart backspace set to True.")
    source_view.set_indent_on_tab(True)
    logger.debug("utils.create_source_view: Indent on tab set to True.")
    source_view.set_tab_width(4)
    logger.debug("utils.create_source_view: Tab width set to 4.")
    source_view.set_insert_spaces_instead_of_tabs(True)
    logger.debug("utils.create_source_view: Insert spaces instead of tabs set to True.")
    source_view.set_highlight_current_line(True)
    logger.debug("utils.create_source_view: Highlight current line set to True.")
    source_view.set_vexpand(True)
    logger.debug("utils.create_source_view: Vexpand set to True.")

    logger.debug(f"utils.create_source_view: Returning source_view: {source_view}, source_buffer: {source_buffer}")
    return source_view, source_buffer
