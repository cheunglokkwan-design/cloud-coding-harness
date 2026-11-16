# Prebuilt sandbox image. Everything an agent commonly needs is baked in so that a sandbox
# never installs toolchains at startup. Extend this image (FROM cch-sandbox) for projects
# that need more (Go, Rust, JDK, ...), and pre-install their dependencies here as well.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep ca-certificates coreutils procps make nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && pip install pytest==8.3.5 ruff==0.11.13 \
 && git config --system --add safe.directory '*' \
 && git config --system user.name harness-agent \
 && git config --system user.email agent@harness.local \
 && useradd --uid 1000 --create-home agent

USER 1000
WORKDIR /workspace
CMD ["sleep", "infinity"]
