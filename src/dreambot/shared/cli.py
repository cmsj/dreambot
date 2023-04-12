import argparse
import json
import logging

class DreambotCLI:
    logger = None
    cli_name = "BaseCLI"
    parser = None
    args = None
    options = None

    def __init__(self):
        self.logger = logging.getLogger("dreambot.cli.{}".format(self.cli_name))

    def parse_args(self):
        self.parser = argparse.ArgumentParser(description="Dreambot {}".format(self.cli_name))
        self.parser.add_argument("-c", "--config", help="Path to config JSON file", required=True)
        self.parser.add_argument("-d", "--debug", help="Enable debug logging", action="store_true")
        self.parser.add_argument("-q", "--quiet", help="Disable most logging", action="store_true")
        self.args = self.parser.parse_args()

    def boot(self):
        self.args = self.parse_args()
        if self.args.debug:
            logging.basicConfig(level=logging.DEBUG)
        elif self.args.quiet:
            logging.basicConfig(level=logging.CRITICAL)
        else:
            logging.basicConfig(level=logging.INFO)

        with open(self.args.config) as f:
            self.options = json.load(f)
