import websockets
import asyncio
import functools
import janus
import json
import time
import io
import base64
import concurrent.futures

from PIL import Image
from ldm.simplet2i import T2I

def stabdiff(queue_prompts, queue_results, opt):
    print("Stable Diffusion booting...")
    t2i = T2I(weights=opt["model"], config=opt["config"], iterations=opt["n_iter"],
              seed=opt["seed"], grid=False, width=opt["W"], height=opt["H"], 
              cfg_scale=opt["scale"], sampler_name=opt["sampler"], 
              precision=opt["precision"], full_precision=opt["full_precision"])
    t2i.load_model()
    print("Stable Diffusion booted")

    while True:
        x = queue_prompts.get()
        x = json.loads(x)
        print("Generating for: " + str(x))
        tic = time.time()

        results = t2i.prompt2image(prompt=x["prompt"], ourdir = "./outputs/")

        image = results[0][0]
        seed = results[0][1]

        mem_fp = io.BytesIO()
        image.save(mem_fp, format='png')
        img_b64 = base64.b64encode(mem_fp.getvalue()).decode()
        mem_fp.close()

        toc = time.time()
        print(f"Generation complete in {toc-tic} seconds.")

        packet = {
            "prompt": x["prompt"],
            "channel": x["channel"],
            "user": x["user"],
            "image": img_b64,
            "time": toc - tic
        }
        queue_results.put(json.dumps(packet))
        queue_prompts.task_done()

class Dreambot:
    def __init__(self, options):
        self.options = options
        self.websocket = None
        self.queue_prompts = None
        self.queue_results = None
    
    async def run_websocket(self):
        async for self.websocket in websockets.connect(self.options["ws_uri"]):
            print("Websocket connected")
            try:
                async for message in self.websocket:
                    print("Received message: " + message)
                    await self.queue_prompts.async_q.put(message)
            except websockets.ConnectionClosed:
                continue

    async def run_stabdiff(self):
        print("Preparing ProcessPoolExecutor")
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            await loop.run_in_executor(pool, stabdiff, self.queue_prompts.sync_q, 
                                       self.queue_results.sync_q, self.options)
    
    async def run_results(self):
        print("Watching for results")
        while True:
            result = await self.queue_results.async_q.get()
            result_x = json.loads(result)
            print("Sending result for: " + result_x["prompt"])
            await self.websocket.send(result)
            self.queue_results.task_done()

    async def main(self):
        print("Creating queues")
        self.queue_prompts: janus.Queue[str] = janus.Queue()
        self.queue_results: janus.queue[str] = janus.Queue()

        print("Creating tasks")
        task_websocket = asyncio.create_task(self.run_websocket())
        task_results = asyncio.create_task(self.run_results())
        task_stabdiff = asyncio.create_task(self.run_stabdiff())

        await task_stabdiff
        await task_websocket
        await task_results

        self.queue_prompts.close()
        await self.queue_prompts.wait_closed()

        self.queue_results.close()
        await self.queue_results.wait_closed()

if __name__ == "__main__":
    opt = {
        'seed': 42,
        'config': "configs/stable-diffusion/v1-inference.yaml",
        'model': "models/ldm/stable-diffusion-v1/model.ckpt",
        'sampler': 'plms',
        'plms': True,
        'precision': "autocast",
        'full_precision': True,
        'scale': 7.5,
        'n_samples': 1,
        'n_rows': 1,
        'n_iter': 1,
        'ddim_eta': 0.0,
        'ddim_steps': 50,
        'H': 512,
        'W': 512,
        'C': 4,
        'f': 8,
        'ws_uri': "wss://jump.tenshu.net:9999/"
    }

    bot = Dreambot(opt)
    asyncio.run(bot.main())