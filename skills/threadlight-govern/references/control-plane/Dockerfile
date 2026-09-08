FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /service
COPY . .
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home govern
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "govern_control_plane.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--no-proxy-headers"]
