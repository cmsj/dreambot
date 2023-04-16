import json
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
  "nats_queue_name": "!invokeai",
  "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
}"""

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

            worker = DreambotBackendInvokeAI(self.options, callback_send_message)
            self.workers.append(worker)
        except Exception as e:
            self.logger.error("Exception during boot: {}".format(e))

        self.run()


def main():
    cli = DreambotBackendInvokeAICLI()
    cli.boot()


if __name__ == "__main__":
    main()
