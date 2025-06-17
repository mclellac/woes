"""A setuptools based setup module for Woes."""

from setuptools import setup, find_packages

setup(
    name="woes",
    version="0.1.0",
    description="A simple application for network troubleshooting.",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "woes=src.main:main",
        ],
    },
    # Add other setup options here
)
