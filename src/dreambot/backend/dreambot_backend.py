import asyncio
import json
import logging
import sys
import nats
import openai
from openai.error import APIError, Timeout, ServiceUnavailableError, RateLimitError, AuthenticationError, InvalidRequestError

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('dreambot_backend')
logger.setLevel(logging.DEBUG)

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
        logger.info("Booting Dreambot {} Backend...".format(self.backend_name))
        self.nats = await nats.connect(self.nats_uri, max_reconnect_attempts=-1)
        logger.info("NATS connected to: {}".format(self.nats.connected_url.netloc))

        self.cb = cb

        async def handle_message(msg):
            if not cb:
                logger.error("No callback provided to handle_message!")
                return
            logger.debug("Received message on queue '{}': {}".format(self.nats_queue_name, msg.data.decode()))
            data = json.loads(msg.data.decode())
            data = self.cb(data)
            await self.nats.publish(data["reply-to"], json.dumps(data).encode())

        logger.info("Subscribing to queue: {}".format(self.nats_queue_name))
        await self.nats.subscribe(self.nats_queue_name, cb=handle_message)

class DreambotBackendGPT(DreambotBackendBase):
    api_key = None
    organization = None
    model = None
    backend_name = "GPT"

    def __init__(self, nats_options, gpt_options):
        super().__init__(nats_options)
        self.api_key = gpt_options["api_key"]
        self.organization = gpt_options["organization"]
        self.model = gpt_options["model"]

    async def boot(self, cb):
        await super().boot(loop, cb)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: {} <config.json>".format(sys.argv[0]))
        sys.exit(1)

    with open(sys.argv[1]) as f:
        options = json.load(f)

    loop = asyncio.get_event_loop()

    logger.info("Dreamboot backend starting up...")
    try:
        async_tasks = []
        gpt = DreambotBackendGPT(options["nats"], options["gpt"])
        loop.run_until_complete(gpt.boot(loop))
        loop.run_forever()
    finally:
        loop.close()
        logger.info("Dreambot backend shutting down...")

# Example JSON config:
# {
#   "gpt": {
#       "api_key": "abc123",
#       "organization": "dreambot",
#       "model": "davinci"
#       "nats_queue_name": "!gpt",
#   },
#   "nats": {
#       "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
#   }
# }
