FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastmcp>=2.10"
COPY src/mcp/server.py ./server.py
COPY specs/sample-data/ ./data/
CMD ["python", "server.py"]
