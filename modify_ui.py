import re

webscan_ui_path = "src/gtk/webscan_page.ui"
with open(webscan_ui_path, "r") as f:
    ui_content = f.read()

# Remove GtkSourceView block
ui_content_after_remove = re.sub(
    r'<object class="GtkSourceView" id="results_textview">.*?</object>',
    '',
    ui_content,
    flags=re.DOTALL
)

# Add id="results_scrolled_window" to the GtkScrolledWindow
# This pattern looks for a GtkScrolledWindow that is a child of the "Scan Results" AdwPreferencesGroup
# and adds the ID if it's not there.
ui_content_final = re.sub(
    r'(<object class="AdwPreferencesGroup">\s*<property name="title">Scan Results</property>.*?<child>\s*)<object class="GtkScrolledWindow">',
    r'\1<object class="GtkScrolledWindow" id="results_scrolled_window">',
    ui_content_after_remove,
    count=1,
    flags=re.DOTALL
)

# Fallback if the specific ScrolledWindow pattern didn't match (e.g., if it already had some other ID or structure changed)
# and the generic one is needed.
if 'id="results_scrolled_window"' not in ui_content_final and '<object class="GtkScrolledWindow">' in ui_content_final :
    ui_content_final = ui_content_final.replace(
        '<object class="GtkScrolledWindow">',
        '<object class="GtkScrolledWindow" id="results_scrolled_window">',
        1
    )

with open(webscan_ui_path, "w") as f:
    f.write(ui_content_final)

print("Modified src/gtk/webscan_page.ui: Removed GtkSourceView and added id to its parent GtkScrolledWindow.")
