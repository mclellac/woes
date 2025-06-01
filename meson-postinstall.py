#!/usr/bin/env python3

import compileall
import sys # Import sys
import os # os can still be used if DESTDIR logic is desired for other purposes, but not for compileall path here.

# The directory to compile is passed by Meson as an argument.
# sys.argv[0] is the script name
# sys.argv[1] is the Meson build directory (relative to source root)
# sys.argv[2] is the first custom argument ('modulesdir' from meson.build)
if len(sys.argv) > 2:
    target_compile_dir = sys.argv[2]
    print(f"Pre-compiling Python files in: {target_compile_dir}")
    # Use quiet=1 to suppress verbose "Listing..." messages unless errors occur.
    # legacy=True might be needed if there are symlinks to directories, but try without first.
    success = compileall.compile_dir(target_compile_dir, force=True, quiet=1)
    if not success:
        print(f"Warning: compileall reported some errors for {target_compile_dir}.")
else:
    print("Warning: meson-postinstall.py did not receive the target directory argument.")

# The DESTDIR logic is generally for staged installs and might not be what's needed here
# for specifying the actual Python module installation path for compilation.
# If DESTDIR is part of the path construction for target_compile_dir by Meson itself,
# then sys.argv[2] would already include it.
# For now, directly use sys.argv[2] as passed by Meson.
