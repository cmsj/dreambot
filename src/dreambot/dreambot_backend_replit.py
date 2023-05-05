"""Dreambot Replit backend launcher."""
from dreambot.backend.replit import DreambotBackendReplit
from dreambot.shared.cli import DreambotCLI


class DreambotBackendReplitCLI(DreambotCLI):
    """Dreambot Replit backend launcher."""

    example_json = """Example JSON config:
{
  "nats_queue_name": "!replit",
  "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
}"""

    def __init__(self):
        """Initialise the instance."""
        super().__init__("BackendReplit")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            worker = DreambotBackendReplit(self.options, self.callback_send_workload)
            self.workers.append(worker)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotBackendReplitCLI()
    cli.boot()


if __name__ == "__main__":
    main()
