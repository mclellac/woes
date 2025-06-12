"""Provides utility classes and functions for the Woes application.

Currently, this module includes the :class:`Helper` class, which adds
common UI enhancements like context menus and keyboard shortcuts
(e.g., ``Ctrl+C`` for copying) to :class:`Gtk.ColumnView` widgets.
Future general-purpose helper functions or classes may also be added here.
"""
from gi.repository import Gdk, Gtk
import gi

gi.require_version("Gtk", "4.0")


class Helper:
    """Enhances :class:`Gtk.ColumnView` with keyboard shortcuts and a context menu.

    This class provides common clipboard operations (copying via ``Ctrl+C``)
    and a right-click context menu featuring a 'Copy' action for items
    within a :class:`Gtk.ColumnView`. It is designed to be attached to a
    specific widget instance.
    """

    def __init__(self, widget: Gtk.Widget, parent_window: Gtk.Window):
        """Initializes the Helper class.

        Attaches keyboard shortcut handling and context menu setup if the provided
        widget is a :class:`Gtk.ColumnView`.

        :param widget: The widget to which the helper is attached. This is
                       expected to be a :class:`Gtk.ColumnView` for full
                       functionality.
        :type widget: Gtk.Widget
        :param parent_window: The parent window containing the widget. Used for
                              popover positioning if needed, though currently popover
                              is parented to the widget itself.
        :type parent_window: Gtk.Window
        """
        self.widget = widget
        self.parent_window = parent_window # Retained for potential future use
        self.popover: Gtk.Popover = Gtk.Popover.new()

        if isinstance(self.widget, Gtk.ColumnView):
            self.setup_keyboard_shortcut()
            self.setup_context_menu()

    def setup_keyboard_shortcut(self) -> None:
        """Sets up a keyboard shortcut (``Ctrl+C``) for copying.

        Connects a :class:`Gtk.EventControllerKey` to the widget to listen for
        ``Ctrl+C`` key presses, triggering the copy operation.

        :return: None
        :rtype: None
        """
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.widget.add_controller(key_controller)

    def setup_context_menu(self) -> None:
        """Sets up a right-click context menu with a 'Copy' option.

        A :class:`Gtk.Popover` is created with a 'Copy' button. A
        :class:`Gtk.GestureClick` controller is attached to the widget to
        show this popover on right-click.

        :return: None
        :rtype: None
        """
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self.on_copy_menu_item_activated)
        vbox.append(copy_button)
        self.popover.set_child(vbox)

        gesture = Gtk.GestureClick.new()
        gesture.set_button(Gdk.BUTTON_SECONDARY) # Explicitly set to right-click
        gesture.connect("pressed", self.on_right_click)
        self.widget.add_controller(gesture)

    def on_right_click(self, gesture: Gtk.GestureClick, n_press: int,
                       x: float, y: float) -> None:
        """Displays the context menu popover at the location of the mouse click.

        Called when the right-click gesture is detected on the widget.

        :param gesture: The gesture that triggered the event.
        :type gesture: Gtk.GestureClick
        :param n_press: The number of mouse button presses (should be 1 for context menu).
        :type n_press: int
        :param x: The x-coordinate of the mouse click relative to the widget.
        :type x: float
        :param y: The y-coordinate of the mouse click relative to the widget.
        :type y: float
        :return: None
        :rtype: None
        """
        _ = gesture  # Mark as intentionally unused
        if n_press == 1:
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            self.popover.set_pointing_to(rect)
            self.popover.set_has_arrow(False) # Optional: remove arrow for cleaner look
            self.popover.set_parent(self.widget)
            self.popover.popup()

    def on_copy_menu_item_activated(self, button: Gtk.Button) -> None:
        """Handles activation of the 'Copy' menu item from the context menu.

        Triggers the copy operation and then hides the popover.

        :param button: The :class:`Gtk.Button` from the context menu that was clicked.
        :type button: Gtk.Button
        :return: None
        :rtype: None
        """
        _ = button  # Mark as intentionally unused
        self.copy_to_clipboard()
        self.popover.popdown()

    def on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType
    ) -> bool:
        """Handles key press events to implement the ``Ctrl+C`` shortcut.

        :param controller: The key controller that triggered the event.
        :type controller: Gtk.EventControllerKey
        :param keyval: The GDK key value of the pressed key (e.g., Gdk.KEY_c).
        :type keyval: int
        :param keycode: The hardware keycode of the pressed key.
        :type keycode: int
        :param state: The state of modifier keys (e.g., Gdk.ModifierType.CONTROL_MASK).
        :type state: Gdk.ModifierType
        :return: True if the ``Ctrl+C`` event was handled (and propagation should stop),
                 False otherwise.
        :rtype: bool
        """
        _ = controller, keycode  # Mark as intentionally unused
        # Check for Ctrl+C combination
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c:
            self.copy_to_clipboard()
            return True # Event handled
        return False # Event not handled, allow further processing

    def copy_to_clipboard(self) -> None:
        """Copies selected content from the :class:`Gtk.ColumnView` to the clipboard.

        This method identifies selected items in the :class:`Gtk.ColumnView`'s model.
        It assumes that items in the model are GObjects with 'key' and 'value'
        properties (or attributes that can be accessed as such), which are then
        formatted as "key: value" strings for copying.
        It supports both :class:`Gtk.MultiSelection` and :class:`Gtk.SingleSelection`
        models. If items are selected, their string representations are joined by
        newlines and placed onto the system clipboard using Gdk.Clipboard.

        :return: None
        :rtype: None
        """
        if not isinstance(self.widget, Gtk.ColumnView):
            return

        selection_model = self.widget.get_model()
        selected_texts = []

        if isinstance(selection_model, Gtk.MultiSelection):
            # Iterate through all items and check if selected
            for i in range(selection_model.get_n_items()):
                if selection_model.is_selected(i):
                    item = selection_model.get_item(i)
                    if item and hasattr(item, 'key') and hasattr(item, 'value'):
                        selected_texts.append(f"{item.key}: {item.value}")
        elif isinstance(selection_model, Gtk.SingleSelection):
            item = selection_model.get_selected_item()
            if item and hasattr(item, 'key') and hasattr(item, 'value'):
                selected_texts.append(f"{item.key}: {item.value}")

        if selected_texts:
            clipboard_text = "\n".join(selected_texts)
            clipboard = self.widget.get_clipboard() # Gtk.Widget.get_clipboard()
            if clipboard: # Clipboard might not be available in all environments
                content_provider = Gdk.ContentProvider.new_for_value(clipboard_text)
                clipboard.set_content(content_provider)
            else:
                # Fallback or logging if clipboard is not accessible
                print(f"Debug: Clipboard not available for widget {self.widget}")
        else:
            # Optional: Provide feedback if nothing was selected to copy
            # print("Debug: No items selected or items lack key/value for copying.")
            pass
