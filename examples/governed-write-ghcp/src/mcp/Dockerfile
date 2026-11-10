# Pilot MCP server (one unbound read tool). MCR base avoids Docker Hub limits in ACR remote builds.
FROM mcr.microsoft.com/oryx/python:3.12
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY server.py ./
ENV MCP_TRANSPORT=streamable-http PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
