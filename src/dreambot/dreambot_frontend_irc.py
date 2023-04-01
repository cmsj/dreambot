import asyncio
import json
import logging
import sys

# import frontend
import frontend.nats_manager
import frontend.irc

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('dreambot_frontend_irc')
logger.setLevel(logging.DEBUG)

def main():
    if len(sys.argv) != 2:
        print("Usage: {} <config.json>".format(sys.argv[0]))
        sys.exit(1)

    with open(sys.argv[1]) as f:
        options = json.load(f)

    try:
        logger.info("Dreambot IRC frontend starting up...")
        servers = []
        delegates = []

        nats_manager = frontend.nats_manager.FrontendNatsManager(nats_uri=options["nats_uri"])
        loop = asyncio.get_event_loop()

        async def trigger_callback(queue_name, message):
            logger.debug("trigger_callback for '{}': {}".format(queue_name, message.decode()))
            await nats_manager.nats_publish(queue_name, message)

        for server_config in options["irc"]:
            server = frontend.irc.FrontendIRC(server_config, options, trigger_callback)
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
        logger.info("Dreambot IRC frontend shutting down...")

if __name__ == "__main__":
    main()