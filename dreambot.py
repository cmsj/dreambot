import argparse, os, sys, glob
import torch
import numpy as np
from omegaconf import OmegaConf
from PIL import Image
from tqdm import tqdm, trange
from itertools import islice
from einops import rearrange
from torchvision.utils import make_grid
import time
from pytorch_lightning import seed_everything
from torch import autocast
from contextlib import contextmanager, nullcontext

from ldm.util import instantiate_from_config
from ldm.models.diffusion.ddim import DDIMSampler
from ldm.models.diffusion.plms import PLMSSampler

import websockets
import asyncio
import json
import io
import base64

class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self

def chunk(it, size):
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())


def load_model_from_config(config, ckpt, verbose=False):
    print(f"Loading model from {ckpt}")
    pl_sd = torch.load(ckpt, map_location="cpu")
    if "global_step" in pl_sd:
        print(f"Global Step: {pl_sd['global_step']}")
    sd = pl_sd["state_dict"]
    model = instantiate_from_config(config.model)
    m, u = model.load_state_dict(sd, strict=False)
    if len(m) > 0 and verbose:
        print("missing keys:")
        print(m)
    if len(u) > 0 and verbose:
        print("unexpected keys:")
        print(u)

    model.to(device='mps')
    model.eval()
    return model


async def run_model():
    opt = AttrDict()
    opt.seed = 42
    opt.config = "configs/stable-diffusion/v1-inference.yaml"
    opt.ckpt = "models/ldm/stable-diffusion-v1/model.ckpt"
    opt.plms = True
    opt.n_samples = 1
    opt.n_rows = 1
    opt.n_iter = 1
    opt.from_file = False
    opt.ws_uri = "ws://jump.tenshu.net:9999/"

    seed_everything(opt.seed)

    config = OmegaConf.load(f"{opt.config}")
    model = load_model_from_config(config, f"{opt.ckpt}")

    device = torch.device("mps")
    model = model.to(device)
    sampler = PLMSSampler(model)

    async for websocket in websockets.connect(opt.ws_uri):
        try:
            async for x in websocket:
                if isinstance(x, websockets.StreamNone):
                    continue
                elif not x or isinstance(x, websockets.StreamEnd):
                    break
                
                x = json.loads(x)
                start_code = None
                precision_scope = autocast if opt.precision=="autocast" else nullcontext
                with torch.no_grad():
                    with precision_scope("cuda"):
                        with model.ema_scope():
                            tic = time.time()
                            all_samples = list()
                            uc = None
                            if opt.scale != 1.0:
                                uc = model.get_learned_conditioning([""])
                            prompts = [x["prompt"]]
                            c = model.get_learned_conditioning(prompts)
                            shape = [opt.C, opt.H // opt.f, opt.W // opt.f]
                            samples_ddim, _ = sampler.sample(S=opt.ddim_steps,
                                                                conditioning=c,
                                                                batch_size=opt.n_samples,
                                                                shape=shape,
                                                                verbose=False,
                                                                unconditional_guidance_scale=opt.scale,
                                                                unconditional_conditioning=uc,
                                                                eta=opt.ddim_eta,
                                                                x_T=start_code)

                            x_samples_ddim = model.decode_first_stage(samples_ddim)
                            x_samples_ddim = torch.clamp((x_samples_ddim + 1.0) / 2.0, min=0.0, max=1.0)

                            x_sample = x_samples_ddim[0]
                            x_sample = 255. * rearrange(x_sample.cpu().numpy(), 'c h w -> h w c')
                            mem_fp = io.BytesIO()
                            Image.fromarray(x_sample.astype(np.uint8)).save(mem_fp, format='png')
                            toc = time.time()

                            packet = {
                                "prompt": prompts[0],
                                "channel": x["channel"],
                                "user": x["user"],
                                "image": base64.b64encode(mem_fp.getvalue()).decode(),
                                "time": toc - tic
                            }

                            websocket.send(json.dumps(packet))
        except websockets.ConnectionClosed:
            continue

if __name__ == "__main__":
    asyncio.run(boot_model())
