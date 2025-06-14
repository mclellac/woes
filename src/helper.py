"""Provides a Helper class for :class:`Gtk.ColumnView` context menus and keyboard shortcuts."""
from gi.repository import Gdk, Gtk
import gi

gi.require_version("Gtk", "4.0")


class Helper:
    """
    Provides keyboard shortcuts and context menu for Gtk.ColumnView.

    This helper class adds common functionalities like ``Ctrl+C`` for copying
    and a right-click context menu with a 'Copy' option to a
    :class:`Gtk.ColumnView` widget.
    """

    def __init__(self, widget: Gtk.Widget, parent_window: Gtk.Window):
        """
        Initialize the Helper class.

        :param widget: The widget to which the helper is attached. This is
                       expected to be a :class:`Gtk.ColumnView` for full
                       functionality.
        :type widget: Gtk.Widget
        :param parent_window: The parent window containing the widget.
        :type parent_window: Gtk.Window
        """
        self.widget = widget
        self.parent_window = parent_window

        if isinstance(self.widget, Gtk.ColumnView):
            self.setup_keyboard_shortcut()
            self.setup_context_menu()

    def setup_keyboard_shortcut(self):
        """
        Set up a keyboard shortcut (``Ctrl+C``) for copying.

        This allows copying selected content from the :class:`Gtk.ColumnView`
        to the clipboard using the ``Ctrl+C`` combination.
        """
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.widget.add_controller(key_controller)

    def setup_context_menu(self):
        """Set up a context menu (right-click) with a 'Copy' option."""
        self.popover = Gtk.Popover.new()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self.on_copy_menu_item_activated)
        vbox.append(copy_button)
        self.popover.set_child(vbox)

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", self.on_right_click)
        self.widget.add_controller(gesture)

    def on_right_click(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        """
        Display the context menu popover at the location of the mouse click.

        Called when the right-click gesture is detected on the widget.

        :param gesture: The gesture that triggered the event.
        :type gesture: Gtk.GestureClick
        :param n_press: The number of mouse button presses (should be 1 for context menu).
        :type n_press: int
        :param x: The x-coordinate of the mouse click relative to the widget.
        :type x: float
        :param y: The y-coordinate of the mouse click relative to the widget.
        :type y: float
        """
        _ = gesture # Unused parameter
        if n_press == 1:
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            self.popover.set_pointing_to(rect)
            self.popover.set_has_arrow(False)
            self.popover.set_parent(self.widget) # type: ignore
            self.popover.popup() # type: ignore

    def on_copy_menu_item_activated(self, button: Gtk.Button) -> None:
        """
        Handle activation of the 'Copy' menu item.

        Copies selected content to the clipboard and hides the popover.

        :param button: The :class:`Gtk.Button` that triggered the event.
        :type button: Gtk.Button
        """
        _ = button # Unused parameter
        self.copy_to_clipboard()
        self.popover.popdown() # type: ignore

    def on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType
    ) -> bool:
        """
        Handle the ``Ctrl+C`` keyboard shortcut to copy selected content.

        :param controller: The key controller that triggered the event.
        :type controller: Gtk.EventControllerKey
        :param keyval: The value of the key pressed (e.g., :attr:`Gdk.KEY_c`).
        :type keyval: int
        :param keycode: The hardware keycode of the key pressed.
        :type keycode: int
        :param state: The state of the modifier keys (e.g., :attr:`Gdk.ModifierType.CONTROL_MASK`).
        :type state: Gdk.ModifierType
        :return: ``True`` if the ``Ctrl+C`` event was handled, ``False`` otherwise.
        :rtype: bool
        """
        _ = controller, keycode # Unused parameters
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c:
            self.copy_to_clipboard()
            return True
        return False

    def copy_to_clipboard(self) -> None:
        """
        Copy selected content from the :class:`Gtk.ColumnView` to the clipboard.

        Assumes the items in the :class:`Gtk.ColumnView` model have 'key' and
        'value' attributes to construct the string "key: value".
        Handles both :class:`Gtk.MultiSelection` and :class:`Gtk.SingleSelection` models.
        """
        if isinstance(self.widget, Gtk.ColumnView):
            selection_model = self.widget.get_model()
            selected_texts = []

            if isinstance(selection_model, Gtk.MultiSelection):
                for index in range(selection_model.get_n_items()): # type: ignore
                    if selection_model.is_selected(index): # type: ignore
                        selected_item = selection_model.get_item(index) # type: ignore
                        if hasattr(selected_item, 'key') and hasattr(selected_item, 'value'):
                            selected_texts.append(f"{selected_item.key}: {selected_item.value}")
            elif isinstance(selection_model, Gtk.SingleSelection):
                selected_item = selection_model.get_selected_item() # type: ignore
                if selected_item and hasattr(selected_item, 'key') and hasattr(selected_item, 'value'):
                    selected_texts.append(f"{selected_item.key}: {selected_item.value}")

            if selected_texts:
                clipboard_text = "\n".join(selected_texts)
                clipboard = self.widget.get_clipboard() # type: ignore
                if clipboard:
                    content_provider = Gdk.ContentProvider.new_for_value(clipboard_text) # type: ignore
                    clipboard.set_content(content_provider)
