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
            server = FrontendDiscord(self.options, self.callback_send_message)
            self.workers.append(server)
        except Exception as e:
            self.logger.error("Exception during boot: {}".format(e))

        self.run()


def main():
    cli = DreambotFrontendDiscordCLI()
    cli.boot()


if __name__ == "__main__":
    main()
