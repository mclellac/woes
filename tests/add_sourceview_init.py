import re

webscan_py_path = "src/webscan_page.py"
with open(webscan_py_path, "r") as f:
    py_content = f.read()

# Marker for insertion point in __init__
init_super_call_marker = "super().__init__(**kwargs)"
# Code to insert
init_insertion_code = """
        super().__init__(**kwargs)
        self.source_view = GtkSource.View()
        # Every GtkSource.View needs a GtkSource.Buffer
        source_buffer = GtkSource.Buffer()
        self.source_view.set_buffer(source_buffer)

        self.source_view.set_hexpand(True)
        self.source_view.set_vexpand(True)
        self.source_view.set_monospace(True)
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        if self.results_scrolled_window:
            self.results_scrolled_window.set_child(self.source_view)
        else:
            # This case should ideally log an error if logger is available
            # For subtask, direct print might be visible if it runs in a context that shows stdout.
            print("ERROR: results_scrolled_window is None in __init__, cannot add GtkSource.View.")

        # The existing call to self._apply_webscan_source_view_style() in __init__ will handle
        # applying the language and style scheme after this setup.
"""

py_content_modified = py_content.replace(init_super_call_marker, init_insertion_code, 1)

with open(webscan_py_path, "w") as f:
    f.write(py_content_modified)

print("Modified src/webscan_page.py: Added programmatic GtkSourceView creation in __init__.")
