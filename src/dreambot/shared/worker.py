"""Base class for Dreambot workers."""
import os
import string
import unicodedata
from typing import Callable, Coroutine, Any
from argparse import ArgumentParser


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


class DreambotWorkerBase:
    """Base class for Dreambot workers."""

    valid_filename_chars = f"_.() {string.ascii_letters}{string.digits}"
    callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]]

    def queue_name(self) -> str:
        """Child classes must override this and return the name of the NATS queue they wish to subscribe to."""
        raise NotImplementedError

    async def boot(self) -> None:
        """Child classes must override this to perform tasks that need to happen between class initialisation and the worker starting."""
        raise NotImplementedError

    async def shutdown(self) -> None:
        """Child classes must override this to perform tasks that need to happen when the worker is shutting down."""
        raise NotImplementedError

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        """Child classes must override this method. It is called when a message is received on the NATS queue."""
        raise NotImplementedError

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        """Return an ArgumentParser instance for this worker.

        This can be used to parse incoming messages for arguments.
        """
        parser = ErrorCatchingArgumentParser(prog=self.queue_name(), exit_on_error=False)
        return parser

    def clean_filename(self, filename: str, replace: str = " ", suffix: str = ".png", output_dir: str = ""):
        """Clean a filename to ensure it is valid for the host OS filesystem.

        This can be used by workers that want to use incoming prompts as the basis of filenames.
        """
        char_limit = os.statvfs(output_dir).f_namemax - len(suffix)
        whitelist = self.valid_filename_chars
        # replace undesired characters
        for char in replace:
            filename = filename.replace(char, "_")

        # keep only valid ascii chars
        cleaned_filename = unicodedata.normalize("NFKD", filename).encode("ASCII", "ignore").decode()

        # keep only whitelisted chars
        cleaned_filename = "".join(c for c in cleaned_filename if c in whitelist).replace("__", "")
        return cleaned_filename[:char_limit] + suffix
