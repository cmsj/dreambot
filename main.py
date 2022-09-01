#!/usr/bin/env python3
import asyncio
import functools
import json
import websockets
from collections import namedtuple

  # import code
  # code.interact(local=dict(globals(), **locals()))

# Websocket message handler
async def receive(websocket, sendcmd, options):
  async for message in websocket:
    # FIXME: This should receive JSON and process it accordingly
    # It will either contain an error message or a nick, a prompt and a dream image
    print(message)
    sendcmd('PRIVMSG', *[options["channel"], message])

# Websocket entrypoint
async def main_websocket(port, sendcmd, options):
  print("Starting websocket server...")
  return await websockets.serve(functools.partial(receive, sendcmd=sendcmd, options=options),
                                'localhost',
                                port,
                                ping_interval=2,
                                ping_timeout=300,
                                max_size=None,
                                max_queue=None,
                                close_timeout=1,
                                read_limit=2 ** 24)

# Various IRC support types/functions
Message = namedtuple('Message', 'prefix command params')
Prefix = namedtuple('Prefix', 'nick ident host')
def parse_line(line):
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
def send_line_to_writer(writer: asyncio.StreamWriter, line):
    print('->', line)
    writer.write(line.encode('utf-8') + b'\r\n')
def send_cmd_to_writer(writer: asyncio.StreamWriter, cmd, *params):
    params = list(params)  # copy
    if params:
        if ' ' in params[-1]:
            params[-1] = ':' + params[-1]
    params = [cmd] + params
    send_line_to_writer(writer, ' '.join(params))

# IRC entrypoint
async def main_irc(options):
    print("Starting IRC client...")
    reader, writer = await asyncio.open_connection(options["host"], options["port"], ssl=options["ssl"])
    return reader, writer
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
            message = parse_line(line)
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
                    prompt = text[len(options["trigger"]):]
                    packet = {
                        "channel": target,
                        "user": source,
                        "prompt": prompt,
                    }

                    if len(websocket.websockets) == 0 :
                      sendcmd('PRIVMSG', *[options["channel"], "Dream sequence collapsed: No websocket connection from backend"])
                    for ws in websocket.websockets:
                      await ws.send(json.dumps(packet))

async def main():
  options = {
    "host": "irc.pl0rt.org",
    "port": 6667,
    "ssl": False,
    "nickname": "dreambot",
    "ident": "dreambot",
    "realname": "I've dreamed things you people wouldn't believe",
    "channel": "#dreambot",
    "trigger": "!dream ",
    "websocket_port": 9999,
  }
  
  reader, writer = await main_irc(options)
  sendline = functools.partial(send_line_to_writer, writer)
  sendcmd = functools.partial(send_cmd_to_writer, writer)

  websocket = await main_websocket(options["websocket_port"], sendcmd, options)
  await irc_loop(reader, sendline, sendcmd, websocket, options)

if __name__ == "__main__":
  print("WebSocket bridge starting up...")
  asyncio.run(main())
