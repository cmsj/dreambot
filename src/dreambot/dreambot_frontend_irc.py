import asyncio
import json
import logging
import sys

# import frontend
from dreambot.shared.nats_manager import NatsManager
from dreambot.frontend.irc import FrontendIRC
from dreambot.shared.cli import DreambotCLI

class DreambotFrontendIRCCLI(DreambotCLI):
    cli_name = "FrontendIRC"

    def boot(self):
        super().boot()

        try:
            self.logger.info("Starting up...")
            servers = []
            delegates = []

            nats_manager = NatsManager(nats_uri=self.options["nats_uri"])
            loop = asyncio.get_event_loop()

            async def trigger_callback(queue_name, message):
                self.logger.debug("trigger_callback for '{}': {}".format(queue_name, message.decode()))
                await nats_manager.nats_publish(queue_name, message)

            for server_config in self.options["irc"]:
                server = FrontendIRC(server_config, self.options, trigger_callback)
                servers.append(server)
                delegates.append({
                    "queue_name": server.queue_name(),
                    "callback": server.cb_handle_response
                })

            loop.create_task(nats_manager.boot(delegates))
            [loop.create_task(x.boot()) for x in servers]
            loop.run_forever()
        finally:
            loop.close()
            self.logger.info("Shutting down...")

if __name__ == "__main__":
    cli = DreambotFrontendIRCCLI()
    cli.boot()