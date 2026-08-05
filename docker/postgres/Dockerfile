# Custom PostgreSQL 17 image for Corpus-KB.
#
# Bundles the three Postgres extensions required by the
# postgres-ecosystem-unification plan:
#   * pgvector     (vector indexing + similarity search)
#   * Apache AGE   (openCypher graph queries)
#   * PostgresML   (in-database embeddings, reranking, ONNX NER)
#
# Built on the official postgres:17-bookworm image. PGDG provides
# postgresql-17-pgvector and postgresql-17-age; pgml packages live at
# apt.postgresml.org and provide postgresql-17-pgml.
#
# No GPU / cloud / pgai dependencies are required. CPU-only runtime is the
# default; the pgml extension downloads models on demand into the container.
FROM postgres:17-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# Install PGDG key once; the official postgres image already ships PGDG,
# but we re-add the key explicitly so this Dockerfile stays self-contained.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /etc/apt/trusted.gpg.d/apt.postgresql.org.gpg \
 && echo "deb [signed-by=/etc/apt/trusted.gpg.d/apt.postgresql.org.gpg] \
        http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list

# Add PostgresML APT repository (trusted, no separate GPG key required).
RUN echo "deb [trusted=yes] https://apt.postgresml.org $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/postgresml.list

# Install the extensions. The exact package names map to the installed PG major.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        postgresql-17-pgvector \
        postgresql-17-age \
        postgresql-17-pgml \
 && rm -rf /var/lib/apt/lists/*

# pgml requires shared_preload_libraries to include 'pgml' so its background
# worker can start. AGE recommends adding 'age' as well. This edits the sample
# used by the official image's initdb-created cluster.
RUN sed -i "s/#shared_preload_libraries = ''/shared_preload_libraries = 'pgml,age'/" \
        /usr/share/postgresql/postgresql.conf.sample

# Default postgres entrypoint remains; see docker-compose.yml for env vars.
