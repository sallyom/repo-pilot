FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ src/

RUN uv pip install --system --no-cache .

FROM python:3.12-slim

COPY --from=builder /usr/local /usr/local

# kubectl for hands-on k8s work (user mounts ~/.kube/config)
ADD https://dl.k8s.io/release/v1.32.3/bin/linux/amd64/kubectl /usr/local/bin/kubectl
RUN chmod +x /usr/local/bin/kubectl

# Repos get mounted here
RUN mkdir /repos
WORKDIR /repos

ENV REPO_PILOT_LLM_API_KEY=""
ENV REPO_PILOT_LLM_MODEL="claude-sonnet-4-6"
ENV REPO_PILOT_LLM_PROVIDER="auto"

ENTRYPOINT ["repo-pilot"]
