# Custom PostgreSQL 17 image for Corpus-KB.
# Multi-stage build: builder compiles PostgresML from source; runtime copies artifacts.
FROM postgres:17 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/root/.cargo/bin:${PATH}"

ARG POSTGRESML_VERSION=2.10.0
ARG PGRX_VERSION=0.12.9

# Build dependencies. PostgresML 2.10.0 links against libpython3.10 (pyo3
# abi3-py310 feature), but the official postgres:17 image is Debian Bookworm
# and ships Python 3.11. We therefore build Python 3.10 from source and set
# PYTHON so cargo-pgrx finds the correct interpreter/libraries.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential pkg-config cmake libssl-dev libreadline-dev \
        zlib1g-dev python3-dev python3-pip libopenblas-dev \
        libclang-dev clang lld git curl ca-certificates gnupg lsb-release \
        postgresql-server-dev-17 \
        libffi-dev libncurses-dev libsqlite3-dev libbz2-dev \
        liblzma-dev uuid-dev libgdbm-dev \
 && rm -rf /var/lib/apt/lists/*

# Install Rust via rustup (stable toolchain).
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable

# Install Python 3.10 from source for the PostgresML build.
RUN curl -fsSL https://www.python.org/ftp/python/3.10.16/Python-3.10.16.tgz \
    | tar -xzf - -C /tmp \
 && cd /tmp/Python-3.10.16 \
 && ./configure --prefix=/usr/local/python3.10 --enable-shared --enable-optimizations \
 && make -j"$(nproc)" \
 && make altinstall \
 && ldconfig \
 && rm -rf /tmp/Python-3.10.16
ENV PYTHON=/usr/local/python3.10/bin/python3.10
ENV PYTHON_LIB=/usr/local/python3.10/lib
ENV LD_LIBRARY_PATH="/usr/local/python3.10/lib:${LD_LIBRARY_PATH}"

# Install cargo-pgrx and initialize PG17.
RUN cargo install cargo-pgrx --version ${PGRX_VERSION} --locked \
 && cargo pgrx init --pg17=/usr/lib/postgresql/17/bin/pg_config

# Clone and build PostgresML from source.
WORKDIR /build
RUN git clone --depth 1 --branch v${POSTGRESML_VERSION} https://github.com/postgresml/postgresml.git \
 && cd postgresml/pgml-extension \
 && cargo pgrx install --release --pg-config=/usr/lib/postgresql/17/bin/pg_config

# ─────────────────────────────────────────────────────────────────
# Runtime stage
# ─────────────────────────────────────────────────────────────────
FROM postgres:17

ENV DEBIAN_FRONTEND=noninteractive

ARG PGVECTOR_VERSION=0.8.6-1.pgdg12+1
ARG AGE_VERSION=1.7.0~rc0-1.pgdg12+1
ARG POSTGRESML_VERSION=2.10.0

# Add PGDG apt repository and install pinned pgvector + AGE.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /etc/apt/trusted.gpg.d/apt.postgresql.org.gpg \
 && echo "deb [signed-by=/etc/apt/trusted.gpg.d/apt.postgresql.org.gpg] \
        http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        postgresql-17-pgvector=${PGVECTOR_VERSION} \
        postgresql-17-age=${AGE_VERSION} \
        libpython3.11 libopenblas0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Copy PostgresML artifacts from builder.
COPY --from=builder /usr/lib/postgresql/17/lib/pgml.so /usr/lib/postgresql/17/lib/
COPY --from=builder /usr/share/postgresql/17/extension/pgml.control /usr/share/postgresql/17/extension/
COPY --from=builder /usr/share/postgresql/17/extension/pgml--*.sql /usr/share/postgresql/17/extension/

# Copy Python 3.10 shared library in case pgml.so links against it at load time.
COPY --from=builder /usr/local/python3.10/lib/libpython3.10.so* /usr/local/lib/
RUN ldconfig

# pgml + AGE must be preloaded.
RUN sed -i "s/#shared_preload_libraries = ''/shared_preload_libraries = 'pgml,age'/" \
        /usr/share/postgresql/postgresql.conf.sample

# Default postgres entrypoint remains; see compose.yaml for env vars.
