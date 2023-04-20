import os
import string
import unicodedata
from typing import Callable, Coroutine, Any
from argparse import ArgumentParser


class UsageException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class ErrorCatchingArgumentParser(ArgumentParser):
    def exit(self, status: int = 0, message: str | None = None):
        raise ValueError(message)

    def error(self, message: str):
        raise ValueError(message)

    def print_usage(self, file: Any = None):
        raise UsageException(self.format_usage())

    def print_help(self, file: Any = None):
        raise UsageException(self.format_usage())


class DreambotWorkerBase:
    valid_filename_chars = "_.() %s%s" % (string.ascii_letters, string.digits)
    callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]]

    def queue_name(self) -> str:
        raise NotImplementedError

    async def boot(self) -> None:
        raise NotImplementedError

    async def shutdown(self) -> None:
        raise NotImplementedError

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        raise NotImplementedError

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        parser = ErrorCatchingArgumentParser(prog=self.queue_name(), exit_on_error=False)
        return parser

    def clean_filename(self, filename: str, replace: str = " ", suffix: str = ".png", output_dir: str = ""):
        char_limit = os.statvfs(output_dir).f_namemax - len(suffix)
        whitelist = self.valid_filename_chars
        # replace undesired characters
        for r in replace:
            filename = filename.replace(r, "_")

        # keep only valid ascii chars
        cleaned_filename = unicodedata.normalize("NFKD", filename).encode("ASCII", "ignore").decode()

        # keep only whitelisted chars
        cleaned_filename = "".join(c for c in cleaned_filename if c in whitelist).replace("__", "")
        return cleaned_filename[:char_limit] + suffix
