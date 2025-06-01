import logging  # Standard library first
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')  # Though Adw is not used, this was in original
gi.require_version('GtkSource', '5')

# pylint: disable=wrong-import-position
from gi.repository import Gtk, GtkSource

# Configure logger for the module - AFTER all imports
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
    if language_name is None:
        language_name = 'txt'  # Ensure a default string value if None is explicitly passed
    language_manager = GtkSource.LanguageManager.get_default()
    language = language_manager.get_language(language_name)

    if language is None:
        logger.warning("GtkSourceView language '%s' not found. Falling back to a plain buffer.", language_name)
        source_buffer = GtkSource.Buffer()  # Create a plain GtkSource.Buffer
    else:
        source_buffer = GtkSource.Buffer.new_with_language(language)
        source_buffer.set_highlight_syntax(True)  # Only set this if language is found

    source_view = GtkSource.View.new_with_buffer(source_buffer)
    source_view.set_show_line_numbers(True)
    source_view.set_monospace(True)
    source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    source_view.set_auto_indent(True)
    source_view.set_smart_backspace(True)
    source_view.set_indent_on_tab(True)
    source_view.set_tab_width(4)
    source_view.set_insert_spaces_instead_of_tabs(True)
    source_view.set_highlight_current_line(True)
    source_view.set_vexpand(True)

    return source_view, source_buffer
