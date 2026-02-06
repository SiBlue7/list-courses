FROM python:3.13-slim

# Variables d'environnement pour Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Répertoire de travail
WORKDIR /app

# Installation des dépendances système
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copie des dépendances et installation
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn psycopg2-binary

# Copie du code de l'application
COPY . .

# Collecte des fichiers statiques
RUN python manage.py collectstatic --noinput || true

# Exposition du port
EXPOSE 8000

# Script de démarrage
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "mealplanner.wsgi:application"]
