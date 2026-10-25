FROM node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d
RUN apt-get update && apt-get install -y --no-install-recommends git python3 python3-venv ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/harness && /opt/harness/bin/pip install --no-cache-dir 'openai>=1.0'
ENV PATH="/opt/harness/bin:$PATH"
COPY repo.bundle /tmp/repo.bundle
RUN git clone /tmp/repo.bundle /app && git -C /app checkout --detach 04a20fdd5ea3b5ad700e451aa4726f8fae78f96b && rm /tmp/repo.bundle && git -C /app remote remove origin
WORKDIR /app
RUN npm ci --no-audit --no-fund
RUN git config user.email eval@local && git config user.name eval
