# W0: Postgres + pgvector Setup Guide

## Prerequisites

You need Postgres 13+ with the pgvector extension. Neither Postgres nor Docker is currently installed.

## Option A: Native Postgres Install (Recommended)

### 1. Install Postgres 16

Download and install from: https://www.postgresql.org/download/windows/

Or use Chocolatey:
```powershell
choco install postgresql16 -y
```

### 2. Install pgvector

After Postgres is installed:
```powershell
# Download pgvector
git clone https://github.com/pgvector/pgvector.git
cd pgvector

# Build (requires Visual Studio Build Tools with C++ workload)
nmake /F Makefile.win
nmake /F Makefile.win install
```

Or if you don't have build tools, use the pre-built package:
```powershell
# If you installed via Chocolatey, pgvector may already be included
# Otherwise, download from: https://github.com/pgvector/pgvector/releases
```

### 3. Create Database and User

```powershell
# Start psql as postgres superuser
psql -U postgres

# In psql:
CREATE DATABASE corpus_kb;
CREATE USER corpus_user WITH PASSWORD 'corpus_pass';
GRANT ALL PRIVILEGES ON DATABASE corpus_kb TO corpus_user;
\c corpus_kb
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO corpus_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO corpus_user;
\q
```

### 4. Verify

```powershell
psql -U corpus_user -d corpus_kb -c "SELECT 1;"
psql -U corpus_user -d corpus_kb -c "SELECT * FROM pg_extension WHERE extname='vector';"
```

## Option B: Docker (if you prefer containerized)

Install Docker Desktop for Windows first, then:
```powershell
docker run -d --name corpus-postgres -e POSTGRES_PASSWORD=corpus_pass -e POSTGRES_DB=corpus_kb -e POSTGRES_USER=corpus_user -p 5432:5432 pgvector/pgvector:pg16
```

## Connection String

After setup, your connection string is:
```
postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
```

Add to `config.yaml`:
```yaml
database:
  connection_string: "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
```

Or set env var:
```
CORPUS_KB_DATABASE_URL=postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb
```

## Next Steps

Once Postgres is running, W1 can proceed (schema creation, event store app, etc.).