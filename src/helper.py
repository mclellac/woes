"""
Provides a Helper class for enhancing :class:`Gtk.ColumnView` widgets.

This module defines the :class:`.Helper` class, which is designed to be
attached to a :class:`Gtk.ColumnView`. It provides common user interaction
enhancements such as keyboard shortcuts (e.g., ``Ctrl+C`` for copying) and
a right-click context menu, primarily offering a 'Copy' functionality
tailored to the content of the ColumnView items.
"""

import logging
from typing import Optional, Any, Tuple, List

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0") # Ensure Gdk is also specified
from gi.repository import Gdk, Gtk, GObject


logger = logging.getLogger(__name__)


class Helper:
    """
    Augments a :class:`Gtk.ColumnView` with keyboard shortcuts and a context menu.

    This class handles:
    - Setting up ``Ctrl+C`` keyboard shortcut for copying selected cell data.
    - Providing a right-click context menu with a 'Copy' option for the
      clicked cell/row.

    The copy functionality assumes that the items in the :class:`Gtk.ColumnView`'s
    model are :class:`GObject.Object` instances that expose 'key', 'value', and
    optionally 'is_special_row' attributes (duck-typing).

    :ivar widget: The widget this helper is attached to.
    :vartype widget: Gtk.Widget
    :ivar parent_window: The parent window of the widget.
    :vartype parent_window: Gtk.Window
    :ivar last_right_click_coords: Stores the (x, y) coordinates of the last right-click.
    :vartype last_right_click_coords: Optional[Tuple[float, float]]
    :ivar popover: The context menu popover.
    :vartype popover: Optional[Gtk.Popover]
    """

    def __init__(self, widget: Gtk.Widget, parent_window: Gtk.Window):
        """
        Initialize the Helper.

        Attaches event controllers for keyboard shortcuts and context menu
        to the provided widget, if it's a :class:`Gtk.ColumnView`.

        :param widget: The widget to attach helper functionalities to.
                       Expected to be a :class:`Gtk.ColumnView` or a widget
                       that can have controllers and a clipboard.
        :type widget: Gtk.Widget
        :param parent_window: The parent :class:`Gtk.Window` of the widget.
                              (Currently unused but kept for potential future use,
                              e.g., for dialogs relative to the window).
        :type parent_window: Gtk.Window
        """
        self.widget: Gtk.Widget = widget
        self.parent_window: Gtk.Window = parent_window # Retained, though not actively used by current methods
        self.last_right_click_coords: Optional[Tuple[float, float]] = None
        self.popover: Optional[Gtk.Popover] = None

        # Ensure the widget is capable of having controllers added.
        # Gtk.ColumnView is a Gtk.Widget.
        if isinstance(self.widget, Gtk.Widget): # More generic check, ColumnView is a Widget
            self.widget.set_sensitive(True)
            self.setup_keyboard_shortcut()
            self.setup_context_menu()
        else:
            logger.warning("Helper: Provided widget is not a Gtk.Widget, cannot attach controllers.")


    def setup_keyboard_shortcut(self) -> None:
        """
        Set up a keyboard shortcut (Ctrl+C) for copying.

        Connects an event controller to the widget to listen for key presses.
        If ``Ctrl+C`` is detected, it triggers the :meth:`.copy_to_clipboard` method.
        """
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.widget.add_controller(key_controller)

    def setup_context_menu(self) -> None:
        """
        Set up a right-click context menu with a 'Copy' option.

        The menu is a :class:`Gtk.Popover` attached to the widget.
        """
        self.popover = Gtk.Popover.new()
        self.popover.set_parent(self.widget)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self.on_copy_menu_item_activated)
        vbox.append(copy_button)
        self.popover.set_child(vbox)

        gesture = Gtk.GestureClick.new()
        gesture.set_button(Gdk.BUTTON_SECONDARY)  # Standard for right-click
        gesture.connect("pressed", self.on_right_click)
        self.widget.add_controller(gesture)

    def on_right_click(self, _gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        """
        Handle the right-click event on the widget.

        Stores the click coordinates and shows the context menu popover.

        :param gesture: The :class:`Gtk.GestureClick` that triggered the event.
        :type gesture: Gtk.GestureClick
        :param n_press: The number of button presses (should be 1 for context menu).
        :type n_press: int
        :param x: The x-coordinate of the click, relative to the widget.
        :type x: float
        :param y: The y-coordinate of the click, relative to the widget.
        :type y: float
        """
        if n_press == 1:  # Process only single clicks
            self.last_right_click_coords = (x, y)
            if self.popover:
                rect = Gdk.Rectangle()
                rect.x = int(x)
                rect.y = int(y)
                rect.width = 1
                rect.height = 1
                self.popover.setpointing_to(rect)
                self.popover.set_has_arrow(False)
                self.popover.popup()

    def copy_context_item_to_clipboard(self) -> None:
        """
        Copy data from the item at the last right-clicked position to the clipboard.

        This method identifies the :class:`Gtk.ListItem` under the stored mouse
        coordinates from the last right-click. It then retrieves the associated
        :class:`GObject.Object` (data item).

        It expects the data item to have attributes like 'key', 'value', and
        optionally 'is_special_row' (duck-typing). Based on these attributes,
        it formats a string representation:
        - If 'is_special_row' is true: uses 'key' and 'value' (if value is meaningful).
        - If 'key' is an empty string: assumes a continuation line and uses 'value'.
        - Otherwise: formats as "key: value".

        The resulting string is then copied to the system clipboard.
        """
        logger.debug("copy_context_item_to_clipboard called.")
        if not isinstance(self.widget, Gtk.ColumnView):
            logger.warning("Helper: Copy context item called on a widget that is not Gtk.ColumnView.")
            return
        if self.last_right_click_coords is None:
            logger.debug("Pre-conditions not met: last_right_click_coords is None.")
            return

        x_coord, y_coord = self.last_right_click_coords
        logger.debug(f"Coordinates for pick: x={x_coord}, y={y_coord}")

        # Gtk.ColumnView.pick() returns the Gtk.Widget at the given coordinates (e.g., a Gtk.Label within a cell)
        # We need to find the Gtk.ListItem that contains this picked widget.
        list_item_widget: Optional[Gtk.ListItem] = None

        # For ColumnView, it's more about which item is at a certain position if it's a list model.
        # The current traversal method is a reasonable fallback.

        picked_child_widget = self.widget.pick(x_coord, y_coord, Gtk.PickFlags.DEFAULT)
        logger.debug(f"widget.pick result: {picked_child_widget}")

        if picked_child_widget is None:
            logger.debug("No widget picked at coordinates.")
            return

        current_widget: Optional[Gtk.Widget] = picked_child_widget
        while current_widget is not None:
            if isinstance(current_widget, Gtk.ListItem):
                list_item_widget = current_widget
                break
            if current_widget == self.widget:
                logger.debug("Reached ColumnView widget while traversing up, Gtk.ListItem not found.")
                break
            current_widget = current_widget.get_parent()

        if not list_item_widget:
            logger.debug("Gtk.ListItem widget not found by traversing from picked_widget.")
            return
        logger.debug(f"Found target_list_item_widget: {list_item_widget}")

        item_obj: Optional[GObject.Object] = list_item_widget.get_item()

        if item_obj is None:
            logger.debug("item_obj is None from target_list_item_widget.get_item().")
            return
        logger.debug(f"Got item_obj: {item_obj}, type: {type(item_obj)}")

        text_to_copy: str = ""
        try:
            # Duck-typing for item attributes
            key_attr = getattr(item_obj, "key", None)
            value_attr = getattr(item_obj, "value", None)
            is_special_attr = getattr(item_obj, "is_special_row", False)

            logger.debug(f"Retrieved attributes: key='{key_attr}', value='{value_attr}', is_special={is_special_attr}")

            if is_special_attr:
                text_to_copy = str(key_attr if key_attr is not None else "")
                val_str = str(value_attr if value_attr is not None else "")
                if val_str.strip() and val_str.lower() != "n/a": # Avoid appending "N/A"
                    text_to_copy += f" {val_str}"
            elif key_attr == "":  # Assumed continuation line for a multi-part header
                text_to_copy = str(value_attr if value_attr is not None else "")
            else:  # Standard header format "key: value"
                text_to_copy = f"{str(key_attr if key_attr is not None else '')}: {str(value_attr if value_attr is not None else '')}"

            if not text_to_copy.strip():
                logger.debug("Formatted text_to_copy is empty or whitespace.")
                # Potentially provide feedback to user that nothing was copied
                return

            logger.debug(f"Constructed text_to_copy: '{text_to_copy}'")

            clipboard: Gdk.Clipboard = self.widget.get_clipboard()
            if clipboard:
                clipboard.set_text(text_to_copy) # Gdk.Clipboard.set_text() is fine
                logger.info("Successfully copied context item to clipboard.")
            else:
                logger.warning("Failed to get clipboard object for context copy.")

        except AttributeError as e:
            logger.warning(f"AttributeError copying context item: {e}. Item might not have expected attributes.", exc_info=True)
        except Exception as e_gen:
            logger.exception(f"Unexpected error in copy_context_item_to_clipboard: {e_gen}")


    def on_copy_menu_item_activated(self, _button: Gtk.Button) -> None:
        """
        Handle activation of the 'Copy' menu item from the context menu.

        Calls :meth:`.copy_context_item_to_clipboard` to perform the copy operation
        and then closes the popover menu.

        :param button: The :class:`Gtk.Button` from the popover that was clicked (unused).
        :type button: Gtk.Button
        """
        self.copy_context_item_to_clipboard()
        if self.popover:
            self.popover.popdown()

    def on_key_pressed(
        self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, state: Gdk.ModifierType
    ) -> bool:
        """
        Handle key press events on the widget, specifically for ``Ctrl+C``.

        If ``Ctrl+C`` is detected, it triggers :meth:`.copy_to_clipboard`.

        :param controller: The :class:`Gtk.EventControllerKey` (unused).
        :type controller: Gtk.EventControllerKey
        :param keyval: The GDK key value (e.g., :const:`Gdk.KEY_c`).
        :type keyval: int
        :param keycode: The hardware keycode (unused).
        :type keycode: int
        :param state: The GDK modifier state (e.g., :const:`Gdk.ModifierType.CONTROL_MASK`).
        :type state: Gdk.ModifierType
        :return: ``True`` if the event was handled (``Ctrl+C`` pressed), ``False`` otherwise.
        :rtype: bool
        """
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c:
            logger.debug("Ctrl+C pressed, calling copy_to_clipboard.")
            self.copy_to_clipboard()
            return True # Event handled
        return False # Event not handled

    def copy_to_clipboard(self) -> None:
        """
        Copy data from selected items in the :class:`Gtk.ColumnView` to the clipboard.

        This method retrieves the selection from the ColumnView's model. It supports
        both :class:`Gtk.MultiSelection` and :class:`Gtk.SingleSelection`.
        For each selected item, it assumes the item object has 'key', 'value',
        and optionally 'is_special_row' attributes (duck-typing) to format
        a string representation.

        The formatted strings from all selected items are joined by newlines
        and copied to the system clipboard.
        """
        if not isinstance(self.widget, Gtk.ColumnView):
            logger.warning("Helper: copy_to_clipboard called on a widget that is not Gtk.ColumnView.")
            return

        selection_model: Optional[GObject.Object] = self.widget.get_model()
        if not selection_model:
            logger.warning("Helper: No model found on ColumnView for copy_to_clipboard.")
            return

        selected_texts: List[str] = []
        items_to_copy: List[Any] = []

        if isinstance(selection_model, Gtk.MultiSelection):
            selection_bitset: Optional[Gtk.Bitset] = selection_model.get_selection()
            if selection_bitset:
                current_pos = selection_bitset.get_minimum()
                while current_pos != Gtk.INVALID_LIST_POSITION:
                    item = selection_model.get_item(current_pos)
                    if item:
                        items_to_copy.append(item)
                    current_pos = selection_bitset.get_next(current_pos)
        elif isinstance(selection_model, Gtk.SingleSelection):
            item = selection_model.get_selected_item()
            if item:
                items_to_copy.append(item)
        else:
            logger.warning("Helper: ColumnView model is not Gtk.MultiSelection or Gtk.SingleSelection.")
            return

        if not items_to_copy:
            logger.info("Helper: No items selected to copy.")
            return

        for item_obj in items_to_copy:
            # Duck-typing for item attributes, similar to copy_context_item_to_clipboard
            key_attr = getattr(item_obj, "key", None)
            value_attr = getattr(item_obj, "value", None)
            is_special_attr = getattr(item_obj, "is_special_row", False)

            line_to_copy: str = ""
            if is_special_attr:
                line_to_copy = str(key_attr if key_attr is not None else "")
                val_str = str(value_attr if value_attr is not None else "")
                if val_str.strip() and val_str.lower() != "n/a":
                    line_to_copy += f" {val_str}"
            elif key_attr == "":
                line_to_copy = str(value_attr if value_attr is not None else "")
            else:
                line_to_copy = f"{str(key_attr if key_attr is not None else '')}: {str(value_attr if value_attr is not None else '')}"
            selected_texts.append(line_to_copy)

        if selected_texts:
            clipboard_text = "\n".join(selected_texts)
            clipboard: Gdk.Clipboard = self.widget.get_clipboard()
            if clipboard:
                clipboard.set_text(clipboard_text)
                logger.info("Successfully copied %d selected line(s) to clipboard.", len(selected_texts))
            else:
                logger.warning("Failed to get clipboard object for selection copy.")
        else:
            logger.info("No text formatted from selected items to copy.")
