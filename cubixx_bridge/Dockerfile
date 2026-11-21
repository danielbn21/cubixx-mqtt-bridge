FROM ghcr.io/home-assistant/base:latest

# Python + a virtualenv. Build tools are only needed if a dependency has no
# prebuilt wheel for this architecture, and are removed again afterwards.
COPY requirements.txt /tmp/requirements.txt
RUN \
    apk add --no-cache python3 py3-pip \
    && apk add --no-cache --virtual .build-deps build-base python3-dev libffi-dev \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && apk del .build-deps \
    && rm -f /tmp/requirements.txt

ENV PATH="/opt/venv/bin:${PATH}"

COPY app /srv/app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

CMD [ "/run.sh" ]
