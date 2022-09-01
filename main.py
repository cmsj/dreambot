#!/usr/bin/env python

import asyncio
import websockets

async def receive(websocket):
  async for message in websocket:
    print(message)

async def main():
  async with websockets.serve(receive, "localhost", 9999):
    await asyncio.Future()

if __name__ == "__main__":
  asyncio.run(main())

