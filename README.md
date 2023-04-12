# Dreambot
by Chris Jones <cmsj@tenshu.net>

## What is it?
Dreambot is a distributed architecture for running chat bots.

There are two types of process in Dreambot - frontends and backends. Frontends are intended to communicate directly with users (e.g. via IRC) and backends implement the heavy/remote tasks requested by users, for example running a machine learning task on a GPU.

These processes hand of requests for work and the responses, via a job queue that is provided by a separate service, [NATS](https://nats.io).
