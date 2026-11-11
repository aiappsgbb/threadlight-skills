# User-scoped OBO downstream (MCP over streamable HTTP). Build context: this folder.
# MCR base: ACR Tasks (remote builds) hit Docker Hub anonymous pull limits.
FROM mcr.microsoft.com/oryx/python:3.12
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY server.py ./
ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
