# Build from the repo root: `docker build -f agents/seo-workbook-agent/Dockerfile .`
# (needs repo-root context so it can COPY the shared/ package in.)
#
# Internal image paths (./common, ./agent_service) are kept as simple flat
# staging directories regardless of the repo's own folder names on the
# left of each COPY — they only exist long enough for `pip install`, so
# there's no need for them to mirror the monorepo layout.
FROM python:3.11-slim

WORKDIR /app

COPY shared/ ./common/
COPY agents/seo-workbook-agent/ ./agent_service/

RUN pip install --no-cache-dir ./common ./agent_service

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "seo_workbook_agent.main"]
