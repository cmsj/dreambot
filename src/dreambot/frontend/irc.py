#!/usr/bin/env python3
import asyncio
import json
import base64
import os
import logging
import unicodedata
import string
import traceback
from collections import namedtuple

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('dreambot_frontend_irc')
logger.setLevel(logging.DEBUG)

class FrontendIRC:
    # Various IRC support types/functions
    Message = namedtuple('Message', 'prefix command params')
    Prefix = namedtuple('Prefix', 'nick ident host')
    valid_filename_chars = "_.() %s%s" % (string.ascii_letters, string.digits)

    options = None
    server = None
    writer = None
    reader = None
    logger = None
    cb_publish = None
    f_namemax = None
    full_ident = ""
    should_reconnect = True
    irc_timeout = 300

    def __init__(self, server, options, cb_publish):
        self.logger = logging.getLogger('dreambot.irc.{}'.format(server["host"]))
        self.logger.setLevel(logging.DEBUG)
        self.server = server
        self.options = options
        self.cb_publish = cb_publish
        self.f_namemax = os.statvfs(self.options["output_dir"]).f_namemax - 4

    async def boot(self, reconnect=True):
        while self.should_reconnect:
            self.should_reconnect = reconnect
            self.logger.info("Booting IRC connection... (reconnect: {})".format(self.should_reconnect))
            try:
                self.reader, self.writer = await asyncio.open_connection(self.server["host"], self.server["port"], ssl=self.server["ssl"])
                try:
                    await self.send_line('NICK ' + self.server["nickname"])
                    await self.send_line('USER ' + self.server["ident"] + ' * * :' + self.server["realname"])
                    self.logger.info("IRC connection booted.")

                    # Loop until the connection is closed
                    while True:
                        self.logger.debug("Waiting for IRC data...")
                        try:
                            if self.reader.at_eof():
                                # There's nothing more waiting for us
                                break
                            data = await asyncio.wait_for(self.reader.readline(), timeout=self.irc_timeout)
                            await self.handle_line(data)
                        except (asyncio.TimeoutError, ConnectionResetError) as e:
                            self.logger.error("IRC connection timeout: {}".format(e))
                            break

                finally:
                    self.logger.info("IRC connection closed")
                    self.writer.close()
                    await self.writer.wait_closed()
            except ConnectionRefusedError:
                self.logger.error("IRC connection refused")
            except Exception as e:
                self.logger.error("IRC connection error: {}".format(e))
            finally:
                self.logger.debug("IRC connection closed")
                if self.writer:
                    self.writer.close()
                    await self.writer.wait_closed()
                if self.reader:
                    self.reader.feed_eof()
                if self.should_reconnect:
                    self.logger.info("Sleeping before reconnecting...")
                    await asyncio.sleep(5)

    def queue_name(self):
        name = "irc.{}".format(self.server["host"])
        name = name.replace('.', '_') # This is important because periods are meaningful in NATS' subject names
        return name

    def parse_line(self, line):
        # parses an irc line based on RFC:
        # https://tools.ietf.org/html/rfc2812#section-2.3.1
        prefix = None

        if line.startswith(':'):
            # prefix
            prefix, line = line.split(None, 1)
            name = prefix[1:]
            ident = None
            host = None
            if '!' in name:
                name, ident = name.split('!', 1)
                if '@' in ident:
                    ident, host = ident.split('@', 1)
            elif '@' in name:
                name, host = name.split('@', 1)
            prefix = self.Prefix(name, ident, host)

        command, *line = line.split(None, 1)
        command = command.upper()

        params = []
        if line:
            line = line[0]
            while line:
                if line.startswith(':'):
                    params.append(line[1:])
                    line = ''
                else:
                    param, *line = line.split(None, 1)
                    params.append(param)
                    if line:
                        line = line[0]

        return self.Message(prefix, command, params)

    async def send_line(self, line):
        if not self.writer:
            raise ValueError("No writer available")

        if len(line) > 510:
            self.logger.warning("Line length exceeds RFC limit of 512 characters: {}".format(len(line)))
        self.logger.debug('-> {}'.format(line))
        self.writer.write(line.encode('utf-8') + b'\r\n')
        await self.writer.drain()

    async def send_cmd(self, cmd, *params):
        params = list(params)  # copy
        if params:
            if ' ' in params[-1]:
                params[-1] = ':' + params[-1]
        params = [cmd] + params
        await self.send_line(' '.join(params))

    async def handle_line(self, line):
        try:
            # try utf-8 first
            line = line.decode('utf-8')
        except UnicodeDecodeError:
            # fall back that always works (but might not be correct)
            line = line.decode('latin1')

        line = line.strip()
        if line:
            message = self.parse_line(line)
            self.logger.debug("{} <- {}".format(self.server["host"], message))
            if message.command.isdigit() and int(message.command) >= 400:
                # might be an error
                self.logger.error("Possible server error: {}".format(str(message)))
            if message.command == 'PING':
                await self.send_cmd('PONG', *message.params)
            elif message.command == '001':
                await self.irc_join(self.server["channels"])
            elif message.command == '443':
                await self.irc_renick()
            elif message.command == 'PRIVMSG':
                await self.irc_received_privmsg(message)
            elif message.command == 'JOIN':
                self.irc_received_join(message)

    async def irc_join(self, channels):
        for channel in channels:
            await self.send_cmd('JOIN', channel)

    async def irc_renick(self):
        self.server["nickname"] = self.server["nickname"] + '_'
        await self.send_line('NICK ' + self.server["nickname"])

    def irc_received_join(self, message):
        nick = message.prefix.nick
        ident = message.prefix.ident
        host = message.prefix.host
        self.full_ident = ":{}!{}@{} ".format(nick, ident, host)

    async def irc_received_privmsg(self, message):
        target = message.params[0]  # channel or
        text = message.params[1]
        source = message.prefix.nick
        for trigger in self.options["triggers"]:
            if text.startswith(trigger):
                self.logger.info('INPUT: {}:{} <{}> {}'.format(self.server["host"], target, source, text))
                prompt = text[len(trigger):]
                packet = json.dumps({"reply-to": self.queue_name(), "frontend": "irc", "server": self.server["host"], "channel": target, "user": source, "trigger": trigger, "prompt": prompt})

                # Publish the trigger
                try:
                    await self.cb_publish(trigger, packet.encode())
                    # await self.send_cmd('PRIVMSG', *[target, "{}: Dream sequence accepted.".format(source)])
                except Exception as e:
                    traceback.print_exc()
                    await self.send_cmd('PRIVMSG', *[target, "{}: Dream sequence failed.".format(source)])

    def clean_filename(self, filename, whitelist=None, replace=' ', char_limit=255):
        if not whitelist:
            whitelist = self.valid_filename_chars
        # replace undesired characters
        for r in replace:
            filename = filename.replace(r,'_')

        # keep only valid ascii chars
        cleaned_filename = unicodedata.normalize('NFKD', filename).encode('ASCII', 'ignore').decode()

        # keep only whitelisted chars
        cleaned_filename = ''.join(c for c in cleaned_filename if c in whitelist).replace('__', '')
        return cleaned_filename[:char_limit]

    async def cb_handle_response(self, _, data):
        message = ""

        try:
            resp = json.loads(data.decode())
        except Exception as e:
            self.logger.error("Failed to parse response: {}".format(e))
            traceback.print_exc()
            return

        if "reply-image" in resp:
            image_bytes = base64.standard_b64decode(resp["reply-image"])
            filename_base = self.clean_filename(resp["prompt"], char_limit = self.f_namemax)
            filename = "{}.png".format(filename_base[:self.f_namemax])
            url = "{}/{}".format(self.options["uri_base"], filename)

            with open(os.path.join(self.options["output_dir"], filename), "wb") as f:
                f.write(image_bytes)
            self.logger.info("OUTPUT: {}:{} <{}> {}".format(resp["server"], resp["channel"], resp["user"], url))
            message = "{}: I dreamed this: {}".format(resp["user"], url)
        elif "reply-text" in resp:
            message = "{}: {}".format(resp["user"], resp["reply-text"])
            self.logger.info("OUTPUT: {}:{} <{}> {}".format(resp["server"], resp["channel"], resp["user"], resp["reply-text"]))
        elif "error" in resp:
            message = "{}: Dream sequence collapsed: {}".format(resp["user"], resp["error"])
            self.logger.error("OUTPUT: {}:{}: ".format(resp["server"], resp["channel"], message))
        elif "usage" in resp:
            message = "{}: {}".format(resp["user"], resp["usage"])
            self.logger.info("OUTPUT: {}:{} <{}> {}".format(resp["server"], resp["channel"], resp["user"], resp["usage"]))
        else:
            message = "{}: Dream sequence collapsed, unknown reason.".format(resp["user"])

        chunks = []
        # We have to send multiline responses separately, so let's split the message into lines
        for line in message.splitlines():
            # IRC has a max line length of 512 bytes, so we need to split the line into chunks
            max_chunk_size = 510 # Start with 510 because send_cmd() adds 2 bytes for the CRLF
            max_chunk_size -= len("{} PRIVMSG {} :".format(self.full_ident, resp["channel"]))
            chunks += [line[i:i+max_chunk_size] for i in range(0, len(line), max_chunk_size)]

        loop = asyncio.get_event_loop()
        for chunk in chunks:
            await self.send_cmd('PRIVMSG', *[resp["channel"], chunk])

# Example JSON config:
# {
#     "triggers": [
#           "!dream ",
#           "!gpt "
#     ],
#     "nats_uri": [ "nats://nats:4222", "nats://nats2:4222" ],
#     "output_dir": "/data",
#     "uri_base": "http://localhost:8080/dreams",
#     "irc": [
#         {
#                 "nickname": "dreambot",
#                 "ident": "dreambot",
#                 "realname": "I've dreamed things you people wouldn't believe",
#                 "host": "irc.server.com",
#                 "port": 6697,
#                 "ssl": true,
#                 "channels": [
#                         "#friends",
#                         "#dreambot"
#                 ]
#         },
#         {
#                 "nickname": "dreambot",
#                 "ident": "dreambot",
#                 "realname": "I've dreamed things you people wouldn't believe",
#                 "host": "other.ircplace.org",
#                 "port": 6667,
#                 "ssl": false,
#                 "channels": [
#                         "#dreambot"
#                 ]
#         }
#     ]
# }
