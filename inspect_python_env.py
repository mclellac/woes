import sys
import os
# import importlib.util # This line caused immediate failure, defer gi checks to try-except block

print(f"--- Python Interpreter Details ---")
python_version_str = sys.version.replace('\n', ' ')
print(f"Python Executable: {sys.executable}")
print(f"Python Version: {python_version_str}")

print("\n--- sys.path ---")
for path_item in sys.path:
    print(f"  - {path_item}")

print("\n--- 'gi' Module Import Attempt ---")
try:
    print("Attempting to import 'gi'")
    import gi
    print(f"Successfully imported 'gi'")
    print(f"gi module path: {gi.__file__}")

    # Check if _gi is loaded and where it might be from
    if hasattr(gi, '_gi'):
        print(f"gi._gi object exists: {gi._gi}")
        if hasattr(gi._gi, '__file__') and gi._gi.__file__:
            print(f"gi._gi module path (if available): {gi._gi.__file__}")
        else:
            print("gi._gi does not have a __file__ attribute (it's a built-in or extension module loaded differently)")
    else:
        print("gi._gi object does NOT exist after importing gi")

    print("Attempting to import 'gi.repository.GObject'")
    from gi.repository import GObject
    print(f"Successfully imported 'gi.repository.GObject'")
    if hasattr(GObject, '__file__'):
        print(f"GObject from gi.repository path: {GObject.__file__}")
    elif hasattr(GObject, '__path__'):
        print(f"GObject from gi.repository __path__: {GObject.__path__}")
    else:
        print(f"GObject from gi.repository: {GObject} (no direct file path)")


except Exception as e:
    print(f"Error importing 'gi' or its submodules: {e}")
    import traceback
    traceback.print_exc()

print("\n--- Environment Variables ---")
relevant_vars = ['PYTHONPATH', 'LD_LIBRARY_PATH', 'GI_TYPELIB_PATH', 'PATH',
                 'GIR_DIR', 'GIRPATH', 'XDG_DATA_DIRS']
for var in relevant_vars:
    print(f"  {var}: {os.environ.get(var, 'Not set')}")

print("\n--- Contents of /usr/lib/python3/dist-packages/gi/ ---")
gi_so_path_actual = None
try:
    gi_dist_path = "/usr/lib/python3/dist-packages/gi/"
    if os.path.exists(gi_dist_path):
        for item in os.listdir(gi_dist_path):
            print(f"  - {item}")
            if item.startswith("_gi.cpython-") and item.endswith(".so"):
                gi_so_path_actual = os.path.join(gi_dist_path, item)
                print(f"    -> Found a _gi.so file: {item}")
                # Note the Python version this .so file is for based on its name
                if "cpython-310" in item:
                    print(f"    -> This .so file appears to be for Python 3.10.")
                elif "cpython-312" in item:
                    print(f"    -> This .so file appears to be for Python 3.12.")
                else:
                    print(f"    -> This .so file is for an unknown Python version based on filename.")

    else:
        print(f"  Path {gi_dist_path} does not exist.")
except Exception as e:
    print(f"  Error listing contents of /usr/lib/python3/dist-packages/gi/: {e}")

if gi_so_path_actual:
    print(f"\n--- Checking ldd on {gi_so_path_actual} ---")
    import subprocess
    try:
        process = subprocess.run(['ldd', gi_so_path_actual], capture_output=True, text=True, check=True)
        print(process.stdout)
    except subprocess.CalledProcessError as e:
        print(f"ldd command failed with error: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
    except FileNotFoundError:
        print("ldd command not found.")
else:
    print("\nNo _gi.so file found in /usr/lib/python3/dist-packages/gi/ to run ldd on.")
