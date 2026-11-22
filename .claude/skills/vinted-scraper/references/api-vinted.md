# API Vinted : référence technique (constatée en octobre 2026)

À lire quand un script renvoie des 404, des champs vides ou des résultats incohérents, pour savoir quoi vérifier et où.

## Session

- Faire un `GET https://www.vinted.fr/` avec un `httpx.Client` persistant. La page pose les cookies `access_token_web`, `anon_id`, `v_udt`… Sans eux, l'API répond `403 {"code":"FORBIDDEN","message":"Access denied."}`.
- Un User-Agent Chrome récent et `Accept-Language: fr-FR` suffisent. Il n'est pas nécessaire d'imiter l'empreinte TLS.
- La page d'accueil (environ 1,7 Mo) embarque **l'arbre complet des catégories** (environ 2 900 nœuds `{"id","title","url","catalogs":[...]}` dans le flux RSC, guillemets échappés en `\"`). `_arbre_categories()` l'extrait sans requête supplémentaire.

## Endpoints

| Usage | Endpoint | Notes |
|---|---|---|
| Recherche | `GET https://api.vinted.fr/svc-catalogue/items` | `search_text`, `price_from`, `price_to`, `order=newest_first`, `page`, `per_page` (≤ 96), `attribute_ids[<code>]` |
| Options d'un filtre | `GET https://api.vinted.fr/svc-filters/filters/facets?filter_code=<code>&…` | mêmes paramètres que la recherche ; renvoie `options[]` (`id`, `title`, `items_count`, options imbriquées pour les groupes de tailles) |
| Liste des filtres | `GET https://api.vinted.fr/svc-filters/filters` | définitions, options vides (`is_lazy`) |
| Sous-catégories | `GET https://api.vinted.fr/svc-filters/faceted_categories?attribute_ids[catalog]=<parent>` | enfants avec `item_count` |
| Marques | `GET https://www.vinted.fr/api/v2/brands?keyword=<texte>` | `id`, `title`, `item_count` |
| Page annonce | `GET https://www.vinted.fr/items/<id>-<slug>` | HTML d'environ 1,6 Mo, seule source de l'âge et du statut |

L'ancien `https://www.vinted.fr/api/v2/catalog/items` renvoie 404, et `/api/v2/items/<id>/details` renvoie 403.

## Filtres `attribute_ids[...]`

Codes possibles : `catalog`, `brand`, `size`, `status`, `color`, `material`, `patterns`.

- **Plusieurs valeurs : séparées par des virgules** (`attribute_ids[status]=6,1`). Vérifié : 1 + 3 = 4 résultats. Répéter le paramètre **désactive** le filtre (on retombe à 960 résultats).
- Id d'état : 6 = Neuf avec étiquette, 1 = Neuf sans étiquette, 2 = Très bon état, 3 = Bon état, 4 = Satisfaisant.
- Exemples d'id : Hommes > T-shirts = 77, Hommes > Polos = 5492, Femmes > T-shirts = 221. Ralph Lauren = 88, Polo Ralph Lauren = 4273. Taille homme M = 208, L = 209. Couleur Noir = 1.
- Équivalent dans l'URL du site : `/catalog?brand_ids[]=88&catalog[]=77&size_ids[]=208&status_ids[]=2&color_ids[]=1`.

**Comptages `items_count` des facettes : incohérents en valeur absolue.** Exemple : la marque 88 seule donne environ 1,4 M d'annonces, alors que les marques 88 et 4273 ensemble en donnent environ 250 k, et la catégorie 77 seule donne moins que 77 + marque 88. On ne les utilise que comme indice relatif dans le temps, pour une requête identique. La facette d'un critère déjà filtré ignore sa propre sélection : c'est le comportement classique des facettes.

## Réponse de recherche

- `pagination` : `total_entries` est **plafonné à 960** et `total_pages` à 10. La page 11 renvoie 400.
- Champs d'une annonce : `id`, `title`, `price {amount, currency_code}`, `total_item_price` (avec frais de protection), `url` (relative), `photo.url`, `favourite_count`, `view_count` (souvent 0), `item_box {first_line (marque), second_line ("taille · état"), accessibility_label}`, `user` (**à ignorer** : pseudo et id du vendeur).
- **Aucune date.** L'ancien `photo.high_resolution.timestamp` a disparu.
- Marque, état et taille ne sont que dans `accessibility_label` : `"Titre, Marque: X, État: Y, Taille: Z, 9.00 €, 10.15 €"`.
- `newest_first` n'est pas strictement trié par id : les annonces remontées s'intercalent.

## Page d'une annonce

- Âge : `itemProp="upload_date"><span …>Il y a 10 heures</span>`. Formats : « A l'instant », « Il y a une minute », « Il y a N minutes/heures/jours/semaines/mois », « Il y a un an ». Pour une annonce remontée, l'âge affiché est plus récent que sa création.
- Statut : dans le flux RSC, bloc `{"data":{"can_buy":true,"instant_buy":true,"is_hidden":false,"is_reserved":false,"item_id":"<id>",…}}`. Plusieurs blocs `data` portent l'`item_id`, il faut prendre celui qui contient `can_buy`. La page contient aussi des annonces similaires, d'où le ciblage par `item_id`.
- **Non encore observé sur un article réellement vendu.** L'hypothèse codée : vendu → `can_buy:false`, ou page 404 / redirection hors de `/items/<id>`. La table `observations` garde les indicateurs bruts (`http`, `can_buy`, `is_reserved`, `is_hidden`) pour recalibrer `lire_statut()` si besoin.

## Estimation des dates

1. Par page de résultats, ouvrir la page de la **dernière annonce dans l'ordre du tri** (plus la première en page 1), lire son âge et le convertir en date : c'est un repère (id → date).
2. Corriger les repères par **enveloppe inférieure** : date(id) ≤ date de tout repère d'id plus grand. Cela neutralise les remontées ; en pratique, environ la moitié des repères sont corrigés.
3. Interpoler linéairement par id entre les repères. Hors des repères, extrapoler avec la **vitesse moyenne de création des id** sur l'ensemble des repères. Ne pas utiliser le segment le plus proche : il est souvent plat et fige les vieilles annonces remontées à la date du repère le plus ancien.

Ordre de grandeur : environ 1 million d'id par heure en octobre 2026 (id actuels vers 10,45 milliards).

## Si un endpoint change

1. Ouvrir la recherche équivalente sur le site dans le navigateur intégré.
2. Cliquer sur la page 2 de la pagination ou ouvrir un filtre (le site charge alors les données côté client).
3. Lister les appels réseau : `performance.getEntriesByType('resource').map(e => e.name).filter(u => u.includes('svc-'))`.
4. Mettre à jour le chemin dans `vinted_commun.py` (`collecter`, `Resolveur.options`), tester avec `--lister`, puis lancer une recherche à une seule tranche.
