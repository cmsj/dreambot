#!/usr/bin/env python3
import asyncio
import json
import base64
import io
import logging
import os
import unicodedata
import string
import traceback
import discord
from typing import Any, Callable, Coroutine
from dreambot.shared.worker import DreambotWorkerBase


class FrontendDiscord(DreambotWorkerBase):
    valid_filename_chars = "_.() %s%s" % (string.ascii_letters, string.digits)

    def __init__(
        self,
        options: dict[str, Any],
        callback_send_message: Callable[[str, bytes], Coroutine[Any, Any, None]],
    ):
        self.logger = logging.getLogger("dreambot.frontend.discord")
        self.token = options["discord"]["token"]
        self.options = options
        self.callback_send_message = callback_send_message
        self.f_namemax = os.statvfs(self.options["output_dir"]).f_namemax - 4

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

                # self.discord.event(lambda: self.on_ready())
                # self.discord.event(lambda message: self.on_message(message))

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
        reply_message = ""
        file_bytes: io.BytesIO | None = None
        filename: str = "prompt.png"

        try:
            resp = json.loads(message.decode())
        except Exception as e:
            self.logger.error("Failed to parse response: {}".format(e))
            traceback.print_exc()
            return True

        channel = self.discord.get_channel(int(resp["channel"]))
        # user = self.discord.get_user(int(resp["user"]))
        origin_message = await channel.fetch_message(int(resp["origin_message"]))

        if origin_message is None:
            self.logger.error("Failed to find origin message {}".format(resp["origin_message"]))
            return True

        if "reply-image" in resp:
            reply_message = "I have an image to send, but I don't know how to do that yet."
            image_bytes = base64.standard_b64decode(resp["reply-image"])
            file_bytes = io.BytesIO(image_bytes)
            filename_base = self.clean_filename(resp["prompt"], char_limit=self.f_namemax)
            filename = "{}.png".format(filename_base[: self.f_namemax])

            reply_message = "I dreamed this:"
        elif "reply-text" in resp:
            reply_message = resp["reply-text"]
            self.logger.info("OUTPUT: {} <{}> {}".format(resp["channel"], resp["user"], resp["reply-text"]))
        elif "reply-none" in resp:
            self.logger.info(
                "SILENCE FOR {}:{} <{}> {}".format(resp["server"], resp["channel"], resp["user"], resp["reply-none"])
            )
            return True
        elif "error" in resp:
            reply_message = "Dream sequence collapsed: {}".format(resp["error"])
            self.logger.error("OUTPUT: {}: ".format(resp["channel"], reply_message))
        elif "usage" in resp:
            reply_message = "{}".format(resp["usage"])
            self.logger.info("OUTPUT: {} <{}> {}".format(resp["channel"], resp["user"], resp["usage"]))
        else:
            reply_message = "Dream sequence collapsed, unknown reason."

        if file_bytes is not None:
            await origin_message.reply(content=reply_message, file=discord.File(file_bytes, filename=filename))
        else:
            await origin_message.reply(content=reply_message)
        return True

    # @self.discord.event
    async def on_ready(self):
        self.logger.info("Discord connection established")

    # @self.discord.event
    async def on_message(self, message: discord.Message):
        if message.author == self.discord.user:
            return
        self.logger.debug("Received message: {}".format(message.content))
        text = message.content

        for trigger in self.options["triggers"]:
            if text.startswith(trigger):
                self.logger.info("INPUT: <{}> {}".format(message.author.name, text))
                prompt = text[len(trigger) :]
                packet = json.dumps(
                    {
                        "reply-to": self.queue_name(),
                        "frontend": "discord",
                        "channel": message.channel.id,
                        "user": message.author.id,
                        "origin_message": message.id,
                        "trigger": trigger,
                        "prompt": prompt,
                        "server": message.guild.id,
                    }
                )

                # Publish the trigger
                try:
                    await self.callback_send_message(trigger, packet.encode())
                    await message.add_reaction("👍")
                except Exception:
                    traceback.print_exc()
                    await message.add_reaction("👎")

    def clean_filename(self, filename: str, replace: str = " ", char_limit: int = 255):
        whitelist = self.valid_filename_chars
        # replace undesired characters
        for r in replace:
            filename = filename.replace(r, "_")

        # keep only valid ascii chars
        cleaned_filename = unicodedata.normalize("NFKD", filename).encode("ASCII", "ignore").decode()

        # keep only whitelisted chars
        cleaned_filename = "".join(c for c in cleaned_filename if c in whitelist).replace("__", "")
        return cleaned_filename[:char_limit]
