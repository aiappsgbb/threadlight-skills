# MCP server image. Build context is the repo root (see azure.yaml).
# MCR base: ACR Tasks builds hit Docker Hub anonymous pull limits.
FROM mcr.microsoft.com/oryx/python:3.12
# Mirror the repo layout (/app/src/mcp, /app/specs/...): a design-owned
# server.py may resolve repo-relative paths such as Path(__file__).parents[2],
# which raise IndexError when the server sits directly under /app.
WORKDIR /app/src/mcp
COPY src/mcp/ /app/src/mcp/
COPY specs/sample-data/ /app/specs/sample-data/
# Exact, hashed pins (mcp==1.29.1; mcp 2.x removed `mcp.server.fastmcp`).
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
ENV SAMPLE_DATA_DIR=/app/specs/sample-data MCP_TRANSPORT=streamable-http PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
