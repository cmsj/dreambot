FROM python:3.10
LABEL org.opencontainers.image.authors="cmsj@tenshu.net"
WORKDIR /app
VOLUME [ "/config" ]

COPY . /app
RUN pushd /app ; \
    pip3 install . ; \
    popd ; \
    rm -rf /app ;\
    rm -rf /root/.cache
