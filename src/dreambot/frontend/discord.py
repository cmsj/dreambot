#!/usr/bin/env python3
import asyncio
import json
import base64
import io
import logging
import traceback
import discord
from typing import Any, Callable, Coroutine
from dreambot.shared.worker import DreambotWorkerBase


class FrontendDiscord(DreambotWorkerBase):
    def __init__(
        self,
        options: dict[str, Any],
        callback_send_message: Callable[[str, bytes], Coroutine[Any, Any, None]],
    ):
        self.logger = logging.getLogger("dreambot.frontend.discord")
        self.token = options["discord"]["token"]
        self.options = options
        self.callback_send_message = callback_send_message

        self.should_reconnect = True
        self.discord: discord.Client

    async def boot(self, reconnect: bool = True):
        while self.should_reconnect:
            self.should_reconnect = reconnect
            self.logger.info("Booting Discord connection... (reconnect: {})".format(self.should_reconnect))
            try:
                intents = discord.Intents.default()
                intents.message_content = True
                self.discord = discord.Client(intents=intents)

                @self.discord.event
                async def on_ready():  # type: ignore
                    await self.on_ready()

                @self.discord.event
                async def on_message(message: discord.Message):  # type: ignore
                    await self.on_message(message)

                await self.discord.start(self.token, reconnect=False)
            except Exception as e:
                self.logger.error("Discord connection error: {}".format(e))
            finally:
                self.logger.debug("Discord connection closed")
                if self.should_reconnect:
                    self.logger.info("Sleeping before reconnecting...")
                    await asyncio.sleep(5)

    async def shutdown(self):
        self.should_reconnect = False
        await self.discord.close()

    def queue_name(self):
        return "discord"

    async def callback_receive_message(self, queue_name: str, message: bytes) -> bool:
        reply_args: dict[str, str | io.BytesIO] = {}

        try:
            resp = json.loads(message.decode())
        except Exception as e:
            self.logger.error("Failed to parse response: {}".format(e))
            traceback.print_exc()
            return True

        channel = self.discord.get_channel(int(resp["channel"]))
        if not channel:
            self.logger.error("Failed to find channel {} ({})".format(resp["channel_name"], resp["channel"]))
            return True

        try:
            origin_message = await channel.fetch_message(int(resp["origin_message"]))  # type: ignore
        except Exception as e:
            self.logger.error("Failed to fetch message {}: {}".format(resp["origin_message"], e))
            return True
        if origin_message is None:
            self.logger.error("Failed to find origin message {}".format(resp["origin_message"]))
            return True

        if "reply-image" in resp:
            image_bytes = base64.standard_b64decode(resp["reply-image"])
            reply_args["file"] = io.BytesIO(image_bytes)
            reply_args["filename"] = self.clean_filename(
                resp["prompt"], suffix=".png", output_dir=self.options["output_dir"]
            )
            reply_args["content"] = "I dreamed this:"
        elif "reply-text" in resp:
            reply_args["content"] = resp["reply-text"]
            self.logger.info("OUTPUT: {} <{}> {}".format(resp["channel"], resp["user"], resp["reply-text"]))
        elif "reply-none" in resp:
            self.logger.info(
                "SILENCE FOR {}:{} <{}> {}".format(resp["server"], resp["channel"], resp["user"], resp["reply-none"])
            )
            return True
        elif "error" in resp:
            reply_args["content"] = "Dream sequence collapsed: {}".format(resp["error"])
            self.logger.error("OUTPUT: {}: ".format(resp["channel"], reply_message))
        elif "usage" in resp:
            reply_args["content"] = "{}".format(resp["usage"])
            self.logger.info("OUTPUT: {} <{}> {}".format(resp["channel"], resp["user"], resp["usage"]))
        else:
            reply_args["content"] = "Dream sequence collapsed, unknown reason."

        await origin_message.reply(**reply_args)  # type: ignore
        return True

    # @self.discord.event
    async def on_ready(self):
        self.logger.info("Discord connection established")

    # @self.discord.event
    async def on_message(self, message: discord.Message):
        if message.author == self.discord.user:
            # Discard messages from self
            return
        self.logger.debug("Received message: {}".format(message.content))
        text = message.content

        for trigger in self.options["triggers"]:
            if text.startswith(trigger):
                prompt = text[len(trigger) :]

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

                packet = json.dumps(packet_dict)

                self.logger.info(
                    "INPUT: {}:{} <{}> {}".format(
                        packet_dict["server_name"], packet_dict["channel_name"], packet_dict["user_name"], text
                    )
                )

                # Publish the trigger
                try:
                    await self.callback_send_message(trigger, packet.encode())
                    await message.add_reaction("👍")
                except Exception:
                    traceback.print_exc()
                    await message.add_reaction("👎")
