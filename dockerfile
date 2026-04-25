# Use a lightweight Python base image
FROM python:3.11-slim

RUN pip install --upgrade pip

# Install curl (needed for health check) and ffmpeg
RUN apt-get update && apt-get install -y curl build-essential cmake ffmpeg libreoffice && rm -rf /var/lib/apt/lists/*

# # Set a non-root user
# ARG USER=myuser
# ARG GROUP=myuser
ARG PORT=8000

# Set working directory
WORKDIR /app

# Install dependencies first for caching optimization
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Set env vars before collectstatic so decouple can load settings
ENV POSTGRESQL_DATABASE_NAME="" \
    POSTGRESQL_USERNAME="" \
    POSTGRESQL_PASSWORD="" \
    POSTGRESQL_SERVER_NAME="" \
    OPENAI_API_KEY="" \
    DEEPGRAM_API_KEY="" \
    EMAIL_HOST="" \
    EMAIL_PORT="" \
    EMAIL_USE_TLS="" \
    EMAIL_HOST_USER="" \
    EMAIL_HOST_PASSWORD="" \
    DEFAULT_FROM_EMAIL="" \
    AWS_ACCESS_KEY_ID="" \
    AWS_SECRET_ACCESS_KEY="" \
    USE_S3="" \
    AWS_STORAGE_BUCKET_NAME="" \
    AWS_S3_REGION_NAME="" \
    AUTH_TOKEN_FOR_WEBSOCKET=""

# Copy application files last (reduces build invalidation)
COPY . .

# Collect static files (this was missing)
RUN python manage.py collectstatic --noinput && echo "Static files collected"


# # Create a non-root user
# RUN groupadd --system $GROUP && useradd --system --gid $GROUP --home-dir /app $USER \
#     && chown -R $USER:$GROUP /app


# Expose port
EXPOSE $PORT

ENV PORT="" \
    DJANGO_SETTINGS_MODULE="EngageX.settings"


# Command to run the application
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "EngageX.asgi:application"]

# Health check (if applicable)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl --fail http://localhost:8000/ || exit 1
