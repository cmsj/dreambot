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
        self.logger = logging.getLogger("dreambot.shared.nats")

    async def shutdown(self):
        self.shutting_down = True

        self.logger.info("Shutting down")
        self.logger.debug("Cancelling NATS subscriber tasks")
        [task.cancel() for task in self.nats_tasks]
        self.nats_tasks = []

        # We do this so the asyncio.gather() in boot() has time to notice its tasks
        # have all finished before we kill all other tasks
        self.logger.debug("Sleeping to allow NATS tasks to cancel...")
        await asyncio.sleep(5)

        if self.nc:
            self.logger.debug("Closing NATS connection")
            await self.nc.close()
        self.logger.debug("NATS shutdown complete")

    async def boot(self, workers):
        self.logger.info("NATS booting")
        try:
            # FIXME: We should make this an infinite loop (conditional on self.shutting_down) and disable nat.connect() reconnect
            self.nc = await nats.connect(self.nats_uri, name="NatsManager")
            self.logger.info(
                "NATS connected to {}".format(self.nc.connected_url.netloc)
            )
            self.js = self.nc.jetstream()

            for worker in workers:
                self.nats_tasks.append(asyncio.create_task(self.subscribe(worker)))

            await asyncio.gather(*self.nats_tasks)
            self.logger.debug("All NATS subscriber tasks gathered")
        except NoServersError:
            self.logger.error("NATS failed to connect to any servers")
        except Exception as e:
            self.logger.error("boot exception: {}".format(e))
            traceback.print_exc()
        finally:
            self.logger.debug(
                "boot() ending, cancelling any remaining NATS subscriber tasks"
            )
            [task.cancel() for task in self.nats_tasks]
            self.nats_tasks = []
            if self.nc:
                await self.nc.close()

    async def subscribe(self, worker):
        callback_receive_message = worker.callback_receive_message
        while True and not self.shutting_down:
            queue_name = worker.queue_name()
            self.logger.info("NATS subscribing to {}".format(queue_name))
            try:
                stream = await self.js.add_stream(
                    name=queue_name, subjects=[queue_name], retention="workqueue"
                )
                self.logger.debug("Created stream: '{}'".format(stream.did_create))
                sub = await self.js.subscribe(queue_name)
                self.logger.debug("Created subscription: '{}'".format(sub))
                self.logger.debug("callback is: {}".format(callback_receive_message))

                while True and not self.shutting_down:
                    self.logger.debug(
                        "Waiting for NATS message on {}".format(queue_name)
                    )
                    try:
                        msg = await sub.next_msg()
                        self.logger.debug(
                            "Received NATS message on '{}': {}".format(
                                queue_name, msg.data.decode()
                            )
                        )

                        # We will remove the message from the queue if the callback returns anything but False
                        worker_callback_result = await callback_receive_message(
                            queue_name, msg.data
                        )
                        if worker_callback_result is not False:
                            self.logger.debug(
                                "Acking message on '{}'".format(queue_name)
                            )
                            await msg.ack()
                        else:
                            self.logger.debug(
                                "Not acking message on '{}'".format(queue_name)
                            )
                    except TimeoutError:
                        await asyncio.sleep(1)
                        continue
                    except Exception as e:
                        self.logger.debug("NATS message exception: {}".format(e))

            except BadRequestError:
                self.logger.warning(
                    "NATS consumer '{}' already exists, likely a previous instance of us hasn't timed out yet".format(
                        queue_name
                    )
                )
                await asyncio.sleep(5)
                continue
            except Exception as e:
                self.logger.error("nats_subscribe exception: {}".format(e))
                traceback.print_exc()
                await asyncio.sleep(5)

    async def publish(self, subject, data):
        self.logger.debug("Publishing to NATS: {} {}".format(subject, data))
        await self.js.publish(subject, data)
