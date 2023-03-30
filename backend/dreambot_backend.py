import asyncio
import json
import logging
import sys
import nats
import openai

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('dreambot_backend')
logger.setLevel(logging.DEBUG)

class DreambotBackendGPT:
    nats = None

    api_key = None
    organization = None
    model = None
    nats_queue_name = None
    nats_uri = None

    def __init__(self, nats_options, gpt_options):
        self.api_key = gpt_options["api_key"]
        self.organization = gpt_options["organization"]
        self.model = gpt_options["model"]
        self.nats_queue_name = gpt_options["nats_queue_name"]

        self.nats_uri = nats_options["nats_uri"]

    async def boot(self, loop):
        async def handle_message(msg):
            data = json.loads(msg.data.decode())
            logger.debug("Received message: {}".format(data))
            response = openai.ChatCompletion.create(
                model = self.model,
                messages = [
                    {"role": "user", "text": "Limit your responses to 500 characters. {}".format(data["prompt"])},
                ]
            )
            reply = response.choices[0].message.content
            data["reply-text"] = reply
            await self.nats.publish(data["reply-to"], json.dumps(data).encode())

        logger.info("Booting Dreambot Backend GPT")
        self.nats = nats.aio.Client()
        await self.nats.connect(self.nats_uri, max_reconnect_attempts=-1, loop=loop)
        await self.nats.subscribe(self.nats_queue_name, cb=handle_message)


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
#       "nats_uri": "nats://localhost:4222"
#   }
# }
