"""IRC frontend for Dreambot."""
import asyncio
import base64
import logging
import os
import traceback
from typing import NamedTuple, Any, Callable, Coroutine, Self
from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType


class Prefix(NamedTuple):
    """Object that represents an IRC prefix."""

    nick: str
    ident: str
    host: str


class Message(NamedTuple):
    """Object that represents an IRC message."""

    prefix: Prefix | None
    command: str
    params: list[str]

    @classmethod
    def parse_line(cls, line: str) -> Self:
        """Parse an IRC line.

        Args:
            line (str): A raw IRC line.

        Returns:
            Message: The parsed line.
        """
        # parses an irc line based on RFC:
        # https://tools.ietf.org/html/rfc2812#section-2.3.1
        prefix: Prefix | None = None

        if line.startswith(":"):
            # prefix
            prefix_str, line = line.split(None, 1)
            name = prefix_str[1:]
            ident = ""
            host = ""
            if "!" in name:
                name, ident = name.split("!", 1)
                if "@" in ident:
                    ident, host = ident.split("@", 1)
            elif "@" in name:
                name, host = name.split("@", 1)
            prefix = Prefix(name, ident, host)

        command, *line = line.split(None, 1)  # type: ignore
        command = command.upper()

        params: list[str] = []
        if line:
            line = line[0]
            while line:
                if line.startswith(":"):
                    params.append(line[1:])
                    line = ""
                else:
                    param, *line = line.split(None, 1)  # type: ignore
                    params.append(param)
                    if line:
                        line = line[0]

        return Message(prefix, command, params)

    def full_ident(self) -> str:
        """Return the full ident of the message."""
        if self.prefix:
            return f"{self.prefix.nick}!{self.prefix.ident}@{self.prefix.host}"
        return "???!???@???"

    def source(self) -> str:
        """Return the source of the message."""
        if self.prefix:
            return self.prefix.nick
        return "???"

    def target(self) -> str:
        """Return the target of the message."""
        target = self.params[0]
        if not target.startswith("#"):
            # This is a private message, so the target is the source
            return self.source()
        return target


class FrontendIRC(DreambotWorkerBase):
    """IRC frontend for Dreambot."""

    def __init__(
        self,
        irc_server: dict[str, Any],
        options: dict[str, Any],
        callback_send_workload: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ):
        """Initialise the class."""
        super().__init__(
            name="IRC",
            queue_name=f"irc.{irc_server['host']}",
            end=DreambotWorkerEndType.FRONTEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.should_reconnect = True
        self.server = irc_server
        self.full_ident = ""
        self.irc_timeout = 300
        self.writer: asyncio.StreamWriter | None = None
        self.reader: asyncio.StreamReader | None = None

    async def boot(self):
        """Boot the instance.

        Args:
            reconnect (bool, optional): Whether or not we should attempt to reconnect to the IRC server. Defaults to True.
        """
        while True:
            self.logger.info("Booting IRC connection... (reconnect: %s)", self.should_reconnect)
            try:
                self.reader, self.writer = await asyncio.open_connection(
                    self.server["host"], self.server["port"], ssl=self.server["ssl"]
                )
                await self.send_line(f"NICK {self.server['nickname']}")
                await self.send_line(f"USER {self.server['ident']} * * :{self.server['realname']}")
                self.logger.info("IRC connection booted.")
                self.is_booted = True

                # Loop until the connection is closed
                while True:
                    self.logger.debug("Waiting for IRC data...")
                    if self.reader.at_eof():  # There's nothing more waiting for us
                        break
                    data = await asyncio.wait_for(self.reader.readline(), timeout=self.irc_timeout)
                    await self.handle_line(data)
            except ConnectionRefusedError:
                self.logger.error("IRC connection refused")
            except (asyncio.TimeoutError, ConnectionResetError) as exc:
                self.logger.error("IRC connection timeout: %s", exc)
            except Exception as exc:
                self.logger.error("IRC connection error: %s", exc)
            finally:
                self.logger.debug("IRC connection closed")
                if self.writer:
                    self.writer.close()
                    await self.writer.wait_closed()
                if self.reader:
                    self.reader.feed_eof()

            if not self.should_reconnect:
                # We don't want to reconnect, so break out of our while True loop
                break

            self.logger.info("Sleeping before reconnecting...")
            await asyncio.sleep(5)

    async def shutdown(self):
        """Shutdown the instance."""
        self.should_reconnect = False
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        if self.reader:
            self.reader.feed_eof()

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Process an incoming workload message.

        Args:
            queue_name (str): The queue name the message was received on.
            message (bytes): The workload message, a JSON string encoded as bytes.

        Returns:
            bool: True if the message should be ack'd in NATS, False otherwise.
        """
        reply_message = ""
        reply_log_level = logging.INFO
        reply_kind = "OUTPUT"

        if "reply-image" in message:
            image_bytes = base64.standard_b64decode(message["reply-image"])
            filename = self.clean_filename(message["prompt"], suffix=".png", output_dir=self.options["output_dir"])
            url = f"{self.options['uri_base']}/{filename}"

            with open(os.path.join(self.options["output_dir"], filename), "wb") as image_file:
                image_file.write(image_bytes)
            reply_message = f"{message['user']}: I dreamed this: {url}"
            self.log_reply(message, reply_message)
        elif "reply-text" in message:
            reply_message = f"{message['user']}: {message['reply-text']}"
        elif "reply-none" in message:
            reply_message = f"{message['user']} {message['reply-none']}"
            reply_kind = "SILENCE"
        elif "error" in message:
            reply_message = f"{message['user']}: Dream sequence collapsed: {message['error']}"
            reply_log_level = logging.ERROR
        elif "usage" in message:
            reply_message = f"{message['user']}: {message['usage']}"
        else:
            reply_message = f"{message['user']}: Dream sequence collapsed, unknown reason."
            reply_log_level = logging.ERROR
            self.logger.error("Unknown workload message: %s", message)

        # Log the reply
        self.log_reply(message, reply_message, level=reply_log_level, kind=reply_kind)
        if reply_kind == "SILENCE":
            # We don't actually send anything back to the user for this kind of reply.
            # Typically this is because some long-running process is now active and we will receive
            # a message on the queue when it's done.
            return True

        for chunk in self.split_lines(message, reply_message):
            await self.send_cmd("PRIVMSG", *[message["channel"], chunk])
        return True

    async def send_line(self, line: str):
        """Send a line of text to the IRC server.

        No modifications will be made to the line other than encoding it to UTF-8 and appending a CRLF.
        This means that you should not include a CRLF in the line you send, and you should have taken care to not exceed the IRC RFC's line length limit of 512 characters.

        Args:
            line (str): The line to send.

        Raises:
            ValueError: No writer is available, we are likely offline.
        """
        if not self.writer:
            raise ValueError("No writer available")

        if len(line) > 510:
            self.logger.warning("Line length exceeds RFC limit of 512 characters: %s", len(line))
        self.logger.debug("-> %s", line)
        self.writer.write(line.encode("utf-8") + b"\r\n")
        await self.writer.drain()

    async def send_cmd(self, cmd: str, *parts: str):
        """Send a command to the IRC server.

        To send a regular message to a channel or private message, cmd should be "PRIVMSG".

        Args:
            cmd (str): The IRC command to send. These commands are documented in the IRC RFC.
            *parts (str): The parameters to send with the command.
        """
        params = list(parts)  # copy
        if params:
            if " " in params[-1]:
                params[-1] = ":" + params[-1]
        params = [cmd] + params
        await self.send_line(" ".join(params))

    async def handle_line(self, data: bytes):
        """Handle an incoming line of text from the IRC server.

        Args:
            data (bytes): The raw line of text from the IRC server.
        """
        try:
            line = data.decode("utf-8")
        except UnicodeDecodeError:
            line = data.decode("latin1")

        line = line.strip()
        if line:
            message = Message.parse_line(line)
            self.logger.debug("%s <- %s", self.server["host"], message)

            if message.command == "PING":
                await self.send_cmd("PONG", *message.params)
            elif message.command == "001":
                await self.irc_join(self.server["channels"])
            elif message.command == "443":
                await self.irc_renick()
            elif message.command == "PRIVMSG":
                await self.irc_received_privmsg(message)
            elif message.command == "JOIN":
                self.irc_received_join(message)
            elif message.command.isdigit() and int(message.command) >= 400:
                # might be an error
                self.logger.error("Possible server error: %s", str(message))

    async def irc_join(self, channels: list[str]):
        """Join an IRC channel.

        Args:
            channels (list[str]): A list of channels to join.
        """
        for channel in channels:
            await self.send_cmd("JOIN", channel)

    async def irc_renick(self):
        """Change the bot's nickname because our desired nickname is already in use."""
        self.server["nickname"] = self.server["nickname"] + "_"
        await self.send_line(f"NICK {self.server['nickname']}")

    def irc_received_join(self, message: Message):
        """Handle a JOIN message from the IRC server. Used to build our full ident string."""
        self.full_ident = f":{message.full_ident()} "

    async def irc_received_privmsg(self, message: Message):
        """Handle a PRIVMSG message from the IRC server.

        If the message starts with one of our trigger words, we'll dispatch it to NATS.

        Args:
            message (Message): A Message object containing the message
        """
        source = message.source()
        target = message.target()
        text = message.params[1].lstrip()

        for trigger in self.options["triggers"]:
            if text.startswith(f"{trigger} "):
                self.logger.info("INPUT: %s:%s <%s> %s", self.server["host"], target, source, text)
                prompt = text[len(trigger) + 1 :]
                reply = {
                    "to": trigger,
                    "reply-to": self.queue_name,
                    "frontend": "irc",
                    "server": self.server["host"],
                    "channel": target,
                    "user": source,
                    "trigger": trigger,
                    "prompt": prompt,
                }

                # Publish the trigger
                try:
                    await self.callback_send_workload(reply)
                except Exception:
                    traceback.print_exc()
                    await self.send_cmd(
                        "PRIVMSG",
                        *[target, f"{source}: Dream sequence failed."],
                    )

    def split_lines(self, message: dict[str, Any], reply_message: str) -> list[str]:
        """Split lines to safe IRC lengths.

        Args:
            message (dict[str, Any]): Original message object we received.
            reply_message (str): Our reply back to IRC.

        Returns:
            list[str]: A list of strings that are IRC RFC compliant lengths (ie <510 characters)
        """
        chunks: list[str] = []
        # We have to send multiline responses separately, so let's split the message into lines
        for line in reply_message.splitlines():
            # IRC has a max line length of 512 bytes, so we need to split the line into chunks
            max_chunk_size = 510  # Start with 510 because send_cmd() adds 2 bytes for the CRLF
            max_chunk_size -= len(f"{self.full_ident} PRIVMSG {message['channel']} :")
            chunks += [line[i : i + max_chunk_size] for i in range(0, len(line), max_chunk_size)]
        return chunks

    def log_reply(self, message: dict[str, Any], reply: str, kind: str = "OUTPUT", level: int = logging.INFO):
        """Log reply messages with a consistent format."""
        self.logger.log(level, "%s: %s:%s %s", kind, message["server"], message["channel"], reply)
