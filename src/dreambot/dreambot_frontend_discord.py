# import frontend
import json
from dreambot.frontend.discord import FrontendDiscord
from dreambot.shared.cli import DreambotCLI


class DreambotFrontendDiscordCLI(DreambotCLI):
    example_json = """Example JSON config:
{
    "triggers": [
          "!dream ",
          "!gpt "
    ],
    "nats_uri": [ "nats://nats:4222", "nats://nats2:4222" ],
    "output_dir": "/data",
    "uri_base": "http://localhost:8080/dreams",
    "discord": {
                "token": "abc123xyz789"
    }
}"""

    def __init__(self):
        super().__init__("FrontendDiscord")

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

            server = FrontendDiscord(self.options, callback_send_message)
            self.workers.append(server)
        except Exception as e:
            self.logger.error("Exception during boot: {}".format(e))

        self.run()


def main():
    cli = DreambotFrontendDiscordCLI()
    cli.boot()


if __name__ == "__main__":
    main()
