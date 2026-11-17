# Prompt HomeHub pour l’ajout de recettes

Tu travailles dans le dépôt HomeHub Vue/Fastify. L’écran « Courses » affiche déjà la liste active et permet d’ajouter un ingrédient. Ajoute la recherche d’une recette existante, le réglage du nombre de personnes, l’aperçu des quantités, la sélection des ingrédients et la confirmation de l’ajout. Examine `apps/api/src/integrations/shopping.ts`, `apps/web/src/ShoppingPage.vue` et `apps/web/src/ShoppingIngredientForm.vue` pour suivre les conventions de la fonctionnalité existante.

## Fastify

- Ajoute les routes HomeHub équivalentes à `GET /api/shopping/recipes`, `GET /api/shopping/recipes/:id` et `POST /api/shopping/lists/:id/recipes`. Garde la lecture et l’ajout manuel existants. Réutilise leur validation des entrées, leurs protections d’accès et la vérification d’origine appliquée à l’écriture.
- Appelle Django en HTTPS depuis Fastify avec les réglages existants `COURSES_API_BASE_URL` et `COURSES_API_TOKEN`. Garde le jeton Bearer côté serveur. Réutilise le délai d’attente, le refus des redirections et `Cache-Control: no-store`.
- Transmets les paramètres de recherche et d’aperçu, le corps d’ajout et l’en-tête `Idempotency-Key`. Relaie les codes métier Django `400`, `404` et `409` utiles à Vue. Traite les erreurs amont d’authentification ou de connexion comme une indisponibilité.

## Écran Courses

- Propose « Ajouter une recette » quand une liste est ouverte. Affiche les résultats au fil de la recherche et permets de charger les pages suivantes. Après sélection, affiche les lignes de la recette avec leurs quantités et toutes les cases cochées initialement.
- Quand le nombre de personnes change, recharge l’aperçu Django et conserve les choix par identifiant de ligne encore présent. Affiche les quantités calculées par Django. Ignore les aperçus arrivés en retard et attends l’aperçu du nombre de personnes courant avant d’autoriser la confirmation. Exige au moins une ligne sélectionnée.
- Réutilise le mécanisme de l’ajout manuel pour créer une clé compatible avec la tablette en HTTP local. Fige l’identifiant de liste, le corps et la clé pendant l’envoi et pour tout nouvel essai après un délai réseau. Une nouvelle action volontaire utilise une nouvelle clé.
- Après `200`, actualise la liste existante. Sur `active_list_changed`, actualise la liste et demande une nouvelle confirmation avant d’envoyer vers une autre liste. Sur `recipe_not_found` ou une sélection devenue invalide, demande à l’utilisateur de recharger la recette ou d’en choisir une autre. Sur `idempotency_conflict`, affiche l’erreur et actualise la liste.

## Contrat Django

Toutes les routes utilisent `Authorization: Bearer <token>`. L’appel d’ajout exige également `Content-Type: application/json` et `Idempotency-Key: <UUID>`.

### Recherche

`GET /api/recipes/?q=curry&page=1`

Le filtre facultatif `q` recherche dans le nom, sans distinction de casse. La pagination commence à 1, à raison de 30 recettes par page. La réponse indique la page suivante ou `null`. Une page invalide renvoie `400` avec `{"error":"invalid_page"}`.

```json
{"recipes":[{"id":12,"name":"Riz au curry"}],"next_page":null}
```

### Aperçu

`GET /api/recipes/12/?people_count=3`

`people_count` est un entier positif de 1 à 2147483647, avec 1 par défaut. La réponse comprend la quantité par personne et le total pour le nombre de personnes demandé. Les quantités sont des chaînes décimales et le libellé d’unité peut être vide.

```json
{
  "id": 12,
  "name": "Riz au curry",
  "people_count": 3,
  "ingredients": [
    {
      "id": 38,
      "ingredient_id": 7,
      "name": "Riz",
      "quantity_per_person": "75.25",
      "quantity": "225.75",
      "unit": "g",
      "unit_label": "g"
    }
  ]
}
```

`ingredients[].id` identifie la ligne de recette et sert à sélectionner les ingrédients. `ingredient_id` identifie l’ingrédient dans le catalogue. L’identifiant d’un article de la liste est encore distinct.

### Ajout

`POST /api/lists/42/recipes/`

```json
{"recipe_id":12,"people_count":3,"included_recipe_ingredient_ids":[38]}
```

Django recalcule avec la recette au moment de l’ajout. Il enregistre les contributions de la recette et cumule les quantités par ingrédient et unité identiques. Une quantité ajoutée à un article déjà coché conserve l’état coché.

| Statut | Réponse ou code d’erreur |
| --- | --- |
| `200` | `{"list_id":42}` si l’ajout a réussi ou avait déjà réussi. |
| `400` | `validation_error` avec `fields` si les portions, les lignes de recette ou le total ne sont pas valides. Les autres erreurs de requête reprennent les codes de l’ajout manuel. |
| `401` | `unauthorized`. |
| `404` | `recipe_not_found` à la consultation ou à l’ajout. |
| `409` | `active_list_changed` ou `idempotency_conflict`. |
| `415` | `unsupported_media_type`. |

Les clés sont partagées entre les deux types d’ajouts et conservées sans expiration. Une répétition avec exactement le même corps et la même liste renvoie le succès d’origine, même après la clôture de la liste. Une demande refusée ne consomme pas la clé.

Teste la pagination, l’aperçu avec plusieurs portions, l’exclusion d’une ligne, la réutilisation de la clé après un échec réseau et le changement de liste active. Après le déploiement Django, vérifie le parcours depuis la tablette. Documente les nouvelles routes HomeHub.
