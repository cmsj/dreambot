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
from collections import namedtuple

# TODO:
# - Attempt support for multiple channels and privmsgs (the latter requires detecting that target is our nick)
# - Decode JSON in ws_receive and process accordingly

# Add this in places where you want to drop to a REPL to investigate something
# import code ; code.interact(local=dict(globals(), **locals()))

logger = logging.getLogger('dreambot')
logger.setLevel(logging.DEBUG)

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
    print('->', line)
    writer.write(line.encode('utf-8') + b'\r\n')

def irc_send_cmd(writer: asyncio.StreamWriter, cmd, *params):
    params = list(params)  # copy
    if params:
        if ' ' in params[-1]:
            params[-1] = ':' + params[-1]
    params = [cmd] + params
    irc_send_line(writer, ' '.join(params))

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
        image_bytes = base64.standard_b64decode(x["image"])
        filename_base = x["prompt"].replace(' ', '_').replace('?', '').replace('\\', '').replace(',', '')
        filename = "{}.png".format(filename_base[:f_namemax])
        url = "{}/{}".format(self.options["uri_base"], filename)
    
        with open(os.path.join(options["output_dir"], filename), "wb") as f:
            f.write(image_bytes)
        logger.info("{}:{} <{}> {}".format(x["server"], x["channel"], x["user"], url))
        for sendcmd in self.sendcmds:
            if sendcmd[0]["host"] == x["server"]:
                sendcmd[1]('PRIVMSG', *[x["channel"], "{}: I dreamed this: {}".format(x["user"], url)])
    
    # IRC entrypoint
    async def irc_boot(self, server):
        logger.info("Starting IRC client for {}...".format(server["host"]))
        reader, writer = await asyncio.open_connection(server["host"], server["port"], ssl=server["ssl"])
        return reader, writer
    
    # IRC message handler
    async def irc_loop(self, server, reader, sendline, sendcmd):
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
                    if text.startswith(self.options["txt2img_trigger"]) or text.startswith(self.options["img2img_trigger"]):
                        logger.info('{} <{}> {}'.format(target, source, text))
                        if len(self.websocket.websockets) == 0:
                          sendcmd('PRIVMSG', *[target, "Dream sequence collapsed: No websocket connection from backend"])
                          continue
    
                        if text.startswith(self.options["txt2img_trigger"]):
                            trigger = self.options["txt2img_trigger"]
                            trigger_type = "txt2img"
                        elif text.startswith(self.options["img2img_trigger"]):
                            trigger = self.options["img2img_trigger"]
                            trigger_type = "img2img"
    
                        prompt = text[len(trigger):]
                        packet = json.dumps({"server": server["host"], "channel": target, "user": source, "prompt": prompt, "prompt_type": trigger_type})
    
                        ws = random.choice(self.websocket.websockets)
                        await ws.send(packet)
                        sendcmd('PRIVMSG', *[target, "{}: Dream sequence accepted.".format(source)])
    
    # Main entrypoint
    async def boot(self):
        self.sendcmds = []

        ws_task = asyncio.create_task(self.ws_boot())

        for server in self.options["irc"]:
            reader, writer = await self.irc_boot(server)
            sendline = functools.partial(irc_send_line, writer)
            sendcmd = functools.partial(irc_send_cmd, writer)
    
            self.sendcmds.append((server, sendcmd))
    
            asyncio.create_task(self.irc_loop(server, reader, sendline, sendcmd))
        
        await ws_task


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
#     "host": "irc.server.com",
#     "port": 6667,
#     "ssl": false,
#     "nickname": "dreambot",
#     "ident": "dreambot",
#     "realname": "I've dreamed things you people wouldn't believe",
#     "channel": "#somechannel",
#     "txt2img_trigger": "!dream ",
#     "img2img_trigger": "!imgdream ",
#     "websocket_host": "0.0.0.0",
#     "websocket_port": 9999,
#     "output_dir": "/data",
#     "uri_base": "http://localhost:8080/dreams",
    # "irc": [
    #         {
    #                 "host": "irc.pl0rt.org",
    #                 "port": 6697,
    #                 "ssl": true,
    #                 "channels": [
    #                         "#ed",
    #                         "#dreambot"
    #                 ]
    #         },
    #         {
    #                 "host": "de1.arcnet.org",
    #                 "port": 6667,
    #                 "ssl": false,
    #                 "channels": [
    #                         "#worms"
    #                 ]
    #         }
    # ]
#   }
