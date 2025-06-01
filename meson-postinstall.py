#!/usr/bin/env python3

import compileall
import sys
import os

if len(sys.argv) > 1:
    base_modules_dir = sys.argv[1]
    # The 'woes' subdirectory is where our actual package files are installed.
    target_compile_dir = os.path.join(base_modules_dir, 'woes')

    if os.path.isdir(target_compile_dir): # Ensure it's a directory
        print(f"Pre-compiling Python files in: {target_compile_dir}")
        # Use quiet=1 to suppress verbose "Listing..." messages unless errors occur.
        # legacy=True helps with symlinks and older .pyc formats if encountered.
        success = compileall.compile_dir(target_compile_dir, force=True, quiet=1, legacy=True)
        if not success:
            print(f"Warning: compileall reported some errors for {target_compile_dir}.")
    else:
        print(f"Warning: Target directory for compilation '{target_compile_dir}' does not exist or is not a directory. Skipping compilation.")
else:
    print("Warning: meson-postinstall.py did not receive the base modules directory argument (sys.argv[1]). Skipping compilation.")
