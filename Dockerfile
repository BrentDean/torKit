# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG POETRY_VERSION=2.4.1

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install "poetry==${POETRY_VERSION}"

WORKDIR /opt/torkit/cli

COPY cli/pyproject.toml cli/poetry.lock ./
RUN poetry install \
    --only main \
    --no-root \
    --no-ansi

COPY cli/onionshare_cli ./onionshare_cli

RUN poetry install \
    --only main \
    --no-ansi

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG TORKIT_UID=10001
ARG TORKIT_GID=10001
ARG TORKIT_BUILD_ID=unknown

LABEL org.torkit.build-id="${TORKIT_BUILD_ID}"

ENV HOME=/home/torkit \
    PATH=/opt/torkit/cli/.venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TORKIT_RUNTIME_DIR=/var/lib/torkit \
    XDG_CONFIG_HOME=/var/lib/torkit/config

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
        tor \
        tor-geoipdb \
    && rm -rf /var/lib/apt/lists/*

# In rootless Docker, container UID/GID 0 map to the unprivileged daemon owner.
# This is needed for protected 0700 bind mounts owned by that host account.
RUN if [ "${TORKIT_UID}:${TORKIT_GID}" = "0:0" ]; then \
        :; \
    else \
        groupadd --gid "${TORKIT_GID}" torkit \
        && useradd \
            --uid "${TORKIT_UID}" \
            --gid "${TORKIT_GID}" \
            --home-dir "${HOME}" \
            --create-home \
            --shell /usr/sbin/nologin \
            torkit; \
    fi \
    && install -d \
        -o "${TORKIT_UID}" \
        -g "${TORKIT_GID}" \
        -m 0700 \
        "${HOME}" "${TORKIT_RUNTIME_DIR}"

COPY --from=builder --chown=${TORKIT_UID}:${TORKIT_GID} \
    /opt/torkit/cli \
    /opt/torkit/cli

COPY --chmod=0755 container/entrypoint.sh /usr/local/bin/torkit-entrypoint
COPY --chmod=0755 container/run-service.py /usr/local/lib/torkit/run-service.py
COPY --chmod=0755 container/healthcheck.py /usr/local/lib/torkit/healthcheck.py

WORKDIR /opt/torkit/cli

USER ${TORKIT_UID}:${TORKIT_GID}

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/torkit-entrypoint"]
CMD ["--help"]
