# syntax=docker/dockerfile:1.7
# --- web UI -------------------------------------------------------------------------------------------
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- current SQLite ---------------------------------------------------------------------------------------
# Debian's libsqlite3 predates the WAL-reset fix (3.51.3 / 3.50.7 / 3.44.6). Build a current one so the ledger
# can run in WAL mode safely (concurrent readers, online backups). Python's _sqlite3 picks it up via ldconfig.
FROM python:3.12-slim AS sqlite
ARG SQLITE_TARBALL=https://www.sqlite.org/2026/sqlite-autoconf-3530100.tar.gz
ARG SQLITE_SHA256=83e6b2020a034e9a7ad4a72feea59e1ad52f162e09cbd26735a3ffb98359fc4f
RUN apt-get update && apt-get install -y --no-install-recommends gcc make libc6-dev curl ca-certificates \
 && curl -fsSL "$SQLITE_TARBALL" -o /tmp/sqlite.tgz && echo "$SQLITE_SHA256  /tmp/sqlite.tgz" | sha256sum -c - && mkdir /tmp/sqlite && tar xzf /tmp/sqlite.tgz -C /tmp/sqlite --strip-components=1 \
 && cd /tmp/sqlite && CFLAGS="-O2 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_JSON1 -DSQLITE_ENABLE_COLUMN_METADATA" ./configure --prefix=/opt/sqlite \
 && make -j"$(nproc)" && make install

# --- server -------------------------------------------------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    LITLEDGER_DATA=/data LITLEDGER_HOST=0.0.0.0 LITLEDGER_PORT=8765
COPY --from=sqlite /opt/sqlite/lib/ /usr/local/lib/
RUN ldconfig && python -c "import sqlite3; v=sqlite3.sqlite_version_info; print('sqlite', sqlite3.sqlite_version); assert v >= (3, 51, 3)"
WORKDIR /app
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
COPY --from=web /web/dist ./src/litledger/web
RUN pip install --no-cache-dir -c constraints.txt . \
 && useradd --create-home --uid 10001 litledger \
 && mkdir -p /data /backups && chown litledger:litledger /data /backups
USER litledger
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=4).status==200 else 1)"
CMD ["litledger", "serve"]
