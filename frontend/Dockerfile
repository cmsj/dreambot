FROM python:3.10
LABEL org.opencontainers.image.authors="cmsj@tenshu.net"
WORKDIR /app
VOLUME [ "/config" ]

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

CMD [ "python3", "dreambot_frontend.py", "/config/config.json" ]

