import asyncio
import json
import logging
import signal
import sys

from dreambot.shared.nats import NatsManager
from dreambot.shared.worker import DreambotWorkerBase
from typing import Any
from argparse import ArgumentParser, RawTextHelpFormatter


class DreambotCLI:
    def __init__(self, cli_name: str):
        self.cli_name = cli_name
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
            raise ValueError("nats_uri not provided in JSON config")

        self.nats = NatsManager(nats_uri=self.options["nats_uri"], name=self.cli_name)

    def run(self):
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.get_event_loop()

            # FIXME: This is ungraceful, but Windows can't do signal handling this way. We could do something like https://stackoverflow.com/questions/45987985/asyncio-loops-add-signal-handler-in-windows
            if sys.platform != "win32":
                for s in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(s, lambda s=s: asyncio.create_task(self.shutdown(s)))
                loop.add_signal_handler(signal.SIGHUP, lambda: self.toggle_debug())

            loop.create_task(self.nats.boot(self.workers))
            [loop.create_task(x.boot()) for x in self.workers]
            loop.run_forever()
        finally:
            if loop:
                loop.close()
            self.logger.info("Shutting down...")

    # Toggle between logging.INFO and logging.DEBUG for *all* loggers in the current process
    def toggle_debug(self):
        if logging.root.level == logging.DEBUG:
            level = logging.INFO
        else:
            level = logging.DEBUG

        logging.root.setLevel(level)
        for logger in [logging.getLogger(name) for name in logging.root.manager.loggerDict]:
            logger.setLevel(level)

    async def shutdown(self, sig: int):
        self.logger.info("Received signal: {}".format(signal.Signals(sig).name))
        await self.nats.shutdown()
        [await x.shutdown() for x in self.workers]

        self.logger.debug("Cancelling all other tasks")
        [task.cancel() for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    async def callback_send_workload(self, queue_name: str, message: bytes) -> None:
        raw_msg = message.decode()
        json_msg = json.loads(raw_msg)
        if "reply-image" in json_msg:
            json_msg["reply-image"] = "** IMAGE **"
        self.logger.debug("callback_send_workload for '{}': {}".format(queue_name, json_msg))
        await self.nats.publish(queue_name, message)
