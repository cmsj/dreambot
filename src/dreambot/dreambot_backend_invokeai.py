import asyncio

from dreambot.backend.invokeai import DreambotBackendInvokeAI
from dreambot.shared.cli import DreambotCLI


class DreambotBackendInvokeAICLI(DreambotCLI):
    cli_name = "BackendInvokeAI"
    example_json = """Example JSON config:
{
  "invokeai": {
      "host": "localhost",
      "port": "9090"
  },
  "nats": {
      "nats_queue_name": "!invokeai",
      "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
  }
}"""

    def boot(self):
        super().boot()

        loop = asyncio.get_event_loop()

        self.logger.info("Starting up...")
        try:
            gpt = DreambotBackendInvokeAI(
                self.options["nats"], self.options["invokeai"]
            )
            loop.run_until_complete(gpt.boot())
            loop.run_forever()
        finally:
            loop.close()
            self.logger.info("Shutting down...")


def main():
    cli = DreambotBackendInvokeAICLI()
    cli.boot()


if __name__ == "__main__":
    main()
