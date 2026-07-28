# Build from the repo root: `docker build -f mcp-servers/seo-testing-mcp/Dockerfile .`
FROM python:3.11-slim

WORKDIR /app

COPY mcp-servers/seo-testing-mcp/ ./seo-testing-mcp/
RUN pip install --no-cache-dir ./seo-testing-mcp

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
# A non-editable `pip install` copies the package into site-packages, so
# __file__-relative lookups inside the installed code can no longer find
# data/ next to the original source tree — point these at where data/
# actually ends up in this image instead (see app.py / tools/geo.py /
# tools/gemini_checks.py, which all respect these env vars when set).
ENV RULES_LOCAL_PATH=/app/seo-testing-mcp/data/rules.json
ENV CITIES_DB_PATH=/app/seo-testing-mcp/data/uscities.json
ENV SITE_DICTIONARIES_PATH=/app/seo-testing-mcp/data/site_dictionaries.json
EXPOSE 8080

CMD ["python", "-m", "seo_testing_mcp.app"]
