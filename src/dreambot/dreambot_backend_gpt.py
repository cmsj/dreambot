import asyncio
import json
import logging
import sys
import openai
from openai.error import APIError, Timeout, ServiceUnavailableError, RateLimitError, AuthenticationError, InvalidRequestError

from dreambot.backend import dreambot_backend_base

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class DreambotBackendGPT(dreambot_backend_base.DreambotBackendBase):
    api_key = None
    organization = None
    model = None
    backend_name = "GPT"

    def __init__(self, nats_options, gpt_options):
        super().__init__(nats_options)
        self.api_key = gpt_options["api_key"]
        self.organization = gpt_options["organization"]
        self.model = gpt_options["model"]
        logger.debug("Set GPT options to: api_key={}, organization={}, model={}".format(self.api_key, self.organization, self.model))

    async def boot(self):
        def gpt_callback(data):
            try:
                response = openai.ChatCompletion.create(
                    model = self.model,
                    messages = [
                        {"role": "user", "content": "Limit your responses to 500 characters. {}".format(data["prompt"])},
                    ]
                )
                reply = response.choices[0].message.content
                data["reply-text"] = reply
            except (APIError, Timeout, ServiceUnavailableError) as e:
                logger.error("GPT service access error: {}".format(e))
                data["error"] = "GPT service unavailable, try again."
            except (RateLimitError, AuthenticationError) as e:
                logger.error("GPT service query error: {}".format(e))
                data["error"] = "GPT service error, ask your bot admin to check logs."
            except InvalidRequestError as e:
                logger.error("GPT request error: {}".format(e))
                data["error"] = "GPT request error, ask your bot admin to check logs."
            except Exception as e:
                logger.error("Unknown error: {}".format(e))
                data["error"] = "Unknown error, ask your bot admin to check logs."
            return data

        # Set the details OpenAI need, and call the base class boot with our callback
        openai.api_key = self.api_key
        openai.organization = self.organization
        await super().boot(gpt_callback)


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
        gpt = DreambotBackendGPT(options["nats"], options["gpt"])
        loop.run_until_complete(gpt.boot())
        loop.run_forever()
    finally:
        loop.close()
        logger.info("Dreambot backend shutting down...")

if __name__ == "__main__":
    main()

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
