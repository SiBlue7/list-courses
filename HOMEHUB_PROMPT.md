# Prompt à transmettre à l’agent HomeHub

Tu travailles dans HomeHub, le tableau de bord Vue/Fastify affiché sur la tablette. Ajoute une vue de consultation des courses. Il faut adapter le backend Fastify et le frontend Vue. Inspecte d’abord les routes, la configuration et les composants existants pour suivre les conventions du projet.

## Backend Fastify

- Ajoute une route de lecture côté HomeHub, selon les conventions du projet, par exemple `GET /api/courses/active`. Applique le contrôle d’accès existant de HomeHub.
- Appelle `GET /api/lists/active/` sur Django depuis Fastify. Configure l’URL de base HTTPS de Django dans `COURSES_API_BASE_URL` et le secret dans `COURSES_API_TOKEN`, ou utilise les conventions de configuration équivalentes de HomeHub. Le secret doit correspondre à `HOMEHUB_API_TOKEN` côté Django.
- Envoie `Authorization: Bearer <token>` uniquement sur l’appel serveur vers Django. Garde le secret dans la configuration serveur Fastify, hors des variables exposées par Vue, des logs et des réponses. Applique un délai d’attente et retourne une erreur HomeHub exploitable si Django est inaccessible ou si son authentification échoue. Désactive la mise en cache de la réponse côté HomeHub.
- Documente les deux réglages dans la configuration d’exemple et indique comment les fournir au service Fastify en production, avec le secret laissé vide dans l’exemple.

## Interface Vue

- Ajoute un accès « Courses » adapté à la tablette. Affiche le nom de la liste, les rayons dans l’ordre reçu, puis le nom de chaque article, sa quantité et son libellé d’unité. `checked` peut servir d’indicateur visuel en lecture seule.
- Affiche un état de chargement, « Aucune liste active » pour `list: null`, « Liste vide » pour `groups: []` et un état « Courses indisponibles » avec un bouton Réessayer en cas d’échec. Ajoute une action Actualiser sur la vue.
- Limite cette intégration à la consultation.

## Contrat Django

La liste active est la plus récemment créée parmi les listes ouvertes. En cas d’égalité, l’identifiant le plus élevé l’emporte. Les rayons et les articles utilisent le même regroupement que le site Django.

`200 OK` avec une liste active

```json
{
  "list": {
    "id": 42,
    "name": "Courses",
    "groups": [
      {
        "label": "Épicerie",
        "items": [
          {
            "id": 105,
            "name": "Riz",
            "quantity": "250.50",
            "unit": "g",
            "unit_label": "g",
            "checked": false
          }
        ]
      }
    ]
  }
}
```

- `items[].id` identifie un article de liste. `quantity` est une chaîne décimale avec un point. Pour l’affichage français, utilise une virgule si besoin.
- `unit` est le code et `unit_label` le libellé à afficher. Les deux champs valent `""` pour un article sans unité. Le rayon « Sans catégorie » regroupe les articles sans catégorie.
- `200 OK` avec `{"list": null}` signifie qu’aucune liste n’est ouverte. Une liste ouverte vide contient `"groups": []`.
- `401 Unauthorized` avec `{"error": "unauthorized"}` signifie que le secret est absent, invalide ou que l’accès HomeHub est désactivé. Affiche une indisponibilité côté Vue.
- Les réponses Django demandent au client de ne pas les stocker en cache (`Cache-Control: no-store`).

Vérifie les états de l’interface et la route Fastify avec des réponses simulées. Une fois Django déployé manuellement, teste la requête HTTPS depuis l’environnement où Fastify tourne, puis vérifie le rendu sur la tablette. L’adresse Django à utiliser doit être joignable depuis Fastify. L’adresse de la tablette donne accès à HomeHub.
