"""Knowledge graph tools for Adverse Outcome Pathways."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aop-kg")
except PackageNotFoundError:  # pragma: no cover - package is not installed
    __version__ = "0.1.0"

__all__ = ["__version__"]
