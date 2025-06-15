#!/usr/bin/env python
"""Builds the flatpak package."""

import os
import subprocess

# Configuration (replace with your actual values)
APP_ID = "com.github.mclellac.woes"  # Replace with your Flatpak App ID
RUNTIME_REPO = "flathub"
RUNTIME = "org.gnome.Platform"
RUNTIME_VERSION = "45"  # Or your desired GNOME runtime version
SDK = "org.gnome.Sdk"
BRANCH = "main"  # Or your desired branch
FLATPAK_MODULE_FILE = "com.github.mclellac.woes.json"  # Or your module file name
OUTPUT_DIR = "flatpak_build"
REPO_NAME = "woes_repo"  # Name for the local Flatpak repository

# Ensure the output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Create Flatpak module file (if it doesn't exist or needs updating)
# This is a simplified example. You'll need to define your build options,
# sources, and cleanup steps according to your project's needs.
# Refer to Flatpak documentation for details:
# https://docs.flatpak.org/en/latest/first-build.html
# The manifest file (com.github.mclellac.woes.json) is now static and part of the repository.
# Ensure it exists and is correctly formatted.
if not os.path.exists(FLATPAK_MODULE_FILE):
    print(f"Error: Flatpak module file {FLATPAK_MODULE_FILE} not found.")
    print("Please ensure the manifest file exists in the root of the repository.")
    exit(1)
else:
    print(f"Using existing Flatpak module file: {FLATPAK_MODULE_FILE}")

# 2. Initialize Flatpak repository (if it doesn't exist)
# The APP_ID in build-init command is taken from the manifest file directly by flatpak-builder,
# but it's good practice to ensure our APP_ID variable matches.
# The command itself doesn't strictly need APP_ID if it can infer from manifest, but let's keep it for clarity.
if not os.path.exists(os.path.join(OUTPUT_DIR, REPO_NAME)):
    print(f"Initializing Flatpak repository: {REPO_NAME}")
    subprocess.run(
        [
            "flatpak",
            "build-init",
            os.path.join(OUTPUT_DIR, REPO_NAME),
            APP_ID, # This should match the app-id in your static manifest
            SDK,
            RUNTIME,
            RUNTIME_VERSION,
            f"--branch={BRANCH}",
        ],
        check=True,
    )
else:
    print(f"Flatpak repository {REPO_NAME} already exists.")

# 3. Build the application
# The flatpak build command reads the APP_ID from the manifest file.
print(f"Building {APP_ID} using manifest {FLATPAK_MODULE_FILE}...")
subprocess.run(["flatpak", "build", os.path.join(OUTPUT_DIR, REPO_NAME), FLATPAK_MODULE_FILE], check=True)

# 4. Finish the build (optional, for creating a runnable Flatpak)
# This step creates a bundle or installs to a local repository.
# For CI, you might skip this or create a bundle.
# For local testing, installing to a local repo is useful.

# Example: Install to the local repository (for testing)
print(f"Installing {APP_ID} to local repository {REPO_NAME}...")
subprocess.run(["flatpak", "build-finish", os.path.join(OUTPUT_DIR, REPO_NAME)], check=True)
# To run after installing to local repo:
# flatpak run --user --command=sh -c 'flatpak install --user --reinstall {REPO_NAME} {APP_ID} && flatpak run {APP_ID}'

# Example: Create a Flatpak bundle
bundle_path = os.path.join(OUTPUT_DIR, f"{APP_ID}.flatpak")
print(f"Creating Flatpak bundle: {bundle_path}")
subprocess.run(
    [
        "flatpak",
        "build-bundle",
        os.path.join(OUTPUT_DIR, REPO_NAME),
        bundle_path,
        APP_ID, # This should match the app-id in your static manifest
        f"--runtime-repo=https://dl.flathub.org/repo/{RUNTIME_REPO}.flatpakrepo", # Use configured RUNTIME_REPO
    ],
    check=True,
)

print("\nFlatpak build process completed.")
print(f"Bundle created at: {bundle_path}")
print(f"To test locally (if you installed to the local repo): flatpak run {APP_ID}")
print(f"To install the bundle: flatpak install {bundle_path}")

# Cleanup the generated module file
# os.remove(FLATPAK_MODULE_FILE) # Optional: remove the manifest
# print(f"Cleaned up {FLATPAK_MODULE_FILE}")

# Note: This script assumes you have flatpak and flatpak-builder installed.
# You might need to adjust paths, build options, and sources based on your project.
# Consider using a more robust build system or tool for complex projects.
# This script is a basic starting point.
# Remember to add a .desktop file and an app icon for your application.
# These are typically installed by your build system (e.g., meson, cmake)
# and referenced in your Flatpak manifest.
#
# For example, your meson.build might have:
# install_data(
#   'com.example.Woes.desktop',
#   install_dir: get_option('datadir') / 'applications'
# )
# install_data(
#   'com.example.Woes.svg',  # Or .png
#   install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'scalable' / 'apps'
# )
#
# And your Flatpak manifest would then pick these up.
# The `command` in the manifest should be the executable name installed by your build.
#
# If your application has Python dependencies, you'll need to add them as modules
# in your Flatpak manifest, typically using `flatpak-pip-generator`.
# Example module for a Python dependency:
# {
#     "name": "python3-requests",
#     "buildsystem": "simple",
#     "build-commands": [
#         "pip3 install --no-index --find-links=\"file://${PWD}\" --prefix=${FLATPAK_DEST} requests"
#     ],
#     "sources": [
#         {
#             "type": "file",
#             "path": "python3-requests-2.25.1-py3-none-any.whl", # Downloaded wheel
#             "sha256": "..."
#         }
#     ]
# }
# The flatpak-pip-generator can help create these source entries.
# See: https://docs.flatpak.org/en/latest/python.html
#
# For more complex builds or dependencies, refer to the official Flatpak documentation.
# Good luck!
