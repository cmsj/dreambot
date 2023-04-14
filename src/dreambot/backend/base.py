import logging
from typing import Any, Callable, Coroutine
from dreambot.shared.worker import DreambotWorkerBase


class DreambotBackendBase(DreambotWorkerBase):
    def __init__(
        self, options: dict[str, Any], callback_send_message: Callable[[str, bytes], Coroutine[Any, Any, None]]
    ):
        self.backend_name = "BaseBackend"
        self.logger = logging.getLogger("dreambot.backend.base.{}".format(self.backend_name))
        self.options = options
        self.queuename: str = options["nats_queue_name"]
        self.callback_send_message = callback_send_message

    def queue_name(self):
        return self.queuename

    async def shutdown(self):
        pass
