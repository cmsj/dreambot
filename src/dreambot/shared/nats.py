import asyncio
import logging
import nats
import signal
import traceback
from nats.errors import TimeoutError, NoServersError
from nats.js.errors import BadRequestError

class NatsManager:
    logger = None
    nats_uri = None
    nc = None
    js = None
    nats_tasks = None
    shutting_down = None

    def __init__(self, nats_uri=None):
        self.nats_uri = nats_uri
        self.nats_tasks = []
        self.shutting_down = False
        self.logger = logging.getLogger("NatsManager")

    async def shutdown(self):
        self.shutting_down = True

        self.logger.info("Shutting down")
        self.logger.debug("Cancelling NATS subscriber tasks")
        [task.cancel() for task in self.nats_tasks]
        self.nats_tasks = []

        # We do this so the asyncio.gather() in boot() has time to notice its tasks
        # have all finished before we kill all other tasks
        await asyncio.sleep(5)

        self.logger.debug("Cancelling all other tasks")
        [task.cancel() for task in asyncio.all_tasks() if task is not asyncio.current_task()]

        if self.nc:
            await self.nc.close()
        self.logger.debug("NATS shutdown complete, {} total asyncio tasks remain".format(len(asyncio.all_tasks())))

    async def boot(self, delegates):
        self.logger.info("NATS booting")
        try:
            # FIXME: We should make this an infinite loop (conditional on self.shutting_down) and disable nat.connect() reconnect
            self.nc = await nats.connect(self.nats_uri, name="NatsManager")
            self.logger.info("NATS connected to {}".format(self.nc.connected_url.netloc))
            self.js = self.nc.jetstream()

            for delegate in delegates:
                self.nats_tasks.append(asyncio.create_task(self.subscribe(delegate)))

            await asyncio.gather(*self.nats_tasks)
            self.logger.debug("All NATS subscriber tasks gathered")
        except NoServersError:
            self.logger.error("NATS failed to connect to any servers")
        except Exception as e:
            self.logger.error("boot exception: {}".format(e))
            import traceback
            traceback.print_exc()
        finally:
            self.logger.debug("boot() ending, cancelling any remaining NATS subscriber tasks")
            [task.cancel() for task in self.nats_tasks]
            self.nats_tasks = []
            if self.nc:
                await self.nc.close()

    async def subscribe(self, delegate):
        if "queue_name" not in delegate or "callback" not in delegate:
            self.logger.error("nats_subscribe delegate missing required keys ('queue_name', 'callback'))")
            raise ValueError("nats_subscribe delegate missing required keys ('queue_name', 'callback'))")
            return

        while True and not self.shutting_down:
            queue_name = delegate["queue_name"]
            self.logger.info("NATS subscribing to {}".format(queue_name))
            try:
                stream = await self.js.add_stream(name=queue_name, subjects=[queue_name], retention="workqueue")
                self.logger.debug("Created stream: '{}'".format(stream.did_create))
                sub = await self.js.subscribe(queue_name)
                self.logger.debug("Created subscription: '{}'".format(sub))
                self.logger.debug("callback is: {}".format(delegate["callback"]))

                while True and not self.shutting_down:
                    self.logger.debug("Waiting for NATS message on {}".format(queue_name))
                    try:
                        msg = await sub.next_msg()
                        self.logger.debug("Received message on '{}': {}".format(queue_name, msg.data.decode()))

                        # We will remove the message from the queue if the callback returns anything but False
                        delegate_result = await delegate["callback"](queue_name, msg.data)
                        if delegate_result is not False:
                            self.logger.debug("Acking message on '{}'".format(queue_name))
                            await msg.ack()
                        else:
                            self.logger.debug("Not acking message on '{}'".format(queue_name))
                    except:
                        await asyncio.sleep(1)
                        continue
            except BadRequestError:
                self.logger.warning("NATS consumer '{}' already exists, likely a previous instance of us hasn't timed out yet".format(queue_name))
                await asyncio.sleep(5)
                continue
            except Exception as e:
                self.logger.error("nats_subscribe exception: {}".format(e))
                traceback.print_exc()
                await asyncio.sleep(5)

    async def publish(self, subject, data):
        self.logger.debug("Publishing to NATS: {} {}".format(subject, data))
        await self.js.publish(subject, data)

# FIXME: This is weird and probably should be in some kind of FrontendManager that supervises both FrontendNatsManager and whatever frontend service being used
async def shutdown(loop, signal=None, objects = []):
    if signal:
        print("Received exit signal {}...".format(signal.name))
    print(">>> Signalling objects to shutdown")
    for object in objects:
        await object.shutdown()

    print(">>> Shutting down")
    loop.stop()


# if __name__ == "__main__":
#     def callback(queue_name, message):
#         print("Received a message on '{}': {}".format(queue_name, message.decode()))
#         print(message)

#     try:
#         nm = FrontendNatsManager(nats_uri="nats://localhost:4222")
#         loop = asyncio.get_event_loop()

#         for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
#              loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(loop, signal=s, objects=[nm])))

#         loop.create_task(nm.boot([{"queue_name": "test1", "callback": callback}, {"queue_name": "test2", "callback": callback}]))
#         loop.run_forever()
#     finally:
#         loop.close()
