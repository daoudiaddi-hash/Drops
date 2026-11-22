# Fonctionnement détaillé

Ce document explique la logique du projet : comment on récupère les annonces Vinted, les pièges rencontrés, et comment la veille déduit ce qui se vend le mieux.

## Sommaire

1. [Le principe](#1-le-principe)
2. [Règles qu'on s'est fixées](#2-règles-quon-sest-fixées)
3. [Reconnaissance : ce qu'on a découvert](#3-reconnaissance--ce-quon-a-découvert)
4. [La recherche par critères](#4-la-recherche-par-critères)
5. [Retrouver une date de publication](#5-retrouver-une-date-de-publication)
6. [La veille : déduire ce qui se vend](#6-la-veille--déduire-ce-qui-se-vend)
7. [Limites](#7-limites)

---

## 1. Le principe

Le site Vinted affiche ses résultats de recherche en interrogeant sa propre API JSON. On appelle cette même API, poliment et lentement, comme le ferait un navigateur, et on écrit les résultats dans un CSV ou dans une base SQLite.

## 2. Règles qu'on s'est fixées

- **Outils gratuits uniquement** : Python 3, `httpx`, la bibliothèque standard.
- **Aucun contournement** : pas de proxy payant, pas de solveur de captcha, pas de rotation d'IP. Si Vinted bloque (captcha, challenge Cloudflare, 403), le script s'arrête, écrit l'erreur et sauvegarde ce qu'il a déjà.
- **Rythme lent** : au moins 1,5 s entre deux requêtes, avec un léger aléa. Sur une erreur 429 ou 503, on attend de plus en plus longtemps (3 s, 6 s, 12 s…).
- **Données publiques seulement** : l'API renvoie le pseudo et l'id du vendeur, ils ne sont **jamais** conservés.

## 3. Reconnaissance : ce qu'on a découvert

On a inspecté le site dans un navigateur : on clique sur la page 2 d'une recherche ou on ouvre un filtre, et on regarde quelles requêtes partent.

| Constat | Détail |
|---|---|
| **Endpoint de recherche** | `https://api.vinted.fr/svc-catalogue/items`. L'ancien `/api/v2/catalog/items`, cité partout sur Internet, renvoie 404. |
| **Cookies obligatoires** | Sans eux, l'API répond 403. Une visite de la page d'accueil avec une session persistante suffit à les obtenir. |
| **Filtres** | `attribute_ids[brand]=88`, `attribute_ids[catalog]=77`, `attribute_ids[size]=208`, `attribute_ids[status]=2`, `attribute_ids[color]=1`… Pour plusieurs valeurs, on les sépare par des virgules (`=6,1`). Répéter le paramètre désactive le filtre. |
| **Plafond de 960 résultats** | Au maximum 10 pages × 96 annonces par recherche. Pour une marque populaire, cela ne couvre que quelques heures d'annonces. |
| **Pas de date de publication** | La liste n'en contient aucune. La page de l'annonce affiche seulement « Ajouté : Il y a 10 heures ». |
| **Pas de données de vente** | Vinted ne publie ni ce qui se vend, ni à quel prix. |
| **Marque, taille, état** | Ils ne sont que dans un texte d'accessibilité : `"Chemise, Marque: Ralph Lauren, État: Très bon état, Taille: M, 9.00 €, 10.15 €"`. |

## 4. La recherche par critères

### Traduire des noms en identifiants

L'API ne comprend que des id. Le script les retrouve tout seul :
- **Catégories** : la page d'accueil, qu'on charge de toute façon pour les cookies, contient l'arbre complet des catégories (environ 2 900). On cherche donc `"Hommes > T-shirts"` dans cet arbre, sans requête supplémentaire. Si le nom est ambigu (« T-shirts » existe chez Hommes, Femmes, Enfants…), le script liste les candidats.
- **Marques** : l'endpoint `api/v2/brands?keyword=…` de Vinted.
- **Taille, état, couleur, matière, motif** : l'endpoint des filtres (`svc-filters/filters/facets`) donne la liste des valeurs avec leurs id, adaptée à la catégorie choisie.

Piège rencontré : « Polo Ralph Lauren » (4273) et « Ralph Lauren » (88) sont deux marques distinctes sur Vinted. Pour tout Ralph Lauren, il faut donc mettre les deux.

### Dépasser le plafond de 960… honnêtement

On ne force rien. On lance **plusieurs recherches distinctes** en découpant par tranche de prix (0–5 €, 5–10 €, … 25–30 €). Chaque tranche a son propre plafond de 960, donc on récupère jusqu'à 6 × 960 annonces. On supprime ensuite les doublons grâce à l'id de l'annonce.

Avec des critères précis (marque, catégorie, taille, état), la recherche ne ramène plus de bruit. Les 960 résultats couvrent alors une période plus longue.

### Pagination

Pour chaque tranche, on demande les pages 1, 2, 3… de la plus récente à la plus ancienne, et on s'arrête dès qu'une de ces conditions est remplie :
- la page est vide ;
- on a atteint la dernière page (la 10e) ;
- l'annonce la plus ancienne de la page dépasse la date limite ;
- en mode veille, toutes les annonces de la page ont déjà été vues.

Chaque page est écrite immédiatement dans un fichier `.partiel.csv` : si le script plante, rien n'est perdu.

## 5. Retrouver une date de publication

Vinted ne donne aucune date, mais deux choses aident :
1. **Les id d'annonces croissent avec le temps** : une annonce créée plus tard a un id plus grand (environ 1 million d'id par heure).
2. **La page d'une annonce affiche son âge** : « Il y a 3 heures ».

D'où la méthode :
- Pour chaque page de résultats, on ouvre **une seule** annonce, la dernière de la page, et on lit son âge. Cela donne un **repère** : cet id correspond à telle date.
- Pour les autres annonces, on **interpole** : si l'id 1000 date de 8 h et l'id 2000 de 10 h, l'id 1500 date d'environ 9 h.
- Pour une annonce dont l'id est hors de la plage des repères, on **extrapole** avec la vitesse moyenne de création des id.

**Piège des annonces « remontées »** : un vendeur peut remonter une vieille annonce. Elle affiche alors « Il y a 2 heures » alors que son id est ancien, ce qui fausserait les repères. La correction : une annonce ne peut pas être plus récente qu'une annonce d'id plus grand. Pour chaque repère, on garde donc la date minimale parmi tous les repères d'id plus grand (une « enveloppe inférieure »). En pratique, environ la moitié des repères sont des annonces remontées.

Coût : une requête de plus par page. Précision : à l'heure près sur les dernières 24 h (vérifié sur des annonces tirées au hasard), plus floue pour les vieilles annonces remontées.

## 6. La veille : déduire ce qui se vend

Comme Vinted ne publie pas ses ventes, on les **déduit** : une annonce qui disparaît vite est une annonce qui s'est vendue.

### Ce que fait chaque passage quotidien

```
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│ 1. Collecte          │──▶│ 2. Relevé de l'offre │──▶│ 3. Suivi d'un échantillon│
│ nouvelles annonces   │   │ comptages par filtre │   │ d'annonces déjà vues     │
│ depuis le passage    │   │ (indice relatif)     │   │ (~150 par veille)        │
│ précédent → CSV      │   │                      │   │                          │
└──────────────────────┘   └──────────────────────┘   └────────────┬─────────────┘
                                                                   │
                                     SQLite (veille.db) ◀──────────┘
                                             │
                                             ▼
                                  4. Rapport de tendances
```

1. **Collecte** : on récupère les annonces publiées depuis le dernier passage, avec deux heures de recouvrement. On les enregistre en base, et les nouvelles sont exportées dans un CSV qui sert d'alerte. Une annonce revue dans la recherche du jour est forcément encore en vente.
2. **Relevé de l'offre** : les filtres Vinted renvoient un nombre d'annonces par taille, état, couleur et marque. Attention, ces nombres sont **incohérents en valeur absolue**. Exemple vérifié : ajouter un filtre de marque peut *augmenter* le total. On ne les utilise donc que comme **indice relatif** : leur évolution d'un jour à l'autre, pour une même requête.
3. **Suivi** : on rouvre un échantillon d'annonces vues il y a plus de 20 h et absentes de la recherche du jour, en commençant par celles jamais vérifiées. Leur page indique :
   - `can_buy: true` → **actif** (toujours en vente) ;
   - `is_reserved: true` → **réservé** (vente en cours) ;
   - `can_buy: false` ou `is_hidden: true` → **indisponible** (probablement vendu) ;
   - page introuvable ou redirection → **disparu** (vendu ou supprimé).

   Les trois derniers statuts comptent comme « partis ». On fait un échantillon et pas tout, parce que chaque page pèse environ 1,6 Mo et qu'on garde le rythme lent. Statistiquement, quelques centaines par jour suffisent.

### Les indicateurs du rapport

Le rapport regroupe les annonces par **taille, état, marque et tranche de prix**, et calcule pour chaque groupe :

| Indicateur | Ce qu'il dit |
|---|---|
| **Taux de départ** | part des annonces vérifiées qui ne sont plus en vente : ce qui part le plus |
| **Délai médian** | temps entre la première vue et la détection du départ : la vitesse de vente |
| **Prix médian parties vs restantes** | le bon prix, celui au-delà duquel ça ne part plus |
| **Favoris moyens** | l'intérêt des acheteurs |
| **Évolution de l'offre** | une offre qui baisse alors que le taux de départ reste élevé signale qu'il manque des vendeurs |

Il contient aussi un **top 10 des segments qui se vendent le mieux**. Un segment de moins de 10 annonces vérifiées est signalé ⚠️ (peu fiable).

### Ordres de grandeur

- Un passage : environ 25 à 130 requêtes de collecte selon le nombre de tranches, plus environ 150 de suivi, soit **10 à 15 minutes**.
- **2 à 3 semaines** de passages quotidiens avant des tendances fiables.

## 7. Limites

- **Dates estimées** : Vinted n'affiche qu'un âge relatif.
- **« Parti » ne veut pas forcément dire « vendu »** : le vendeur a pu supprimer son annonce. La détection n'a pas encore été vérifiée sur un article réellement vendu. Les indicateurs bruts sont conservés en base pour recalibrer si besoin.
- **Prix réel inconnu** : les offres sont négociées, on n'a que le prix affiché.
- **Échantillonnage** : les petites catégories (taille ou couleur rare) ont une forte marge d'erreur.
- **Vinted change son site** : l'endpoint de recherche a déjà changé une fois. La procédure de mise à jour est dans [references/api-vinted.md](../references/api-vinted.md).
