import re

webscan_py_path = "src/webscan_page.py"
with open(webscan_py_path, "r") as f:
    py_content = f.read()

py_content_modified = py_content.replace(
    "results_textview = Gtk.Template.Child()",
    "results_scrolled_window = Gtk.Template.Child() # Parent of GtkSourceView",
    1
)

with open(webscan_py_path, "w") as f:
    f.write(py_content_modified)

print("Modified src/webscan_page.py: Updated Gtk.Template.Child reference.")
