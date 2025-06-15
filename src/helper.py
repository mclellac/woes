"""Provides a Helper class for :class:`Gtk.ColumnView` context menus and keyboard shortcuts."""
from typing import Optional, List
from gi.repository import Gdk, Gtk, GObject
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
        """Initialize the Helper class.

        :param widget: The widget to which the helper is attached. This is
                       expected to be a :class:`Gtk.ColumnView` for full
                       functionality.
        :type widget: Gtk.Widget
        :param parent_window: The parent window containing the widget.
        :type parent_window: Gtk.Window
        """
        self.widget: Gtk.Widget = widget
        self.parent_window: Gtk.Window = parent_window
        self.last_right_click_coords: Optional[tuple[float, float]] = None
        self.popover: Optional[Gtk.Popover] = None


        if isinstance(self.widget, Gtk.ColumnView):
            self.widget.set_sensitive(True) # Ensure widget can receive events
            self.setup_keyboard_shortcut()
            self.setup_context_menu()

    def setup_keyboard_shortcut(self) -> None:
        """Set up a keyboard shortcut (``Ctrl+C``) for copying.

        This allows copying selected content from the :class:`Gtk.ColumnView`
        to the clipboard using the ``Ctrl+C`` combination.

        :return: None
        """
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.widget.add_controller(key_controller)

    def setup_context_menu(self) -> None:
        """Set up a context menu (right-click) with a 'Copy' option.

        :return: None
        """
        self.popover = Gtk.Popover.new()
        if isinstance(self.widget, Gtk.Widget):
            self.popover.set_parent(self.widget)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self.on_copy_menu_item_activated)
        vbox.append(copy_button)
        self.popover.set_child(vbox)

        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)  # Right-click
        gesture.connect("pressed", self.on_right_click)
        self.widget.add_controller(gesture)

    def on_right_click(self, _gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        """Display the context menu popover at the location of the mouse click.

        Called when the right-click gesture is detected on the widget.

        :param _gesture: The gesture that triggered the event (unused).
        :param n_press: The number of mouse button presses.
        :type n_press: int
        :param x: The x-coordinate of the mouse click relative to the widget.
        :type x: float
        :param y: The y-coordinate of the mouse click relative to the widget.
        :type y: float
        """
        if n_press == 1:  # Process only single clicks for context menu
            self.last_right_click_coords = (x, y)
            if self.popover:
                rect = Gdk.Rectangle()
                rect.x = int(x)
                rect.y = int(y)
                rect.width = 1
                rect.height = 1
                self.popover.set_pointing_to(rect)
                self.popover.set_has_arrow(False)
                self.popover.popup()

    def copy_context_item_to_clipboard(self) -> None:
        """Copy the content of the right-clicked item to the clipboard.

        This method uses the coordinates stored from the last right-click event
        to identify the specific :class:`Gtk.ListItem` and its underlying data item
        (e.g., a `HeaderItem` from `http_page.py`). It then formats the text
        based on the item's attributes (special row, continuation line, or
        standard header) and copies it to the clipboard.

        :return: None
        """
        if not isinstance(self.widget, Gtk.ColumnView) or \
           not hasattr(self, 'last_right_click_coords') or \
           self.last_right_click_coords is None:
            return

        x_coord, y_coord = self.last_right_click_coords

        picked_widget = self.widget.pick(x_coord, y_coord, Gtk.PickFlags.DEFAULT)

        if picked_widget is None:
            return

        target_list_item_widget = None
        current_widget: Optional[Gtk.Widget] = picked_widget
        # Traverse up to find the Gtk.ListItem.
        while current_widget is not None:
            if isinstance(current_widget, Gtk.ListItem):
                target_list_item_widget = current_widget
                break
            if current_widget == self.widget: # Stop if we've reached the ColumnView itself
                break
            current_widget = current_widget.get_parent()

        if not target_list_item_widget:
            return

        item_obj: Optional[GObject.Object] = target_list_item_widget.get_item()

        if item_obj is None:
            return

        text_to_copy = ""
        try:
            # Assuming item_obj is HeaderItem-like (has 'key', 'value', 'is_special_row')
            key_attr = getattr(item_obj, 'key', None)
            value_attr = getattr(item_obj, 'value', None)
            is_special_attr = getattr(item_obj, 'is_special_row', False)

            if is_special_attr:
                text_to_copy = str(key_attr if key_attr is not None else '')
                val_str = str(value_attr if value_attr is not None else '')
                if val_str.strip() and val_str != "N/A":
                    text_to_copy += f" {val_str}"
            elif key_attr == "": # Continuation line for a multi-part header
                text_to_copy = str(value_attr if value_attr is not None else '')
            else: # Standard header (key: value)
                text_to_copy = f"{str(key_attr if key_attr is not None else '')}: {str(value_attr if value_attr is not None else '')}"

            clipboard = self.widget.get_clipboard()
            if clipboard:
                content_provider = Gdk.ContentProvider.new_for_value(text_to_copy)
                clipboard.set_content(content_provider)

        except AttributeError:
            # This might happen if item_obj is not a HeaderItem-like object.
            # Consider logging this error for debugging if necessary.
            pass

    def on_copy_menu_item_activated(self, _button: Gtk.Button) -> None:
        """Handle activation of the 'Copy' menu item.

        Copies the right-clicked item's content to the clipboard and hides the popover.

        :param _button: The :class:`Gtk.Button` that triggered the event (unused).
        :return: None
        """
        self.copy_context_item_to_clipboard()
        if self.popover:
            self.popover.popdown()

    def on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType
    ) -> bool:
        """
        Handle the ``Ctrl+C`` keyboard shortcut to copy selected content.

        :param _controller: The key controller that triggered the event (unused).
        :type _controller: Gtk.EventControllerKey
        :param keyval: The value of the key pressed (e.g., :attr:`Gdk.KEY_c`).
        :type keyval: int
        :param _keycode: The hardware keycode of the key pressed (unused).
        :type _keycode: int
        :param state: The state of the modifier keys (e.g., :attr:`Gdk.ModifierType.CONTROL_MASK`).
        :type state: Gdk.ModifierType
        :return: ``True`` if the ``Ctrl+C`` event was handled, ``False`` otherwise.
        :rtype: bool
        """
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c:
            self.copy_to_clipboard()
            return True
        return False

    def copy_to_clipboard(self) -> None:
        """Copy selected content from the :class:`Gtk.ColumnView` to the clipboard.

        Assumes the items in the :class:`Gtk.ColumnView` model have 'key' and
        'value' attributes to construct the string. This method formats the copied text
        similarly to `copy_context_item_to_clipboard` for consistency, handling
        special rows, continuation lines, and standard headers.
        Handles both :class:`Gtk.MultiSelection` and :class:`Gtk.SingleSelection` models.

        :return: None
        """
        if not isinstance(self.widget, Gtk.ColumnView):
            return

        selection_model = self.widget.get_model()
        selected_texts: List[str] = []

        items_to_copy: List[GObject.Object] = [] # type: ignore
        if isinstance(selection_model, Gtk.MultiSelection):
            selection_bitset = selection_model.get_selection()
            current_pos = selection_bitset.get_minimum()
            while current_pos != Gtk.INVALID_LIST_POSITION: # type: ignore[comparison-overlap]
                item = selection_model.get_item(current_pos) # type: ignore
                if item:
                    items_to_copy.append(item) # type: ignore
                current_pos = selection_bitset.get_next(current_pos)
        elif isinstance(selection_model, Gtk.SingleSelection):
            item = selection_model.get_selected_item() # type: ignore
            if item:
                items_to_copy.append(item) # type: ignore

        for item_obj in items_to_copy:
            if item_obj and hasattr(item_obj, 'key') and hasattr(item_obj, 'value'):
                key_attr = getattr(item_obj, 'key', None)
                value_attr = getattr(item_obj, 'value', None)
                is_special_attr = getattr(item_obj, 'is_special_row', False)

                line_to_copy = ""
                if is_special_attr:
                    line_to_copy = str(key_attr if key_attr is not None else '')
                    val_str = str(value_attr if value_attr is not None else '')
                    if val_str.strip() and val_str != "N/A":
                        line_to_copy += f" {val_str}"
                elif key_attr == "": # Continuation line
                    line_to_copy = str(value_attr if value_attr is not None else '')
                else: # Standard header
                    line_to_copy = f"{str(key_attr if key_attr is not None else '')}: {str(value_attr if value_attr is not None else '')}"
                selected_texts.append(line_to_copy)

        if selected_texts:
            clipboard_text = "\n".join(selected_texts)
            clipboard = self.widget.get_clipboard()
            if clipboard:
                content_provider = Gdk.ContentProvider.new_for_value(clipboard_text)
                clipboard.set_content(content_provider)
