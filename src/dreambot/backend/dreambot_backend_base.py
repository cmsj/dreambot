import asyncio
import json
import logging
import nats

logger = logging.getLogger(__name__)

class DreambotBackendBase:
    nats = None
    nats_uri = None
    nats_queue_name = None
    backend_name = "Base"
    cb = None

    def __init__(self, nats_options):
        self.nats_uri = nats_options["nats_uri"]
        self.nats_queue_name = nats_options["nats_queue_name"]

    async def boot(self, loop, cb):
        if not cb:
            logger.error("No callback provided to handle_message!")
            return
        logger.info("Booting Dreambot {} Backend...".format(self.backend_name))
        self.nats = await nats.connect(self.nats_uri, max_reconnect_attempts=-1)
        logger.info("NATS connected to: {}".format(self.nats.connected_url.netloc))

        self.cb = cb

        async def handle_message(msg):
            logger.debug("Received message on queue '{}': {}".format(self.nats_queue_name, msg.data.decode()))
            data = json.loads(msg.data.decode())
            data = self.cb(data)
            await self.nats.publish(data["reply-to"], json.dumps(data).encode())

        logger.info("Subscribing to queue: {}".format(self.nats_queue_name))
        await self.nats.subscribe(self.nats_queue_name, cb=handle_message)