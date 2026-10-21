"""termSwitch: grouped AWS profiles that open authenticated terminals."""

# BASE_VERSION sets MAJOR.MINOR (and the lowest patch) for the next release.
# Release builds stamp the exact released version into _version.py; source
# checkouts report BASE_VERSION.
BASE_VERSION = "0.6.0"

try:
    from ._version import __version__  # written by the release workflow
except ImportError:
    __version__ = BASE_VERSION
