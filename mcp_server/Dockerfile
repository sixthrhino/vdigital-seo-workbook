# Build from the repo root: `docker build -f mcp_server/Dockerfile .`
# (needs repo-root context so it can COPY the shared common/ package in.)
FROM python:3.11-slim

# WeasyPrint (seo-workbook-common's `output` extra) needs Pango/Cairo/
# GDK-Pixbuf at runtime to rasterize the PDF summary — these aren't
# optional, WeasyPrint fails to import without them.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi8 \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY common/ ./common/
COPY mcp_server/ ./mcp_server/
COPY data/ ./data/

RUN pip install --no-cache-dir "./common[output]" ./mcp_server

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "seo_workbook_mcp.app"]
