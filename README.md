# WebOps Evaluation Suite

![woes](images/app.png)

WOES is a graphical user interface designed for performing various web operations tasks. Built with GTK 4 and Adwaita, it provides an intuitive and user-friendly interface for tasks such as fetching HTTP headers, performing port scans, running OS fingerprint detection, and executing DNS queries.

## Features
- **Fetch and Display HTTP Headers**: Retrieve and view HTTP headers for any given URL, with the ability to enable Akamai debug headers.
- **Port Scanning**: Perform port scans on specified targets with customizable options, including OS fingerprint detection and NSE scripts.
- **OS Fingerprint Detection**: Detect operating systems on scanned targets as part of the port scanning process.
- **NSE Script Integration**: Run Nmap Scripting Engine (NSE) scripts as part of the scanning process.
- **DNS Lookup Tool**: Perform DNS queries for various record types, such as A, AAAA, MX, TXT, and more. Also supports reverse DNS lookups by entering an IP address.

## Requirements

- `GTK 4` / `libadwaita`
- `GtkSourceView`
- `Python 3`

### Python dependencies: 
- `requests` 
- `pyYAML` 
- `python-nmap`
- `dnspython`

```bash
> pip install requests PyYAML python-nmap dnspython
```

## System Dependencies:

Ensure you have the following tools installed for building the project:

* `Meson`
* `Ninja`
* `Flatpak`

For most systems, you can install these with your package manager:

```bash
# For Fedora/Red Hat-based distributions
> sudo dnf install meson ninja-build flatpak

# For Debian/Ubuntu-based distributions
> sudo apt install meson ninja-build flatpak
```

## Clone the Repository

First, clone the repository to your local machine:

```bash
> git clone https://github.com/mclellac/woes.git
> cd woes
```

## Build the Project

### Using Meson and Ninja:

Set up the build directory and compile the project using Meson and Ninja:

```bash
> meson setup build --wipe
> ninja -C build --verbose
```

### Install the application:

```bash
> sudo ninja -C build install --verbose
```

### Using Flatpak:

Build the Flatpak package:

```bash
> ./build-flatpak.py
```

Once the build is successful, you can install and run the Flatpak package in the flatpak directory.

## Development Status
This application is currently in early development. The HTTP Headers, Nmap, and DNS pages are functional, with more features planned for future releases.
