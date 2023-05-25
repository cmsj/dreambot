"""Scaffolding for building a Dreambot worker command."""
import asyncio
import json
import logging
import signal
import sys

from typing import Any
from argparse import ArgumentParser, RawTextHelpFormatter, Namespace

from dreambot.shared.nats import NatsManager
from dreambot.shared.worker import DreambotWorkerBase


class DreambotCLI:
    """This class should be used for building a Dreambot worker command."""

    def __init__(self, cli_name: str):
        """Initialise the class.

        Args:
            cli_name (str): Name of the CLI, used for logging and as a prefix for NATS queues.
        """
        self.cli_name = cli_name
        self.logger = logging.getLogger(f"dreambot.cli.{self.cli_name}")
        self.example_json = ""

        self.workers: list[DreambotWorkerBase] = []
        self.parser: ArgumentParser
        self.args: Namespace
        self.options: dict[str, Any] = {}
        self.nats: NatsManager

    def parse_args(self):
        """Parse command line arguments.

        Note: There is currently no way for subclasses to add their own arguments.
        """
        self.parser = ArgumentParser(
            description=f"Dreambot {self.cli_name}",
            epilog=self.example_json,
            formatter_class=RawTextHelpFormatter,
        )
        self.parser.add_argument("-c", "--config", help="Path to config JSON file", required=True)
        self.parser.add_argument("-d", "--debug", help="Enable debug logging", action="store_true")
        self.parser.add_argument("-q", "--quiet", help="Disable most logging", action="store_true")
        self.args = self.parser.parse_args()

    def boot(self):
        """Boot this instance.

        Prepares logging, loads config, and configures NATS.

        Raises:
            ValueError: If nats_uri is not provided in the config.
        """
        self.parse_args()
        if self.args.debug:
            logging.basicConfig(level=logging.DEBUG)
        elif self.args.quiet:
            logging.basicConfig(level=logging.CRITICAL)
        else:
            logging.basicConfig(level=logging.INFO)

        self.logger.info("Starting up...")

        with open(self.args.config, encoding="utf8") as config_file:
            self.options = json.load(config_file)

        if "nats_uri" not in self.options:
            raise ValueError("nats_uri not provided in JSON config")

        self.nats = NatsManager(nats_uri=self.options["nats_uri"], name=self.cli_name)

    def run(self):
        """Establish the event loop and run the associated tasks."""
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.get_event_loop()

            # FIXME: This is ungraceful, but Windows can't do signal handling this way. We could do something like https://stackoverflow.com/questions/45987985/asyncio-loops-add-signal-handler-in-windows
            if sys.platform != "win32":
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, lambda sig=sig: asyncio.create_task(self.shutdown(sig)))
                loop.add_signal_handler(
                    signal.SIGHUP, lambda: self.toggle_debug()  # pylint: disable=unnecessary-lambda
                )
            loop.create_task(self.nats.boot(self.workers))
            _ = [loop.create_task(x.boot()) for x in self.workers]
            loop.run_forever()
        finally:
            if loop:
                loop.close()
            self.logger.info("Shutting down...")

    def toggle_debug(self):
        """Toggle between logging.INFO and logging.DEBUG for *all* loggers in the current process."""
        if logging.root.level == logging.DEBUG:
            level = logging.INFO
        else:
            level = logging.DEBUG

        loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]  # pylint: disable=no-member
        logging.root.setLevel(level)
        for logger in loggers:
            logger.setLevel(level)

    async def shutdown(self, sig: int):
        """Shut down the event loop and stop all associated tasks.

        Args:
            sig (int): The signal that triggered the shutdown.
        """
        self.logger.info("Received signal: %s", signal.Signals(sig).name)
        await self.nats.shutdown()
        _ = [await x.shutdown() for x in self.workers]

        self.logger.debug("Cancelling all other tasks")
        _ = [task.cancel() for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    async def callback_send_workload(self, message: dict[str, Any]) -> None:
        """Send NATS messages from a subclass, to NATS.

        Args:
            queue_name (str): The queue to send the message to.
            message (bytes): The message to send, as a JSON string encoded as bytes.
        """
        self.logger.info("callback_send_workload: %s", message)
        await self.nats.publish(message)
