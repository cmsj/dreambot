import asyncio
import json
import logging
import nats


class DreambotBackendBase:
    logger = None
    backend_name = "BaseBackend"
    queuename = None
    callback_send_message = None
    options = None

    def __init__(self, options, callback_send_message):
        self.logger = logging.getLogger(
            "dreambot.backend.base.{}".format(self.backend_name)
        )
        self.options = options
        self.queuename = options["nats_queue_name"]
        self.callback_send_message = callback_send_message

    def queue_name(self):
        return self.queuename

    async def shutdown(self):
        pass
