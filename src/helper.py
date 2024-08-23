import gi
from gi.repository import Gtk, Gdk, Gio

class Helper:
    def __init__(self, widget, parent_window):
        self.widget = widget
        self.parent_window = parent_window

        if isinstance(self.widget, Gtk.ColumnView):
            self.setup_keyboard_shortcut()
            self.setup_context_menu()

    def setup_keyboard_shortcut(self):
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.widget.add_controller(key_controller)

    def setup_context_menu(self):
        # Directly attach the popover to the widget
        self.popover = Gtk.Popover.new()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self.on_copy_menu_item_activated)
        vbox.append(copy_button)
        self.popover.set_child(vbox)

        gesture = Gtk.GestureClick()
        gesture.set_button(3)  # Right-click
        gesture.connect("pressed", self.on_right_click)
        self.widget.add_controller(gesture)

    def on_right_click(self, gesture, n_press, x, y):
        if n_press == 1:
            # Set the popover to show at the location of the mouse click
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            self.popover.set_pointing_to(rect)
            self.popover.set_has_arrow(False)
            self.popover.set_parent(self.widget)
            self.popover.popup()

    def on_copy_menu_item_activated(self, button):
        self.copy_to_clipboard()
        self.popover.popdown()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_c:
            self.copy_to_clipboard()
            return True
        return False

    def copy_to_clipboard(self):
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

