import asyncio
import json
import logging
import traceback

from asyncio import Task
from typing import Any, Coroutine, Callable

from nats.js.errors import BadRequestError
from nats.js import JetStreamContext
from nats.aio.client import Client as NATSClient

import nats
import nats.errors

from dreambot.shared.worker import DreambotWorkerBase


class NatsManager:
    def __init__(self, nats_uri: str, name: str):
        self.name = name
        self.nats_uri = nats_uri
        self.nats_tasks: list[Task[Any]] = []
        self.shutting_down = False
        self.logger = logging.getLogger(f"dreambot.shared.nats.{self.name}")

        self.jets: JetStreamContext
        self.nats: NATSClient | None = None

    async def shutdown(self):
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
        callback_receive_workload: Callable[[str, bytes], Coroutine[Any, Any, bool]] = worker.callback_receive_workload
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
                        raw_msg = msg.data.decode()
                        json_msg = json.loads(raw_msg)
                        if "reply-image" in json_msg:
                            json_msg["reply-image"] = "** IMAGE **"
                        self.logger.debug("Received NATS message on '%s': %s", queue_name, json_msg)

                        worker_callback_result = None
                        try:
                            # We will remove the message from the queue if the callback returns anything but False
                            worker_callback_result = await callback_receive_workload(queue_name, msg.data)
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
        raw_msg = data.decode()
        json_msg = json.loads(raw_msg)
        if "reply-image" in json_msg:
            json_msg["reply-image"] = "** IMAGE **"
        self.logger.debug("Publishing to NATS: %s %s", subject, json_msg)
        await self.jets.publish(subject, data)  # type: ignore
