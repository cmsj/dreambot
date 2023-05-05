"""Base class for Dreambot backends."""
import logging
from typing import Any, Callable, Coroutine
from dreambot.shared.worker import DreambotWorkerBase


class DreambotBackendBase(DreambotWorkerBase):
    """Dreambot backend base class."""

    def __init__(
        self,
        name: str,
        options: dict[str, Any],
        callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]],
    ):
        """Initialise the class."""
        self.backend_name = name
        self.logger = logging.getLogger(f"dreambot.backend.base.{self.backend_name}")
        self.options = options
        self.queuename: str = options["nats_queue_name"]
        self.callback_send_workload = callback_send_workload

    def queue_name(self):
        """Return the NATS queue name for this backend."""
        return self.queuename

    async def shutdown(self) -> None:
        """Shut the instance down.

        This method must be overriden by subclasses.
        """
        raise NotImplementedError
