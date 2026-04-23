"""Cross-project memory for AI coding agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("crossmem")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for editable installs before first build
