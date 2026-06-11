# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Run as a non-root user (see the chart's securityContext). No build toolchain is
# needed: all dependencies ship as wheels.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# Give the numeric UID a real /etc/passwd entry. kopf calls getpass.getuser()
# on startup, which falls back to pwd.getpwuid(); without an entry for 65532 it
# raises "uid not found" and the operator crashes before it ever reconciles.
RUN echo 'nonroot:x:65532:65532:nonroot:/home/nonroot:/usr/sbin/nologin' >> /etc/passwd \
    && mkdir -p /home/nonroot \
    && chown 65532:65532 /home/nonroot
ENV HOME=/home/nonroot

# Drop privileges. UID matches the chart's runAsNonRoot expectation.
USER 65532:65532

# Kopf is the operator runtime. -A watches all namespaces (cluster-wide);
# --standalone runs without peering (a single replica owns reconciliation);
# --liveness exposes /healthz for the Kubernetes probes.
ENTRYPOINT ["kopf", "run", \
    "--standalone", \
    "-A", \
    "--liveness=http://0.0.0.0:8080/healthz", \
    "-m", "adomi_platform_controller.operator"]