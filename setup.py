#!/usr/bin/env python3
"""Installs dependencies and builds the Woes application."""

import argparse
import subprocess
import sys
import shutil
import platform
import os
from pathlib import Path

distros = {
    "debian": "debian",
    "ubuntu": "debian",  # Use the same config as Debian
    "linuxmint": "debian",
    "kali": "debian",
    "fedora": "fedora",
    "centos": "fedora",
    "rhel": "fedora",
    "arch": "arch",
    "archarm": "arch",
    "manjaro": "arch",
    "alpine": "alpine",
    "darwin": "darwin",
    "freebsd": "freebsd",
    "openbsd": "openbsd",
    "netbsd": "netbsd",
}

# Package data for different OS and distributions
package_data = {
    "Linux": {
        "debian": {
            "manager": "apt",
            "update": ["update", "-y"],
            "options": ["install", "-y"],
            "packages": [
                "meson",
                "python3-mesonpy",
                "ninja-build",
                "libgtk-4-dev",
                "libadwaita-1-dev",
                "desktop-file-utils",
                "python3-dnspython",
                "python3-gi",
                "libglib2.0-dev",
                "python-gi-dev",
                "gettext",
            ],
        },
        "fedora": {
            "manager": "dnf",
            "update": ["update", "-y"],
            "options": ["install", "-y"],
            "packages": [
                "meson",
                "python3-meson-python",
                "ninja-build",
                "gtk4-devel",
                "libadwaita-devel",
                "desktop-file-utils",
                "python3-dns",
                "python3-gobject",
                "glib2-devel",
                "cmake",
                "python3-gobject-devel",
                "gettext",
            ],
        },
        "arch": {
            "manager": "pacman",
            "update": ["-Syu", "--noconfirm"],
            "options": ["-S", "--noconfirm"],
            "packages": [
                "meson",
                "meson-python",
                "ninja",
                "gtk4",
                "libadwaita",
                "desktop-file-utils",
                "python-dnspython",
                "python-gobject",
                "glib2",
            ],
        },
        "alpine": {
            "manager": "apk",
            "update": ["update"],
            "options": ["add"],
            "packages": [
                "meson",
                "ninja",
                "gtk4.0-dev",
                "libadwaita-dev",
                "py3-gobject3",
                "py3-requests",
                "py3-yaml",
                "py3-dnspython",
                "gettext",
            ],
        },
    },
    "Darwin": {
        "darwin": {
            "manager": "brew",
            "update": ["update"],
            "options": ["install"],
            "packages": [
                "meson",
                "ninja",
                "gtk4",
                "libadwaita",
                "desktop-file-utils",
                "pygobject3",
                "glib",
                "gettext",
            ],
        }
    },
    "FreeBSD": {
        "freebsd": {
            "manager": "pkg",
            "update": ["update"],
            "options": ["install", "-y"],
            "packages": [
                "meson",
                "ninja",
                "gtk4",
                "libadwaita",
                "pkgconf",
                "py311-gobject3",
                "py311-requests",
                "py311-yaml",
                "py311-dnspython",
                "gettext",
            ],
        }
    },
}


def get_elevated_prefix():
    """Get privilege escalation command prefix if running as non-root user."""
    os_type = platform.system()
    if os_type == "Darwin":
        # On macOS, Homebrew prefix (/opt/homebrew or /usr/local) is user-owned.
        # Running sudo on macOS is unnecessary and breaks PATH environment for Homebrew tools.
        return []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    if shutil.which("doas"):
        return ["doas"]
    return []


def run_command(cmd):
    """Run a system command live with output streamed directly to terminal."""
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: Command {' '.join(map(str, cmd))} failed with exit code {e.returncode}.")
        sys.exit(e.returncode)


def detect_os_and_distro():
    """Detect the operating system and distribution."""
    os_type = platform.system()
    distro = "unknown"
    if os_type == "Linux":
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    lines = f.readlines()
                    distro_info = {}
                    for line in lines:
                        if "=" in line:
                            key, value = line.strip().split("=", 1)
                            distro_info[key] = value.strip('"')
                    distro = distro_info.get("ID", "unknown")
                    if distro not in distros and "ID_LIKE" in distro_info:
                        for like in distro_info["ID_LIKE"].split():
                            if like in distros:
                                distro = like
                                break
        except Exception as e:
            print(f"[Warning] Could not determine Linux distribution: {e}")
    elif os_type == "Darwin":
        distro = "darwin"
    elif "BSD" in os_type or os_type in ("FreeBSD", "OpenBSD", "NetBSD", "DragonFly"):
        distro = os_type.lower()
    else:
        distro = os_type.lower()

    return os_type, distro


def install_packages():
    """Install necessary system packages based on the detected OS and distribution."""
    os_type, distro = detect_os_and_distro()
    distro_key = distros.get(distro, None)  # Map distro to its config key

    if os_type in package_data and distro_key in package_data[os_type]:
        data = package_data[os_type][distro_key]
        manager = data["manager"]

        if os_type == "Darwin" and manager == "brew":
            missing_packages = []
            for pkg in data["packages"]:
                res = subprocess.run(
                    ["brew", "list", "--formula", pkg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode != 0:
                    missing_packages.append(pkg)
            if missing_packages:
                print("Running command: brew update")
                run_command(["brew", "update"])
                print("Installing missing Homebrew dependencies:", " ".join(missing_packages))
                run_command(["brew", "install"] + missing_packages)
            else:
                print("[Homebrew] All required dependencies are already installed.")
        else:
            update_cmd = [manager] + data["update"]
            install_cmd = [manager] + data["options"] + data["packages"]

            elevated = get_elevated_prefix()
            if elevated:
                update_cmd = elevated + update_cmd
                install_cmd = elevated + install_cmd

            print("Running command:", " ".join(update_cmd))
            run_command(update_cmd)
            print("Running command:", " ".join(install_cmd))
            run_command(install_cmd)
    else:
        print(f"[Notice] Automated package installation is not pre-configured for {os_type} ({distro}).")
        print("Please ensure required dependencies (meson, ninja, gtk4, libadwaita, pygobject, python-nmap, requests, PyYAML, dnspython) are installed.")


def check_and_delete_directory(directory):
    """Check if a directory exists and delete it if it does, with user message."""
    if os.path.exists(directory):
        print(f"[Cleanup] Removing existing directory: {directory}")
        try:
            shutil.rmtree(directory)
        except OSError:
            elevated = get_elevated_prefix()
            run_command(elevated + ["rm", "-rf", str(directory)])


def build_application(os_type):
    """Build and install the application with informative messages."""
    build_dir = Path("build")
    check_and_delete_directory(build_dir)

    # Meson setup
    if os_type == "Darwin":
        meson_cmd = [
            "meson",
            "setup",
            "--prefix=/usr/local",
            str(build_dir),
        ]
    else:
        meson_cmd = ["meson", "setup", str(build_dir)]

    print("\n[Build] Running Meson setup command:", " ".join(meson_cmd))
    run_command(meson_cmd)

    # Ninja build
    ninja_build_cmd = ["ninja", "-C", str(build_dir)]
    print("[Build] Running Ninja build command:", " ".join(ninja_build_cmd))
    run_command(ninja_build_cmd)

    # Ninja install
    elevated = get_elevated_prefix()
    ninja_install_cmd = elevated + ["ninja", "-C", str(build_dir), "install"]
    print("[Build] Running Ninja install command:", " ".join(ninja_install_cmd))
    run_command(ninja_install_cmd)

    print("[Build] Installation complete!")


def configure_environment_paths():
    """Ensure PATH and PKG_CONFIG_PATH include standard Homebrew/BSD locations."""
    os_type = platform.system()
    if os_type == "Darwin":
        extra_bins = ["/opt/homebrew/bin", "/usr/local/bin"]
        extra_pkgs = [
            "/opt/homebrew/lib/pkgconfig",
            "/opt/homebrew/share/pkgconfig",
            "/usr/local/lib/pkgconfig",
            "/usr/local/share/pkgconfig",
        ]
    elif "BSD" in os_type or os_type in ("FreeBSD", "OpenBSD", "NetBSD"):
        extra_bins = ["/usr/local/bin", "/usr/pkg/bin"]
        extra_pkgs = [
            "/usr/local/libdata/pkgconfig",
            "/usr/local/lib/pkgconfig",
            "/usr/pkg/lib/pkgconfig",
        ]
    else:
        extra_bins = []
        extra_pkgs = []

    current_path = os.environ.get("PATH", "")
    for b in extra_bins:
        if os.path.exists(b) and b not in current_path.split(":"):
            current_path = b + ":" + current_path
    os.environ["PATH"] = current_path

    current_pkg = os.environ.get("PKG_CONFIG_PATH", "")
    pkg_list = [p for p in extra_pkgs if os.path.exists(p)]
    if current_pkg:
        pkg_list.append(current_pkg)
    if pkg_list:
        os.environ["PKG_CONFIG_PATH"] = ":".join(pkg_list)


def check_homebrew():
    """Check if Homebrew is installed on macOS."""
    if shutil.which("brew") is None:
        print(">> Homebrew not found. Please install it or install the dependencies manually.")
        sys.exit(1)


def main():
    """Parse command-line arguments and run the setup process."""
    parser = argparse.ArgumentParser(description="Dependency installer and application builder")
    parser.add_argument("-i", "--install-deps", action="store_true", help="Install dependencies")
    parser.add_argument("-b", "--build", action="store_true", help="Build and install the application")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(0)

    configure_environment_paths()

    os_type, distro = detect_os_and_distro()
    print(f"[System] Detected operating system: {os_type}")
    print(f"[System] Detected distribution: {distro}")

    if args.install_deps:
        print("\n--- Installing Dependencies ---")
        if os_type == "Darwin":
            check_homebrew()
        install_packages()

        print("\n[Success] Dependencies installed successfully!\n")

    if args.build:
        print("\n--- Building Application ---")
        build_application(os_type)


if __name__ == "__main__":
    main()
