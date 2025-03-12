#!/bin/bash
set -e  # Exit on any error

echo "Running collectstatic..."
mkdir -p /var/app/current/staticfiles
source /var/app/venv/*/bin/activate  # Activate virtual environment
python /var/app/current/manage.py collectstatic --noinput
echo "collectstatic completed."


# Change ownership so Nginx can access static files
chown -R nginx:nginx /var/app/current/staticfiles