"""Helper classes for argument parsing within prompts.

This is necessary because argparse assumes it is running on a command line and can do reasonable command line things like printing to stdout, and exiting the process.
We *really* don't want either of those things, so this allows us to raise exceptions with useful information, which the Dreambot backend workers can catch and handle appropriately.
"""
from argparse import ArgumentParser
from typing import Any


class UsageException(Exception):
    """Exception for command line argument errors.

    Args:
        message (str): Usage message.
    """

    def __init__(self, message: str):
        """Initialise the class."""
        super().__init__(message)


class ErrorCatchingArgumentParser(ArgumentParser):
    """Parser class for use with argparse that raises exceptions rather than printing output."""

    def exit(self, status: int = 0, message: str | None = None):
        """Raise an exception instead of exiting."""
        raise ValueError(message)

    def error(self, message: str):
        """Raise an exception when an argument error is encountered."""
        raise ValueError(message)

    def print_usage(self, file: Any = None):
        """Raise an exception when usage information is required."""
        raise UsageException(self.format_help())

    def print_help(self, file: Any = None):
        """Raise an exception when help information is required."""
        raise UsageException(self.format_help())
