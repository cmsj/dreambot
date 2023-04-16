import json
from dreambot.backend.gpt import DreambotBackendGPT
from dreambot.shared.cli import DreambotCLI


class DreambotBackendGPTCLI(DreambotCLI):
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

    def __init__(self):
        super().__init__("BackendGPT")

    def boot(self):
        super().boot()

        try:

            async def callback_send_message(queue_name: str, message: bytes) -> None:
                raw_msg = message.decode()
                json_msg = json.loads(raw_msg)
                if "reply-image" in json_msg:
                    json_msg["reply-image"] = "** IMAGE **"
                self.logger.debug("callback_send_message for '{}': {}".format(queue_name, json_msg))
                await self.nats.publish(queue_name, message)

            worker = DreambotBackendGPT(self.options, callback_send_message)
            self.workers.append(worker)
        except Exception as e:
            self.logger.error("Exception during boot: {}".format(e))

        self.run()


def main():
    cli = DreambotBackendGPTCLI()
    cli.boot()


if __name__ == "__main__":
    main()
