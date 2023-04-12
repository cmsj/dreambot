import asyncio

from dreambot.backend.gpt import DreambotBackendGPT
from dreambot.shared.cli import DreambotCLI

class DreambotBackendGPTCLI(DreambotCLI):
    cli_name = "BackendGPT"

    def boot(self):
        super().boot()

        loop = asyncio.get_event_loop()

        self.logger.info("Starting up...")
        try:
            async_tasks = []
            gpt = DreambotBackendGPT(self.options["nats"], self.options["gpt"])
            loop.run_until_complete(gpt.boot())
            loop.run_forever()
        finally:
            loop.close()
            self.logger.info("Shutting down...")

if __name__ == "__main__":
    cli = DreambotBackendGPTCLI()
    cli.boot()

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
