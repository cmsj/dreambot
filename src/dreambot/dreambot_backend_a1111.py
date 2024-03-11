"""Dreambot A1111 backend launcher."""

from dreambot.backend.a1111 import DreambotBackendA1111
from dreambot.shared.cli import DreambotCLI


class DreambotBackendA1111CLI(DreambotCLI):
    """Dreambot A1111 backend launcher."""

    example_json = """Example JSON config:
{
  "a1111": {
      "host": "localhost",
      "port": "9090"
  },
  "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
}"""

    def __init__(self):
        """Initialise the instance."""
        super().__init__("BackendA1111")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            worker = DreambotBackendA1111(self.options, self.callback_send_workload)
            self.workers.append(worker)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotBackendA1111CLI()
    cli.boot()


if __name__ == "__main__":
    main()
