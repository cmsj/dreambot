import asyncio
import json
import logging
import sys
import socketio

from dreambot.backend import dreambot_backend_base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class DreambotBackendInvokeAI(dreambot_backend_base.DreambotBackendBase):
    backend_name = "InvokeAI"
    sio = None
    invokeai_uri = "http://localhost:9090"

    def __init__(self, nats_options, invokeai_options):
        super().__init__(nats_options)
        self.invokeai_uri = invokeai_options["uri"]
        logger.debug("Set InvokeAI options to: uri={}".format(self.invokeai_uri))

    async def boot(self):
        self.sio = socketio.Client()
        self.sio.connect(self.invokeai_uri)

        def invokeai_callback(data):
            try:
                prompt = data["prompt"]
                data["error"] = "Not yet implemented"
            except Exception as e:
                logger.error("Unknown error: {}".format(e))
                data["error"] = "Unknown error, ask your bot admin to check logs."
            return data

        await super().boot(invokeai_callback)


def main():
    if len(sys.argv) != 2:
        print("Usage: {} <config.json>".format(sys.argv[0]))
        sys.exit(1)

    with open(sys.argv[1]) as f:
        options = json.load(f)

    loop = asyncio.get_event_loop()

    logger.info("Dreamboot backend starting up...")
    try:
        async_tasks = []
        gpt = DreambotBackendInvokeAI(options["nats"], options["invokeai"])
        loop.run_until_complete(gpt.boot())
        loop.run_forever()
    finally:
        loop.close()
        logger.info("Dreambot backend shutting down...")

if __name__ == "__main__":
    main()

# Example JSON config:
# {
#   "invokeai": {
#       "uri": "http://localhost:9090"
#   },
#   "nats": {
#       "nats_queue_name": "!invokeai",
#       "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
#   }
# }
