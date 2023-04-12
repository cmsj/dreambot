import asyncio

# import frontend
from dreambot.shared.nats import NatsManager
from dreambot.frontend.irc import FrontendIRC
from dreambot.shared.cli import DreambotCLI

class DreambotFrontendIRCCLI(DreambotCLI):
    cli_name = "FrontendIRC"
    example_json = """Example JSON config:
{
    "triggers": [
          "!dream ",
          "!gpt "
    ],
    "nats_uri": [ "nats://nats:4222", "nats://nats2:4222" ],
    "output_dir": "/data",
    "uri_base": "http://localhost:8080/dreams",
    "irc": [
        {
                "nickname": "dreambot",
                "ident": "dreambot",
                "realname": "I've dreamed things you people wouldn't believe",
                "host": "irc.server.com",
                "port": 6697,
                "ssl": true,
                "channels": [
                        "#friends",
                        "#dreambot"
                ]
        }
    ]
}"""

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
                await nats_manager.publish(queue_name, message)

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

def main():
    cli = DreambotFrontendIRCCLI()
    cli.boot()

if __name__ == "__main__":
    main()