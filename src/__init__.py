# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

# AI Code Review - Internal modules
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("code-review-ai-cli")
except PackageNotFoundError:
    __version__ = "dev"
