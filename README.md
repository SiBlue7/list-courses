# 🍽️ List Courses - Gestion des repas et listes de courses

Application Django pour gérer vos recettes et générer automatiquement vos listes de courses.

---

## 🚀 Déploiement Rapide (Recommandé)

### Sur Proxmox avec Docker

Consultez le guide complet : **[Plan de Déploiement](deployment_plan.md)**

**Résumé rapide :**

1. Créer une VM Ubuntu sur Proxmox
2. Installer Docker et Docker Compose
3. Cloner le projet
4. Modifier `docker-compose.yml` (IP serveur, SECRET_KEY, mot de passe)
5. Lancer : `docker-compose up -d`

**Mise à jour en une commande :**

```bash
./update.sh
```

---

## 💻 Installation Locale (Développement)

### Prérequis

- Python 3.13+
- pip

### Installation

1. **Créer un environnement virtuel**

```powershell
py -m venv .venv
.\.venv\Scripts\activate
```

2. **Installer les dépendances**

```powershell
pip install -r requirements.txt
```

3. **Initialiser la base de données**

```powershell
python manage.py makemigrations
python manage.py migrate
```

4. **Créer un superutilisateur**

```powershell
python manage.py createsuperuser
```

5. **Lancer le serveur**

```powershell
python manage.py runserver
```

6. **Accéder à l'application**

- Application : http://localhost:8000
- Admin : http://localhost:8000/admin

---

## 📁 Structure du Projet

```
list-courses/
├── core/                   # Application Django principale
│   ├── models.py          # Modèles (Recipe, Ingredient, ShoppingList)
│   ├── views.py           # Vues et logique métier
│   └── templates/         # Templates HTML
├── mealplanner/           # Configuration Django
│   ├── settings.py        # Configuration (supporte env vars)
│   └── urls.py            # Routes
├── templates/             # Templates globaux
├── Dockerfile             # Image Docker
├── docker-compose.yml     # Orchestration Docker
├── nginx.conf             # Configuration Nginx
├── update.sh              # Script de mise à jour
└── requirements.txt       # Dépendances Python
```

---

## 🎯 Fonctionnalités

- ✅ **Gestion des recettes** avec ingrédients et quantités
- ✅ **Création de listes de courses**
- ✅ **Calcul automatique** des quantités selon le nombre de personnes
- ✅ **Interface de cochage** pour marquer les ingrédients achetés
- ✅ **Archivage** des listes terminées
- ✅ **Multi-utilisateurs** avec authentification
- ✅ **Partage de listes** (pour colocations)

---

## 🔧 Configuration

### Variables d'Environnement (Production)

Créer un fichier `.env` basé sur `.env.example` :

```bash
DEBUG=False
SECRET_KEY=votre-cle-secrete-generee
ALLOWED_HOSTS=localhost,127.0.0.1,votre-ip-serveur
DATABASE_URL=postgresql://user:password@db:5432/mealplanner
```

### Générer une SECRET_KEY

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## 🐳 Commandes Docker Utiles

```bash
# Démarrer l'application
docker-compose up -d

# Voir les logs
docker-compose logs -f

# Arrêter l'application
docker-compose down

# Reconstruire après modification du code
docker-compose up -d --build

# Créer un superutilisateur
docker-compose exec web python manage.py createsuperuser

# Accéder au shell Django
docker-compose exec web python manage.py shell

# Backup de la base de données
docker-compose exec db pg_dump -U mealplanner mealplanner > backup.sql
```

---

## 📊 Base de Données

### Développement Local

- **SQLite** (automatique, fichier `db.sqlite3`)

### Production (Docker)

- **PostgreSQL** (configuré via `docker-compose.yml`)

### Migrations

```bash
# Créer des migrations
python manage.py makemigrations

# Appliquer les migrations
python manage.py migrate
```

---

## 🔒 Sécurité

### En Production

- [ ] Changer `SECRET_KEY` dans `docker-compose.yml`
- [ ] Définir `DEBUG=False`
- [ ] Configurer `ALLOWED_HOSTS` avec votre IP/domaine
- [ ] Changer le mot de passe PostgreSQL
- [ ] Configurer un firewall (UFW)
- [ ] (Optionnel) Ajouter HTTPS avec Let's Encrypt

---

## 🆘 Dépannage

### Erreur "python.exe introuvable"

Utilisez `py` au lieu de `python` sur Windows, ou activez l'environnement virtuel :

```powershell
.\.venv\Scripts\activate
```

### Les migrations ne s'appliquent pas

```bash
# Vérifier l'état des migrations
python manage.py showmigrations

# Forcer la migration
python manage.py migrate --run-syncdb
```

### Docker : conteneurs ne démarrent pas

```bash
# Voir les logs
docker-compose logs

# Reconstruire complètement
docker-compose down -v
docker-compose up -d --build
```

---

## 📝 Licence

Projet personnel - Usage libre

---

## 🤝 Contribution

Pour ajouter des fonctionnalités :

1. Modifier le code
2. Tester localement avec `python manage.py runserver`
3. Créer les migrations si nécessaire
4. Déployer avec `./update.sh` (Docker) ou `git pull` + redémarrage

---

## 📞 Support

Pour toute question, consultez :

- [Guide de déploiement complet](deployment_plan.md)
- Documentation Django : https://docs.djangoproject.com/
- Documentation Docker : https://docs.docker.com/
