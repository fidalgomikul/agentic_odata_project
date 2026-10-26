# Small official Python image (Debian, minimal packages)
FROM python:3.13-slim

# No .pyc files in the image; flush output immediately
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only the dependency list first. Docker caches each step, so this
# layer is rebuilt only when requirements.txt changes, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY mpcserver.py .

# Run as an unprivileged user instead of root
RUN useradd --create-home appuser
USER appuser

# The server speaks MCP over stdin/stdout, so run the container with `docker run -i`
CMD ["python", "mpcserver.py"]
