# AI Code Review - Internal modules
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("ai-code-review-cli")
except PackageNotFoundError:
    __version__ = "dev"
