---
name: vinted-scraper
description: Recherche d'annonces publiques Vinted par critères (marque, catégorie, taille, état, couleur, matière, motif, prix, ancienneté) exportée en CSV, et veille automatique (recherches enregistrées, nouveautés quotidiennes, suivi des annonces vendues, rapport des tendances de ce qui se vend le mieux). Utilise ce skill dès que l'utilisateur veut chercher, extraire, lister, surveiller ou exporter des annonces Vinted, comparer des prix sur Vinted, être alerté des nouvelles annonces, savoir ce qui se vend ou se revend le mieux sur Vinted, ou planifier un suivi de marché Vinted — même s'il ne dit ni « scraper » ni « veille » (ex. « trouve-moi les jeans Levi's taille 32 à moins de 20 € », « quelles tailles de Ralph Lauren partent le plus vite ? »).
---

# Vinted : recherche par critères et veille

Deux scripts couvrent tout, appuyés sur `scripts/vinted_commun.py` qui gère l'API actuelle de Vinted et ses pièges :

| Besoin | Script |
|---|---|
| « Trouve-moi… », « exporte… », « liste… » (une recherche ponctuelle) | `scripts/vinted_recherche.py` |
| « Surveille… », « alerte-moi… », « qu'est-ce qui se vend le mieux… » | `scripts/vinted_veille.py` |

Les chemins `scripts/...` sont relatifs au dossier de ce skill. Lance-les avec leur chemin complet depuis le dossier de travail de l'utilisateur, car les CSV et `veille_vinted/` sont créés dans le dossier courant. Dépendance unique : `pip install httpx` (+ `tzdata` sous Windows si `zoneinfo` échoue).

Utilise ces scripts plutôt que de réécrire un scraper. Ne les modifie que si la reconnaissance (étape 1) montre que Vinted a changé ; le détail technique est dans `references/api-vinted.md`.

## Règles non négociables

Elles protègent l'IP et le compte de l'utilisateur, et respectent les CGU de Vinted :

- Uniquement des outils gratuits (httpx, bibliothèque standard). Pas de proxy payant, pas de solveur de captcha, pas de rotation d'IP ou d'empreinte TLS.
- En cas de captcha, de challenge Cloudflare ou de 403, les scripts s'arrêtent proprement (code de sortie 2) et gardent ce qui a déjà été récupéré. Ne relance pas en boucle et ne cherche pas d'autre technique : explique la situation à l'utilisateur.
- Au moins 1,5 s entre deux requêtes, avec backoff exponentiel sur 429/503 (codé en dur, `--delai` ne peut pas descendre en dessous).
- Uniquement les données publiques des annonces. Aucun pseudo ni id de vendeur dans les fichiers.

## 1. Reconnaissance rapide (30 s, avant un long run)

```bash
python scripts/vinted_recherche.py --lister etat --categorie "Hommes > T-shirts"
```

- La liste des états s'affiche : l'API fonctionne, continue.
- Erreur de connexion ou refus de proxy : arrête-toi et signale-le, sans contournement.
- 404 sur `svc-catalogue` ou `svc-filters` : l'endpoint a changé. Suis la procédure de `references/api-vinted.md`.

## 2. Traduire la demande en critères

Les critères sont répétables. Plusieurs valeurs d'un même critère sont combinées en OU, les critères différents en ET.

| Argument | Exemple | Remarques |
|---|---|---|
| `--recherche` | `"levis 501"` | texte libre ; ramène du bruit, préfère les filtres |
| `--marque` | `"Ralph Lauren"` | nom ou id. Attention aux marques sœurs : « Polo Ralph Lauren » (4273) est distincte de « Ralph Lauren » (88), donc ajoute les deux |
| `--categorie` | `"Hommes > T-shirts"` | chemin partiel, nom exact ou id. Un nom ambigu (« T-shirts ») donne une erreur qui liste les candidats |
| `--taille` `--etat` `--couleur` `--matiere` `--motif` | `M`, `"Très bon état"`, `Noir`, `Coton`, `Rayures` | libellés exacts de Vinted (voir `--lister`) |
| `--prix-min` / `--prix-max` | `30` | `--prix-max` permet le découpage en tranches |
| `--tranches` | `6` | voir plafond ci-dessous. 1 = environ 25 requêtes, 6 = environ 130 |
| `--mois` | `3` | ancienneté maximale (recherche ponctuelle) |
| `--filtre` | `levi` | mot obligatoire dans le titre ou la marque (anti-bruit pour `--recherche`) |

En cas de doute sur un libellé ou un id, explore avant de lancer, au coût d'une ou deux requêtes :

```bash
python scripts/vinted_recherche.py --lister categories --contient "jean"
python scripts/vinted_recherche.py --lister marque --contient "levi"
python scripts/vinted_recherche.py --lister taille --categorie "Femmes > Jeans"
```

Valeurs possibles de `--lister` : `categories`, `marque`, `taille`, `etat`, `couleur`, `matiere`, `motif`.

**Plafond de 960 résultats** : Vinted ne renvoie jamais plus de 10 pages × 96 annonces par recherche. Avec des critères larges, cela ne représente que quelques heures d'annonces. Les tranches de prix multiplient ce plafond. Préviens l'utilisateur quand la période demandée dépasse ce qu'on peut atteindre : c'est le cas qui l'intéresse le plus, et la veille quotidienne est la seule vraie réponse.

## 3a. Recherche ponctuelle → CSV

Lance la commande en arrière-plan si plus d'une tranche (environ 5 s par page) :

```bash
PYTHONIOENCODING=utf-8 python scripts/vinted_recherche.py --marque "Ralph Lauren" --marque "Polo Ralph Lauren" --categorie "Hommes > T-shirts" --taille M --etat "Très bon état" --prix-max 30 --mois 3 --tranches 6
```

Sortie : `vinted_<critères>_<date>.csv` dans le dossier courant (ou `--sortie`), trié par prix. Colonnes : `id, titre, prix, devise, taille, etat, marque, date_publication, url, photo_url, favoris`. Le script sauvegarde progressivement dans `.partiel.csv`. Codes de sortie : 0 = OK, 2 = blocage (CSV partiel sauvegardé), 1 = erreur inattendue.

**Vérifie avant de conclure** : tire 5 lignes au hasard et contrôle le prix, la date (dans la fenêtre), et que l'URL ouvre l'annonce (GET qui renvoie 200, au rythme lent). Si quelque chose est incohérent, corrige et relance.

## 3b. Veille (suivi dans le temps)

Toutes les données sont dans `--dossier`, par défaut `./veille_vinted/` : `veilles.json`, `veille.db` (SQLite), `exports/` et `rapports/`.

```bash
# 1. Enregistrer une veille (les noms sont traduits en id une seule fois)
python scripts/vinted_veille.py ajouter ralph-tshirts --marque "Ralph Lauren" --marque "Polo Ralph Lauren" --categorie "Hommes > T-shirts" --prix-max 30
# 2. Exécuter : nouveautés + relevé de l'offre + vérification de 150 annonces déjà vues
python scripts/vinted_veille.py lancer --rapport
# 3. Planifier une fois par jour : affiche la commande schtasks ou cron à exécuter
python scripts/vinted_veille.py planifier --heure 08:00
# 4. Rapport de tendances à tout moment
python scripts/vinted_veille.py rapport ralph-tshirts --jours 14
```

Autres commandes : `liste`, `supprimer NOM [--donnees]`, `lancer --sans-suivi`, `lancer --suivi 300`.

Ce que fait `lancer` à chaque passage :
1. Il collecte les annonces publiées depuis l'exécution précédente, s'arrête dès qu'une page est entièrement connue, et exporte les nouveautés dans `exports/nouveautes_<veille>_<date>.csv`. Ce fichier sert d'alerte.
2. Il relève l'offre, c'est-à-dire les comptages Vinted par état, taille, couleur et marque. C'est un indice relatif : les valeurs absolues de Vinted sont incohérentes.
3. Il revérifie un échantillon d'annonces vues il y a plus de 20 h et absentes de la recherche du jour. Chacune ressort `actif`, `reserve`, `indisponible` (can_buy=false) ou `disparu` (404 ou redirection). Les trois derniers comptent comme « partis », donc probablement vendus.

Le rapport (`rapports/rapport_<veille>_<date>.md` + `_segments.csv`) donne, par taille, état, marque et tranche de prix : le taux de départ, le délai médian, le prix médian des annonces parties et des restantes, les favoris, un top 10 des segments qui se vendent le mieux, et l'évolution de l'offre.

**Coût d'un passage** : environ 25 à 130 requêtes de collecte selon les tranches, plus `--suivi` requêtes de vérification. Avec les réglages par défaut, cela fait environ 10 à 15 min par jour. Il faut **2 à 3 semaines** avant des tendances fiables : dis-le à l'utilisateur, le rapport l'affiche aussi tant que l'échantillon est trop petit.

Planifier une tâche crée une configuration persistante sur la machine : montre la commande `planifier` à l'utilisateur et laisse-le l'exécuter, ou demande-lui son accord explicite.

## 4. Résumé à l'utilisateur

Donne les chiffres de fin d'exécution (pages, annonces, période couverte, chemin des fichiers) et rappelle les limites qui changent l'interprétation :
- Les dates de publication sont **estimées** : Vinted n'affiche qu'un âge relatif. Elles sont interpolées à partir des id, à l'heure près sur quelques jours.
- « Parti » ne distingue pas une vente d'un retrait de l'annonce, et le prix réellement payé est inconnu (offres négociées).
- Le plafond de 960 résultats par recherche limite la profondeur d'une recherche ponctuelle.
