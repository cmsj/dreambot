import websockets
import asyncio
import json
import io
import base64

import time
from PIL import Image
from ldm.simplet2i import T2I

class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self

async def run_model():
    opt = AttrDict()
    opt.seed = 42
    opt.config = "configs/stable-diffusion/v1-inference.yaml"
    opt.model = "models/ldm/stable-diffusion-v1/model.ckpt"
    opt.plms = True
    opt.precision = "autocast"
    opt.scale = 7.5
    opt.n_samples = 1
    opt.n_rows = 1
    opt.n_iter = 1
    opt.ddim_eta = 0.0
    opt.ddim_steps = 50
    opt.H = 512
    opt.W = 512
    opt.C = 4
    opt.f = 8
    opt.ws_uri = "wss://jump.tenshu.net:9999/"

    t2i = T2I(weights=opt.model, config=opt.config, iterations=opt.n_iter, seed=opt.seed, grid=False, width=opt.W, height=opt.H, cfg_scale=opt.scale)
    t2i.load_model()

    print(f"Connecting to {opt.ws_uri}")
    async for websocket in websockets.connect(opt.ws_uri):
        try:
            async for x in websocket:
                x = json.loads(x)
                print("Generating for: " + str(x))
                tic = time.time()

                results = t2i.prompt2image(prompt=x["prompt"], ourdir = "./outputs/")

                image = results[0][0]
                seed = results[0][1]

                mem_fp = io.BytesIO()
                image.save(mem_fp, format='png')
                toc = time.time()

                packet = {
                    "prompt": x["prompt"],
                    "channel": x["channel"],
                    "user": x["user"],
                    "image": base64.b64encode(mem_fp.getvalue()).decode(),
                    "time": toc - tic
                }
                print(f"Generation complete in {toc-tic} seconds. Sending reply.")
                await websocket.send(json.dumps(packet))
        except websockets.ConnectionClosed:
            continue

if __name__ == "__main__":
    asyncio.run(run_model())
