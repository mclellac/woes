"""Provides a Helper class for :class:`Gtk.ColumnView` context menus and keyboard shortcuts."""

import logging
from typing import Optional
from gi.repository import Gdk, Gtk, GObject
import gi

gi.require_version("Gtk", "4.0")

logger = logging.getLogger(__name__)


class Helper:
    """
    Provides keyboard shortcuts and context menu for :class:`Gtk.ColumnView`.

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
        :param parent_window: The parent :class:`Gtk.Window` containing the widget.
        :type parent_window: Gtk.Window
        """
        self.widget: Gtk.Widget = widget
        self.parent_window: Gtk.Window = parent_window
        self.last_right_click_coords: Optional[tuple[float, float]] = None
        self.popover: Optional[Gtk.Popover] = None

        if isinstance(self.widget, Gtk.ColumnView):
            self.widget.set_sensitive(True)  # Ensure widget can receive events
            self.setup_keyboard_shortcut()
            self.setup_context_menu()

    def setup_keyboard_shortcut(self) -> None:
        """
        Set up a keyboard shortcut (``Ctrl+C``) for copying.

        This allows copying selected content from the :class:`Gtk.ColumnView`
        to the clipboard using the ``Ctrl+C`` combination.

        :return: None
        :rtype: None
        """
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self.on_key_pressed)  # type: ignore[no-untyped-call]
        self.widget.add_controller(key_controller)  # type: ignore[no-untyped-call]

    def setup_context_menu(self) -> None:
        """
        Set up a context menu (right-click) with a 'Copy' option.

        :return: None
        :rtype: None
        """
        self.popover = Gtk.Popover.new()
        if isinstance(self.widget, Gtk.Widget):
            self.popover.set_parent(self.widget)  # type: ignore[no-untyped-call]

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self.on_copy_menu_item_activated)  # type: ignore[no-untyped-call]
        vbox.append(copy_button)  # type: ignore[no-untyped-call]
        self.popover.set_child(vbox)  # type: ignore[no-untyped-call]

        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)  # Right-click # type: ignore[no-untyped-call]
        gesture.connect("pressed", self.on_right_click)  # type: ignore[no-untyped-call]
        self.widget.add_controller(gesture)  # type: ignore[no-untyped-call]

    def on_right_click(self, _gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        """
        Display the context menu popover at the location of the mouse click.

        Called when the right-click gesture is detected on the widget.

        :param _gesture: The :class:`Gtk.GestureClick` that triggered the event (unused).
        :type _gesture: Gtk.GestureClick
        :param n_press: The number of mouse button presses.
        :type n_press: int
        :param x: The x-coordinate of the mouse click relative to the widget.
        :type x: float
        :param y: The y-coordinate of the mouse click relative to the widget.
        :type y: float
        :rtype: None
        """
        if n_press == 1:  # Process only single clicks for context menu
            self.last_right_click_coords = (x, y)
            if self.popover:
                rect = Gdk.Rectangle()
                rect.x = int(x)
                rect.y = int(y)
                rect.width = 1
                rect.height = 1
                self.popover.set_pointing_to(rect)  # type: ignore[no-untyped-call]
                self.popover.set_has_arrow(False)  # type: ignore[no-untyped-call]
                self.popover.popup()  # type: ignore[no-untyped-call]

    def copy_context_item_to_clipboard(self) -> None:
        """
        Copy the content of the right-clicked item to the clipboard.

        This method uses the coordinates stored from the last right-click event
        to identify the specific :class:`Gtk.ListItem` and its underlying data item
        (e.g., a `HeaderItem` from `http_page.py`). It then formats the text
        based on the item's attributes (special row, continuation line, or
        standard header) and copies it to the clipboard.

        :return: None
        :rtype: None
        """
        logger.debug("copy_context_item_to_clipboard called.")
        if (
            not isinstance(self.widget, Gtk.ColumnView)
            or not hasattr(self, "last_right_click_coords")
            or self.last_right_click_coords is None
        ):
            logger.debug("Pre-conditions not met for copy_context_item_to_clipboard (widget type, coords).")
            return

        x_coord, y_coord = self.last_right_click_coords
        logger.debug(f"Coordinates for pick: x={x_coord}, y={y_coord}")

        picked_widget = self.widget.pick(x_coord, y_coord, Gtk.PickFlags.DEFAULT)  # type: ignore[no-untyped-call]
        logger.debug(f"widget.pick result: {picked_widget}")

        if picked_widget is None:
            logger.debug("No widget picked at coordinates.")
            return

        target_list_item_widget: Optional[Gtk.ListItem] = None
        current_widget: Optional[Gtk.Widget] = picked_widget
        # Traverse up to find the Gtk.ListItem.
        while current_widget is not None:
            logger.debug(f"Widget traversal: current_widget is {type(current_widget)}")
            if isinstance(current_widget, Gtk.ListItem):
                target_list_item_widget = current_widget
                break
            if current_widget == self.widget:  # Stop if we've reached the ColumnView itself
                logger.debug("Reached ColumnView widget while traversing up, Gtk.ListItem not found in path.")
                break
            current_widget = current_widget.get_parent()

        if not target_list_item_widget:
            logger.debug("Gtk.ListItem widget not found by traversing up from picked_widget.")
            return
        logger.debug(f"Found target_list_item_widget: {target_list_item_widget}")

        item_obj: Optional[GObject.Object] = target_list_item_widget.get_item()

        if item_obj is None:
            logger.debug("item_obj is None from target_list_item_widget.get_item().")
            return
        logger.debug(f"Got item_obj: {item_obj}, type: {type(item_obj)}")

        text_to_copy: str = ""
        try:
            logger.debug("Attempting to treat item_obj as HeaderItem-like (attributes: key, value, is_special_row).")
            key_attr = getattr(item_obj, "key", None)
            value_attr = getattr(item_obj, "value", None)
            is_special_attr = getattr(item_obj, "is_special_row", False)
            logger.debug(f"Retrieved attributes: key='{key_attr}', value='{value_attr}', is_special={is_special_attr}")

            if is_special_attr:
                text_to_copy = str(key_attr if key_attr is not None else "")
                val_str = str(value_attr if value_attr is not None else "")
                if val_str.strip() and val_str != "N/A":
                    text_to_copy += f" {val_str}"
            elif key_attr == "":  # Continuation line for a multi-part header
                text_to_copy = str(value_attr if value_attr is not None else "")
            else:  # Standard header (key: value)
                text_to_copy = f"{str(key_attr if key_attr is not None else '')}: {str(value_attr if value_attr is not None else '')}"

            if not text_to_copy:
                logger.debug("text_to_copy is empty after attribute processing.")
            else:
                logger.debug(f"Constructed text_to_copy: '{text_to_copy}'")

            # Correct way to get clipboard
            display = self.widget.get_display()
            clipboard = Gdk.Display.get_clipboard(display)  # Use Gdk.Display.get_clipboard(display)

            if clipboard:
                clipboard.set_text(
                    text_to_copy, -1
                )  # Use set_text for simplicity if Gdk.ContentProvider is complex here
                # content_provider = Gdk.ContentProvider.new_for_value(text_to_copy)
                # clipboard.set_content(content_provider)  # type: ignore[no-untyped-call] # Keep if set_text not preferred
                logger.debug("Successfully set clipboard content.")
            else:
                logger.warning("Failed to get clipboard object.")

        except AttributeError as e:
            logger.warning(f"AttributeError in copy_context_item_to_clipboard: {e}")

    def on_copy_menu_item_activated(self, _button: Gtk.Button) -> None:
        """
        Handle activation of the 'Copy' menu item.

        Copies the right-clicked item's content to the clipboard and hides the popover.

        :param _button: The :class:`Gtk.Button` that triggered the event (unused).
        :type _button: Gtk.Button
        :return: None
        :rtype: None
        """
        self.copy_context_item_to_clipboard()
        if self.popover:
            self.popover.popdown()  # type: ignore[no-untyped-call]

    def on_key_pressed(
        self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, state: Gdk.ModifierType
    ) -> bool:
        """
        Handle the ``Ctrl+C`` keyboard shortcut to copy selected content.

        :param _controller: The :class:`Gtk.EventControllerKey` that triggered the event (unused).
        :type _controller: Gtk.EventControllerKey
        :param keyval: The value of the key pressed (e.g., :const:`Gdk.KEY_c`).
        :type keyval: int
        :param _keycode: The hardware keycode of the key pressed (unused).
        :type _keycode: int
        :param state: The state of the modifier keys (e.g., :const:`Gdk.ModifierType.CONTROL_MASK`).
        :type state: Gdk.ModifierType
        :return: ``True`` if the ``Ctrl+C`` event was handled, ``False`` otherwise.
        :rtype: bool
        """
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c:
            self.copy_to_clipboard()
            return True
        return False

    def copy_to_clipboard(self) -> None:
        """
        Copy selected content from the :class:`Gtk.ColumnView` to the clipboard.

        Assumes the items in the :class:`Gtk.ColumnView` model have 'key' and
        'value' attributes to construct the string. This method formats the copied text
        similarly to :meth:`.copy_context_item_to_clipboard` for consistency, handling
        special rows, continuation lines, and standard headers.
        Handles both :class:`Gtk.MultiSelection` and :class:`Gtk.SingleSelection` models.

        :return: None
        :rtype: None
        """
        if not isinstance(self.widget, Gtk.ColumnView):
            return

        selection_model: Optional[GObject.Object] = self.widget.get_model()
        selected_texts: list[str] = []

        items_to_copy: list[GObject.Object] = []
        if isinstance(selection_model, Gtk.MultiSelection):
            selection_bitset: Gtk.Bitset = selection_model.get_selection()
            current_pos = selection_bitset.get_minimum()
            while current_pos != Gtk.INVALID_LIST_POSITION:
                item = selection_model.get_item(current_pos)  # type: ignore[call-overload] # Common for Gtk.SelectionModel
                if item:
                    items_to_copy.append(item)
                current_pos = selection_bitset.get_next(current_pos)
        elif isinstance(selection_model, Gtk.SingleSelection):
            item = selection_model.get_selected_item()  # type: ignore[call-overload] # Common for Gtk.SelectionModel
            if item:
                items_to_copy.append(item)

        for item_obj in items_to_copy:
            if item_obj and hasattr(item_obj, "key") and hasattr(item_obj, "value"):
                key_attr = getattr(item_obj, "key", None)
                value_attr = getattr(item_obj, "value", None)
                is_special_attr = getattr(item_obj, "is_special_row", False)

                line_to_copy: str = ""
                if is_special_attr:
                    line_to_copy = str(key_attr if key_attr is not None else "")
                    val_str = str(value_attr if value_attr is not None else "")
                    if val_str.strip() and val_str != "N/A":
                        line_to_copy += f" {val_str}"
                elif key_attr == "":  # Continuation line
                    line_to_copy = str(value_attr if value_attr is not None else "")
                else:  # Standard header
                    line_to_copy = f"{str(key_attr if key_attr is not None else '')}: {str(value_attr if value_attr is not None else '')}"
                selected_texts.append(line_to_copy)

        if selected_texts:
            clipboard_text = "\n".join(selected_texts)
            display = self.widget.get_display()
            clipboard = Gdk.Display.get_clipboard(display)  # Correct way to get clipboard
            if clipboard:
                clipboard.set_text(clipboard_text, -1)  # Use set_text for simplicity
                # content_provider = Gdk.ContentProvider.new_for_value(clipboard_text)
                # clipboard.set_content(content_provider) # type: ignore[no-untyped-call] # Keep if set_text not preferred
