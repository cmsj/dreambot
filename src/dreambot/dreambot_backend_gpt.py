import asyncio

from dreambot.backend.gpt import DreambotBackendGPT
from dreambot.shared.cli import DreambotCLI

class DreambotBackendGPTCLI(DreambotCLI):
    cli_name = "BackendGPT"
    example_json = """Example JSON config:
{
  "gpt": {
      "api_key": "abc123",
      "organization": "dreambot",
      "model": "davinci"
  },
  "nats_queue_name": "!gpt",
  "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
}"""

    def boot(self):
        super().boot()

        try:
            async def callback_send_message(queue_name, message):
                self.logger.debug("callback_send_message for '{}': {}".format(queue_name, message.decode()))
                await self.nats.publish(queue_name, message)

            gpt = DreambotBackendGPT(self.options, callback_send_message)
            self.workers.append(gpt)
        except Exception as e:
            self.logger.error("Exception during boot: {}".format(e))

        self.run()

def main():
    cli = DreambotBackendGPTCLI()
    cli.boot()

if __name__ == "__main__":
    main()