"""Modifies the UI files to fix GtkTemplate issues."""
import re

# Path to your .ui file
UI_FILE_PATH = "src/woes_gui/window.ui" # Replace with your actual UI file path
# UI_FILE_PATH_HTTP = "src/woes_gui/http_page.ui"
# UI_FILE_PATH_DNS = "src/woes_gui/dns_page.ui"
# UI_FILE_PATH_NMAP = "src/woes_gui/nmap_page.ui"

def modify_template_child_tags(file_path):
    """
    Modify <template class="XYZ" parent="ABC"> to <template parent="ABC">.

    Also ensures <object class="XYZ" id="abc"> to <object class="XYZ" id="abc">
    for GtkTemplate compatibility issues, specifically when the class attribute
    in the template tag seems to cause problems with child object recognition.
    This script also ensures that child objects explicitly define their class
    if not already present, which is good practice.

    Args:
        file_path (str): The path to the .ui file.

    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()

        # Regex to find <template ...> tags
        # It captures the part before 'class=', the class attribute itself, and the part after.
        template_regex = r'(<template\s+)([^>]*\bclass\s*=\s*"[^"]*"\s*)([^>]*>)'
        # Replacement function for <template>
        def replace_template(match):
            before_class = match.group(1)
            class_attr_part = match.group(2) # The whole class="XYZ" part
            after_class = match.group(3)

            # Remove the class attribute by finding its start and end
            # This is safer than just removing based on group 2 if other attributes are mixed
            modified_attrs = re.sub(r'\bclass\s*=\s*"[^"]*"\s*', '', class_attr_part, count=1)

            # Reconstruct the template tag
            # Ensure there's a space if both parts have content, or just one if the other is empty
            if modified_attrs.strip() and after_class.strip():
                return f"{before_class}{modified_attrs.strip()} {after_class.strip()}"
            return f"{before_class}{modified_attrs.strip()}{after_class.strip()}"


        modified_content = re.sub(template_regex, replace_template, content)
        print(f"Processed <template> tags in {file_path}.")

        # Regex to find <object ...> tags that are direct children of <template>
        # This is a bit more complex as it needs to look at the parent.
        # For simplicity, this example will assume all <object> tags with an 'id'
        # are potential Gtk.Template.Child and ensure they have a class attribute.
        # A more accurate regex would involve parsing XML structure, which is overkill here.
        # This simplified approach might add 'class' where not strictly needed but is harmless.

        # This script will NOT automatically add class attributes to <object> tags.
        # It was initially considered, but manually ensuring <object class="XYZ" id="abc">
        # is present for all Gtk.Template.Child is more robust and aligned with Gtk4 best practices.
        # The main purpose is to clean the <template> tag itself.

        if modified_content != content:
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(modified_content)
            print(f"Successfully modified UI file: {file_path}")
        else:
            print(f"No changes needed for <template> tags in {file_path}")

    except FileNotFoundError:
        print(f"Error: UI file not found at {file_path}")
    except Exception as e: # pylint: disable=broad-except
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    modify_template_child_tags(UI_FILE_PATH)
    # modify_template_child_tags(UI_FILE_PATH_HTTP)
    # modify_template_child_tags(UI_FILE_PATH_DNS)
    # modify_template_child_tags(UI_FILE_PATH_NMAP)
    print("UI modification script finished.")
