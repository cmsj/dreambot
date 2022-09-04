print("ElectricDream AI Art bot starting up...")

PIPE = None

import asyncio
from collections import namedtuple
import functools
import random
import traceback

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


async def main_loop(host, port, **options):
    reader, writer = await asyncio.open_connection(
        host, port, ssl=options.get('ssl', False))

    # some partials
    sendline = functools.partial(send_line_to_writer, writer)
    sendcmd = functools.partial(send_cmd_to_writer, writer)

    sendline('NICK {nickname}'.format(**options))
    sendline('USER {ident} * * :{realname}'.format(**options))

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
                print(message)

            if message.command == 'PING':
                sendcmd('PONG', *message.params)
            elif message.command == '001':
                sendcmd('JOIN', options['autojoin'])
            elif message.command == 'PRIVMSG':
                target = message.params[0]  # channel or
                text = message.params[1]
                source = message.prefix.nick
                print('<{}{}> {}'.format(source, target, text))
                if text.startswith("!dream "):
                    prompt = text[7:]
                    filename = prompt.replace(' ', '_').replace('?', '').replace('\\', '') + ".png"
                    with torch.autocast("cuda"):
                        try:
                            image = PIPE(prompt)["sample"][0]
                            image.save("X:\\dreams\\" + filename)
                            print(f"Saved to: {filename}")

                            sendcmd("PRIVMSG", *[target, f"{source}: I dreamed https://dreams.tenshu.net/{filename} for '{prompt}'"])
                        except BaseException as err:
                            err_params = [target, "Dream sequence collapsed: " + str(err)]
                            sendcmd("PRIVMSG", *err_params)
                            traceback.print_exc()



def main():
    options = {
        'nickname': 'ElectricSheep',
        'ident': 'dreambot',
        'realname': "I've dreamed things you people wouldn't believe",
        'autojoin': '#ed',
    }

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main_loop('irc.pl0rt.org', 6667, **options))


if __name__ == '__main__':
    print("Importing torch...")
    import torch
    from diffusers import StableDiffusionPipeline

    print("Loading Stable Diffusion...")
    PIPE = StableDiffusionPipeline.from_pretrained("CompVis/stable-diffusion-v1-4", use_auth_token=True)
    PIPE = PIPE.to("cuda")

    main()