#!/usr/bin/env python3
import asyncio
import functools
import json
import websockets
import base64
import os
import sys
import random
import logging
import unicodedata
import string
from collections import namedtuple

# TODO:
# - Decode JSON in ws_receive and process accordingly

# Add this in places where you want to drop to a REPL to investigate something
# import code ; code.interact(local=dict(globals(), **locals()))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('dreambot')
logger.setLevel(logging.INFO)

# Various IRC support types/functions
Message = namedtuple('Message', 'prefix command params')
Prefix = namedtuple('Prefix', 'nick ident host')
def irc_parse_line(line):
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
        prefix = Prefix(name, ident, host)

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

    return Message(prefix, command, params)

def irc_send_line(writer: asyncio.StreamWriter, line):
    logger.debug('-> {}'.format(line))
    writer.write(line.encode('utf-8') + b'\r\n')

def irc_send_cmd(writer: asyncio.StreamWriter, cmd, *params):
    params = list(params)  # copy
    if params:
        if ' ' in params[-1]:
            params[-1] = ':' + params[-1]
    params = [cmd] + params
    irc_send_line(writer, ' '.join(params))

# Filename sanitisation
valid_filename_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
def clean_filename(filename, whitelist=valid_filename_chars, replace=' ', char_limit=255):
    # replace undesired characters
    for r in replace:
        filename = filename.replace(r,'_')

    # keep only valid ascii chars
    cleaned_filename = unicodedata.normalize('NFKD', filename).encode('ASCII', 'ignore').decode()

    # keep only whitelisted chars
    cleaned_filename = ''.join(c for c in cleaned_filename if c in whitelist)
    return cleaned_filename[:char_limit]

class DreamBot:
    websocket = None
    sendcmds = None
    options = None

    def __init__(self, options):
        self.options = options

    # Websocket entrypoint
    async def ws_boot(self):
      logger.info("Starting websocket server...")
      self.websocket = await websockets.serve(self.ws_receive,
                                                self.options["websocket_host"], self.options["websocket_port"],
                                                ping_interval=2, ping_timeout=3000,
                                                max_size=None, max_queue=None,
                                                close_timeout=1, read_limit=2 ** 24)

    # Websocket message handler
    async def ws_receive(self, websocket, path):
      f_namemax = os.statvfs(self.options["output_dir"]).f_namemax - 4

      async for message in websocket:
        # FIXME: Wrap this all in a try/except like mado_orig.py and sendcmd() the error
        x = json.loads(message)
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

        for sendcmd in self.sendcmds:
            if sendcmd[0]["host"] == x["server"]:
                sendcmd[1]('PRIVMSG', *[x["channel"], message])

    # IRC entrypoint
    async def irc_boot(self, server):
        logger.info("Starting IRC client for {}...".format(server["host"]))
        reader, writer = await asyncio.open_connection(server["host"], server["port"], ssl=server["ssl"])
        return reader, writer

    # IRC message handler
    async def irc_loop(self, server, reader, sendline, sendcmd):
        logger.info("Starting irc_loop for {}".format(server["host"]))
        sendline('NICK ' + self.options["nickname"])
        sendline('USER ' + self.options["ident"] + ' * * :' + self.options["realname"])

        while not reader.at_eof():
            line = await reader.readline()
            try:
                # try utf-8 first
                line = line.decode('utf-8')
            except UnicodeDecodeError:
                # fall back that always works (but might not be correct)
                line = line.decode('latin1')

            line = line.strip()
            if line:
                message = irc_parse_line(line)
                logger.debug("{} <- {}".format(server["host"], message))
                if message.command.isdigit() and int(message.command) >= 400:
                    # might be an error
                    logger.error(str(message))

                if message.command == 'PING':
                    sendcmd('PONG', *message.params)
                elif message.command == '001':
                    for channel in server["channels"]:
                        sendcmd('JOIN', channel)
                elif message.command == '443':
                    self.options["nickname"] = self.options["nickname"] + '_'
                    sendline('NICK ' + self.options["nickname"])
                elif message.command == 'PRIVMSG':
                    target = message.params[0]  # channel or
                    text = message.params[1]
                    source = message.prefix.nick
                    if text.startswith(self.options["trigger"]):
                        logger.info('INPUT: {}:{} <{}> {}'.format(server["host"], target, source, text))
                        if len(self.websocket.websockets) == 0:
                          logger.error("No websocket connections to send to!")
                          sendcmd('PRIVMSG', *[target, "Dream sequence collapsed: No websocket connection from backend"])
                          continue

                        prompt = text[len(self.options["trigger"]):]
                        packet = json.dumps({"server": server["host"], "channel": target, "user": source, "trigger": self.options["trigger"], "prompt": prompt})

                        for ws in self.websocket.websockets:
                            # FIXME: Make this a random choice of websocket
                            await ws.send(packet)
                            sendcmd('PRIVMSG', *[target, "{}: Dream sequence accepted.".format(source)])

        logger.info("Ended irc_loop for {}".format(server["host"]))

    # Main entrypoint
    async def boot(self):
        self.sendcmds = []
        ircloops = []

        ws_task = asyncio.create_task(self.ws_boot())

        for server in self.options["irc"]:
            logger.info("Preparing IRC connection for {}".format(server["host"]))
            reader, writer = await self.irc_boot(server)
            sendline = functools.partial(irc_send_line, writer)
            sendcmd = functools.partial(irc_send_cmd, writer)

            self.sendcmds.append((server, sendcmd))

            ircloops.append(asyncio.create_task(self.irc_loop(server, reader, sendline, sendcmd)))

        await ws_task
        for ircloop in ircloops:
            await ircloop


if __name__ == "__main__":
  if len(sys.argv) != 2:
    print("Usage: {} <config.json>".format(sys.argv[0]))
    sys.exit(1)

  with open(sys.argv[1]) as f:
    options = json.load(f)

  logger.info("WebSocket bridge starting up...")
  dreambot = DreamBot(options)
  asyncio.run(dreambot.boot())

# Example JSON config:
# {
#     "nickname": "dreambot",
#     "ident": "dreambot",
#     "realname": "I've dreamed things you people wouldn't believe",
#     "trigger": "!dream ",
#     "websocket_host": "0.0.0.0",
#     "websocket_port": 9999,
#     "output_dir": "/data",
#     "uri_base": "http://localhost:8080/dreams",
#     "irc": [
#         {
#                 "host": "irc.server.com",
#                 "port": 6697,
#                 "ssl": true,
#                 "channels": [
#                         "#friends",
#                         "#dreambot"
#                 ]
#         },
#         {
#                 "host": "other.ircplace.org",
#                 "port": 6667,
#                 "ssl": false,
#                 "channels": [
#                         "#dreambot"
#                 ]
#         }
#     ]
# }
