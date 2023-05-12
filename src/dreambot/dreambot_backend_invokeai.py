"""Dreambot InvokeAI backend launcher."""
from dreambot.backend.invokeai import DreambotBackendInvokeAI
from dreambot.shared.cli import DreambotCLI


class DreambotBackendInvokeAICLI(DreambotCLI):
    """Dreambot InvokeAI backend launcher."""

    example_json = """Example JSON config:
{
  "invokeai": {
      "host": "localhost",
      "port": "9090"
  },
  "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
}"""

    def __init__(self):
        """Initialise the instance."""
        super().__init__("BackendInvokeAI")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            worker = DreambotBackendInvokeAI(self.options, self.callback_send_workload)
            self.workers.append(worker)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotBackendInvokeAICLI()
    cli.boot()


if __name__ == "__main__":
    main()
