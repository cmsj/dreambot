import logging
from typing import Any, Callable, Coroutine
from dreambot.shared.worker import DreambotWorkerBase


class DreambotBackendBase(DreambotWorkerBase):
    def __init__(
        self,
        name: str,
        options: dict[str, Any],
        callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]],
    ):
        self.backend_name = name
        self.logger = logging.getLogger("dreambot.backend.base.{}".format(self.backend_name))
        self.options = options
        self.queuename: str = options["nats_queue_name"]
        self.callback_send_workload = callback_send_workload

    def queue_name(self):
        return self.queuename

    async def shutdown(self) -> None:
        raise NotImplementedError
