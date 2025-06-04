import sys
import os

print(f"--- Python Interpreter Details ---")
python_version_str = sys.version.replace('\n', ' ')
print(f"Python Executable: {sys.executable}")
print(f"Python Version: {python_version_str}")

print("\n--- 'gi' Module Import Attempt ---")
try:
    import gi
    print(f"Successfully imported 'gi'")
    print(f"gi module path: {gi.__file__}")

    # Check for the _gi.so file's name to infer its Python version
    gi_dir = os.path.dirname(gi.__file__)
    so_file_found = False
    print(f"Contents of gi module directory ({gi_dir}):")
    for f_name in os.listdir(gi_dir):
        print(f"  - {f_name}")
        if f_name.startswith('_gi.') and f_name.endswith('.so'):
            print(f"    -> Found _gi shared object: {f_name}")
            so_file_found = True
            # Check the python version tag in the .so file name
            if f".cpython-{sys.version_info.major}{sys.version_info.minor}-" in f_name:
                print(f"    -> SO file name tag matches current Python {sys.version_info.major}.{sys.version_info.minor}")
            else:
                print(f"    -> WARNING: SO file name tag does NOT match current Python {sys.version_info.major}.{sys.version_info.minor}")
            break # Found one, that's enough for this check
    if not so_file_found:
        print("Could not find _gi.*.so file in gi module directory.")

    from gi.repository import GObject
    print("Successfully imported 'gi.repository.GObject'")
    print("Python/PyGObject versions appear compatible.")

except Exception as e:
    print(f"Error importing 'gi' or its submodules: {e}")
    import traceback
    traceback.print_exc()
    print("Python/PyGObject version incompatibility likely STILL EXISTS.")

sys.exit(0) # Ensure script exits cleanly for subtask reporting
