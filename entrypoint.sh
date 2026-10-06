#!/bin/sh

echo "Running database migrations..."
python manage.py migrate

exec "$@"