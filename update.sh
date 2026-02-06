#!/bin/bash

# Script de mise à jour simple pour l'application
# Usage: ./update.sh

echo "🔄 Mise à jour de l'application..."

# Arrêt des conteneurs
echo "⏸️  Arrêt des conteneurs..."
docker-compose down

# Récupération des dernières modifications (si vous utilisez git)
# git pull

# Reconstruction et redémarrage
echo "🔨 Reconstruction de l'image Docker..."
docker-compose build --no-cache web

echo "🚀 Redémarrage des services..."
docker-compose up -d

# Attendre que la base de données soit prête
echo "⏳ Attente de la base de données..."
sleep 5

# Migrations
echo "📊 Application des migrations..."
docker-compose exec -T web python manage.py migrate

# Collecte des fichiers statiques
echo "📁 Collecte des fichiers statiques..."
docker-compose exec -T web python manage.py collectstatic --noinput

echo "✅ Mise à jour terminée!"
echo "🌐 L'application est accessible sur http://votre-ip"

# Afficher les logs
echo ""
echo "📋 Logs des dernières secondes:"
docker-compose logs --tail=20
