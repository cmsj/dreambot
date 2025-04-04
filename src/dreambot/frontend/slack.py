"""Slack frontend for Dreambot."""
import asyncio
import base64
import io
import os
import traceback

from typing import Any

from slack_bolt.app.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.errors import SlackApiError

from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType, CallbackSendWorkload


class FrontendSlack(DreambotWorkerBase):
    """Class implementing a Slack client worker."""

    def __init__(
        self,
        options: dict[str, Any],
        callback_send_workload: CallbackSendWorkload,
    ):
        """Initialise the class."""
        super().__init__(
            name="Slack",
            end=DreambotWorkerEndType.FRONTEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.token = options["slack"]["token"]
        self.socket_mode_token = options["slack"]["socketModeToken"]
        self.slack: AsyncApp
        self.handler: AsyncSocketModeHandler
        self.should_reconnect = True
        self.user_id: str = ""
        self.user_name_cache: dict[str, str] = {}
        self.channel_name_cache: dict[str, str] = {}

    async def boot(self):
        """Boot this instance."""
        while self.should_reconnect:
            self.logger.info("Booting Slack connection... (reconnect: %s)", self.should_reconnect)
            try:
                self.slack = AsyncApp(token=self.token)
                self.handler = AsyncSocketModeHandler(self.slack, self.socket_mode_token)

                @self.slack.event("message")  # type: ignore
                async def on_message(body: dict[str, Any]):  # type: ignore
                    try:
                        await self.on_message(body)
                    except Exception as exc:
                        self.logger.error("Error handling Slack message: %s", exc)
                
                # Add an event handler for when the app is ready
                @self.slack.event("app_mention")  # type: ignore
                async def on_app_mention(body: dict[str, Any]):  # type: ignore
                    # This is just a dummy handler to ensure the app is ready
                    pass
                
                # Start the handler
                await self.handler.start_async()
                
                # Verify the connection by making a test API call
                auth_test = await self.slack.client.auth_test()  # type: ignore
                self.logger.info("Connected to Slack workspace: %s", auth_test["team"])
                
                # Set the booted flag
                self.is_booted = True
                self.logger.info("Slack connection established")
            except SlackApiError as exc:
                self.logger.error("Slack API error: %s", exc)
            except Exception as exc:
                self.logger.error("Slack connection error: %s", exc)
            finally:
                self.logger.debug("Slack connection closed")

                if self.should_reconnect:
                    self.logger.info("Sleeping before reconnecting...")
                    await asyncio.sleep(5)

    async def shutdown(self):
        """Shutdown this instance."""
        self.should_reconnect = False
        await self.handler.close_async()

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Process an incoming workload message.

        Args:
            queue_name (str): The queue name the message was received on.
            message (bytes): The workload message, a JSON string encoded as bytes.

        Returns:
            bool: True if the message should be ack'd in NATS, False otherwise.
        """
        self.logger.info("Received message for queue %s", queue_name)
        
        # Get the channel to send the message to
        channel_id = message["channel"]
        
        # Prepare the message content
        reply_content = ""
        reply_blocks = []
        
        if "reply-image" in message:
            # For image replies, we need to upload the image to Slack
            image_bytes = base64.standard_b64decode(message["reply-image"])
            filename = self.clean_filename(message["prompt"], suffix=".png", output_dir=self.options["output_dir"])
            
            # Save the image to disk
            with open(os.path.join(self.options["output_dir"], filename), "wb") as image_file:
                image_file.write(image_bytes)
            
            # Upload the image to Slack
            try:
                with open(os.path.join(self.options["output_dir"], filename), "rb") as image_file:
                    upload_result = await self.slack.client.files_upload_v2(
                        file=image_file,
                        filename=filename,
                        title="Dream result",
                        initial_comment="I dreamed this:"
                    )
                
                # Get the file URL from the upload result
                file_url = upload_result["file"]["url_private"]
                
                # Create a message with the image
                reply_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "I dreamed this:"
                        }
                    },
                    {
                        "type": "image",
                        "image_url": file_url,
                        "alt_text": "Dream result"
                    }
                ]
                
                self.logger.info("OUTPUT: %s %s", self.log_slug(message), "Image uploaded")
            except Exception as exc:
                self.logger.error("Failed to upload image: %s", exc)
                reply_content = "Failed to upload dream image."
                self.logger.error("OUTPUT: %s %s", self.log_slug(message), reply_content)
        elif "reply-text" in message:
            reply_content = message["reply-text"]
            self.logger.info("OUTPUT: %s %s", self.log_slug(message), message["reply-text"])
        elif "reply-none" in message:
            self.logger.info("SILENCE FOR %s %s", self.log_slug(message), message["reply-none"])
            return True
        elif "error" in message:
            reply_content = f"Dream sequence collapsed: {message['error']}"
            self.logger.error("OUTPUT: %s %s", self.log_slug(message), reply_content)
        elif "usage" in message:
            reply_content = f"{message['usage']}"
            self.logger.info("OUTPUT: %s %s", self.log_slug(message), message["usage"])
        else:
            reply_content = "Dream sequence collapsed, unknown reason."
            self.logger.error("Unknown workload message: %s", message)
        
        # Send the message to the channel
        try:
            if reply_blocks:
                await self.slack.client.chat_postMessage(
                    channel=channel_id,
                    blocks=reply_blocks
                )
            else:
                await self.slack.client.chat_postMessage(
                    channel=channel_id,
                    text=reply_content
                )
            
            # Add a reaction to the original message if we have the message ID
            if "origin_message" in message:
                try:
                    await self.slack.client.reactions_add(
                        channel=channel_id,
                        timestamp=message["origin_message"],
                        name="thumbsup"
                    )
                except Exception as exc:
                    self.logger.error("Failed to add reaction: %s", exc)
        except Exception as exc:
            self.logger.error("Failed to send reply: %s", exc)
            traceback.print_exc()
            
            # Try to add a thumbs down reaction to the original message
            if "origin_message" in message:
                try:
                    await self.slack.client.reactions_add(
                        channel=channel_id,
                        timestamp=message["origin_message"],
                        name="thumbsdown"
                    )
                except Exception as exc:
                    self.logger.error("Failed to add reaction: %s", exc)
        
        return True

    async def on_message(self, msg: dict[str, Any]):
        """Handle an incoming Slack websocket message.

        Args:
            msg (aiohttp.WSMessage): The message.
        """
        self.logger.info("Received Slack message: %s", msg)
        if self.user_id == "":
            self.user_id = (await self.slack.client.auth_test())["user_id"]  # type: ignore
            self.logger.debug("Discovered our user_id: %s", self.user_id)

        if msg["event"]["user"] == self.user_id:
            # Discard messages from self
            self.logger.debug("Ignoring message from self")
            return

        text = msg["event"]["text"]
        for trigger in self.options["triggers"]:
            self.logger.debug("Checking trigger: '%s'", trigger + " ")
            if text.startswith(trigger + " "):
                prompt = text[len(trigger) + 1 :]

                reply = {
                    "to": self.options["triggers"][trigger],
                    "reply-to": self.address,
                    "frontend": "slack",
                    "channel": msg["event"]["channel"],
                    "user": msg["event"]["user"],
                    "origin_message": msg["event"]["ts"],  # Slack uses timestamps as message IDs
                    "trigger": trigger,
                    "prompt": prompt,
                }

                # Get user information
                if reply["user"] not in self.user_name_cache:
                    user_info = await self.slack.client.users_info(user=reply["user"])  # type: ignore
                    self.user_name_cache[reply["user"]] = user_info["user"]["real_name"]
                reply["user_name"] = self.user_name_cache[reply["user"]]

                # Get channel information
                channel_id = reply["channel"]
                if channel_id not in self.channel_name_cache:
                    try:
                        channel_info = await self.slack.client.conversations_info(channel=channel_id)  # type: ignore
                        self.channel_name_cache[channel_id] = channel_info["channel"]["name"]
                    except Exception as exc:
                        self.logger.error("Failed to get channel info: %s", exc)
                        self.channel_name_cache[channel_id] = "DM"
                
                reply["channel_name"] = self.channel_name_cache[channel_id]
                
                # Get workspace (server) information
                try:
                    workspace_info = await self.slack.client.team_info()  # type: ignore
                    reply["server_name"] = workspace_info["team"]["name"]
                    reply["server_id"] = workspace_info["team"]["id"]
                except Exception as exc:
                    self.logger.error("Failed to get workspace info: %s", exc)
                    reply["server_name"] = "Slack"
                    reply["server_id"] = "unknown"

                # Check if the message has an image
                if "files" in msg["event"] and msg["event"]["files"]:
                    for file in msg["event"]["files"]:
                        if file["filetype"] in ["png", "jpg", "jpeg", "gif"]:
                            reply["image_url"] = file["url_private"]
                            break

                self.logger.info("INPUT: %s %s", self.log_slug(reply), text)

                # Publish the trigger
                try:
                    await self.callback_send_workload(reply)
                    # Add a thumbs-up reaction to the message
                    await self.slack.client.reactions_add(
                        channel=channel_id,
                        timestamp=msg["event"]["ts"],
                        name="thumbsup"
                    )
                except Exception:
                    traceback.print_exc()
                    # Add a thumbs-down reaction to the message
                    await self.slack.client.reactions_add(
                        channel=channel_id,
                        timestamp=msg["event"]["ts"],
                        name="thumbsdown"
                    )

    def log_slug(self, resp: dict[str, str]) -> str:
        """Return a string to identify a message in logs."""
        return f"{resp['server_name']}:#{resp['channel_name']} <{resp['user_name']}>"
