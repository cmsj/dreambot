"""Base class for Dreambot workers."""
import logging
import os
import string
import unicodedata
from enum import Enum
from typing import Callable, Coroutine, Any

from dreambot.shared.custom_argparse import ErrorCatchingArgumentParser

CallbackSendWorkload = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class DreambotWorkerEndType(Enum):
    """Enum for identifying frontend/backend workers."""

    FRONTEND = "frontend"
    BACKEND = "backend"


class DreambotWorkerBase:
    """Base class for Dreambot workers."""

    valid_filename_chars = f"_.() {string.ascii_letters}{string.digits}"

    def __init__(
        self,
        name: str,
        end: DreambotWorkerEndType,
        options: dict[str, Any],
        callback_send_workload: CallbackSendWorkload,
        subname: str = "",
    ):
        """Initialise the base worker class.

        Args:
            name (str): The name of the worker. This should be a single word that contains no periods. The combination of `name` and `subname` arguments must be unique across all workers.
            queue_name (str): The NATS queue this worker will fetch work from.
            end (str): The "end" of the worker (frontend or backend).
            options (dict[str, Any]): The contents of this worker's JSON config file.
            callback_send_workload (Callable[[str, bytes], Coroutine[Any, Any, None]]): A callback function that can be used to send workloads to other workers.
            subname (str, optional): A subname for this worker. Defaults to "". This is used to disamiguate multiple workers of the same `name`. For example, multiple IRC frontend workers can specify `name="irc"` and `subname` as the IRC server hostname.
        """
        self.is_booted = False

        self.name = name
        self.subname = subname
        self.end = end
        self.options = options
        self.callback_send_workload = callback_send_workload
        self.logger = logging.getLogger(f"dreambot.{self.end.value}.{self.name}")
        self.should_reconnect = False
        self.address = ""  # This will be given to us later by NatsManager

    async def send_message(self, resp: dict[str, Any]):
        """Send a message to NATS.

        Args:
            resp (dict[str, Any]): A dictionary containing the message to send.
        """
        # Worker subclasses are not expected to flip the to/reply-to, we do it for them here
        if resp["to"] == self.address:
            resp["to"] = resp["reply-to"]
            resp["reply-to"] = self.address

        try:
            self.logger.info("Sending response: %s with %s", resp, self.callback_send_workload)
            await self.callback_send_workload(resp)
        except Exception as exc:
            self.logger.error("Failed to send response: %s", format(exc))

    async def boot(self) -> None:
        """Child classes must override this to perform tasks that need to happen between class initialisation and the worker starting.

        When the child class has reached a point where it is ready to start receiving messages, it should set self.is_booted = True.
        """
        raise NotImplementedError

    async def shutdown(self) -> None:
        """Child classes must override this to perform tasks that need to happen when the worker is shutting down."""
        raise NotImplementedError

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Child classes must override this method. It is called when a message is received on the NATS queue."""
        raise NotImplementedError

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        """Return an ArgumentParser instance for this worker.

        This can be used to parse incoming messages for arguments.
        """
        return ErrorCatchingArgumentParser(prog=self.name, exit_on_error=False)

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
