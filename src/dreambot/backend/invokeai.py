"""InvokeAI backend for Dreambot."""

import asyncio
import base64
import io

from typing import Any, Tuple
from argparse import REMAINDER, ArgumentError, Namespace

import aiohttp
import requests
import socketio

from PIL import Image
from dreambot.shared.custom_argparse import UsageException, ErrorCatchingArgumentParser
from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType, CallbackSendWorkload


class ImageFetchException(Exception):
    """Exception raised when we fail to fetch an image."""

    def __init__(self, message: str):
        """Initialise the class."""
        super().__init__(message)


class DreambotBackendInvokeAI(DreambotWorkerBase):
    """InvokeAI backend for Dreambot."""

    def __init__(self, options: dict[str, Any], callback_send_workload: CallbackSendWorkload):
        """Initialise the class."""
        super().__init__(
            name="invokeai",
            end=DreambotWorkerEndType.BACKEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.sio: socketio.Client
        self.invokeai_host = options["invokeai"]["host"]
        self.invokeai_port = options["invokeai"]["port"]
        self.request_cache: dict[str, Any] = {}
        self.last_completion: dict[str, Any] = {}
        self.ws_uri = f"ws://{self.invokeai_host}:{self.invokeai_port}/"
        self.api_uri = f"http://{self.invokeai_host}:{self.invokeai_port}/api/v1/"

        # Set our default InvokeAI options
        self.model = "stable-diffusion-1.5"
        self.sampler = "keuler_a"
        self.steps = 50
        self.seed = -1

    async def boot(self):
        """Boot the backend."""
        self.logger.info("InvokeAI API URI: %s, socket.io URI: %s", self.api_uri, self.ws_uri)

        self.sio = socketio.Client(reconnection_delay_max=10)
        self.sio.on("connect", self.on_connect)  # type: ignore
        self.sio.on("disconnect", self.on_disconnect)  # type: ignore
        self.sio.on("invocation_complete", self.on_invocation_complete)  # type: ignore
        self.sio.on("graph_execution_state_complete", self.on_graph_execution_state_complete)  # type: ignore
        self.sio.on("invocation_error", self.on_invocation_error)  # type: ignore
        self.sio.connect(self.ws_uri, socketio_path="/ws/socket.io")  # type: ignore

        self.is_booted = True

    async def shutdown(self):
        """Shutdown the backend."""
        self.sio.disconnect()  # type: ignore

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Process in incoming workload message.

        Args:
            queue_name (str): The name of the queue we received the message from.
            message (bytes): The message we received, as a dictionary.

        Returns:
            bool: True if the message should be ack'd to NATS, False otherwise.
        """
        self.logger.info("callback_receive_workload: %s", message)

        try:
            argparser = self.arg_parser()
            args = argparser.parse_args(message["prompt"].split(" "))
            args.prompt = " ".join(args.prompt)

            # Image URLs can arrive separately, so update args if we have one
            if "image_url" in message:
                args.imgurl = message["image_url"]

            if not self.sio.connected:  # type: ignore
                message["error"] = "Not connected to InvokeAI right now, I'll try again later"
                await self.send_message(message)
                return False

            graph: dict[str, Any] = await self.build_image_graph(args)

            sessions_url = f"{self.api_uri}sessions/"
            self.logger.info("POSTing graph to InvokeAI: %s :: %s", sessions_url, graph)
            async with aiohttp.ClientSession() as session:
                async with session.post(sessions_url, json=graph) as req:
                    if not req.ok:
                        message["error"] = f"Error from InvokeAI: {req.reason}"  # type: ignore
                        await self.send_message(message)
                        return True
                    response = await req.json()

            self.request_cache[response["id"]] = message
            self.logger.debug("InvokeAI response: %s", response)

            self.logger.info("Subscribing to InvokeAI session and invoking: %s", response["id"])
            self.sio.emit("subscribe", {"session": response["id"]})  # type: ignore

            async with aiohttp.ClientSession() as session:
                async with session.put(f"{sessions_url}{response['id']}/invoke?all=true") as req:
                    if not req.ok:
                        message["error"] = f"Error from InvokeAI: {req.reason}"  # type: ignore
                        await self.send_message(message)
                        return True

            message["reply-none"] = "Waiting for InvokeAI to generate a response..."
        except UsageException as exc:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text, which is in the UsageException
            message["reply-text"] = str(exc)
        except (ValueError, ArgumentError) as exc:
            message["error"] = f"Something is wrong with your arguments, try {message['trigger']} --help ({exc})"
        except ImageFetchException as exc:
            message["error"] = str(exc)
        except Exception as exc:
            message["error"] = f"Unknown error: {exc}"

        await self.send_message(message)
        return True

    def on_connect(self):
        """Act on a successful connection to InvokeAI."""
        self.logger.info("Connected to InvokeAI socket.io")

    def on_disconnect(self):
        """Act on a disconnection from InvokeAI."""
        self.logger.info("Disconnected from InvokeAI socket.io")

    def on_invocation_complete(self, data: dict[str, Any]):
        """Handle a successful invocation from InvokeAI.

        Args:
            data (dict[str, Any]): A dictionary of data returned by InvokeAI.
        """
        self.last_completion[data["graph_execution_state_id"]] = data

    def on_graph_execution_state_complete(self, data: dict[str, Any]):
        """Handle a successful graph execution from InvokeAI.

        Args:
            data (dict[str, Any]): A dictionary of data returned by InvokeAI.
        """
        graph_id = data["graph_execution_state_id"]

        self.logger.info("Graph execution state complete, unsubscribing from InvokeAI session: %s", graph_id)
        self.sio.emit("unsubscribe", {"session": graph_id})  # type: ignore

        request = self.request_cache[graph_id]
        # We likely have a reply-none from when we first replied to this request, so remove it
        request.pop("reply-none", None)

        if graph_id not in self.last_completion:
            self.logger.error("No last_completion for %s", graph_id)
            self.sync_send_reply(request)
            return

        data = self.last_completion[graph_id]
        req = requests.get(f"{self.api_uri}images/results/{data['result']['image']['image_name']}", timeout=30)
        self.last_completion.pop(graph_id, None)

        if req.status_code != 200:
            request["error"] = f"Error from InvokeAI: {req.reason}"
            self.sync_send_reply(request)
            return
        else:
            request["reply-image"] = base64.b64encode(req.content).decode("utf8")

        self.logger.debug(
            "Sending image response to queue '%s': for %s <%s> %s",
            request["reply-to"],
            request["channel"],
            request["user"],
            request["prompt"],
        )
        self.sync_send_reply(request)

    def sync_send_reply(self, request: dict[str, Any]):
        """Send a reply to NATS from a synchronous context.

        Args:
            request (dict[str, Any]): A dictionary containing the message to send.
        """
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.callback_send_workload(request))
        loop.close()

    def on_invocation_error(self, data: dict[str, Any]):
        """Handle an invocation error from InvokeAI.

        Args:
            data (dict[str, Any]): A dictionary of data returned by InvokeAI.
        """
        graph_id = data["graph_execution_state_id"]
        self.logger.error("Invocation error: %s", graph_id)

        self.sio.emit("unsubscribe", {"session": graph_id})  # type: ignore

        request = self.request_cache[graph_id]
        # We likely have a reply-none from when we first replied to this request, so remove it
        request.pop("reply-none", None)
        request["error"] = "InvokeAI pipeline failure, contact your bot admin"
        self.sync_send_reply(request)

    async def build_image_graph(self, args: Namespace) -> dict[str, Any]:
        """Build a graph for an image request.

        Args:
            args (Namespace): The output of a previous call to parse_args().

        Returns:
            dict[str, Any]: A dictionary containing a graph suitable to send to InvokeAI.
        """

        # Example txt2img graph:
        # {
        #     "id": "77154ebd-4539-4a5a-ad63-3d7d8a0d1572",
        #     "nodes": {
        #         "93dc02a4-d05b-48ed-b99c-c9b616af3402": {
        #             "id": "93dc02a4-d05b-48ed-b99c-c9b616af3402",
        #             "is_intermediate": true,
        #             "use_cache": true,
        #             "prompt": "",
        #             "clip": null,
        #             "type": "compel"
        #         },
        #         "55705012-79b9-4aac-9f26-c0b10309785b": {
        #             "id": "55705012-79b9-4aac-9f26-c0b10309785b",
        #             "is_intermediate": true,
        #             "use_cache": true,
        #             "seed": 0,
        #             "width": 512,
        #             "height": 512,
        #             "use_cpu": true,
        #             "type": "noise"
        #         },
        #         "c8d55139-f380-4695-b7f2-8b3d1e1e3db8": {
        #             "id": "c8d55139-f380-4695-b7f2-8b3d1e1e3db8",
        #             "is_intermediate": true,
        #             "use_cache": true,
        #             "model": {
        #                 "key": "081d864e-2c02-4362-8c94-dd37d10878b6",
        #                 "hash": "4b394e9f74f765b8b1518a9d3571827967c84f5e8dd3b1a7497c551eec696d7a",
        #                 "name": "stable-diffusion-v1-5",
        #                 "base": "sd-1",
        #                 "type": "main",
        #                 "submodel_type": null
        #             },
        #             "type": "main_model_loader"
        #         },
        #         "7d8bf987-284f-413a-b2fd-d825445a5d6c": {
        #             "id": "7d8bf987-284f-413a-b2fd-d825445a5d6c",
        #             "is_intermediate": true,
        #             "use_cache": true,
        #             "prompt": "Tired programmer",
        #             "clip": null,
        #             "type": "compel"
        #         },
        #         "ea94bc37-d995-4a83-aa99-4af42479f2f2": {
        #             "id": "ea94bc37-d995-4a83-aa99-4af42479f2f2",
        #             "is_intermediate": true,
        #             "use_cache": false,
        #             "low": 0,
        #             "high": 2147483647,
        #             "type": "rand_int"
        #         },
        #         "eea2702a-19fb-45b5-9d75-56b4211ec03c": {
        #             "id": "eea2702a-19fb-45b5-9d75-56b4211ec03c",
        #             "is_intermediate": true,
        #             "use_cache": true,
        #             "positive_conditioning": null,
        #             "negative_conditioning": null,
        #             "noise": null,
        #             "steps": 50,
        #             "cfg_scale": 7.5,
        #             "denoising_start": 0,
        #             "denoising_end": 1,
        #             "scheduler": "unipc",
        #             "unet": null,
        #             "control": null,
        #             "ip_adapter": null,
        #             "t2i_adapter": null,
        #             "cfg_rescale_multiplier": 0,
        #             "latents": null,
        #             "denoise_mask": null,
        #             "type": "denoise_latents"
        #         },
        #         "58c957f5-0d01-41fc-a803-b2bbf0413d4f": {
        #             "board": null,
        #             "metadata": null,
        #             "id": "58c957f5-0d01-41fc-a803-b2bbf0413d4f",
        #             "is_intermediate": false,
        #             "use_cache": true,
        #             "latents": null,
        #             "vae": null,
        #             "tiled": false,
        #             "fp32": true,
        #             "type": "l2i"
        #         },
        #         "5186c8b7-b415-497b-8908-b2a99ea2e54b": {
        #             "board": null,
        #             "metadata": null,
        #             "id": "5186c8b7-b415-497b-8908-b2a99ea2e54b",
        #             "is_intermediate": true,
        #             "use_cache": true,
        #             "image": null,
        #             "model_name": "RealESRGAN_x4plus.pth",
        #             "tile_size": 400,
        #             "type": "esrgan"
        #         }
        #     },
        #     "edges": [
        #         {
        #             "source": {
        #                 "node_id": "ea94bc37-d995-4a83-aa99-4af42479f2f2",
        #                 "field": "value"
        #             },
        #             "destination": {
        #                 "node_id": "55705012-79b9-4aac-9f26-c0b10309785b",
        #                 "field": "seed"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "c8d55139-f380-4695-b7f2-8b3d1e1e3db8",
        #                 "field": "clip"
        #             },
        #             "destination": {
        #                 "node_id": "7d8bf987-284f-413a-b2fd-d825445a5d6c",
        #                 "field": "clip"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "c8d55139-f380-4695-b7f2-8b3d1e1e3db8",
        #                 "field": "clip"
        #             },
        #             "destination": {
        #                 "node_id": "93dc02a4-d05b-48ed-b99c-c9b616af3402",
        #                 "field": "clip"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "55705012-79b9-4aac-9f26-c0b10309785b",
        #                 "field": "noise"
        #             },
        #             "destination": {
        #                 "node_id": "eea2702a-19fb-45b5-9d75-56b4211ec03c",
        #                 "field": "noise"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "7d8bf987-284f-413a-b2fd-d825445a5d6c",
        #                 "field": "conditioning"
        #             },
        #             "destination": {
        #                 "node_id": "eea2702a-19fb-45b5-9d75-56b4211ec03c",
        #                 "field": "positive_conditioning"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "93dc02a4-d05b-48ed-b99c-c9b616af3402",
        #                 "field": "conditioning"
        #             },
        #             "destination": {
        #                 "node_id": "eea2702a-19fb-45b5-9d75-56b4211ec03c",
        #                 "field": "negative_conditioning"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "c8d55139-f380-4695-b7f2-8b3d1e1e3db8",
        #                 "field": "unet"
        #             },
        #             "destination": {
        #                 "node_id": "eea2702a-19fb-45b5-9d75-56b4211ec03c",
        #                 "field": "unet"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "eea2702a-19fb-45b5-9d75-56b4211ec03c",
        #                 "field": "latents"
        #             },
        #             "destination": {
        #                 "node_id": "58c957f5-0d01-41fc-a803-b2bbf0413d4f",
        #                 "field": "latents"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "c8d55139-f380-4695-b7f2-8b3d1e1e3db8",
        #                 "field": "vae"
        #             },
        #             "destination": {
        #                 "node_id": "58c957f5-0d01-41fc-a803-b2bbf0413d4f",
        #                 "field": "vae"
        #             }
        #         },
        #         {
        #             "source": {
        #                 "node_id": "58c957f5-0d01-41fc-a803-b2bbf0413d4f",
        #                 "field": "image"
        #             },
        #             "destination": {
        #                 "node_id": "5186c8b7-b415-497b-8908-b2a99ea2e54b",
        #                 "field": "image"
        #             }
        #         }
        #     ]
        # }
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
        """Fetch an image from a URL.

        Args:
            url (str): The URL of an image to fetch.

        Raises:
            ImageFetchException: Either the image could not be fetched, or the URL returned a non-image.

        Returns:
            Tuple[str, io.BytesIO]: A tuple containing the MIME type of the image and a file-like object containing the image data.
        """
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

                # Resize the image so it's not too big for our VRAM
                resp_image = io.BytesIO()
                thumbnail = Image.open(io.BytesIO(image))
                thumbnail.thumbnail((512, 512), Image.Resampling.LANCZOS)
                thumbnail.save(resp_image, "JPEG")
                resp_image.flush()
                resp_image.seek(0)

                return ("image/jpeg", resp_image)

    async def upload_image(self, url: str) -> Tuple[str, str]:
        """Fetch an image from an arbitrary URL and upload it to InvokeAI.

        Args:
            url (str): The URL of an image to fetch and upload to InvokeAI.

        Raises:
            ImageFetchException: The image upload failed.

        Returns:
            Tuple[str, str]: A tuple containing the name of the image and the MIME type of the image.
        """
        image_name = "Unknown"
        (content_type, image) = await self.fetch_image(url)
        upload_url = self.api_uri + "images/uploads/"

        self.logger.info("Uploading image (%s) to InvokeAI: %s -> %s", content_type, url, upload_url)
        files: dict[str, Tuple[str, io.BytesIO, str]] = {
            "file": (image_name, image, content_type),
        }
        response = requests.post(
            f"http://{self.invokeai_host}:{self.invokeai_port}/api/v1/images/uploads/", files=files, timeout=30
        )
        if not response.ok:
            self.logger.error("Error uploading image to InvokeAI: %s", response.reason)
            raise ImageFetchException(f"Error uploading image to InvokeAI: {response.reason}")
        body = response.json()
        image_name = body["image_name"]
        image_type = body["image_type"]
        self.logger.info("Image uploaded as: %s (%s)", image_name, image_type)
        return (image_name, image_type)

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        """Get an argument parser for this worker.

        Returns:
            ErrorCatchingArgumentParser: An argument parser that can be used with parse_args().
        """
        parser = super().arg_parser()
        parser.add_argument("-m", "--model", help="InvokeAI model to use", default=self.model)
        parser.add_argument("-s", "--sampler", help="InvokeAI sampler to use", default=self.sampler)
        parser.add_argument("-t", "--steps", help="Number of steps to run InvokeAI for", default=self.steps, type=int)
        parser.add_argument("-i", "--imgurl", help="Start with an image from URL", default=None)
        # parser.add_argument("-r", "--reroll", help="Reroll the image", action="store_true") # FIXME: Implement this?
        parser.add_argument(
            "-e", "--seed", help="Seed to use for InvokeAI (-1 for random)", default=self.seed, type=int
        )
        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
