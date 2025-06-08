"""Provides a Helper class for Gtk.ColumnView context menus and keyboard shortcuts."""
from gi.repository import Gdk, Gtk
import gi

gi.require_version("Gtk", "4.0")


class Helper:
    """A helper class to add keyboard shortcuts (Ctrl+C) and context menu functionality
    (right-click to copy) to a Gtk.ColumnView widget.
    """

    def __init__(self, widget, parent_window):
        """Initialize the Helper class.

        Args:
            widget (Gtk.Widget): The widget to which the helper is attached.
            parent_window (Gtk.Window): The parent window containing the widget.

        """
        self.widget = widget
        self.parent_window = parent_window

        if isinstance(self.widget, Gtk.ColumnView):
            self.setup_keyboard_shortcut()
            self.setup_context_menu()

    def setup_keyboard_shortcut(self):
        """Set up a keyboard shortcut (Ctrl+C) for copying selected content
        from the Gtk.ColumnView to the clipboard.
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

    def on_right_click(self, _gesture, n_press, x, y):
        """Display the context menu popover at the location of the mouse click.

        Args:
            _gesture (Gtk.GestureClick): The gesture that triggered the event.
            n_press (int): The number of mouse button presses.
            x (float): The x-coordinate of the mouse click relative to the widget.
            y (float): The y-coordinate of the mouse click relative to the widget.

        """
        if n_press == 1:
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            self.popover.set_pointing_to(rect)
            self.popover.set_has_arrow(False)
            self.popover.set_parent(self.widget)
            self.popover.popup()

    def on_copy_menu_item_activated(self, _button):
        """Handle activation of the 'Copy' menu item.

        Copies selected content to the clipboard and hides the popover.

        Args:
            _button (Gtk.Button): The button that triggered the event.

        """
        self.copy_to_clipboard()
        self.popover.popdown()

    def on_key_pressed(self, _controller, keyval, _keycode, state):
        """Handle the Ctrl+C keyboard shortcut to copy selected content to the clipboard.

        Args:
            _controller (Gtk.EventControllerKey): The key controller that triggered the event.
            keyval (int): The value of the key pressed.
            _keycode (int): The code of the key pressed.
            state (Gdk.ModifierType): The state of the modifier keys.

        Returns:
            bool: True if the event was handled, False otherwise.

        """
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c:
            self.copy_to_clipboard()
            return True
        return False

    def copy_to_clipboard(self):
        """Copy selected content from the Gtk.ColumnView to the clipboard."""
        if isinstance(self.widget, Gtk.ColumnView):
            selection_model = self.widget.get_model()
            selected_texts = []

            if isinstance(selection_model, Gtk.MultiSelection):
                for index in range(selection_model.get_n_items()):
                    if selection_model.is_selected(index):
                        selected_item = selection_model.get_item(index)
                        selected_texts.append(f"{selected_item.key}: {selected_item.value}")

            elif isinstance(selection_model, Gtk.SingleSelection):
                selected_item = selection_model.get_selected_item()
                if selected_item:
                    selected_texts.append(f"{selected_item.key}: {selected_item.value}")

            if selected_texts:
                clipboard_text = "\n".join(selected_texts)
                clipboard = self.widget.get_clipboard()
                content_provider = Gdk.ContentProvider.new_for_value(clipboard_text)
                clipboard.set_content(content_provider)
