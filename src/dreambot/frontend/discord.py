"""Discord frontend for Dreambot."""
import asyncio
import json
import base64
import io
import logging
import traceback

from typing import Any, Callable, Coroutine

import discord

from dreambot.shared.worker import DreambotWorkerBase


class FrontendDiscord(DreambotWorkerBase):
    """Class implementing a Discord client worker."""

    def __init__(
        self,
        options: dict[str, Any],
        callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]],
    ):
        """Initialise the class."""
        self.logger = logging.getLogger("dreambot.frontend.discord")
        self.token = options["discord"]["token"]
        self.options = options
        self.callback_send_workload = callback_send_workload

        self.should_reconnect = True
        self.discord: discord.Client

    async def boot(self, reconnect: bool = True):
        """Boot this instance.

        Args:
            reconnect (bool, optional): Should we try and reconnect to Discord after errors/failures. Defaults to True.
        """
        while self.should_reconnect:
            self.should_reconnect = reconnect
            self.logger.info("Booting Discord connection... (reconnect: %s)", self.should_reconnect)
            try:
                intents = discord.Intents.default()
                intents.message_content = True
                # intents.members = True # FIXME: We should have this so we can switch fetch_user() to get_user() below
                self.discord = discord.Client(intents=intents)

                @self.discord.event
                async def on_ready():  # type: ignore
                    await self.on_ready()

                @self.discord.event
                async def on_message(message: discord.Message):  # type: ignore
                    await self.on_message(message)

                await self.discord.start(self.token, reconnect=False)
            except Exception as exc:
                self.logger.error("Discord connection error: %s", exc)
            finally:
                self.logger.debug("Discord connection closed")
                if self.should_reconnect:
                    self.logger.info("Sleeping before reconnecting...")
                    await asyncio.sleep(5)

    async def shutdown(self):
        """Shutdown this instance."""
        self.should_reconnect = False
        await self.discord.close()

    def queue_name(self):
        """Return the name of the NATS queue this worker should subscribe to."""
        return "discord"

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        """Message is received on the NATS queue."""
        reply_args: dict[str, str | discord.File] = {}
        self.logger.info("Received message for queue %s", queue_name)
        if not self.discord.is_ready():
            self.logger.error("Discord not ready, cannot send message")
            return False

        try:
            resp = json.loads(message.decode())
        except Exception as exc:
            self.logger.error("Failed to parse response: %s", exc)
            return True

        channel = None
        if "channel_name" in resp and resp["channel_name"] == "DM":
            # This came from a DM
            user = await self.discord.fetch_user(int(resp["user"]))
            if user:
                channel = user.dm_channel or await user.create_dm()
        else:
            # This came from a real channel
            channel = self.discord.get_channel(int(resp["channel"]))

        if not channel:
            self.logger.error("Failed to find channel %s (%s)", resp["channel_name"], resp["channel"])
            return True

        try:
            origin_message = await channel.fetch_message(int(resp["origin_message"]))  # type: ignore
        except Exception as exc:
            self.logger.error("Failed to fetch message %s: %s", resp["origin_message"], exc)
            return True
        if origin_message is None:
            self.logger.error("Failed to find origin message %s", resp["origin_message"])
            return True

        if "reply-image" in resp:
            image_bytes = base64.standard_b64decode(resp["reply-image"])
            file_bytes = io.BytesIO(image_bytes)
            filename = self.clean_filename(resp["prompt"], suffix=".png", output_dir=self.options["output_dir"])
            reply_args["file"] = discord.File(file_bytes, filename=filename)
            reply_args["content"] = "I dreamed this:"
        elif "reply-text" in resp:
            reply_args["content"] = resp["reply-text"]
            self.logger.info("OUTPUT: %s %s", self.log_slug(resp), resp["reply-text"])
        elif "reply-none" in resp:
            self.logger.info("SILENCE FOR %s %s", self.log_slug(resp), resp["reply-none"])
            return True
        elif "error" in resp:
            reply_args["content"] = f"Dream sequence collapsed: {resp['error']}"
            self.logger.error("OUTPUT: %s %s: ", self.log_slug(resp), reply_args["content"])
        elif "usage" in resp:
            reply_args["content"] = f"{resp['usage']}"
            self.logger.info("OUTPUT: %s %s ", self.log_slug(resp), resp["usage"])
        else:
            reply_args["content"] = "Dream sequence collapsed, unknown reason."

        try:
            self.logger.info("Sending reply to %s", self.log_slug(resp))
            await origin_message.reply(**reply_args)  # type: ignore
        except Exception as exc:
            self.logger.error("Failed to send reply: %s", exc)
            traceback.print_exc()
        return True

    async def on_ready(self):
        """Discord connection is ready."""
        self.logger.info("Discord connection established")

    async def on_message(self, message: discord.Message):
        """Message received on Discord."""
        if message.author == self.discord.user:
            # Discard messages from self
            return
        self.logger.debug("Received message: %s", message.content)
        text = message.content

        for trigger in self.options["triggers"]:
            if text.startswith(trigger + " "):
                prompt = text[len(trigger) + 1 :]

                packet_dict = {
                    "reply-to": self.queue_name(),
                    "frontend": "discord",
                    "channel": message.channel.id,
                    "user": message.author.id,
                    "user_name": message.author.name,
                    "origin_message": message.id,
                    "trigger": trigger,
                    "prompt": prompt,
                }

                if hasattr(message.channel, "name"):
                    packet_dict["channel_name"] = str(message.channel.name) if message.channel.name else "DM"  # type: ignore
                else:
                    packet_dict["channel_name"] = "DM"

                if message.guild:
                    packet_dict["server_name"] = message.guild.name
                    packet_dict["server_id"] = message.guild.id
                else:
                    packet_dict["server_name"] = "DM"

                # If the message has an image, attach it to the packet
                if len(message.embeds) > 0 and message.embeds[0].image:
                    image = message.embeds[0].image
                    if image:
                        packet_dict["image_url"] = image.url

                packet = json.dumps(packet_dict)

                self.logger.info("INPUT: %s %s", self.log_slug(packet_dict), text)  # type: ignore

                # Publish the trigger
                try:
                    await self.callback_send_workload(trigger, packet.encode())
                    await message.add_reaction("👍")
                except Exception:
                    traceback.print_exc()
                    await message.add_reaction("👎")

    def log_slug(self, resp: dict[str, str]) -> str:
        """Return a string to identify a message in logs."""
        return f"{resp['server_name']}:#{resp['channel_name']} <{resp['user_name']}>"
