import asyncio
import base64
import json
import requests
import socketio
import traceback

from typing import Any, Callable, Coroutine
from dreambot.backend.base import DreambotBackendBase


class DreambotBackendInvokeAI(DreambotBackendBase):
    backend_name = "InvokeAI"

    def __init__(
        self, options: dict[str, Any], callback_send_message: Callable[[str, bytes], Coroutine[Any, Any, None]]
    ):
        super().__init__(options, callback_send_message)
        self.sio: socketio.Client
        self.invokeai_host = options["invokeai"]["host"]
        self.invokeai_port = options["invokeai"]["port"]
        self.request_cache: dict[str, Any] = {}
        self.ws_uri = "ws://{}:{}/".format(self.invokeai_host, self.invokeai_port)
        self.api_uri = "http://{}:{}/api/v1/".format(self.invokeai_host, self.invokeai_port)
        self.logger.debug("Set InvokeAI options to: host={}, port={}".format(self.invokeai_host, self.invokeai_port))

    async def boot(self):
        self.logger.info("InvokeAI API URI: {}".format(self.api_uri))
        self.logger.info("Connecting to InvokeAI socket.io at {}".format(self.ws_uri))
        self.sio = socketio.Client(reconnection_delay_max=10)
        self.sio.on("connect", self.on_connect)  # type: ignore
        self.sio.on("disconnect", self.on_disconnect)  # type: ignore
        self.sio.on("invocation_complete", self.on_invocation_complete)  # type: ignore
        self.sio.connect(self.ws_uri, socketio_path="/ws/socket.io")  # type: ignore

    async def shutdown(self):
        self.sio.disconnect()  # type: ignore

    async def callback_receive_message(self, queue_name: str, message: bytes) -> bool:
        self.logger.info("callback_receive_message: {}".format(message.decode()))
        try:
            resp = json.loads(message.decode())
        except Exception as e:
            self.logger.error("Failed to parse message: {}".format(e))
            return True

        try:
            prompt = resp["prompt"]
            if not self.sio.connected:  # type: ignore
                self.logger.error("Socket.io not connected, cannot send prompt")
                return False

            self.logger.info("Sending prompt to InvokeAI: {}".format(prompt))
            id = 1
            nodes = {}
            nodes[str(id)] = {
                "id": str(id),
                "type": "txt2img",
                "prompt": prompt,
                "model": "stable-diffusion-1.5",
                "sampler": "keuler_a",
                "steps": 50,
                "seed": -1,
            }
            id += 1
            nodes[str(id)] = {
                "id": str(id),
                "type": "show_image",
            }
            links = [
                {
                    "source": {"node_id": "1", "field": "image"},
                    "destination": {"node_id": "2", "field": "image"},
                }
            ]
            graph: dict[str, Any] = {"nodes": nodes, "edges": links}
            self.logger.debug("Sending graph to InvokeAI: {}".format(graph))

            r = requests.post(self.api_uri + "sessions", json=graph)
            if r.status_code != 200:
                self.logger.error("Error POSTing session to InvokeAI: {}".format(r.reason))
                resp["error"] = "Error from InvokeAI: {}".format(r.reason)
                return resp

            response = r.json()
            self.request_cache[response["id"]] = resp
            self.logger.debug("InvokeAI response: {}".format(response))

            self.logger.info("Subscribing to InvokeAI session and invoking: {}".format(response["id"]))
            self.sio.emit("subscribe", {"session": response["id"]})  # type: ignore
            r = requests.put(self.api_uri + "sessions/{}/invoke".format(response["id"]))
            if r.status_code != 202:
                self.logger.error("Error PUTing session to InvokeAI: {}".format(r.reason))
                resp["error"] = "Error from InvokeAI: {}".format(r.reason)
                return resp

            # No more work to do here, InvokeAI will send us a message when it's done
            resp["reply-none"] = "Waiting for InvokeAI to generate a response..."
        except Exception as e:
            self.logger.error("Unknown error: {}".format(e))
            resp["error"] = "Unknown error, ask your bot admin to check logs."

        # Technically we don't need to send this because we set reply-none, but for the sake of
        # completeness and future possibility, we'll send it anyway
        try:
            self.logger.info("Sending response: {} with {}".format(resp, self.callback_send_message))
            packet = json.dumps(resp)
            await self.callback_send_message(resp["reply-to"], packet.encode())
            self.logger.debug("Response sent!")
        except Exception as e:
            self.logger.error("Failed to send response: {}".format(e))
            traceback.print_exc()

        return True

    def on_connect(self):
        self.logger.info("Connected to InvokeAI socket.io")

    def on_disconnect(self):
        self.logger.info("Disconnected from InvokeAI socket.io")

    def on_invocation_complete(self, data: dict[str, Any]):
        id = data["graph_execution_state_id"]

        self.logger.info("Invocation complete: {}".format(id))
        self.logger.debug("Invocation complete data: {}".format(data))

        self.logger.info("Unsubscribing from InvokeAI session: {}".format(id))
        self.sio.emit("unsubscribe", {"session": id})  # type: ignore

        request = self.request_cache[id]
        # We likely have a reply-none from when we first replied to this request, so remove it
        request.pop("reply-none", None)

        r = requests.get(self.api_uri + "images/results/{}".format(data["result"]["image"]["image_name"]))
        if r.status_code != 200:
            self.logger.error("Error fetching image from InvokeAI: {}".format(r.reason))
            request["error"] = "Error from InvokeAI: {}".format(r.reason)
            return
        else:
            # FIXME: There is a 1MB limit on NATS messages. We should instead write to disk here and merely send a URL over NATS
            # (or we get fancy and use some kind of object store, and send a URL to that)
            request["reply-image"] = base64.b64encode(r.content).decode("utf8")

        self.logger.debug(
            "Sending image response to queue '{}': for {} <{}> {}".format(
                request["reply-to"],
                request["channel"],
                request["user"],
                request["prompt"],
            )
        )

        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.callback_send_message(request["reply-to"], json.dumps(request).encode()))
        loop.close()
        self.logger.debug("Sent")
