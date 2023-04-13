import argparse
import asyncio
import json
import logging
import signal

from dreambot.shared.nats import NatsManager
from argparse import RawTextHelpFormatter


class DreambotCLI:
    logger = None
    cli_name = "BaseCLI"
    example_json = ""
    parser = None
    args = None
    options = None
    nats = None

    workers = None

    def __init__(self):
        self.logger = logging.getLogger("dreambot.cli.{}".format(self.cli_name))
        self.workers = []

    def parse_args(self):
        self.parser = argparse.ArgumentParser(description="Dreambot {}".format(self.cli_name),
                                              epilog=self.example_json,
                                              formatter_class=RawTextHelpFormatter)
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
        try:
            loop = asyncio.get_event_loop()

            for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(s, lambda s=s: asyncio.create_task(self.shutdown(s)))

            loop.create_task(self.nats.boot(self.workers))
            [loop.create_task(x.boot()) for x in self.workers]
            loop.run_forever()
        finally:
            loop.close()
            self.logger.info("Shutting down...")

    async def shutdown(self, sig):
        self.logger.info("Received signal: {}".format(signal.Signals(sig).name))
        await self.nats.shutdown()
        [await x.shutdown() for x in self.workers]

        self.logger.debug("Cancelling all other tasks")
        [task.cancel() for task in asyncio.all_tasks() if task is not asyncio.current_task()]
