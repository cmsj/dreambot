"""Dreambot IRC frontend launcher."""
from dreambot.frontend.irc import FrontendIRC
from dreambot.shared.cli import DreambotCLI


class DreambotFrontendIRCCLI(DreambotCLI):
    """Dreambot IRC frontend launcher."""

    example_json = """Example JSON config:
{
    "triggers": [
          "!dream",
          "!gpt"
    ],
    "nats_uri": [ "nats://nats:4222", "nats://nats2:4222" ],
    "output_dir": "/data",
    "uri_base": "http://localhost:8080/dreams",
    "irc": [
        {
                "nickname": "dreambot",
                "ident": "dreambot",
                "realname": "I've dreamed things you people wouldn't believe",
                "host": "irc.server.com",
                "port": 6697,
                "ssl": true,
                "channels": [
                        "#friends",
                        "#dreambot"
                ]
        }
    ]
}"""

    def __init__(self):
        """Initialise the instance."""
        super().__init__("FrontendIRC")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            for server_config in self.options["irc"]:
                server = FrontendIRC(server_config, self.options, self.callback_send_workload)
                self.workers.append(server)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotFrontendIRCCLI()
    cli.boot()


if __name__ == "__main__":
    main()
