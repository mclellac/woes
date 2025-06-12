import re

webscan_py_path = "src/webscan_page.py"
with open(webscan_py_path, "r") as f:
    py_content = f.read()

# 1. Globally replace self.results_textview with self.source_view
py_content_modified = py_content.replace("self.results_textview", "self.source_view")

# 2. Adapt _apply_webscan_source_view_style
# This pattern aims to replace the start of the method up to where the scheme is applied.
apply_style_pattern = re.compile(
    r"(def _apply_webscan_source_view_style\(self\):)(.*?)apply_source_style_scheme\(scheme_manager, buffer, final_scheme_name\)",
    re.DOTALL
)
new_apply_style_start = """\\1
        if not hasattr(self, 'source_view') or not self.source_view:
            # print("DEBUG: _apply_webscan_source_view_style: self.source_view not ready")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print("DEBUG: _apply_webscan_source_view_style: buffer not ready")
            return

        is_dark = self.style_manager.get_dark()
        user_scheme_name = self.settings.get_string("source-style-scheme")
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        final_scheme_name = "Adwaita" # Default fallback

        if is_dark:
            if user_scheme_name.lower() in ["adwaita", "default", "classic", "light"]:
                final_scheme_name = "Adwaita-dark"
            else:
                if scheme_manager.get_scheme(user_scheme_name):
                    final_scheme_name = user_scheme_name
                else:
                    # print(f"DEBUG: User scheme '{user_scheme_name}' not found for dark theme, falling back to Adwaita-dark.")
                    final_scheme_name = "Adwaita-dark"
        else: # Light theme
            if user_scheme_name.lower() in ["adwaita-dark", "dark"]:
                final_scheme_name = "Adwaita"
            else:
                if scheme_manager.get_scheme(user_scheme_name):
                    final_scheme_name = user_scheme_name
                else:
                    # print(f"DEBUG: User scheme '{user_scheme_name}' not found for light theme, falling back to Adwaita.")
                    final_scheme_name = "Adwaita"

        if not scheme_manager.get_scheme(final_scheme_name):
            # print(f"DEBUG: Scheme '{final_scheme_name}' could not be loaded. Defaulting to basic Adwaita (light/dark).")
            final_scheme_name = "Adwaita-dark" if is_dark else "Adwaita"

        # print(f"DEBUG: Applying source style scheme: {final_scheme_name} (Dark: {is_dark}, User: {user_scheme_name})")
        apply_source_style_scheme(scheme_manager, buffer, final_scheme_name)"""
py_content_modified = apply_style_pattern.sub(new_apply_style_start, py_content_modified, 1)


# 3. Adapt _update_textview
# Replace: scroll_adj = self.source_view.get_parent().get_vadjustment()
# With: scroll_adj = self.results_scrolled_window.get_vadjustment() if self.results_scrolled_window else None
py_content_modified = py_content_modified.replace(
    "scroll_adj = self.source_view.get_parent().get_vadjustment()",
    "scroll_adj = self.results_scrolled_window.get_vadjustment() if self.results_scrolled_window else None"
)
# Add safety checks at the beginning of _update_textview
update_textview_def_pattern = r"def _update_textview\(self, stdout_content: Optional\[str\], stderr_content: Optional\[str\], is_error_message: bool = False\):"
update_textview_insertion = """
        if not hasattr(self, 'source_view') or not self.source_view:
            # print("DEBUG: _update_textview - source_view not found")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print("DEBUG: _update_textview - buffer not found")
            return"""
py_content_modified = re.sub(
    f"({update_textview_def_pattern})",
    f"\\1{update_textview_insertion}",
    py_content_modified,
    count=1
)

# 4. Add safety checks (hasattr and buffer) for other methods
methods_to_check = {
    "_on_clear_results_clicked": r"def _on_clear_results_clicked\(self, _button: Gtk.Button\):",
    "_on_copy_results_clicked": r"def _on_copy_results_clicked\(self, _button: Gtk.Button\):",
    "on_scan_button_clicked": r"def on_scan_button_clicked\(self, _widget: Gtk.Button\):"
}
for method_key, method_pattern_def in methods_to_check.items():
    insertion_code = f"""
        if not hasattr(self, 'source_view') or not self.source_view:
            # print(f"DEBUG: {method_key} - source_view not found")
            return
        buffer = self.source_view.get_buffer()
        if not buffer:
            # print(f"DEBUG: {method_key} - buffer not found")
            return"""
    py_content_modified = re.sub(
        f"({method_pattern_def})",
        f"\\1{insertion_code}",
        py_content_modified,
        count=1
    )

with open(webscan_py_path, "w") as f:
    f.write(py_content_modified)

print("Modified src/webscan_page.py: Adapted methods for programmatic GtkSourceView.")
