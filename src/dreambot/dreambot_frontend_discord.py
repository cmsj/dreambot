"""Dreambot Discord frontend launcher."""
from dreambot.frontend.discord import FrontendDiscord
from dreambot.shared.cli import DreambotCLI


class DreambotFrontendDiscordCLI(DreambotCLI):
    """Dreambot Discord frontend launcher."""

    example_json = """Example JSON config:
{
    "triggers": [
          "!dream",
          "!gpt"
    ],
    "nats_uri": [ "nats://nats:4222", "nats://nats2:4222" ],
    "output_dir": "/data",
    "uri_base": "http://localhost:8080/dreams",
    "discord": {
                "token": "abc123xyz789"
    }
}"""

    def __init__(self):
        """Initialise the instance."""
        super().__init__("FrontendDiscord")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            server = FrontendDiscord(self.options, self.callback_send_workload)
            self.workers.append(server)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotFrontendDiscordCLI()
    cli.boot()


if __name__ == "__main__":
    main()
