import logging
import gi

gi.require_version('Gtk', '4.0')
# pylint: disable=wrong-import-position
from gi.repository import Gdk, Gtk

# Configure logger for the module - AFTER all imports
logger = logging.getLogger(__name__)


class Helper:
    """
    A helper class to add keyboard shortcuts and context menu functionality
    to a Gtk.ColumnView widget.
    """

    def __init__(self, widget, parent_window):
        """
        Initialize the Helper class.

        Args:
            widget (Gtk.Widget): The widget to which the helper is attached.
            parent_window (Gtk.Window): The parent window containing the widget.
        """
        logger.debug(f"Helper.__init__: Starting with widget: {widget}, parent_window: {parent_window}")
        self.widget = widget
        self.parent_window = parent_window

        if isinstance(self.widget, Gtk.ColumnView):
            logger.debug("Helper.__init__: Widget is Gtk.ColumnView, setting up keyboard shortcut and context menu.")
            logger.debug("Helper.__init__: Before self.setup_keyboard_shortcut()")
            self.setup_keyboard_shortcut() # Logs itself
            logger.debug("Helper.__init__: After self.setup_keyboard_shortcut()")
            logger.debug("Helper.__init__: Before self.setup_context_menu()")
            self.setup_context_menu() # Logs itself
            logger.debug("Helper.__init__: After self.setup_context_menu()")
        else:
            logger.debug("Helper.__init__: Widget is not Gtk.ColumnView, skipping setup.")
        logger.debug("Helper.__init__: Finished.")

    def setup_keyboard_shortcut(self):
        """
        Set up a keyboard shortcut (Ctrl+C) for copying selected content from
        the Gtk.ColumnView to the clipboard.
        """
        logger.debug("Helper.setup_keyboard_shortcut: Starting.")
        key_controller = Gtk.EventControllerKey()
        logger.debug(f"Helper.setup_keyboard_shortcut: Gtk.EventControllerKey created: {key_controller}")
        logger.debug("Helper.setup_keyboard_shortcut: Before key_controller.connect('key-pressed')")
        key_controller.connect("key-pressed", self.on_key_pressed)
        logger.debug("Helper.setup_keyboard_shortcut: After key_controller.connect('key-pressed')")
        self.widget.add_controller(key_controller)
        logger.debug(f"Helper.setup_keyboard_shortcut: Added key_controller to widget {self.widget}")
        logger.debug("Helper.setup_keyboard_shortcut: Finished.")

    def setup_context_menu(self):
        """
        Set up a context menu that appears on right-click and provides a copy option.
        """
        logger.debug("Helper.setup_context_menu: Starting.")
        self.popover = Gtk.Popover.new()
        logger.debug(f"Helper.setup_context_menu: Gtk.Popover created: {self.popover}")
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        logger.debug(f"Helper.setup_context_menu: Gtk.Box created: {vbox}")
        copy_button = Gtk.Button(label="Copy")
        logger.debug(f"Helper.setup_context_menu: Gtk.Button 'Copy' created: {copy_button}")
        logger.debug("Helper.setup_context_menu: Before copy_button.connect('clicked')")
        copy_button.connect("clicked", self.on_copy_menu_item_activated)
        logger.debug("Helper.setup_context_menu: After copy_button.connect('clicked')")
        vbox.append(copy_button)
        logger.debug("Helper.setup_context_menu: Appended copy_button to vbox.")
        self.popover.set_child(vbox)
        logger.debug("Helper.setup_context_menu: Set vbox as child of popover.")

        gesture = Gtk.GestureClick()
        logger.debug(f"Helper.setup_context_menu: Gtk.GestureClick created: {gesture}")
        gesture.set_button(3) # Right click
        logger.debug("Helper.setup_context_menu: Gesture button set to 3 (right-click).")
        logger.debug("Helper.setup_context_menu: Before gesture.connect('pressed')")
        gesture.connect("pressed", self.on_right_click)
        logger.debug("Helper.setup_context_menu: After gesture.connect('pressed')")
        self.widget.add_controller(gesture)
        logger.debug(f"Helper.setup_context_menu: Added gesture controller to widget {self.widget}")
        logger.debug("Helper.setup_context_menu: Finished.")

    def on_right_click(self, _gesture, n_press, x, y):
        """
        Display the context menu popover at the location of the mouse click.

        Args:
            n_press (int): The number of mouse button presses.
            x (float): The x-coordinate of the mouse click relative to the widget.
            y (float): The y-coordinate of the mouse click relative to the widget.
        """
        logger.debug(f"Helper.on_right_click: Triggered with gesture: {_gesture}, n_press: {n_press}, x: {x}, y: {y}")
        if n_press == 1:
            logger.debug("Helper.on_right_click: n_press is 1, proceeding to show popover.")
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            logger.debug(f"Helper.on_right_click: Created Gdk.Rectangle: {rect.x},{rect.y} {rect.width}x{rect.height}")
            self.popover.set_pointing_to(rect)
            logger.debug("Helper.on_right_click: Popover pointing_to set.")
            self.popover.set_has_arrow(False)
            logger.debug("Helper.on_right_click: Popover has_arrow set to False.")
            self.popover.set_parent(self.widget)
            logger.debug(f"Helper.on_right_click: Popover parent set to widget: {self.widget}")
            self.popover.popup()
            logger.debug("Helper.on_right_click: Popover shown with popup().")
        else:
            logger.debug(f"Helper.on_right_click: n_press is {n_press}, not showing popover.")
        logger.debug("Helper.on_right_click: Finished.")

    def on_copy_menu_item_activated(self, _button):
        """
        Handle the activation of the copy menu item by copying selected content
        to the clipboard and hiding the popover.

        Args:
            _button (Gtk.Button): The button that triggered the event. (Keeping for clarity on source)
        """
        logger.debug(f"Helper.on_copy_menu_item_activated: Triggered by button: {_button}")
        logger.debug("Helper.on_copy_menu_item_activated: Before self.copy_to_clipboard()")
        self.copy_to_clipboard() # Logs itself
        logger.debug("Helper.on_copy_menu_item_activated: After self.copy_to_clipboard()")
        self.popover.popdown()
        logger.debug("Helper.on_copy_menu_item_activated: Popover popdown() called.")
        logger.debug("Helper.on_copy_menu_item_activated: Finished.")

    def on_key_pressed(self, _controller, keyval, _keycode, state):
        """
        Handle the Ctrl+C keyboard shortcut to copy selected content to the clipboard.

        Args:
            keyval (int): The value of the key pressed.
            state (Gdk.ModifierType): The state of the modifier keys.
            # _controller and _keycode are unused.

        Returns:
            bool: True if the event was handled, False otherwise.
        """
        logger.debug(f"Helper.on_key_pressed: Triggered with controller: {_controller}, keyval: {keyval}, keycode: {_keycode}, state: {state}")
        is_ctrl_c = bool(state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c)
        logger.debug(f"Helper.on_key_pressed: Is Ctrl+C: {is_ctrl_c}")
        if is_ctrl_c:
            logger.debug("Helper.on_key_pressed: Ctrl+C detected, calling copy_to_clipboard().")
            self.copy_to_clipboard() # Logs itself
            logger.debug("Helper.on_key_pressed: Returning True (event handled).")
            return True
        logger.debug("Helper.on_key_pressed: Returning False (event not handled).")
        return False

    def copy_to_clipboard(self):
        """
        Copy the selected content from the Gtk.ColumnView to the clipboard.
        """
        logger.debug("Helper.copy_to_clipboard: Starting.")
        if isinstance(self.widget, Gtk.ColumnView):
            selection_model = self.widget.get_model()
            logger.debug(f"Helper.copy_to_clipboard: Widget is Gtk.ColumnView. Selection model: {selection_model} (type: {type(selection_model)})")
            selected_texts = []

            if isinstance(selection_model, Gtk.MultiSelection):
                logger.debug("Helper.copy_to_clipboard: Model is Gtk.MultiSelection.")
                for index in range(selection_model.get_n_items()):
                    if selection_model.is_selected(index):
                        selected_item = selection_model.get_item(index)
                        # Assuming selected_item has 'key' and 'value' attributes as per HttpPage's HeaderItem example
                        item_text = f"{getattr(selected_item, 'key', 'N/A')}: {getattr(selected_item, 'value', 'N/A')}"
                        selected_texts.append(item_text)
                        logger.debug(f"Helper.copy_to_clipboard: Selected item at index {index}: '{item_text}'")

            elif isinstance(selection_model, Gtk.SingleSelection):
                logger.debug("Helper.copy_to_clipboard: Model is Gtk.SingleSelection.")
                selected_item = selection_model.get_selected_item()
                if selected_item:
                    item_text = f"{getattr(selected_item, 'key', 'N/A')}: {getattr(selected_item, 'value', 'N/A')}"
                    selected_texts.append(item_text)
                    logger.debug(f"Helper.copy_to_clipboard: Selected item: '{item_text}'")
                else:
                    logger.debug("Helper.copy_to_clipboard: No item selected in SingleSelection model.")
            else:
                logger.warning(f"Helper.copy_to_clipboard: Selection model is of unexpected type: {type(selection_model)}")


            if selected_texts:
                clipboard_text = "\n".join(selected_texts)
                logger.debug(f"Helper.copy_to_clipboard: Text to copy to clipboard (len {len(clipboard_text)}): '{clipboard_text[:100]}...'")
                clipboard = self.widget.get_clipboard()
                logger.debug(f"Helper.copy_to_clipboard: Got clipboard: {clipboard}")
                content_provider = Gdk.ContentProvider.new_for_value(clipboard_text)
                logger.debug(f"Helper.copy_to_clipboard: Gdk.ContentProvider created: {content_provider}")
                clipboard.set_content(content_provider)
                logger.debug("Helper.copy_to_clipboard: Clipboard content set.")
            else:
                logger.debug("Helper.copy_to_clipboard: No text selected to copy.")
        else:
            logger.warning(f"Helper.copy_to_clipboard: Widget is not Gtk.ColumnView, cannot copy. Widget type: {type(self.widget)}")
        logger.debug("Helper.copy_to_clipboard: Finished.")
