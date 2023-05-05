"""Modules implementing backends for Dreambot.

Backends implement the actual functionality of Dreambot. They listen for messages from frontends via NATS and perform the requested actions.
They can either be high-resource, long-running tasks (e.g. ML image generation) or low-resource, short-lived tasks (e.g. an OpenAI API call).
"""
