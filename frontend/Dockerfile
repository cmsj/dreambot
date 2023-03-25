FROM python:3.10
LABEL org.opencontainers.image.authors="cmsj@tenshu.net"
WORKDIR /app
VOLUME [ "/config" ]

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY dreambot_frontend.py dreambot_frontend.py

CMD [ "python3", "/app/dreambot_frontend.py", "/config/config.json" ]