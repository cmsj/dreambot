"""Dreambot GPT backend launcher."""
from dreambot.backend.gpt import DreambotBackendGPT
from dreambot.shared.cli import DreambotCLI


class DreambotBackendGPTCLI(DreambotCLI):
    """Dreambot GPT backend launcher."""

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
        """Initialise the instance."""
        super().__init__("BackendGPT")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            worker = DreambotBackendGPT(self.options, self.callback_send_workload)
            self.workers.append(worker)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotBackendGPTCLI()
    cli.boot()


if __name__ == "__main__":
    main()
