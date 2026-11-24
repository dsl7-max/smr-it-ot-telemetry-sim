# Citadel Plant Floor (PC #2, Purdue L0-1): reactor model + Virtual PLC (Modbus TCP :5020, OPC UA :4840).
# Build context = repo root:  docker build -f simulators/Dockerfile -t citadel/plant:dev .
# Air-gapped target: everything is installed at build time; the running container never downloads anything.
# Base image pinned by multi-arch index digest (US-206); tag kept for readability. Bump both together.

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# --- build stage: resolve the locked dependency set into a self-contained venv --------------------
FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt constraints.txt /tmp/deps/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --only-binary=:all: -r /tmp/deps/requirements.txt

# --- runtime stage: no pip cache, no build files, non-root --------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
# Set by CI (compose build args): source links the ghcr.io package to the repo; revision = git commit.
ARG IMAGE_SOURCE=""
ARG IMAGE_REVISION=""
LABEL org.opencontainers.image.title="citadel-plant" \
      org.opencontainers.image.description="Citadel reactor simulator + Virtual PLC (Modbus TCP, OPC UA)" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${IMAGE_REVISION}"

RUN groupadd --system --gid 10001 citadel \
 && useradd --system --uid 10001 --gid citadel --no-create-home --shell /usr/sbin/nologin citadel

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root simulators/ ./simulators/

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLC_BIND_HOST=0.0.0.0 \
    PLC_MODBUS_PORT=5020 \
    PLC_OPCUA_PORT=4840 \
    SIM_TICK_SECONDS=1.0

# Unprivileged ports; compose publishes 5020 as host 502.
EXPOSE 5020/tcp 4840/tcp
USER 10001:10001

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "simulators.virtual_plc.healthcheck"]

CMD ["python", "-m", "simulators.virtual_plc"]
