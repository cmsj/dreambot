import queue
import websockets
import asyncio
import janus
import requests

import json
import time
import io
import sys
import os
import traceback
import base64
import tempfile
import concurrent.futures
import argparse

from urllib.parse import urlparse, unquote

from PIL import Image
from ldm.simplet2i import T2I

# TODO
# - Expose sampler choice as an argument
# - Maybe add upscaling?
# - Make image fetching substantially less fragile
# - Return an error packet when errors happen

# Argument parsing scaffolding
class UsageException(Exception):
    def __init__(self, message):
        super().__init__(message)

class ErrorCatchingArgumentParser(argparse.ArgumentParser):
    def exit(self, status=0, message=None):
        raise ValueError(message)
    def error(what, message):
        raise ValueError(message)
    def print_usage(self, file=None):
        raise UsageException(self.format_usage())
    def print_help(self, file=None):
        raise UsageException(self.format_usage())

# Validate various arguments
def check_steps(value):
    ivalue = int(value)
    if ivalue <= 0 or ivalue > 50:
        raise argparse.ArgumentTypeError("steps must be between 1 and 50")
    return ivalue
def check_cfgscale(value):
    ivalue = float(value)
    if ivalue < 1.0:
        raise argparse.ArgumentTypeError("cfgscale must be at least 1.0")
    return ivalue

def send_error(queue, server, channel, user, message, key="error"):
    packet = {
        key: message,
        "server": server,
        "channel": channel,
        "user": user,
    }
    x = json.dumps(packet)
    print("Enqueueing result: {}, results queue size is: {}".format(str(x), queue.qsize()))
    queue.put(x)

def send_usage(queue, server, channel, user, message):
    send_error(queue, server, channel, user, message, key="usage")

def stabdiff(die, queue_prompts, queue_results, opt):
    print("Stable Diffusion booting...")
    t2i = T2I(weights=opt["model"], config=opt["config"], iterations=opt["n_iter"],
              steps=opt["steps"], seed=opt["seed"], grid=False, width=opt["W"], height=opt["H"],
              cfg_scale=opt["scale"], sampler_name=opt["sampler"],
              precision=opt["precision"], full_precision=opt["full_precision"])
    t2i.load_model()
    print("Stable Diffusion booted")

    argparser = ErrorCatchingArgumentParser(prog="dreambot", exit_on_error=False)
    argparser.add_argument("--img", type=str)
    argparser.add_argument("--seed", type=int, default=opt["seed"])
    argparser.add_argument("--cfgscale", type=check_cfgscale, default=opt["scale"])
    argparser.add_argument("--steps", type=check_steps, default=opt["steps"])
    argparser.add_argument("prompt", nargs=argparse.REMAINDER)

    while not die.is_set():
        # Wait until a prompt becomes available, but regularly loop
        # to check whether the Event has happened yet.
        try:
            x = queue_prompts.get(timeout=1)
        except janus.SyncQueueEmpty:
            continue

        x = json.loads(x)
        print("Dequeued prompt: " + str(x))
        tic = time.time()

        try:
            args = argparser.parse_args(x["prompt"].split())
            args.prompt = ' '.join(args.prompt)
        except UsageException as ex:
            send_usage(queue_results, x["server"], x["channel"], x["user"], str(ex))
            continue
        except (ValueError, argparse.ArgumentError) as ex:
            send_error(queue_results, x["server"], x["channel"], x["user"], str(ex))
            continue
        except Exception as ex:
            print("ERROR: Unexpected exception: {}".format(str(ex)))
            continue


        print("Generating image...")
        if args.img is None:
            try:
                results = t2i.prompt2image(prompt=args.prompt, seed=args.seed, cfg_scale=args.cfgscale, steps=args.steps)
            except Exception as ex:
                send_error(queue_results, x["server"], x["channel"], x["user"], str(ex))
                continue
        else:
            url_parts = urlparse(args.img)
            x["prompt"] = "{}_{}".format(os.path.basename(unquote(url_parts.path)), args.prompt)
            x["img_url"] = args.img

            tmpfile = tempfile.NamedTemporaryFile()
            try:
                r = requests.get(args.img)
                # FIXME: Check that r.headers['content-type'] starts with 'image/'

                tmpfile.write(r.content)
                tmpfile.flush()
                tmpfile.seek(0)

                image = Image.open(tmpfile)
                image.thumbnail((opt["W"], opt["H"]), Image.Resampling.LANCZOS)

                tmpfile.seek(0)
                tmpfile.truncate(0)
                image.save(tmpfile, format="PNG")
                tmpfile.flush()
                tmpfile.seek(0)

                results = t2i.prompt2image(init_img=tmpfile.name, prompt=args.prompt, seed=args.seed, cfg_scale=args.cfgscale, steps=args.steps)
                tmpfile.close()
            except Exception as ex:
                tmpfile.close()
                print("Failed to fetch image: " + args.img)
                send_error(queue_results, x["server"], x["channel"], x["user"], str(ex))
                continue

        image = results[0][0]
        seed = results[0][1]

        mem_fp = io.BytesIO()
        image.save(mem_fp, format='png')
        img_b64 = base64.b64encode(mem_fp.getvalue()).decode()
        mem_fp.close()

        toc = time.time()

        packet = {
            "prompt": x["prompt"],
            "server": x["server"],
            "channel": x["channel"],
            "user": x["user"],
            "image": img_b64,
            "time": toc - tic
        }
        if args.img:
            packet["img_url"] = args.img
        queue_results.put(json.dumps(packet))
        print(f"Generation complete in {toc-tic} seconds, results queue size is {queue_results.qsize()}")

class Dreambot:
    def __init__(self, options):
        self.options = options
        self.websocket = None
        self.queue_prompts = None
        self.queue_results = None
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.die = asyncio.Event()

    async def run_websocket(self):
        async for self.websocket in websockets.connect(self.options["ws_uri"]):
            print("Websocket connected")
            try:
                async for message in self.websocket:
                    await self.queue_prompts.async_q.put(message)
                    print(f"Queued prompt (qsize {self.queue_prompts.async_q.qsize()}): {message}")
            except websockets.ConnectionClosed:
                continue

    async def run_stabdiff(self):
        print("Preparing ProcessPoolExecutor")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.pool, stabdiff, self.die, self.queue_prompts.sync_q,
                                       self.queue_results.sync_q, self.options)

    async def run_results(self):
        print("Watching for results")
        while not self.die.is_set():
            print("Awaiting next result...")
            result = await self.queue_results.async_q.get()
            result_x = json.loads(result)
            if "usage" in result_x:
                print("Sending usage")
            elif "error" in result_x:
                print("Sending error: {}".format(result_x["error"]))
            else:
                print("Sending result to {}:{}:{} for: {}".format(result_x["server"], result_x["channel"], result_x["user"], result_x["prompt"]))
            await self.websocket.send(result)
            print("Result sent")

    async def main(self):
        # loop = asyncio.get_running_loop()
        # loop.set_debug(True)

        print("Creating queues")
        self.queue_prompts: janus.Queue[str] = janus.Queue()
        self.queue_results: janus.Queue[str] = janus.Queue()

        print("Creating tasks")
        try:
            await asyncio.gather(
                self.run_websocket(),
                self.run_results(),
                self.run_stabdiff())
        except Exception as ex:
            print(f"arrgh i died: {ex}")
            print(traceback.print_exc())
            self.die.set()
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.queue_prompts.close()
            await self.queue_prompts.wait_closed()
            self.queue_results.close()
            await self.queue_results.wait_closed()
            print("i think it's all dead")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: {} <config.json>".format(sys.argv[0]))
        sys.exit(1)

    with open(sys.argv[1]) as f:
        opt = json.load(f)

    # Sample JSON config file:
    # {
    #    "seed": 42,
    #    "config": "configs/stable-diffusion/v1-inference.yaml",
    #    "model": "models/ldm/stable-diffusion-v1/model.ckpt",
    #    "sampler": "plms",
    #    "precision": "autocast",
    #    "full_precision": true,
    #    "scale": 7.5,
    #    "n_iter": 1,
    #    "steps": 50,
    #    "H": 512,
    #    "W": 512,
    #    "C": 4,
    #    "f": 8,
    #    "ws_uri": "wss://ws.server.com:9999/"
    # }

    bot = Dreambot(opt)
    asyncio.run(bot.main())
