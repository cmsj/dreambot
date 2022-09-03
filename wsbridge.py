#!/usr/bin/env python3
import asyncio
import functools
import json
import websockets
import base64
import os
from collections import namedtuple

# TODO:
# - Attempt support for multiple channels and privmsgs (the latter requires detecting that target is our nick)
# - Decode JSON in ws_receive and process accordingly

# Add this in places where you want to drop to a REPL to investigate something
# import code ; code.interact(local=dict(globals(), **locals()))

# Websocket entrypoint
async def ws_boot(sendcmd, options):
  print("Starting websocket server...")
  return await websockets.serve(functools.partial(ws_receive, sendcmd=sendcmd, options=options),
                                options["websocket_host"], options["websocket_port"],
                                ping_interval=2, ping_timeout=3000,
                                max_size=None, max_queue=None,
                                close_timeout=1, read_limit=2 ** 24)
# Websocket message handler
async def ws_receive(websocket, sendcmd, options):
  f_namemax = os.statvfs(options["output_dir"]).f_namemax - 4

  async for message in websocket:
    # FIXME: Wrap this all in a try/except like mado_orig.py and sendcmd() the error
    x = json.loads(message)
    print("Received response for: {} <{}> {}".format(x["channel"], x["user"], x["prompt"]))
    image_bytes = base64.standard_b64decode(x["image"])
    filename_base = x["prompt"].replace(' ', '_').replace('?', '').replace('\\', '').replace(',', '')
    filename = "{}.png".format(filename_base[:f_namemax])
    with open(os.path.join(options["output_dir"], filename), "wb") as f:
        f.write(image_bytes)
    sendcmd('PRIVMSG', *[x["channel"], "{}: I dreamed this for '{}': https://dreams.tenshu.net/{}".format(x["user"], x["prompt"], filename)])

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

# IRC entrypoint
async def irc_boot(options):
    print("Starting IRC client...")
    reader, writer = await asyncio.open_connection(options["host"], options["port"], ssl=options["ssl"])
    return reader, writer
# IRC message handler
async def irc_loop(reader, sendline, sendcmd, websocket, options):
    sendline('NICK ' + options["nickname"])
    sendline('USER ' + options["ident"] + ' * * :' + options["realname"])

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
                print("ERROR: " + str(message))

            if message.command == 'PING':
                sendcmd('PONG', *message.params)
            elif message.command == '001':
                sendcmd('JOIN', options["channel"])
            elif message.command == 'PRIVMSG':
                target = message.params[0]  # channel or
                text = message.params[1]
                source = message.prefix.nick
                if text.startswith(options["trigger"]):
                    print('{} <{}> {}'.format(target, source, text))
                    if len(websocket.websockets) == 0:
                      sendcmd('PRIVMSG', *[target, "Dream sequence collapsed: No websocket connection from backend"])
                      continue

                    prompt = text[len(options["trigger"]):]
                    packet = json.dumps({"channel": target, "user": source, "prompt": prompt})
                    for ws in websocket.websockets:
                      await ws.send(packet)
                      sendcmd('PRIVMSG', *[target, "{}: Dream sequence accepted: {}".format(source, prompt)])

# Main entrypoint
async def boot(options):
  reader, writer = await irc_boot(options)
  sendline = functools.partial(irc_send_line, writer)
  sendcmd = functools.partial(irc_send_cmd, writer)

  websocket = await ws_boot(sendcmd, options)
  await irc_loop(reader, sendline, sendcmd, websocket, options)

if __name__ == "__main__":
  print("WebSocket bridge starting up...")
  options = {
    "host": "irc.pl0rt.org",
    "port": 6667,
    "ssl": False,
    "nickname": "dreambot",
    "ident": "dreambot",
    "realname": "I've dreamed things you people wouldn't believe",
    "channel": "#ed",
    "trigger": "!dream ",
    "websocket_host": "0.0.0.0",
    "websocket_port": 9999,
    "output_dir": "/data"
  }
  asyncio.run(boot(options))
