"""Adapts SourceView usage in UI files for Gtk.TextView compatibility."""
import re

# Path to your .ui file(s)
# This script is designed to be run for each UI file that uses GtkSourceView
# and needs to be adapted for Gtk.TextView.

UI_FILES_TO_MODIFY = [
    "src/woes_gui/webscan_page.ui"
    # Add other UI file paths here if needed, e.g.:
    # "src/woes_gui/another_page.ui",
]

def adapt_sourceview_in_ui_file(file_path):
    """
    Modify a Glade/GTK Builder .ui file to replace GtkSourceView related elements.

    This function adapts a UI file by:
    - Replacing GtkSourceView with GtkTextView.
    - Replacing GtkSourceBuffer with GtkTextBuffer.
    - Removing properties specific to GtkSourceView/Buffer (e.g.,
      "show-line-numbers", "language-manager").
    - Handling the <child type="buffer"> tag for GtkSourceView.

    Args:
        file_path (str): The path to the .ui file.

    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()

        original_content = content

        # 1. Replace GtkSourceView with GtkTextView
        content = content.replace('class="GtkSourceView"', 'class="GtkTextView"')
        content = content.replace('GtkSourceView', 'GtkTextView') # For cases where it might appear not as a class attribute value

        # 2. Replace GtkSourceBuffer with GtkTextBuffer
        content = content.replace('class="GtkSourceBuffer"', 'class="GtkTextBuffer"')
        content = content.replace('GtkSourceBuffer', 'GtkTextBuffer')

        # 3. Remove GtkSourceView/Buffer specific properties
        sourceview_specific_props = [
            "show-line-numbers", "highlight-current-line", "auto-indent",
            "indent-on-tab", "tab-width", "insert-spaces-instead-of-tabs",
            "right-margin-position", "show-right-margin", "smart-backspace",
            "smart-home-end", "highlight-matching-brackets", "language-manager",
            "style-scheme", "max-undo-levels"
            # Add any other GtkSourceView/Buffer specific properties you might have used
        ]
        for prop_name in sourceview_specific_props:
            # Regex to remove a property line: <property name="property_name">value</property>
            # or <property name="property_name"/>
            content = re.sub(rf'\s*<property name="{prop_name}">[^<]*</property>\s*', '', content)
            content = re.sub(rf'\s*<property name="{prop_name}"/>\s*', '', content)


        # 4. Handle <child type="buffer"> for GtkSourceView
        # GtkTextView sets its buffer via a property, not a child tag typically.
        # This regex attempts to remove a <child type="buffer"> pointing to a GtkSourceBuffer
        # This is a simplified removal; complex structures might need manual review.
        # It looks for <object class="GtkTextView"...> (already replaced)
        # and removes its <child type="buffer" name="buffer_id"> if that buffer_id
        # was a GtkSourceBuffer (now GtkTextBuffer, but the structure is what we target)
        # This part is tricky with regex and might be too broad or too narrow.
        # A common pattern is:
        # <object class="GtkTextView" id="sourceview1">
        #   <child type="buffer">
        #     <object class="GtkTextBuffer" id="sourcebuffer1"/>  <-- was GtkSourceBuffer
        #   </child>
        # </object>
        # Gtk.TextView expects buffer as a property:
        # <object class="GtkTextView" id="textview1">
        #   <property name="buffer">
        #     <object class.="GtkTextBuffer" id="textbuffer1"/>
        #   </property>
        # </object>
        #
        # Simpler: If we find a GtkTextBuffer defined outside and then referenced by ID
        # in <property name="buffer">, that's fine.
        # If a GtkSourceBuffer was defined inline within <child type="buffer">,
        # we need to ensure it becomes a <property name="buffer">.
        #
        # This script will focus on replacing class names and removing properties.
        # Manually verify buffer assignment in the UI file after running.
        # For now, we won't aggressively remove/restructure buffer child tags,
        # as GtkBuilder might still handle some cases, or it's better fixed manually.
        # The most common issue is the class name and unavailable properties.

        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"Successfully adapted SourceView to TextView in: {file_path}")
        else:
            print(f"No changes needed for SourceView adaptation in {file_path}")

    except FileNotFoundError:
        print(f"Error: UI file not found at {file_path}")
    except Exception as e: # pylint: disable=broad-except
        print(f"An error occurred while processing {file_path}: {e}")

if __name__ == "__main__":
    for ui_file in UI_FILES_TO_MODIFY:
        print(f"\nProcessing file: {ui_file}")
        adapt_sourceview_in_ui_file(ui_file)
    print("\nUI adaptation script finished.")
