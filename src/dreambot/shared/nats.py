"""NATS manager for Dreambot."""

import asyncio
import json
import logging
import sys
import traceback

from asyncio import Task
from typing import Any

from nats.js.errors import BadRequestError, NotFoundError
from nats.js import JetStreamContext, JetStreamManager
from nats.aio.client import Client as NATSClient

import nats
import nats.errors

from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType


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
        self.stream_name = "dreambot"

        self.nats: NATSClient | None = None
        self.jets: JetStreamContext
        self.jsm: JetStreamManager

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
            workers (list[DreambotWorkerBase]): A list of Dreambot workers.
        """

        async def nats_error(e: nats.errors.Error):
            """Slow Consumer errors mean NATS has stopped talking to us. We could try to reconnect, but we'll rely on Docker to restart us."""
            if isinstance(e, nats.errors.SlowConsumerError):
                self.logger.critical("NATS slow consumer detected. Exiting.")
                sys.exit(1)

        self.logger.info("NATS booting")
        try:
            self.nats = await nats.connect(self.nats_uri, name=self.name, error_cb=nats_error)  # type: ignore
            self.logger.info("NATS connected to %s", self.nats.connected_url.netloc)  # type: ignore
            self.jets = self.nats.jetstream()  # type: ignore
            self.jsm = self.nats.jsm()  # type: ignore

            for worker in workers:
                # Each worker needs to know its own NATS 'address' (ie subject)
                worker.address = self.get_subject(worker)

                # Set up NATS subscriptions for each worker
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
            worker (DreambotWorkerBase): A Dreambot worker instance.
        """
        while not self.shutting_down:
            queue_name = self.get_queue_name(worker).replace(".", "_")
            subject = self.get_subject(worker)

            try:
                try:
                    _ = await self.jsm.stream_info(self.stream_name)  # type: ignore
                    self.logger.error("NATS stream '%s' already exists, not creating it", self.stream_name)
                except NotFoundError:
                    self.logger.error("NATS stream '%s' does not exist, creating it", self.stream_name)
                    _ = await self.jets.add_stream(name=self.stream_name, subjects=[f"{end.value}.>" for end in DreambotWorkerEndType], retention="workqueue")  # type: ignore

                self.logger.info(
                    "Subscribing on stream '%s' to subject '%s', durable name '%s', queue group '%s'",
                    self.stream_name,
                    subject,
                    queue_name,
                    queue_name,
                )
                sub = await self.jets.subscribe(
                    subject, stream=self.stream_name, durable=queue_name, manual_ack=True, queue=queue_name
                )

                while not self.shutting_down:
                    self.logger.debug("Waiting for NATS message on %s", subject)
                    try:
                        msg = await sub.next_msg()
                        msg_str = msg.data.decode()
                        msg_dict: dict[str, Any] = json.loads(msg_str)

                        log_dict = msg_dict.copy()
                        if "reply-image" in log_dict:
                            log_dict["reply-image"] = "** IMAGE **"
                        self.logger.debug("Received NATS message on '%s': %s", subject, log_dict)

                        worker_callback_result = None
                        try:
                            if not worker.is_booted:
                                self.logger.debug("Worker not fully booted yet, skipping message")
                                await asyncio.sleep(1)
                                continue

                            # We will remove the message from the queue if the callback returns anything but False
                            worker_callback_result = await worker.callback_receive_workload(subject, msg_dict)
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
                    subject,
                )
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                self.logger.error("nats_subscribe exception: %s", exc)
                traceback.print_exc()
                await asyncio.sleep(5)

    async def publish(self, message: dict[str, Any]):
        """Publish a message to NATS.

        Args:
            subject (str): The NATS queue to publish to.
            data (bytes): The message to publish, as a bytes encoded JSON string.
        """
        json_msg = message.copy()
        if "reply-image" in json_msg:
            json_msg["reply-image"] = "** IMAGE **"
        self.logger.debug("Publishing to NATS: %s", json_msg)

        data = json.dumps(message).encode()
        await self.jets.publish(message["to"], data)  # type: ignore

    def get_queue_name(self, worker: DreambotWorkerBase) -> str:
        """Construct the queue name for a worker.

        Args:
            worker (DreambotWorkerBase): A Dreambot worker instance.

        Returns:
            str: The name of the queue for the given worker.
        """
        if worker.subname != "":
            return f"{worker.name}.{worker.subname.replace('.', '_')}"
        else:
            return worker.name

    def get_subject(self, worker: DreambotWorkerBase) -> str:
        """Construct the subject a worker should subscribe to.

        Args:
            worker (DreambotWorkerBase): A Dreambot worker instance.

        Returns:
            str: The subject the given worker should subscribe to.
        """
        return f"{worker.end.value}.{self.get_queue_name(worker)}"
