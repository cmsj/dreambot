"""ComfyUI backend for Dreambot."""

import asyncio
import base64
import io
import uuid

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


class DreambotBackendComfyUI(DreambotWorkerBase):
    """ComfyUI backend for Dreambot."""

    def __init__(self, options: dict[str, Any], callback_send_workload: CallbackSendWorkload):
        """Initialise the class."""
        super().__init__(
            name="comfyui",
            end=DreambotWorkerEndType.BACKEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.comfyui_host = options["comfyui"]["host"]
        self.comfyui_port = options["comfyui"]["port"]
        self.api_uri = f"http://{self.comfyui_host}:{self.comfyui_port}"

    async def boot(self):
        """Boot the backend."""
        self.logger.info("ComfyUI API URI: %s", self.api_uri)
        self.is_booted = True

    async def shutdown(self):
        """Shutdown the backend."""

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Process in incoming workload message.

        Args:
            queue_name (str): The name of the queue we received the message from.
            message (dict[str, Any]): The message we received, as a dictionary.

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

            if args.list_workflows:
                message["reply-text"] = f"Available workflows: {', '.join(self.options['comfyui']['workflows'].keys())}"
            else:
                # Figure out which workflow we should use
                if args.workflow is not None:
                    # User specified a workflow, go with that
                    workflow_name = args.workflow
                elif message["trigger"][1:] in self.options["comfyui"]["workflows"]:
                    # Trigger word matches the name of a workflow, go with that
                    workflow_name = message["trigger"][1:]
                else:
                    # Go with our default
                    workflow_name = self.options["comfyui"]["default_workflow"]

                # Validate that the workflow exists
                if workflow_name not in self.options["comfyui"]["workflows"]:
                    message["error"] = f"Unknown workflow: {workflow_name}. Available workflows: {', '.join(self.options['comfyui']['workflows'].keys())}"
                    await self.send_message(message)
                    return True

                workflow = self.options["comfyui"]["workflows"][workflow_name]["workflow"].copy()

                # Update the prompt in the workflow
                # ComfyUI workflows typically have a CLIPTextEncode node for positive prompt
                # We need to find it and update it
                prompt_updated = False
                for node_data in workflow.values():
                    if node_data.get("class_type") == "CLIPTextEncode" and "inputs" in node_data:
                        if not prompt_updated:
                            # Update the first CLIPTextEncode (positive prompt)
                            node_data["inputs"]["text"] = args.prompt
                            prompt_updated = True
                            break

                # Handle img2img if image URL is provided
                if args.imgurl:
                    image = await self.fetch_image(args.imgurl)
                    # Upload the image to ComfyUI
                    image_name = await self.upload_image(image)
                    # Update workflow to use the uploaded image
                    for node_data in workflow.values():
                        if node_data.get("class_type") == "LoadImage":
                            node_data["inputs"]["image"] = image_name
                            break

                # Generate a unique client_id for this request
                client_id = str(uuid.uuid4())

                # Queue the prompt
                prompt_request = {
                    "prompt": workflow,
                    "client_id": client_id
                }

                self.logger.info("POSTing workflow to ComfyUI: %s/prompt", self.api_uri)

                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{self.api_uri}/prompt", json=prompt_request) as req:
                        if not req.ok:
                            message["error"] = f"Error from ComfyUI: {req.reason}"
                            await self.send_message(message)
                            return True
                        response = await req.json()
                        prompt_id = response.get("prompt_id")

                        if not prompt_id:
                            message["error"] = "ComfyUI did not return a prompt_id"
                            await self.send_message(message)
                            return True

                # Wait for the generation to complete and get the result
                # ComfyUI uses websockets for real-time updates, but for simplicity,
                # we'll poll the history endpoint
                result_image = await self.wait_for_completion(prompt_id)

                if result_image:
                    message["reply-image"] = result_image
                else:
                    message["error"] = "Failed to retrieve generated image from ComfyUI"

        except UsageException as exc:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text
            message["reply-text"] = str(exc)
        except (ValueError, ArgumentError) as exc:
            message["error"] = f"Something is wrong with your arguments, try {message['trigger']} --help ({exc})"
        except ImageFetchException as exc:
            message["error"] = str(exc)
        except Exception as exc:
            message["error"] = f"Unknown error: {exc}"

        await self.send_message(message)
        return True

    async def wait_for_completion(self, prompt_id: str, max_attempts: int = 60) -> str | None:
        """Wait for a ComfyUI workflow to complete and retrieve the result.

        Args:
            prompt_id (str): The prompt ID returned by ComfyUI.
            max_attempts (int): Maximum number of polling attempts.

        Returns:
            str | None: Base64-encoded image if successful, None otherwise.
        """
        async with aiohttp.ClientSession() as session:
            for _ in range(max_attempts):
                await asyncio.sleep(1)  # Wait 1 second between polls

                async with session.get(f"{self.api_uri}/history/{prompt_id}") as req:
                    if not req.ok:
                        continue

                    history = await req.json()

                    if prompt_id not in history:
                        continue

                    prompt_history = history[prompt_id]

                    # Check if the prompt has completed
                    if "outputs" in prompt_history:
                        # Find the SaveImage node output
                        for node_output in prompt_history["outputs"].values():
                            if "images" in node_output:
                                images = node_output["images"]
                                if images:
                                    # Get the first image
                                    image_info = images[0]
                                    filename = image_info["filename"]
                                    subfolder = image_info.get("subfolder", "")
                                    folder_type = image_info.get("type", "output")

                                    # Download the image
                                    params = {
                                        "filename": filename,
                                        "subfolder": subfolder,
                                        "type": folder_type
                                    }

                                    async with session.get(f"{self.api_uri}/view", params=params) as img_req:
                                        if img_req.ok:
                                            image_data = await img_req.read()
                                            return base64.b64encode(image_data).decode("utf8")

        return None

    async def fetch_image(self, url: str) -> io.BytesIO:
        """Fetch an image from a URL.

        Args:
            url (str): The URL of an image to fetch.

        Raises:
            ImageFetchException: Either the image could not be fetched, or the URL returned a non-image.

        Returns:
            io.BytesIO: A file-like object containing the image data.
        """
        self.logger.info("Fetching image: %s", url)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise ImageFetchException(f"Unable to fetch: {resp.status}")
                if not resp.content_type.startswith("image/"):
                    raise ImageFetchException(f"URL was not an image: {resp.content_type}")

                image = await resp.read()
                self.logger.info("Fetched %s bytes of %s", len(image), resp.content_type)

                # Resize the image so it's not too big for our VRAM
                resp_image = io.BytesIO()
                thumbnail = Image.open(io.BytesIO(image))
                if thumbnail.mode != "RGB":
                    # Some images have weird colour modes, so convert them to RGB
                    thumbnail = thumbnail.convert("RGB")
                thumbnail.thumbnail((512, 512), Image.Resampling.LANCZOS)
                thumbnail.save(resp_image, "PNG")
                resp_image.flush()
                resp_image.seek(0)

                return resp_image

    async def upload_image(self, image_data: io.BytesIO) -> str:
        """Upload an image to ComfyUI.

        Args:
            image_data (io.BytesIO): The image data to upload.

        Raises:
            ImageFetchException: The image upload failed.

        Returns:
            str: The filename of the uploaded image.
        """
        async with aiohttp.ClientSession() as session:
            # ComfyUI expects multipart/form-data upload
            form = aiohttp.FormData()
            form.add_field("image", image_data, filename="upload.png", content_type="image/png")

            async with session.post(f"{self.api_uri}/upload/image", data=form) as resp:
                if not resp.ok:
                    raise ImageFetchException(f"Failed to upload image to ComfyUI: {resp.reason}")

                result = await resp.json()
                return result.get("name", "upload.png")

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        """Get an argument parser for this worker.

        Returns:
            ErrorCatchingArgumentParser: An argument parser that can be used with parse_args().
        """
        parser = super().arg_parser()
        parser.add_argument("-i", "--imgurl", help="Start with an image from URL", default=None)
        parser.add_argument("-w", "--workflow", help="Workflow to use", default=self.options["comfyui"]["default_workflow"])
        parser.add_argument("-l", "--list-workflows", help="List available workflows", action="store_true")
        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
