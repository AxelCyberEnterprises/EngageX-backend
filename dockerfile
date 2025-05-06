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

# Copy application files last (reduces build invalidation)
COPY . .

# Collect static files (this was missing)
RUN python manage.py collectstatic --noinput && echo "Static files collected"


# # Create a non-root user
# RUN groupadd --system $GROUP && useradd --system --gid $GROUP --home-dir /app $USER \
#     && chown -R $USER:$GROUP /app

# Switch to non-root user
USER $USER

# Expose port
EXPOSE $PORT

# Environment variables (should be set at runtime)
# PostgreSQL Config
ENV POSTGRESQL_DATABASE_NAME=""
ENV POSTGRESQL_USERNAME=""
ENV POSTGRESQL_PASSWORD=""
ENV POSTGRESQL_SERVER_NAME=""
ENV PORT=""

# # OpenAI config
ENV OPENAI_API_KEY=""
ENV DEEPGRAM_API_KEY=""


# # AWS SES config
ENV EMAIL_HOST=""
ENV EMAIL_PORT=""
ENV EMAIL_USE_TLS=""
ENV EMAIL_HOST_USER=""
ENV EMAIL_HOST_PASSWORD=""
ENV DEFAULT_FROM_EMAIL=""

# # AWS S3
ENV USE_S3=""
ENV AWS_STORAGE_BUCKET_NAME=""
ENV AWS_S3_REGION_NAME=""

ENV AWS_ACCESS_KEY_ID=""
ENV AWS_SECRET_ACCESS_KEY=""
ENV AUTH_TOKEN_FOR_WEBSOCKET=""

ENV DJANGO_SETTINGS_MODULE="EngageX.settings"


# Command to run the application
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "EngageX.asgi:application"]

# Health check (if applicable)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl --fail http://localhost:8000/ || exit 1
