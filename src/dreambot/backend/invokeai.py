import aiohttp
import asyncio
import base64
import io
import json
import requests
import socketio


from typing import Any, Callable, Coroutine, Tuple
from argparse import REMAINDER, ArgumentError, Namespace
from PIL import Image
from dreambot.backend.base import DreambotBackendBase
from dreambot.shared.worker import UsageException, ErrorCatchingArgumentParser


class ImageFetchException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class DreambotBackendInvokeAI(DreambotBackendBase):
    def __init__(
        self, options: dict[str, Any], callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]]
    ):
        super().__init__("InvokeAI", options, callback_send_workload)
        self.sio: socketio.Client
        self.invokeai_host = options["invokeai"]["host"]
        self.invokeai_port = options["invokeai"]["port"]
        self.request_cache: dict[str, Any] = {}
        self.last_completion: dict[str, Any] | None = None
        self.ws_uri = "ws://{}:{}/".format(self.invokeai_host, self.invokeai_port)
        self.api_uri = "http://{}:{}/api/v1/".format(self.invokeai_host, self.invokeai_port)

        # Set our default InvokeAI options
        self.model = "stable-diffusion-1.5"
        self.sampler = "keuler_a"
        self.steps = 50
        self.seed = -1

    async def boot(self):
        self.logger.info("InvokeAI API URI: %s", self.api_uri)
        self.logger.info("Connecting to InvokeAI socket.io at %s", self.ws_uri)
        self.sio = socketio.Client(reconnection_delay_max=10)
        self.sio.on("connect", self.on_connect)  # type: ignore
        self.sio.on("disconnect", self.on_disconnect)  # type: ignore
        self.sio.on("invocation_complete", self.on_invocation_complete)  # type: ignore
        self.sio.on("graph_execution_state_complete", self.on_graph_execution_state_complete)  # type: ignore
        self.sio.on("invocation_error", self.on_invocation_error)  # type: ignore
        self.sio.connect(self.ws_uri, socketio_path="/ws/socket.io")  # type: ignore

    async def shutdown(self):
        self.sio.disconnect()  # type: ignore

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        self.logger.info("callback_receive_workload: %s", message.decode())
        try:
            resp = json.loads(message.decode())
        except Exception as exc:
            self.logger.error("Failed to parse message: %s", exc)
            return True

        try:
            argparser = self.arg_parser()
            args = argparser.parse_args(resp["prompt"].split(" "))
            args.prompt = " ".join(args.prompt)

            # Image URLs can arrive separately, so update args if we have one
            if "image_url" in resp:
                args.imgurl = resp["image_url"]

            if not self.sio.connected:  # type: ignore
                resp["error"] = "Not connected to InvokeAI right now, I'll try again later"
                await self.send_message(resp)
                return False

            graph: dict[str, Any] = await self.build_image_graph(args)

            sessions_url = f"{self.api_uri}sessions/"
            self.logger.info("POSTing graph to InvokeAI: %s :: %s", sessions_url, graph)
            async with aiohttp.ClientSession() as session:
                async with session.post(sessions_url, json=graph) as r:
                    if not r.ok:
                        resp["error"] = f"Error from InvokeAI: {r.reason}"  # type: ignore
                        await self.send_message(resp)
                        return True
                    response = await r.json()

            self.request_cache[response["id"]] = resp
            self.logger.debug("InvokeAI response: %s", response)

            self.logger.info("Subscribing to InvokeAI session and invoking: %s", response["id"])
            self.sio.emit("subscribe", {"session": response["id"]})  # type: ignore

            async with aiohttp.ClientSession() as session:
                async with session.put(f"{sessions_url}{response['id']}/invoke?all=true") as r:
                    if not r.ok:
                        resp["error"] = f"Error from InvokeAI: {r.reason}"  # type: ignore
                        await self.send_message(resp)
                        return True

            resp["reply-none"] = "Waiting for InvokeAI to generate a response..."
        except UsageException as exc:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text, which is in the UsageException
            resp["reply-text"] = str(exc)
        except (ValueError, ArgumentError) as exc:
            resp["error"] = f"Something is wrong with your arguments, try {self.queue_name()} --help ({exc})"
        except ImageFetchException as exc:
            resp["error"] = str(exc)
        except Exception as exc:
            resp["error"] = f"Unknown error: {exc}"
            await self.send_message(resp)
            return True

        await self.send_message(resp)
        return True

    async def send_message(self, resp: dict[str, Any]):
        try:
            self.logger.info("Sending response: %s with %s", resp, self.callback_send_workload)
            packet = json.dumps(resp)
            await self.callback_send_workload(resp["reply-to"], packet.encode())
        except Exception as exc:
            self.logger.error("Failed to send response: %s", exc)

    def on_connect(self):
        self.logger.info("Connected to InvokeAI socket.io")

    def on_disconnect(self):
        self.logger.info("Disconnected from InvokeAI socket.io")

    def on_invocation_complete(self, data: dict[str, Any]):
        if not self.last_completion:
            self.last_completion = {}
        self.last_completion[data["graph_execution_state_id"]] = data

    def on_graph_execution_state_complete(self, data: dict[str, Any]):
        id = data["graph_execution_state_id"]

        self.logger.info("Graph execution state complete, unsubscribing from InvokeAI session: %s", id)
        self.sio.emit("unsubscribe", {"session": id})  # type: ignore

        request = self.request_cache[id]
        # We likely have a reply-none from when we first replied to this request, so remove it
        request.pop("reply-none", None)

        if not self.last_completion or id not in self.last_completion:
            self.logger.error("No last_completion for %s", id)
            self.sync_send_reply(request)
            return

        data = self.last_completion[id]
        r = requests.get(f"{self.api_uri}images/results/{data['result']['image']['image_name']}", timeout=30)
        self.last_completion.pop(id, None)

        if r.status_code != 200:
            request["error"] = "Error from InvokeAI: {}".format(r.reason)
            self.sync_send_reply(request)
            return
        else:
            request["reply-image"] = base64.b64encode(r.content).decode("utf8")

        self.logger.debug(
            "Sending image response to queue '%s': for %s <%s> %s",
            request["reply-to"],
            request["channel"],
            request["user"],
            request["prompt"],
        )
        self.sync_send_reply(request)

    def sync_send_reply(self, request: dict[str, Any]):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.callback_send_workload(request["reply-to"], json.dumps(request).encode()))
        loop.close()

    def on_invocation_error(self, data: dict[str, Any]):
        id = data["graph_execution_state_id"]
        self.logger.error("Invocation error: %s", id)

        self.sio.emit("unsubscribe", {"session": id})  # type: ignore

        request = self.request_cache[id]
        # We likely have a reply-none from when we first replied to this request, so remove it
        request.pop("reply-none", None)
        request["error"] = "InvokeAI pipeline failure, contact your bot admin"
        self.sync_send_reply(request)

    async def build_image_graph(self, args: Namespace) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []

        def add_node(node_type: str, **kwargs: Any):
            nonlocal nodes
            nodes.append({"id": str(len(nodes)), "type": node_type, **kwargs})

        if args.imgurl is not None:
            (image_name, image_type) = await self.upload_image(args.imgurl)
            add_node(
                node_type="load_image",
                image_name=image_name,
                image_type=image_type,
            )
            add_node(
                node_type="img2img",
                prompt=args.prompt,
                model=args.model,
                sampler=args.sampler,
                steps=args.steps,
                seed=args.seed,
                progress_images=False,
            )
        else:
            add_node(
                node_type="txt2img",
                prompt=args.prompt,
                model=args.model,
                sampler=args.sampler,
                steps=args.steps,
                seed=args.seed,
                progress_images=False,
            )
        add_node(node_type="upscale")

        for idx in range(0, len(nodes) - 1):
            links.append(
                {
                    "source": {"node_id": str(idx), "field": "image"},
                    "destination": {"node_id": str(idx + 1), "field": "image"},
                }
            )

        graph: dict[str, Any] = {"nodes": dict(enumerate(nodes)), "edges": links}
        return graph

    async def fetch_image(self, url: str) -> Tuple[str, io.BytesIO]:
        self.logger.info("Fetching image: %s", url)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise ImageFetchException(f"Unable to fetch: {resp.status}")
                if not resp.content_type.startswith("image/"):
                    raise ImageFetchException(f"URL was not an image: {resp.content_type}")

                image = await resp.read()
                resp.close()
                self.logger.info("Fetched %s bytes of %s", len(image), resp.content_type)

                # Resize the image
                resp_image = io.BytesIO()
                thumbnail = Image.open(io.BytesIO(image))
                thumbnail.thumbnail((512, 512), Image.ANTIALIAS)
                thumbnail.save(resp_image, "JPEG")
                resp_image.flush()
                resp_image.seek(0)

                return ("image/jpeg", resp_image)

    async def upload_image(self, url: str) -> Tuple[str, str]:
        image_name = "Unknown"
        (content_type, image) = await self.fetch_image(url)
        upload_url = self.api_uri + "images/uploads/"

        self.logger.info("Uploading image (%s) to InvokeAI: %s -> %s", content_type, url, upload_url)
        files: dict[str, Tuple[str, io.BytesIO, str]] = {
            "file": (image_name, image, content_type),
        }
        response = requests.post("http://invokeai.chrul.tenshu.net/api/v1/images/uploads/", files=files, timeout=30)
        if not response.ok:
            self.logger.error("Error uploading image to InvokeAI: %s", response.reason)
            raise ImageFetchException(f"Error uploading image to InvokeAI: {response.reason}")
        body = response.json()
        image_name = body["image_name"]
        image_type = body["image_type"]
        self.logger.info("Image uploaded as: %s (%s)", image_name, image_type)
        return (image_name, image_type)

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        parser = super().arg_parser()
        parser.add_argument("-m", "--model", help="InvokeAI model to use", default=self.model)
        parser.add_argument("-s", "--sampler", help="InvokeAI sampler to use", default=self.sampler)
        parser.add_argument("-t", "--steps", help="Number of steps to run InvokeAI for", default=self.steps, type=int)
        parser.add_argument("-i", "--imgurl", help="Start with an image from URL", default=None)
        # parser.add_argument("-r", "--reroll", help="Reroll the image", action="store_true")
        parser.add_argument(
            "-e", "--seed", help="Seed to use for InvokeAI (-1 for random)", default=self.seed, type=int
        )
        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
