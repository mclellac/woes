"""Modifies the webscan_page.py file to adapt SourceView usage."""

import re

webscan_py_path = "src/webscan_page.py"

try:
    with open(webscan_py_path, "r", encoding="utf-8") as file:
        content = file.read()

    # Replace Gtk.SourceView with Gtk.TextView
    modified_content = content.replace("Gtk.SourceView", "Gtk.TextView")
    # Replace Gtk.SourceBuffer with Gtk.TextBuffer
    modified_content = modified_content.replace("Gtk.SourceBuffer", "Gtk.TextBuffer")
    # Replace Gtk.SourceLanguageManager with a placeholder or remove if not critical
    # If language highlighting isn't essential, you can remove lines related to it.
    # For now, let's comment out lines that might cause issues if SourceLanguageManager is not available.
    # This is a heuristic. A more robust solution would be to understand how language setting is used.
    modified_content = re.sub(r".*Gtk\.SourceLanguageManager.*", r"# \g<0> # Commented out by script", modified_content)
    # Comment out .set_language
    modified_content = re.sub(
        r"(\s*)self\.source_buffer\.set_language\(.*\)", r"\1# \g<0> # Commented out by script", modified_content
    )

    if modified_content != content:
        with open(webscan_py_path, "w", encoding="utf-8") as file:
            file.write(modified_content)
        print(f"Successfully modified {webscan_py_path} for Gtk.TextView compatibility.")
    else:
        print(f"No changes needed in {webscan_py_path}.")

except FileNotFoundError:
    print(f"Error: File not found at {webscan_py_path}")
except Exception as e:  # pylint: disable=broad-except
    print(f"An error occurred: {e}")
