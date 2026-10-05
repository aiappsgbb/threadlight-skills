# MCP server image. Build context is the repo root (see azure.yaml).
# MCR base: ACR Tasks builds hit Docker Hub anonymous pull limits.
FROM mcr.microsoft.com/oryx/python:3.12
WORKDIR /app
COPY src/mcp/ /app/
COPY specs/sample-data/ /app/sample-data/
# mcp 2.x removed `mcp.server.fastmcp`; keep the upper bound.
RUN pip install --no-cache-dir "mcp>=1.10,<2"
ENV SAMPLE_DATA_DIR=/app/sample-data MCP_TRANSPORT=streamable-http PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
