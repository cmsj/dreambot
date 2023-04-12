import asyncio
import json
import logging
import nats

class DreambotBackendBase:
    logger = None
    nats = None
    nats_uri = None
    nats_queue_name = None
    backend_name = "BaseBackend"
    cb = None

    def __init__(self, nats_options):
        self.logger = logging.getLogger("dreambot.backend.base.{}".format(self.backend_name))
        self.nats_uri = nats_options["nats_uri"]
        self.nats_queue_name = nats_options["nats_queue_name"]

    async def boot(self, cb):
        if not cb:
            self.logger.error("No callback provided to handle_message!")
            return
        self.logger.info("Booting Dreambot {} Backend...".format(self.backend_name))
        self.nats = await nats.connect(self.nats_uri, max_reconnect_attempts=-1)
        self.logger.info("NATS connected to: {}".format(self.nats.connected_url.netloc))

        self.cb = cb

        async def handle_message(msg):
            self.logger.debug("Received message on queue '{}': {}".format(self.nats_queue_name, msg.data.decode()))
            data = json.loads(msg.data.decode())
            data = self.cb(data)
            self.logger.debug("Sending response to queue '{}': {}".format(data["reply-to"], json.dumps(data).encode()))
            await self.nats.publish(data["reply-to"], json.dumps(data).encode())

        self.logger.info("Subscribing to queue: {}".format(self.nats_queue_name))
        await self.nats.subscribe(self.nats_queue_name, cb=handle_message)