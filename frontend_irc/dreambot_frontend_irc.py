#!/usr/bin/env python3
import asyncio
import functools
import json
import base64
import os
import signal
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
logger = logging.getLogger('dreambot_frontend_irc')
logger.setLevel(logging.DEBUG)

class DreambotFrontendIRC:
    # Various IRC support types/functions
    Message = namedtuple('Message', 'prefix command params')
    Prefix = namedtuple('Prefix', 'nick ident host')

    options = None
    server = None
    writer = None
    reader = None
    logger = None
    cb_publish = None
    f_namemax = None

    def __init__(self, server, options, cb_publish):
        self.logger = logging.getLogger('dreambot.irc.{}'.format(server["host"]))
        self.logger.setLevel(logging.DEBUG)
        self.server = server
        self.options = options
        self.cb_publish = cb_publish
        self.f_namemax = os.statvfs(self.options["output_dir"]).f_namemax - 4

    async def boot(self, max_reconnects=0):
        reconnect = True
        while reconnect and max_reconnects >= 0:
            self.logger.info("Booting IRC connection...")
            try:
                self.reader, self.writer = await asyncio.open_connection(self.server["host"], self.server["port"], ssl=self.server["ssl"])
                try:
                    self.send_line('NICK ' + self.server["nickname"])
                    self.send_line('USER ' + self.server["ident"] + ' * * :' + self.server["realname"])
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
            finally:
                if max_reconnects == 1:
                    reconnect = False
                max_reconnects -= 1
                await asyncio.sleep(5)

    def queue_name(self):
        return "irc.{}".format(self.server["host"])

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
        self.logger.debug('-> {}'.format(line))
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
            self.logger.debug("{} <- {}".format(self.server["host"], message))
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
                packet = json.dumps({"reply-to": self.queue_name(), "frontend": "irc", "server": self.server["host"], "channel": target, "user": source, "trigger": trigger, "prompt": prompt})

                # Publish the trigger
                try:
                    await self.cb_publish(trigger, packet.encode())
                    self.send_cmd('PRIVMSG', *[target, "{}: Dream sequence accepted.".format(source)])
                except:
                    self.send_cmd('PRIVMSG', *[target, "{}: Dream sequence failed.".format(source)])

    def handle_response(self, resp):
        message = ""

        if "reply-image" in resp:
            image_bytes = base64.standard_b64decode(resp["reply-image"])
            filename_base = clean_filename(resp["prompt"], char_limit = self.f_namemax)
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

        self.send_cmd('PRIVMSG', *[resp["channel"], message])

# FIXME: Move this into the IRC class probably
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

class Dreambot:
    logger = None
    nats = None
    js = None
    options = None
    irc_servers = None
    nats_tasks = None

    def __init__(self, options):
        self.logger = logging.getLogger("dreambot.frontend")
        self.logger.setLevel(logging.DEBUG)
        self.options = options
        self.irc_servers = {}
        self.nats_tasks = []

    async def nats_boot(self, max_reconnects=0):
        self.logger.info("Booting NATS subscriber...")
        try:
            self.nats = await nats.connect(self.options["nats_uri"], name="dreambot-frontend-irc", max_reconnect_attempts=max_reconnects)
            self.logger.info("NATS connected to {}".format(self.nats.connected_url.netloc))
            self.js = self.nats.jetstream()
            consumer_info = await self.js.consumer_info()
            self.logger.info("JetStream connected to: {}::{}".format(consumer_info.cluster, consumer_info.stream_name))

            for server_name in self.irc_servers:
                server = self.irc_servers[server_name]
                self.nats_tasks.append(asyncio.create_task(self.handle_nats_messages(server.queue_name())))

            await asyncio.gather(*self.nats_tasks)
        except nats.errors.NoServersError:
            self.logger.error("No NATS servers available.")
        else:
            self.logger.warning("NATS connection closed.")
        finally:
            [task.cancel() for task in self.nats_tasks]
            self.nats_tasks = []
            await asyncio.sleep(5)

    async def handle_nats_messages(self, queue_name):
        await self.js.add_stream(name=queue_name, subjects=[queue_name])
        sub = await self.js.subscribe(queue_name)

        async for msg in sub.messages:
            try:
                x = json.loads(msg.data)
                self.irc_servers[x["queue_name"]].handle_response(x)
            except json.decoder.JSONDecodeError:
                logger.error("nats message is not json: {}".format(msg.data))
                continue
            except KeyError:
                logger.error("nats message is for unknown server: {}".format(x["queue_name"]))
                continue
            else:
                self.logger.error("unknown nats message failure")

    def handle_exception(self, loop, context):
        msg = context.get("exception", context["message"])
        logger.error("Caught exception: %s", msg)
        logger.debug("Exception context: %s", context)
        # asyncio.create_task(self.shutdown(loop))

    async def shutdown(loop, signal=None):
        if signal:
            logger.info("Received exit signal %s...", signal.name)
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

        [task.cancel() for task in tasks]
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.stop()

    def publish_callback(self, subject, data):
        self.nats.publish(subject, data)

    # Main entrypoint
    async def boot(self, max_reconnects=0):
        async_tasks = []
        loop = asyncio.get_event_loop()

        loop.set_exception_handler(lambda loop,context: self.handle_exception(loop, context))
        signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
        for s in signals:
            loop.add_signal_handler(s, lambda s=s: asyncio.create_task(self.shutdown(loop, signal=s)))

        self.logger.debug("Found %d IRC servers to boot", len(self.options["irc"]))
        for server in self.options["irc"]:
            server = DreambotFrontendIRC(server, self.options, self.publish_callback)
            self.irc_servers[server.queue_name()] = server

            async_tasks.append(asyncio.create_task(server.boot(max_reconnects=max_reconnects)))

        async_tasks.append(self.nats_boot(max_reconnects=max_reconnects))
        await asyncio.gather(*async_tasks)


if __name__ == "__main__":
  if len(sys.argv) != 2:
    print("Usage: {} <config.json>".format(sys.argv[0]))
    sys.exit(1)

  with open(sys.argv[1]) as f:
    options = json.load(f)

  loop = asyncio.get_event_loop()

  logger.info("Dreamboot IRC frontend starting up...")
  try:
    dreambot = Dreambot(options)
    loop.run_until_complete(dreambot.boot())
    loop.run_forever()
  finally:
    loop.close()
    logger.info("Dreambot IRC frontend shutting down...")

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
