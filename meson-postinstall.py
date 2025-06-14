"""Performs post-install actions for Meson build system."""
import os
import subprocess

def main():
    """Execute post-install steps."""
    print("Starting Meson post-install script...")

    # Get environment variables set by Meson
    meson_build_root = os.environ.get("MESON_BUILD_ROOT")
    meson_source_root = os.environ.get("MESON_SOURCE_ROOT")
    meson_install_prefix = os.environ.get("MESON_INSTALL_PREFIX")
    destdir = os.environ.get("DESTDIR", "") # DESTDIR might not be set

    if not all([meson_build_root, meson_source_root, meson_install_prefix]):
        print("Error: Required Meson environment variables are not set. Skipping post-install.")
        return

    print(f"  MESON_BUILD_ROOT: {meson_build_root}")
    print(f"  MESON_SOURCE_ROOT: {meson_source_root}")
    print(f"  MESON_INSTALL_PREFIX: {meson_install_prefix}")
    print(f"  DESTDIR: {destdir if destdir else '(Not set)'}")

    # Construct the full installation path, considering DESTDIR
    # Example: /usr/local
    install_prefix = os.path.join(destdir, meson_install_prefix.lstrip(os.sep))
    print(f"  Full install prefix: {install_prefix}")

    # --- Example Post-Install Tasks ---

    # 1. Compile GSettings schemas (if your application uses GSettings)
    #    Requires `glib-compile-schemas` to be available in the build environment
    #    or on the target system if run during packaging.
    schemas_dir = os.path.join(install_prefix, "share", "glib-2.0", "schemas")
    if os.path.isdir(schemas_dir):
        print(f"\nCompiling GSettings schemas in {schemas_dir}...")
        try:
            subprocess.run(["glib-compile-schemas", schemas_dir], check=True)
            print("  GSettings schemas compiled successfully.")
        except FileNotFoundError:
            print("  Error: glib-compile-schemas command not found. Skipping schema compilation.")
            print("         Ensure it's installed if your app uses GSettings.")
        except subprocess.CalledProcessError as e:
            print(f"  Error compiling GSettings schemas: {e}")
    else:
        print(f"\nSkipping GSettings schema compilation: Directory {schemas_dir} not found.")

    # 2. Update icon cache (if you installed new icons)
    #    Requires `gtk-update-icon-cache`
    icon_theme_dir = os.path.join(install_prefix, "share", "icons", "hicolor")
    if os.path.isdir(icon_theme_dir):
        print(f"\nUpdating icon cache for {icon_theme_dir}...")
        try:
            subprocess.run(["gtk-update-icon-cache", "-f", "-t", icon_theme_dir], check=True)
            print("  Icon cache updated successfully.")
        except FileNotFoundError:
            print("  Error: gtk-update-icon-cache command not found. Skipping icon cache update.")
            print("         Ensure it's installed if your app installs icons.")
        except subprocess.CalledProcessError as e:
            print(f"  Error updating icon cache: {e}")
    else:
        print(f"\nSkipping icon cache update: Directory {icon_theme_dir} not found.")


    # 3. Update .desktop file database (if you installed .desktop files)
    #    Requires `update-desktop-database`
    desktop_files_dir = os.path.join(install_prefix, "share", "applications")
    if os.path.isdir(desktop_files_dir):
        print(f"\nUpdating .desktop file database for {desktop_files_dir}...")
        try:
            subprocess.run(["update-desktop-database", "-q", desktop_files_dir], check=True)
            print("  .desktop file database updated successfully.")
        except FileNotFoundError:
            print("  Error: update-desktop-database command not found. Skipping .desktop database update.")
            print("         Ensure it's installed if your app installs .desktop files.")
        except subprocess.CalledProcessError as e:
            print(f"  Error updating .desktop file database: {e}")
    else:
        print(f"\nSkipping .desktop file database update: Directory {desktop_files_dir} not found.")

    # --- Add other post-install tasks as needed ---
    # Examples:
    # - Moving or renaming files
    # - Creating symbolic links
    # - Setting permissions (though Meson's install functions usually handle this)
    #
    # Example: Create a symbolic link
    # source_file = os.path.join(install_prefix, "lib", "libmyapp.so.1")
    # link_name = os.path.join(install_prefix, "lib", "libmyapp.so")
    # if os.path.exists(source_file) and not os.path.exists(link_name):
    #     try:
    #         os.symlink(os.path.basename(source_file), link_name) # Relative link
    #         print(f"  Created symlink: {link_name} -> {os.path.basename(source_file)}")
    #     except OSError as e:
    #         print(f"  Error creating symlink {link_name}: {e}")

    print("\nMeson post-install script finished.")

if __name__ == "__main__":
    main()
