"""Discord frontend for Dreambot."""
import asyncio
import base64
import io
import traceback

from typing import Any, Callable, Coroutine

import discord

from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType


class FrontendDiscord(DreambotWorkerBase):
    """Class implementing a Discord client worker."""

    def __init__(
        self,
        options: dict[str, Any],
        callback_send_workload: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ):
        """Initialise the class."""
        super().__init__(
            name="Discord",
            end=DreambotWorkerEndType.FRONTEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.token = options["discord"]["token"]
        self.discord: discord.Client
        self.should_reconnect = True

    async def boot(self):
        """Boot this instance.

        Args:
            reconnect (bool, optional): Should we try and reconnect to Discord after errors/failures. Defaults to True.
        """
        while self.should_reconnect:
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

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Message is received on the NATS queue."""
        reply_args: dict[str, str | discord.File] = {}
        self.logger.info("Received message for queue %s", queue_name)
        if not self.discord.is_ready():
            self.logger.error("Discord not ready, cannot send message")
            return False

        channel = None
        if "channel_name" in message and message["channel_name"] == "DM":
            # This came from a DM
            user = await self.discord.fetch_user(int(message["user"]))
            if user:
                channel = user.dm_channel or await user.create_dm()
        else:
            # This came from a real channel
            channel = self.discord.get_channel(int(message["channel"]))

        if not channel:
            self.logger.error("Failed to find channel %s (%s)", message["channel_name"], message["channel"])
            return True

        try:
            origin_message = await channel.fetch_message(int(message["origin_message"]))  # type: ignore
        except Exception as exc:
            self.logger.error("Failed to fetch message %s: %s", message["origin_message"], exc)
            return True
        if origin_message is None:
            self.logger.error("Failed to find origin message %s", message["origin_message"])
            return True

        if "reply-image" in message:
            image_bytes = base64.standard_b64decode(message["reply-image"])
            file_bytes = io.BytesIO(image_bytes)
            filename = self.clean_filename(message["prompt"], suffix=".png", output_dir=self.options["output_dir"])
            reply_args["file"] = discord.File(file_bytes, filename=filename)
            reply_args["content"] = "I dreamed this:"
        elif "reply-text" in message:
            reply_args["content"] = message["reply-text"]
            self.logger.info("OUTPUT: %s %s", self.log_slug(message), message["reply-text"])
        elif "reply-none" in message:
            self.logger.info("SILENCE FOR %s %s", self.log_slug(message), message["reply-none"])
            return True
        elif "error" in message:
            reply_args["content"] = f"Dream sequence collapsed: {message['error']}"
            self.logger.error("OUTPUT: %s %s: ", self.log_slug(message), reply_args["content"])
        elif "usage" in message:
            reply_args["content"] = f"{message['usage']}"
            self.logger.info("OUTPUT: %s %s ", self.log_slug(message), message["usage"])
        else:
            reply_args["content"] = "Dream sequence collapsed, unknown reason."
            self.logger.error("Unknown workload message: %s", message)

        try:
            self.logger.info("Sending reply to %s", self.log_slug(message))
            await origin_message.reply(**reply_args)  # type: ignore
        except Exception as exc:
            self.logger.error("Failed to send reply: %s", exc)
            traceback.print_exc()
        return True

    async def on_ready(self):
        """Discord connection is ready."""
        self.logger.info("Discord connection established")
        self.is_booted = True

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

                reply = {
                    "to": self.options["triggers"][trigger],
                    "reply-to": self.address,
                    "frontend": "discord",
                    "channel": message.channel.id,
                    "user": message.author.id,
                    "user_name": message.author.name,
                    "origin_message": message.id,
                    "trigger": trigger,
                    "prompt": prompt,
                }

                if hasattr(message.channel, "name"):
                    reply["channel_name"] = str(message.channel.name) if message.channel.name else "DM"  # type: ignore
                else:
                    reply["channel_name"] = "DM"

                if message.guild:
                    reply["server_name"] = message.guild.name
                    reply["server_id"] = message.guild.id
                else:
                    reply["server_name"] = "DM"

                # If the message has an image, attach it to the packet
                if len(message.embeds) > 0 and message.embeds[0].image:
                    image = message.embeds[0].image
                    if image:
                        reply["image_url"] = image.url

                self.logger.info("INPUT: %s %s", self.log_slug(reply), text)  # type: ignore

                # Publish the trigger
                try:
                    await self.callback_send_workload(reply)
                    await message.add_reaction("👍")
                except Exception:
                    traceback.print_exc()
                    await message.add_reaction("👎")

    def log_slug(self, resp: dict[str, str]) -> str:
        """Return a string to identify a message in logs."""
        return f"{resp['server_name']}:#{resp['channel_name']} <{resp['user_name']}>"
