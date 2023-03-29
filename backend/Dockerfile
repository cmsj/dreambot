#FROM invokeai/invokeai:main-cuda
FROM python:3.10
LABEL org.opencontainers.image.authors="cmsj@tenshu.net"
WORKDIR /app
VOLUME [ "/config", "/data" ]

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY dreambot_backend.py dreambot_backend.py

CMD [ "python3", "/app/dreambot_backend.py", "/config/config.json" ]