#!/usr/bin/env python3
import asyncio
import functools
import json
import base64
import os
import sys
import random
import logging
import unicodedata
import string
import nats
from collections import namedtuple

# TODO:
# Nothing

# Add this in places where you want to drop to a REPL to investigate something
# import code ; code.interact(local=dict(globals(), **locals()))

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('dreambot')
logger.setLevel(logging.DEBUG)

class DreambotFrontendIRC:
    # Various IRC support types/functions
    Message = namedtuple('Message', 'prefix command params')
    Prefix = namedtuple('Prefix', 'nick ident host')

    reconnect = True
    options = None
    server = None
    writer = None
    reader = None
    logger = None

    @classmethod
    async def create(server, options):
        self = DreambotFrontendIRC()
        self.logger = logging.getLogger('dreambot.irc.{}'.format(server["host"]))
        self.logger.setLevel(logging.DEBUG)
        self.server = server
        self.options = options
        return self

    async def boot(self):
        while self.reconnect:
            self.logger.info("Booting IRC connection...")
            try:
                self.reader, self.writer = await asyncio.open_connection(self.server["host"], self.server["port"], ssl=self.server["ssl"])
                try:
                    self.send_line('NICK ' + self.options["nickname"])
                    self.send_line('USER ' + self.options["ident"] + ' * * :' + self.options["realname"])
                    self.logger.info("IRC connection booted.")

                    # Loop until the connection is closed
                    while not self.reader.at_eof():
                        data = await self.reader.readline()
                        await self.handle_line(data)
                finally:
                    self.logger.info("IRC connection closed")
                    self.writer.close()
            except ConnectionRefusedError:
                self.logger.error("IRC connection refused")
            else:
                self.logger.error("IRC connection closed")
            await asyncio.sleep(5)

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

    def send_line(self, line):
        logger.debug('-> {}'.format(line))
        self.writer.write(line.encode('utf-8') + b'\r\n')

    def send_cmd(self, cmd, *params):
        params = list(params)  # copy
        if params:
            if ' ' in params[-1]:
                params[-1] = ':' + params[-1]
        params = [cmd] + params
        self.send_line(' '.join(params))

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
                logger.debug("{} <- {}".format(self.server["host"], message))
                if message.command.isdigit() and int(message.command) >= 400:
                    # might be an error
                    self.logger.error("Possible server error: {}".format(str(message)))
                if message.command == 'PING':
                    self.send_cmd('PONG', *message.params)
                elif message.command == '001':
                    self.irc_join(self.server["channels"])
                elif message.command == '443':
                    self.irc_renick()
                elif message.command == 'PRIVMSG':
                    await self.irc_privmsg(message)

    def irc_join(self, channels):
        for channel in channels:
            self.send_cmd('JOIN', channel)

    def irc_renick(self):
        self.server["nickname"] = self.server["nickname"] + '_'
        self.send_line('NICK ' + self.server["nickname"])

    async def irc_privmsg(self, message):
        target = message.params[0]  # channel or
        text = message.params[1]
        source = message.prefix.nick
        for trigger in self.options["triggers"]:
            if text.startswith(trigger):
                self.logger.info('INPUT: {}:{} <{}> {}'.format(self.server["host"], target, source, text))
                prompt = text[len(trigger):]
                packet = json.dumps({"frontend": "irc", "server": self.server["host"], "channel": target, "user": source, "trigger": trigger, "prompt": prompt})

                # Send request to NATS queue
                # FIXME: This doesn't work, we don't have a handle for the NATS connection yet
                await self.nats.publish(trigger, packet.encode())
                self.send_cmd('PRIVMSG', *[target, "{}: Dream sequence accepted.".format(source)])

# Filename sanitisation
valid_filename_chars = "_.() %s%s" % (string.ascii_letters, string.digits)
def clean_filename(filename, whitelist=valid_filename_chars, replace=' ', char_limit=255):
    # replace undesired characters
    for r in replace:
        filename = filename.replace(r,'_')

    # keep only valid ascii chars
    cleaned_filename = unicodedata.normalize('NFKD', filename).encode('ASCII', 'ignore').decode()

    # keep only whitelisted chars
    cleaned_filename = ''.join(c for c in cleaned_filename if c in whitelist).replace('__', '')
    return cleaned_filename[:char_limit]

class DreamBot:
    nats = None
    js = None
    options = None

    def __init__(self, options):
        self.options = options

    # NATS entrypoint
    async def nats_boot(self):
      logger.info("Starting NATS subscriber...")
      f_namemax = os.statvfs(self.options["output_dir"]).f_namemax - 4

      self.nats = await nats.connect(self.options["nats_uri"], name="dreambot-frontend-irc")
      self.js = self.nats.jetstream()

      await self.js.add_stream(name='irc', subjects=['irc'])

      sub = await self.js.subscribe("irc")

      async for message in sub.messages:
        try:
            x = json.loads(message.data)
        except:
            logger.error("nats message is not json: {}".format(message.data))
            continue
        message = ""

        if "image" in x:
            image_bytes = base64.standard_b64decode(x["image"])
            filename_base = clean_filename(x["prompt"], char_limit = f_namemax)
            filename = "{}.png".format(filename_base[:f_namemax])
            url = "{}/{}".format(self.options["uri_base"], filename)

            with open(os.path.join(options["output_dir"], filename), "wb") as f:
                f.write(image_bytes)
            logger.info("OUTPUT: {}:{} <{}> {}".format(x["server"], x["channel"], x["user"], url))
            message = "{}: I dreamed this: {}".format(x["user"], url)
        elif "error" in x:
            message = "{}: Dream sequence collapsed: {}".format(x["user"], x["error"])
            logger.error("OUTPUT: {}:{}: ".format(x["server"], x["channel"], message))
        elif "usage" in x:
            message = "{}: {}".format(x["user"], x["usage"])
            logger.info("OUTPUT: {}:{} <{}> {}".format(x["server"], x["channel"], x["user"], x["usage"]))
        else:
            message = "{}: Dream sequence collapsed, unknown reason.".format(x["user"])

        # FIXME: This doesn't work, sendcmds no longer exists
        for sendcmd in self.sendcmds:
            if sendcmd[0]["host"] == x["server"]:
                sendcmd[1]('PRIVMSG', *[x["channel"], message])

    # Main entrypoint
    async def boot(self):
        irc_servers = {}
        for server in self.options["irc"]:
            irc_servers[server["host"]] = await DreambotFrontendIRC.create(server, self.options)

        tasks = []
        tasks.append(asyncio.create_task(self.nats_boot()))
        for host in irc_servers:
            tasks.append(asyncio.create_task(irc_servers[host].boot()))
        await asyncio.gather(*tasks)


if __name__ == "__main__":
  if len(sys.argv) != 2:
    print("Usage: {} <config.json>".format(sys.argv[0]))
    sys.exit(1)

  with open(sys.argv[1]) as f:
    options = json.load(f)

  logger.info("Dreamboot IRC frontend starting up...")
  dreambot = DreamBot(options)
  asyncio.run(dreambot.boot())

# Example JSON config:
# {
#     "triggers": [
#           "!dream ",
#           "!gpt "
#     ],
#     "nats_uri": "nats://nats:4222",
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
