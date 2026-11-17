# syntax=docker/dockerfile:1
# Corpus-KB application server image (compose `app` profile; linux/amd64 NAS).
#
# Base is pinned to python:3.11-slim-bookworm (Debian 12) for the same reason
# the Postgres image pins postgres:17-bookworm: reproducible apt resolution on
# Synology Container Manager and any linux/amd64 Docker host.
#
# Build context is the repo ROOT (compose.yaml sets `context: .`); the
# per-Dockerfile ignore file (Dockerfile.dockerignore) keeps the context to
# the packaging minimum.
#
# PREREQUISITE: the built SPA must exist at src/corpus_kb/ui_dist/ BEFORE
# this build (ui_dist/ is gitignored, so a fresh checkout cannot ship it).
# CI runs `poe ui-build` ahead of the docker smoke; on a NAS run
#   pnpm -C ui_src install && pnpm -C ui_src build
# once first (Node is a build-time tool only). The wheel self-check below
# fails the build when the dist is missing — a UI-less image is never
# produced silently.
#
# Runtime shape:
#   - installs the built wheel (UI dist + packaged config + migrations ship
#     as package data; the wheel self-check below fails the build otherwise)
#   - runs as a non-root user; the JSON-RPC socket lands in /tmp
#   - entrypoint waits for Postgres, runs migrations, then serves HTTP
#     on 0.0.0.0:${CORPUS_KB_PORT:-8010}

FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# --no-deps: only this package's wheel is built here; the runtime stage
# resolves the dependencies from PyPI when it installs the wheel.
# The pip download cache rides a BuildKit cache mount, so it survives
# rebuilds and never lands in an image layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --no-deps --wheel-dir /wheels .

# Wheel self-check (zero-drift: the built UI dist, the packaged config,
# the base schema, and the migrations must all ride along; a bare wheel
# means a broken NAS deploy with no UI, no schema, and a silent no-op
# `corpus-kb migrate`). 004/005 are pinned because they create the AGE
# graph and the PostgresML bindings; the glob count catches a package-data
# regression that drops every migration. Plain python -c keeps this
# working on any BuildKit frontend.
RUN python -c "import glob, sys, zipfile; \
names = zipfile.ZipFile(glob.glob('/wheels/corpus_kb-*.whl')[0]).namelist(); \
required = ('corpus_kb/ui_dist/index.html', 'corpus_kb/config.yaml', 'corpus_kb/storage/schema.sql', 'corpus_kb/migrations/004_enable_age.sql', 'corpus_kb/migrations/005_enable_pgml.sql'); \
migrations = [n for n in names if n.startswith('corpus_kb/migrations/') and n.endswith('.sql')]; \
missing = [p for p in required if p not in names] + ([] if migrations else ['corpus_kb/migrations/*.sql']); \
sys.exit('wheel is missing: ' + ', '.join(missing)) if missing else print('wheel self-check ok: ui_dist + config.yaml + schema.sql + %d migration files' % len(migrations))"

FROM python:3.11-slim-bookworm

# No PIP_NO_CACHE_DIR here: the install below uses a BuildKit cache mount.
# The mount keeps downloaded wheels between rebuilds and keeps them OUT of
# the image layers, so the shipped image stays as slim as before.
ENV PYTHONUNBUFFERED=1

# libmagic1: the `magic` module behind unstructured partitioning loads it on
# Debian (the CI Windows equivalent is python-magic-bin). No git, no curl:
# the healthcheck and entrypoint use python only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /wheels/corpus_kb-*.whl \
 && rm -rf /wheels

# Non-root by default (Synology security hygiene). UID is fixed so a bind
# mount stays predictable; the home dir holds nothing durable (state lives
# in Postgres).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --chmod=755 docker/app/entrypoint.sh /usr/local/bin/entrypoint.sh

USER appuser
WORKDIR /home/appuser

EXPOSE 8010

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
