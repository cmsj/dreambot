"""A1111 backend for Dreambot."""

import base64
import io

from typing import Any
from argparse import REMAINDER, ArgumentError

import aiohttp

from PIL import Image
from dreambot.shared.custom_argparse import UsageException, ErrorCatchingArgumentParser
from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType, CallbackSendWorkload


class ImageFetchException(Exception):
    """Exception raised when we fail to fetch an image."""

    def __init__(self, message: str):
        """Initialise the class."""
        super().__init__(message)


class DreambotBackendA1111(DreambotWorkerBase):
    """A1111 backend for Dreambot."""

    def __init__(self, options: dict[str, Any], callback_send_workload: CallbackSendWorkload):
        """Initialise the class."""
        super().__init__(
            name="a1111",
            end=DreambotWorkerEndType.BACKEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.a1111_host = options["a1111"]["host"]
        self.a1111_port = options["a1111"]["port"]
        self.api_uri = f"http://{self.a1111_host}:{self.a1111_port}/sdapi/v1"

    async def boot(self):
        """Boot the backend."""
        self.logger.info("A1111 API URI: %s", self.api_uri)
        self.is_booted = True

    async def shutdown(self):
        """Shutdown the backend."""

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

            if args.list_models:
                message["reply-text"] = f"Available models: {', '.join(self.options['a1111']['models'].keys())}"
            else:
                # Figure out which model we should use
                if args.model != None:
                    # User specified a model, go with that
                    model_name = args.model
                elif message["trigger"][1:] in self.options["a1111"]["models"]:
                    # Trigger word matches the name of a model, go with that
                    model_name = message["trigger"][1:]
                else:
                    # Go with our default
                    model_name = self.options["a1111"]["default_model"]

                payload = self.options["a1111"]["models"][model_name]["payload"].copy()
                payload["prompt"] = args.prompt

                post_url = f"{self.api_uri}/txt2img"
                if args.imgurl:
                    image = await self.fetch_image(args.imgurl)
                    post_url = f"{self.api_uri}/img2img"
                    payload["init_images"] = [base64.b64encode(image.getvalue()).decode("utf8")]

                self.logger.info(
                    "POSTing graph to A1111: %s :: %s",
                    post_url,
                    {k: payload[k] for k in set(list(payload.keys())) - set(["init_images"])},
                )

                async with aiohttp.ClientSession() as session:
                    async with session.post(post_url, json=payload) as req:
                        if not req.ok:
                            message["error"] = f"Error from A1111: {req.reason}"  # type: ignore
                            await self.send_message(message)
                            return True
                        response = await req.json()
                        if "images" not in response:
                            raise ImageFetchException("A1111 did not return any images")
                        i = response["images"][0]
                        # A1111 returns a base64 encoded image, so we can just send that as a reply
                        message["reply-image"] = i.split(",", 1)[0]
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

    async def fetch_image(self, url: str) -> io.BytesIO:
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
                if thumbnail.mode != "RGB":
                    # Some images have weird colour modes, so convert them to RGB
                    thumbnail = thumbnail.convert("RGB")
                thumbnail.thumbnail((512, 512), Image.Resampling.LANCZOS)
                thumbnail.save(resp_image, "JPEG")
                resp_image.flush()
                resp_image.seek(0)

                return resp_image

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        """Get an argument parser for this worker.

        Returns:
            ErrorCatchingArgumentParser: An argument parser that can be used with parse_args().
        """
        parser = super().arg_parser()
        parser.add_argument("-i", "--imgurl", help="Start with an image from URL", default=None)
        parser.add_argument("-m", "--model", help="Model to use", default=self.options["a1111"]["default_model"])
        parser.add_argument("-l", "--list-models", help="List available models", action="store_true")
        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
