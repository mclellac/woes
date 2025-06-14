"""Adds GtkSourceView initialization to webscan_page.py if not present."""
import re

webscan_py_path = "src/webscan_page.py" # Path to your webscan_page.py

initialization_code = """
        # Initialize GtkSourceView related objects if GtkSource available
        lm = None
        self.source_buffer = None
        if GtkSource:
            lm = GtkSource.LanguageManager.get_default()
            self.source_buffer = GtkSource.Buffer()
            # Example: Set a language, if desired and available
            # lang = lm.get_language("python")
            # if lang:
            #     self.source_buffer.set_language(lang)
            # scheme_id = self.settings.get_string("sourceview-style-scheme")
            # scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(scheme_id)
            # if scheme:
            #     self.source_buffer.set_style_scheme(scheme)
        else:
            # Fallback to Gtk.TextBuffer if GtkSource.Buffer is not available
            self.source_buffer = Gtk.TextBuffer()

        self.source_view.set_buffer(self.source_buffer) # Use the initialized buffer
"""

# Regex to find the __init__ method and a common line within it
# We'll insert our code before a line like 'self.source_view.set_editable(False)'
# or similar, assuming it's a good spot after basic Gtk.TextView setup.
# This needs to be adjusted based on the actual structure of your __init__ method.
# A more robust way might be to find the end of the __init__ before any method calls
# on self.source_view that depend on the buffer being set.

# Let's target insertion before a line that uses self.source_view,
# for example, self.source_view.set_hexpand(True) or similar.
# A simpler approach for this script: insert after `super().__init__(**kwargs)`
# and before any specific `self.source_view` configurations.

# Revised strategy: Insert after `super().__init__(**kwargs)` and basic `Gtk.TextView()` instantiation.
# Find:
#         super().__init__(**kwargs)
#         self.source_view = Gtk.TextView() # Or the line where self.source_view is created
# (Potentially some lines here)
# And insert before the first configuration line for self.source_view like `set_hexpand` or `set_buffer` (if it already exists but needs GtkSourceBuffer logic)

# For this script, we'll keep it simple:
# Look for `self.source_view = Gtk.TextView()` and insert after it.
# Or, if `self.source_view.set_buffer` already exists, insert before it.
# This is still heuristic.

# Let's try to find the line `self.source_view = Gtk.TextView()`
# and insert the initialization code right after it.
# If `self.source_view.set_buffer` is found, we assume GtkSourceView was already partially handled.

try:
    with open(webscan_py_path, 'r', encoding='utf-8') as file:
        content = file.read()

    # Check if the initialization code (or a significant part of it) already exists
    if "GtkSource.LanguageManager.get_default()" in content or "self.source_buffer = GtkSource.Buffer()" in content:
        print(f"GtkSourceView initialization code seems to already exist in {webscan_py_path}.")
    else:
        # Target for insertion: after `self.source_view = Gtk.TextView()`
        # and before other `self.source_view.set_...` calls if possible.
        # A simple but potentially fragile way is to find `self.source_view.set_hexpand(True)`
        # and insert before it, assuming `self.source_view` was created just before.

        # Let's find the line where self.source_view is created:
        creation_pattern = r"(^\s*self\.source_view\s*=\s*Gtk\.TextView\(\s*\))"
        match = re.search(creation_pattern, content, re.MULTILINE)

        if match:
            insertion_point = match.end(1)
            # Indent the initialization_code to match the found line's indentation
            indentation = match.group(1)[:match.group(1).find("self.")]
            indented_init_code = "\n" + "\n".join([indentation + line for line in initialization_code.splitlines()])

            modified_content = content[:insertion_point] + indented_init_code + content[insertion_point:]

            # Also, ensure GtkSource is imported conditionally at the top
            gtk_source_import = """
try:
    import gi
    gi.require_version('GtkSource', '5') # Or '4' depending on your version
    from gi.repository import GtkSource
except (ImportError, ValueError):
    GtkSource = None
    print("GtkSourceView not found, webscan will use basic Gtk.TextView.")
"""
            # Add this import near other gi imports if not already present
            if "from gi.repository import GtkSource" not in content:
                # Find a common import to insert after, e.g., `from gi.repository import Gtk`
                import_insertion_match = re.search(r"(from gi\.repository import Gtk.*)", content)
                if import_insertion_match:
                    import_insertion_point = import_insertion_match.end(1)
                    modified_content = modified_content[:import_insertion_point] + "\n" + gtk_source_import + modified_content[import_insertion_point:]
                else: # Fallback: add at the top after module docstring
                    docstring_match = re.match(r"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?''')", modified_content)
                    if docstring_match:
                        import_insertion_point = docstring_match.end(0)
                        modified_content = modified_content[:import_insertion_point] + "\n" + gtk_source_import + modified_content[import_insertion_point:]
                    else: # Prepend if no docstring
                         modified_content = gtk_source_import + "\n" + modified_content


            with open(webscan_py_path, 'w', encoding='utf-8') as file:
                file.write(modified_content)
            print(f"Added GtkSourceView initialization to {webscan_py_path}")
        else:
            print(f"Could not find `self.source_view = Gtk.TextView()` pattern in {webscan_py_path} to insert initialization code.")
            print("Please manually verify GtkSourceView initialization.")


except FileNotFoundError:
    print(f"Error: File not found at {webscan_py_path}")
except Exception as e: # pylint: disable=broad-except
    print(f"An error occurred: {e}")
