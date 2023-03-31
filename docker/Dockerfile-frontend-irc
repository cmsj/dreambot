FROM python:3.10
LABEL org.opencontainers.image.authors="cmsj@tenshu.net"
WORKDIR /app
VOLUME [ "/config" ]

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY dreambot_frontend_irc.py dreambot_frontend_irc.py

CMD [ "python3", "/app/dreambot_frontend_irc.py", "/config/config.json" ]