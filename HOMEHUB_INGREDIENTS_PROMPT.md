# Prompt HomeHub pour l’ajout d’ingrédients

Tu travailles dans le dépôt HomeHub Vue/Fastify. La consultation des courses fonctionne déjà. Ajoute la recherche et l’ajout d’un ingrédient existant depuis l’écran « Courses » sur la tablette. Inspecte `apps/api/src/integrations/shopping.ts`, ses tests et `apps/web/src/ShoppingPage.vue`. Réutilise `COURSES_API_BASE_URL` et `COURSES_API_TOKEN`. Cette étape couvre les ajouts manuels d’ingrédients.

## Fastify

- Conserve `GET /api/shopping/items`. Ajoute par exemple `GET /api/shopping/ingredients` et `POST /api/shopping/lists/:id/items`. Valide la requête entrante et applique les protections d’accès du projet. Pour l’écriture, limite l’appel à l’origine HomeHub selon les conventions du projet et exige du JSON et une clé d’idempotence.
- Appelle Django exclusivement depuis Fastify, en HTTPS avec `Authorization: Bearer <COURSES_API_TOKEN>`, un délai d’attente et les redirections désactivées. Exclus le jeton des réponses, des logs et du code Vue. Applique `Cache-Control: no-store`.
- Transmets `Idempotency-Key` et conserve les réponses Django de validation et de conflit sous une forme exploitable par Vue. L’utilitaire `fetchText` actuel lève une erreur pour les réponses non-2xx. Prévois un traitement qui permet de distinguer les deux conflits `409` et les erreurs de validation. Un échec de connexion ou d’authentification amont doit s’afficher comme une indisponibilité.

## Écran Courses

- Ajoute « Ajouter un ingrédient » quand une liste est ouverte. Recherche par texte et charge les pages suivantes. Utilise les unités renvoyées par Django et propose une saisie décimale adaptée à la tablette. Convertis la virgule de saisie en point pour envoyer une chaîne JSON, par exemple `"2.50"`.
- À la confirmation, associe l’identifiant de la liste affichée, le corps de la demande et un UUID unique à cette action. La tablette est accessible en HTTP sur le réseau local. Utilise une génération d’UUID compatible avec ce contexte, par exemple via `crypto.getRandomValues`. Désactive les doubles clics pendant l’envoi.
- Si la réponse est perdue ou si l’issue est incertaine, « Réessayer » doit renvoyer le même identifiant de liste, le même corps et le même UUID. Une nouvelle action volontaire utilise une nouvelle clé.
- Après `200`, actualise la liste via la route de lecture existante. Sur `active_list_changed`, actualise et demande une nouvelle confirmation si une liste est ouverte. Cette confirmation cible la liste désormais affichée et utilise une nouvelle clé. Sur `idempotency_conflict`, affiche une erreur et actualise la liste avant toute nouvelle action.
- La case « acheté » reste en lecture seule. Une quantité ajoutée à un article déjà coché conserve l’état coché.

## Contrat Django

### Rechercher les ingrédients

`GET /api/ingredients/?q=riz&page=1`

`q` est facultatif. Django cherche dans le nom sans tenir compte de la casse et trie par nom. La page commence à 1 et contient jusqu’à 30 ingrédients. Une page invalide renvoie `400 {"error":"invalid_page"}`.

```json
{
  "ingredients": [{"id": 7, "name": "Riz"}],
  "units": [{"code": "", "label": "Sans unité"}, {"code": "g", "label": "g"}],
  "next_page": null
}
```

L’exemple abrège les unités. La réponse réelle fournit toutes les unités permises à chaque page. `next_page` vaut le numéro à charger ou `null`. L’identifiant d’un ingrédient du catalogue est distinct de celui d’un article de la liste.

### Ajouter un ingrédient

`POST /api/lists/42/items/` avec `Content-Type: application/json` et `Idempotency-Key: <UUID>`.

```json
{"ingredient_id": 7, "quantity": "250.50", "unit": "g"}
```

Les trois champs sont requis. La quantité est strictement positive, avec au plus deux décimales et huit chiffres avant le point. L’unité vide est acceptée. Django cumule les quantités uniquement lorsque l’ingrédient et le code unité sont identiques.

| Statut | Réponse et traitement |
| --- | --- |
| `200` | `{"list_id":42}` signifie que l’ajout a réussi ou avait déjà réussi avec cette clé. |
| `400` | `invalid_json`, `invalid_payload`, `invalid_idempotency_key` ou `validation_error`. Pour `validation_error`, `fields` associe les champs concernés à des listes de messages. |
| `401` | `unauthorized`. Vérifier la configuration serveur. |
| `409` | `active_list_changed` si l’identifiant ne correspond plus à la liste active ou qu’aucune liste n’est ouverte. |
| `409` | `idempotency_conflict` si la clé d’un ajout réussi est réutilisée avec une autre liste ou un autre contenu. |
| `415` | `unsupported_media_type`. Envoyer du JSON. |

Les erreurs portent le code dans le champ `error`. Une clé réussie est conservée sans expiration dans cette version et sa portée couvre tous les ajouts manuels HomeHub. La même liste et les mêmes valeurs redonnent `200`, même après la fermeture de la liste. Conserve notamment la représentation décimale d’origine lors d’un nouvel essai. Un ajout refusé ne consomme pas la clé.

Teste la recherche paginée, le succès, les deux conflits et la répétition d’une demande après un délai réseau simulé. Une fois Django déployé avec la migration `0006_homehubitemaddition`, vérifie depuis le backend HomeHub puis sur la tablette. Documente les nouvelles routes dans HomeHub.
