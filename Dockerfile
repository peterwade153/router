# Use the official lightweight Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies required for GeoDjango (GDAL, GEOS, PROJ) and psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gdal-bin \
    libgdal-dev \
    binutils \
    libproj-dev \
    geos-bin \
    libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

# Set GDAL environment variables
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files and entrypoint script into the container
COPY . /app/
RUN chmod +x /app/entrypoint.sh

# Expose Django's port
EXPOSE 8000

# Set entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Default command passed to the entrypoint script
CMD ["gunicorn", "-c", "gunicorn.conf.py", "fuel_project.asgi:application"]
