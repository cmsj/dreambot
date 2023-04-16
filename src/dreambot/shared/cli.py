import asyncio
import json
import logging
import signal

from dreambot.shared.nats import NatsManager
from dreambot.shared.worker import DreambotWorkerBase
from typing import Any
from argparse import ArgumentParser, RawTextHelpFormatter


class DreambotCLI:
    def __init__(
        self,
    ):  # FIXME: We need to accept the cli_name here, because otherwise it's too late for the logger setup
        self.cli_name = "BaseCLI"
        self.logger = logging.getLogger("dreambot.cli.{}".format(self.cli_name))
        self.example_json = ""

        self.workers: list[DreambotWorkerBase] = []
        self.parser: ArgumentParser
        # self.args = None
        self.options: dict[str, Any] = {}
        self.nats: NatsManager

    def parse_args(self):
        self.parser = ArgumentParser(
            description="Dreambot {}".format(self.cli_name),
            epilog=self.example_json,
            formatter_class=RawTextHelpFormatter,
        )
        self.parser.add_argument("-c", "--config", help="Path to config JSON file", required=True)
        self.parser.add_argument("-d", "--debug", help="Enable debug logging", action="store_true")
        self.parser.add_argument("-q", "--quiet", help="Disable most logging", action="store_true")
        self.args = self.parser.parse_args()

    def boot(self):
        self.parse_args()
        if self.args.debug:
            logging.basicConfig(level=logging.DEBUG)
        elif self.args.quiet:
            logging.basicConfig(level=logging.CRITICAL)
        else:
            logging.basicConfig(level=logging.INFO)

        self.logger.info("Starting up...")

        with open(self.args.config) as f:
            self.options = json.load(f)

        if "nats_uri" not in self.options:
            self.logger.error("nats_uri missing from {}".format(self.args.config))
            raise ValueError("nats_uri not provided in JSON config")

        self.nats = NatsManager(nats_uri=self.options["nats_uri"])

    def run(self):
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.get_event_loop()

            for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(s, lambda s=s: asyncio.create_task(self.shutdown(s)))

            loop.create_task(self.nats.boot(self.workers))
            [loop.create_task(x.boot()) for x in self.workers]
            loop.run_forever()
        finally:
            if loop:
                loop.close()
            self.logger.info("Shutting down...")

    async def shutdown(self, sig: int):
        self.logger.info("Received signal: {}".format(signal.Signals(sig).name))
        await self.nats.shutdown()
        [await x.shutdown() for x in self.workers]

        self.logger.debug("Cancelling all other tasks")
        [task.cancel() for task in asyncio.all_tasks() if task is not asyncio.current_task()]
