#!/usr/bin/env python3
import os
import subprocess
import shutil
import sys

# Constants
FLATPAK_MANIFEST = "com.github.mclellac.WebOpsEvaluationSuite.json"
BUILD_DIR = "build-dir"
REPO_DIR = "repo"
FLATPAK_DIR = "flatpak"
FLATPAK_BUNDLE = "WebOpsEvaluationSuite.flatpak"

def run_command(command, check=True):
    """Run a shell command with error handling and verbosity."""
    try:
        print(f"Running command: {' '.join(command)}")
        subprocess.run(command, check=check, stdout=sys.stdout, stderr=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Error: Command '{' '.join(command)}' failed with return code {e.returncode}")
        sys.exit(1)

def ensure_flatpak_installed():
    """Ensure that flatpak and flatpak-builder are installed."""
    try:
        subprocess.run(["flatpak", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["flatpak-builder", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        print("Flatpak or flatpak-builder not installed. Installing...")
        try:
            distro = subprocess.check_output(["lsb_release", "-is"], text=True).strip().lower()
        except subprocess.CalledProcessError:
            print("Failed to detect distribution. Please install flatpak and flatpak-builder manually.")
            sys.exit(1)

        if distro in ["arch", "manjaro"]:
            run_command(["sudo", "pacman", "-S", "--noconfirm", "flatpak", "flatpak-builder"])
        elif distro in ["ubuntu", "debian", "kali"]:
            run_command(["sudo", "apt-get", "update"])
            run_command(["sudo", "apt-get", "install", "-y", "flatpak", "flatpak-builder"])
        elif distro in ["fedora"]:
            run_command(["sudo", "dnf", "install", "-y", "flatpak", "flatpak-builder"])
        else:
            print("Unsupported distribution. Please install flatpak and flatpak-builder manually.")
            sys.exit(1)

def clean_up():
    """Clean up build directories and repository."""
    if os.path.exists(BUILD_DIR):
        print(f"Removing directory: {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    if os.path.exists(REPO_DIR):
        print(f"Removing directory: {REPO_DIR}")
        shutil.rmtree(REPO_DIR, ignore_errors=True)

def build_flatpak():
    """Build the Flatpak application and bundle it."""
    print("Building the Flatpak application...")

    # Ensure flatpak is installed
    ensure_flatpak_installed()

    # Clean previous builds
    clean_up()

    # Build the Flatpak
    run_command(["flatpak-builder", "--force-clean", "-v", BUILD_DIR, FLATPAK_MANIFEST])

    # Create a repository from the build
    run_command(["flatpak-builder", "--repo=" + REPO_DIR, "--force-clean", BUILD_DIR, FLATPAK_MANIFEST])

    # Create the Flatpak bundle
    if not os.path.exists(FLATPAK_DIR):
        os.makedirs(FLATPAK_DIR)

    flatpak_bundle_path = os.path.join(FLATPAK_DIR, FLATPAK_BUNDLE)
    run_command(["flatpak", "build-bundle", "-v", REPO_DIR, flatpak_bundle_path, "com.github.mclellac.WebOpsEvaluationSuite"])

    print(f"Flatpak bundle created at {flatpak_bundle_path}")

    # Clean up build directories and repo after bundling
    clean_up()

if __name__ == "__main__":
    build_flatpak()

