FROM invokeai/invokeai:main-cuda
VOLUME [ "/config", "/data" ]
COPY dreambot_backend.py /
COPY --chmod=644 entrypoint.sh /
ENTRYPOINT [ "/entrypoint.sh" ]