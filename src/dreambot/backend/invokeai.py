import asyncio
import base64
import json
import requests
import socketio

from dreambot.backend.base import DreambotBackendBase

class DreambotBackendInvokeAI(DreambotBackendBase):
    backend_name = "InvokeAI"
    sio = None
    invokeai_host = None
    invokeai_port = None
    ws_uri = None
    api_uri = None
    request_cache = None

    def __init__(self, nats_options, invokeai_options):
        super().__init__(nats_options)
        self.invokeai_host = invokeai_options["host"]
        self.invokeai_port = invokeai_options["port"]
        self.logger.debug("Set InvokeAI options to: {}".format(invokeai_options))
        self.request_cache = {}

    async def boot(self):
        self.ws_uri = "ws://{}:{}/".format(self.invokeai_host, self.invokeai_port)
        self.api_uri = "http://{}:{}/api/v1/".format(self.invokeai_host, self.invokeai_port)
        self.logger.info("InvokeAI API URI: {}".format(self.api_uri))
        self.logger.info("Connecting to InvokeAI socket.io at {}".format(self.ws_uri))
        self.sio = socketio.Client(reconnection_delay_max=10)

        @self.sio.event
        def connect():
            self.logger.info("Connected to InvokeAI socket.io")
        @self.sio.event
        def disconnect():
            self.logger.info("Disconnected from InvokeAI socket.io")
        @self.sio.event
        def invocation_complete(data):
            id = data["graph_execution_state_id"]

            self.logger.info("Invocation complete: {}".format(id))
            self.logger.debug("Invocation complete data: {}".format(data))

            self.logger.info("Unsubscribing from InvokeAI session: {}".format(id))
            self.sio.emit('unsubscribe', {'session': id})

            request = self.request_cache[id]
            request.pop("reply-none", None) # We likely have a reply-none from when we first replied to this request, so remove it

            r = requests.get(self.api_uri + "images/results/{}".format(data["result"]["image"]["image_name"]))
            if r.status_code != 200:
                self.logger.error("Error POSTing session to InvokeAI: {}".format(r.reason))
                request["error"] = "Error from InvokeAI: {}".format(r.reason)
                return data
            else:
                # FIXME: There is a 1MB limit on NATS messages. We should instead write to disk here and merely send a URL over NATS
                request["reply-image"] = base64.b64encode(r.content).decode('utf8')

            self.logger.debug("Sending image response to queue '{}': for {} <{}> {}".format(request["reply-to"], request["channel"], request["user"], request["prompt"]))

            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.nats.publish(request["reply-to"], json.dumps(request).encode()))
            loop.run_until_complete(self.nats.flush())
            loop.close()
            self.logger.debug("Sent")

        def invokeai_callback(data):
            try:
                prompt = data["prompt"]
                if not self.sio.connected:
                    data["error"] = "InvokeAI backend not connected"
                    return data

                self.logger.info("Sending prompt to InvokeAI: {}".format(prompt))
                id = 1
                nodes = {}
                nodes[str(id)] = {
                    "id": str(id),
                    "type": "txt2img",
                    "prompt": prompt,
                    "model": "stable-diffusion-1.5",
                    "sampler": "keuler_a",
                    "steps": 50,
                    "seed": -1
                }
                id += 1
                nodes[str(id)] = {
                    "id": str(id),
                    "type": "show_image",
                }
                links = [
                    {"source": { "node_id": "1", "field": "image" },
                     "destination": { "node_id": "2", "field": "image" }}
                ]
                graph = {
                    "nodes": nodes,
                    "edges": links
                }
                self.logger.debug("Sending graph to InvokeAI: {}".format(graph))

                r = requests.post(self.api_uri + "sessions", json=graph)
                if r.status_code != 200:
                    self.logger.error("Error POSTing session to InvokeAI: {}".format(r.reason))
                    data["error"] = "Error from InvokeAI: {}".format(r.reason)
                    return data

                response = r.json()
                self.request_cache[response["id"]] = data
                self.logger.debug("InvokeAI response: {}".format(response))

                self.logger.info("Subscribing to InvokeAI session and invoking: {}".format(response["id"]))
                self.sio.emit('subscribe', {'session': response["id"]})
                r = requests.put(self.api_uri + "sessions/{}/invoke".format(response["id"]))
                if r.status_code != 202:
                    self.logger.error("Error PUTing session to InvokeAI: {}".format(r.reason))
                    data["error"] = "Error from InvokeAI: {}".format(r.reason)
                    return data

                # No more work to do here, InvokeAI will send us a message when it's done
                data["reply-none"] = "Waiting for InvokeAI to generate a response..."
            except Exception as e:
                self.logger.error("Unknown error: {}".format(e))
                data["error"] = "Unknown error, ask your bot admin to check logs."
            return data

        self.sio.connect(self.ws_uri, socketio_path="/ws/socket.io")
        await super().boot(invokeai_callback)