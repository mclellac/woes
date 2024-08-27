#!/usr/bin/env python3
import os
import subprocess
import shutil

def run_command(command, cwd=None):
    """Run a shell command and print its output."""
    result = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: Command '{command}' failed with error:\n{result.stderr}")
        exit(1)
    else:
        print(result.stdout)

def is_installed(command):
    """Check if a command is installed."""
    result = subprocess.run(f"command -v {command}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0

def install_package(package_name):
    """Install the specified package using the appropriate package manager."""
    try:
        distro = get_distro()
        if distro in ["arch", "manjaro"]:
            run_command(f"sudo pacman -Sy --noconfirm {package_name}")
        elif distro in ["fedora", "rhel", "centos"]:
            run_command(f"sudo dnf install -y {package_name}")
        elif distro in ["debian", "ubuntu", "kali"]:
            run_command(f"sudo apt-get update && sudo apt-get install -y {package_name}")
        else:
            print(f"Unsupported distribution: {distro}")
            exit(1)
    except Exception as e:
        print(f"Failed to install {package_name}: {str(e)}")
        exit(1)

def get_distro():
    """Determine the Linux distribution."""
    try:
        with open("/etc/os-release") as f:
            release_info = f.read().lower()
            if "arch" in release_info:
                return "arch"
            elif "manjaro" in release_info:
                return "manjaro"
            elif "fedora" in release_info:
                return "fedora"
            elif "rhel" in release_info:
                return "rhel"
            elif "centos" in release_info:
                return "centos"
            elif "debian" in release_info:
                return "debian"
            elif "ubuntu" in release_info:
                return "ubuntu"
            elif "kali" in release_info:
                return "kali"
            else:
                return "unknown"
    except FileNotFoundError:
        print("Error: Unable to determine the Linux distribution.")
        exit(1)

def clean_up(directories):
    """Remove specified directories if they exist."""
    for directory in directories:
        if os.path.exists(directory):
            print(f"Removing directory: {directory}")
            shutil.rmtree(directory)

def main():
    # Check for required tools
    if not is_installed("flatpak"):
        print("Flatpak is not installed. Installing Flatpak...")
        install_package("flatpak")

    if not is_installed("flatpak-builder"):
        print("Flatpak Builder is not installed. Installing Flatpak Builder...")
        install_package("flatpak-builder")

    app_id = "com.github.mclellac.WebOpsEvaluationSuite"
    manifest_file = f"{app_id}.json"
    build_dir = "build-dir"
    repo_dir = "repo"
    flatpak_dir = "flatpak"
    flatpak_file = os.path.join(flatpak_dir, f"{app_id}.flatpak")

    # Clean up any previous build artifacts
    clean_up([build_dir, repo_dir, flatpak_dir])

    # Create the flatpak directory
    os.makedirs(flatpak_dir, exist_ok=True)

    # Build the Flatpak application
    print("Building the Flatpak application...")
    run_command(f"flatpak-builder --repo={repo_dir} {build_dir} {manifest_file}")

    # Create the Flatpak bundle
    print("Creating the Flatpak bundle...")
    run_command(f"flatpak build-bundle {repo_dir} {flatpak_file} {app_id}")

    # Copy relevant files to the flatpak directory
    print("Copying files to the flatpak directory...")
    shutil.copy(manifest_file, flatpak_dir)

    print(f"Build complete. The Flatpak file and manifest are in the '{flatpak_dir}' directory.")

if __name__ == "__main__":
    main()

