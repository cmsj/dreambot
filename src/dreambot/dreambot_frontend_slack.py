"""Dreambot Slack frontend launcher."""
from dreambot.frontend.slack import FrontendSlack
from dreambot.shared.cli import DreambotCLI


class DreambotFrontendSlackCLI(DreambotCLI):
    """Dreambot Slack frontend launcher."""

    example_json = """Example JSON config:
{
    "triggers": {
            "!dream": "backend.invokeai",
            "!gpt": "backend.gpt"
    },
    "nats_uri": [ "nats://nats:4222", "nats://nats2:4222" ],
    "output_dir": "/data",
    "uri_base": "http://localhost:8080/dreams",
    "slack": {
        "token": "xoxb-1234567890-123456789012-1234567890-1234567890",
        "socketModeToken": "xapp-1-1234567890-123456789012-1234567890-1234567890",
    }
}"""

    def __init__(self):
        """Initialise the instance."""
        super().__init__("FrontendSlack")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            server = FrontendSlack(self.options, self.callback_send_workload)
            self.workers.append(server)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotFrontendSlackCLI()
    cli.boot()


if __name__ == "__main__":
    main()
