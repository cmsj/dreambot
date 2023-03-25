FROM invokeai/invokeai:main-cuda
LABEL org.opencontainers.image.authors="cmsj@tenshu.net"
WORKDIR /app
VOLUME [ "/config", "/data" ]

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY dreambot_backend.py dreambot_backend.py
COPY --chmod=644 entrypoint.sh entrypoint.sh

ENTRYPOINT [ "/app/entrypoint.sh" ]