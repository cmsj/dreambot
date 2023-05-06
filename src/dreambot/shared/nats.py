"""NATS manager for Dreambot."""
import asyncio
import json
import logging
import traceback

from asyncio import Task
from typing import Any

from nats.js.errors import BadRequestError
from nats.js import JetStreamContext
from nats.aio.client import Client as NATSClient

import nats
import nats.errors

from dreambot.shared.worker import DreambotWorkerBase


class NatsManager:
    """This class handles NATS connections and subscriptions for Dreambot workers."""

    def __init__(self, nats_uri: str, name: str):
        """Initialise the class.

        Args:
            nats_uri (str): A URI for the NATS server to connect to, e.g. nats://localhost:4222
            name (str): The name of this client, used for logging and as a NATS client name.
        """
        self.name = name
        self.nats_uri = nats_uri
        self.nats_tasks: list[Task[Any]] = []
        self.shutting_down = False
        self.logger = logging.getLogger(f"dreambot.shared.nats.{self.name}")

        self.jets: JetStreamContext
        self.nats: NATSClient | None = None

    async def shutdown(self):
        """Shutdown this instance."""
        self.shutting_down = True

        self.logger.info("Shutting down")
        _ = [task.cancel() for task in self.nats_tasks]
        self.nats_tasks = []

        # We do this so the asyncio.gather() in boot() has time to notice its tasks
        # have all finished before we kill all other tasks
        await asyncio.sleep(5)

        if self.nats:
            await self.nats.close()

    async def boot(self, workers: list[DreambotWorkerBase]):
        """Boot this instance.

        Args:
            workers (list[DreambotWorkerBase]): A list of Dreambot workers each of which exposes a queue_name() method we can use to subscribe to their queues.
        """
        self.logger.info("NATS booting")
        try:
            self.nats = await nats.connect(self.nats_uri, name=self.name)  # type: ignore
            self.logger.info("NATS connected to %s", self.nats.connected_url.netloc)  # type: ignore
            self.jets = self.nats.jetstream()  # type: ignore

            for worker in workers:
                self.nats_tasks.append(asyncio.create_task(self.subscribe(worker)))

            await asyncio.gather(*self.nats_tasks)
        except nats.errors.NoServersError:
            self.logger.error("NATS failed to connect to any servers")
        except Exception as exc:
            self.logger.error("boot exception: %s", exc)
        finally:
            self.logger.debug("boot() ending, cancelling any remaining NATS subscriber tasks")
            _ = [task.cancel() for task in self.nats_tasks]
            self.nats_tasks = []
            if self.nats:
                await self.nats.close()

    async def subscribe(self, worker: DreambotWorkerBase) -> None:
        """Subscribe to a NATS queue.

        Args:
            worker (DreambotWorkerBase): A Dreambot worker instance that exposes a queue_name() method we can use to subscribe to its queue, and a callback_receive_workload() method we can pass messages to when they arrive.
        """
        while True and not self.shutting_down:
            queue_name = worker.queue_name()
            self.logger.info("NATS subscribing to %s", queue_name)
            try:
                _ = await self.jets.add_stream(name=queue_name, subjects=[queue_name], retention="workqueue")  # type: ignore
                sub = await self.jets.subscribe(queue_name)

                while True and not self.shutting_down:
                    self.logger.debug("Waiting for NATS message on %s", queue_name)
                    try:
                        msg = await sub.next_msg()
                        msg_str = msg.data.decode()
                        msg_dict: dict[str, Any] = json.loads(msg_str)

                        log_dict = msg_dict.copy()
                        if "reply-image" in log_dict:
                            log_dict["reply-image"] = "** IMAGE **"
                        self.logger.debug("Received NATS message on '%s': %s", queue_name, log_dict)

                        worker_callback_result = None
                        try:
                            if not worker.is_booted:
                                self.logger.debug("Worker not fully booted yet, skipping message")
                                await asyncio.sleep(1)
                                continue

                            # We will remove the message from the queue if the callback returns anything but False
                            worker_callback_result = await worker.callback_receive_workload(queue_name, msg_dict)
                            if worker_callback_result is not False:
                                await msg.ack()
                        except Exception as exc:
                            self.logger.error("callback_receive_workload exception: %s", exc)
                            traceback.print_exc()
                            await msg.ack()
                    except nats.errors.TimeoutError:
                        await asyncio.sleep(1)
                        continue
                    except Exception as exc:
                        self.logger.error("NATS message exception: %s", exc)
                        traceback.print_exc()

            except BadRequestError:
                self.logger.warning(
                    "NATS consumer '%s' already exists, likely a previous instance of us hasn't timed out yet. Sleeping...",
                    queue_name,
                )
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                self.logger.error("nats_subscribe exception: %s", exc)
                await asyncio.sleep(5)

    async def publish(self, subject: str, data: bytes):
        """Publish a message to NATS.

        Args:
            subject (str): The NATS queue to publish to.
            data (bytes): The message to publish, as a bytes encoded JSON string.
        """
        raw_msg = data.decode()
        json_msg = json.loads(raw_msg)
        if "reply-image" in json_msg:
            json_msg["reply-image"] = "** IMAGE **"
        self.logger.debug("Publishing to NATS: %s %s", subject, json_msg)
        await self.jets.publish(subject, data)  # type: ignore
