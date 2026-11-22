# vinted-scraper

Recherche d'annonces **Vinted** par critères et **veille** automatique (nouveautés, suivi des ventes, tendances), en Python, avec uniquement des outils gratuits.

Le dépôt fonctionne de deux façons :
- comme **skill Claude Code** : tu demandes en langage naturel (« trouve-moi les t-shirts Ralph Lauren taille M à moins de 30 € », « surveille les jeans Levi's et dis-moi quelles tailles partent le plus vite ») et Claude lance les bons scripts ;
- comme **scripts autonomes** en ligne de commande.

> ⚠️ Projet non affilié à Vinted. Il ne lit que des **données publiques d'annonces**, à un rythme lent (au moins 1,5 s entre deux requêtes), ne conserve **aucune donnée de vendeur** et **s'arrête proprement** au premier captcha ou blocage, sans jamais chercher à le contourner. Vérifie que ton usage respecte les conditions d'utilisation de Vinted et reste personnel.

---

## Fonctionnalités

**Recherche par critères → CSV**
- Critères : marque, catégorie (par chemin lisible : `"Hommes > T-shirts"`), taille, état, couleur, matière, motif, prix min/max, ancienneté, texte libre.
- Les noms sont traduits automatiquement en identifiants Vinted (`--lister` permet d'explorer les valeurs possibles).
- Contourne *honnêtement* le plafond de 960 résultats de Vinted en découpant la recherche par tranches de prix.
- Estime la date de publication de chaque annonce, que Vinted n'expose pas.
- CSV trié par prix, sauvegardé au fur et à mesure (rien n'est perdu en cas de crash).

**Veille**
- Recherches enregistrées, lancées une fois par jour (Planificateur de tâches Windows ou cron).
- Export des **nouvelles annonces** à chaque passage, qui sert d'alerte.
- **Suivi des ventes** : revérification d'un échantillon d'annonces déjà vues (encore en vente ? réservée ? partie ?).
- **Rapport de tendances** en Markdown et CSV : taux de départ, délai médian de vente, prix des annonces parties et des restantes, par taille, état, marque et tranche de prix. Il contient aussi un top 10 des segments qui se vendent le mieux et l'évolution de l'offre.

---

## Installation

Python 3.9 ou plus récent.

```bash
git clone https://github.com/antoineMllt1/vinted-scraper.git
cd vinted-scraper
pip install -r requirements.txt
```

**Comme skill Claude Code** : clone le dépôt dans ton dossier de skills, puis redémarre Claude Code.

```bash
# macOS / Linux
git clone https://github.com/antoineMllt1/vinted-scraper.git ~/.claude/skills/vinted-scraper
# Windows (PowerShell)
git clone https://github.com/antoineMllt1/vinted-scraper.git "$env:USERPROFILE\.claude\skills\vinted-scraper"
```

Ensuite, il suffit de demander à Claude : *« cherche sur Vinted les vestes Carhartt taille L à moins de 60 € »*.

---

## Utilisation

> Sous Windows, si les accents font planter l'affichage : `set PYTHONIOENCODING=utf-8` (cmd) ou `$env:PYTHONIOENCODING="utf-8"` (PowerShell).

### Recherche par critères

```bash
# T-shirts homme Ralph Lauren, taille M ou L, très bon état ou neuf, <= 30 €, 3 derniers mois
python scripts/vinted_recherche.py \
  --marque "Ralph Lauren" --marque "Polo Ralph Lauren" \
  --categorie "Hommes > T-shirts" \
  --taille M --taille L \
  --etat "Très bon état" --etat "Neuf sans étiquette" \
  --prix-max 30 --mois 3 --tranches 6

# Recherche texte avec filtre anti-bruit
python scripts/vinted_recherche.py --recherche "levis 501" --prix-max 25 --filtre levi
```

Plusieurs valeurs d'un même critère sont combinées en **OU**, les critères différents en **ET**.

**Explorer les valeurs possibles :**

```bash
python scripts/vinted_recherche.py --lister categories --contient "jean"
python scripts/vinted_recherche.py --lister marque --contient "carhartt"
python scripts/vinted_recherche.py --lister taille --categorie "Femmes > Jeans"
python scripts/vinted_recherche.py --lister etat
```

Colonnes du CSV : `id, titre, prix, devise, taille, etat, marque, date_publication, url, photo_url, favoris`.

### Veille

```bash
# 1. Enregistrer une veille
python scripts/vinted_veille.py ajouter ralph-tshirts \
  --marque "Ralph Lauren" --marque "Polo Ralph Lauren" --categorie "Hommes > T-shirts" --prix-max 30

# 2. La lancer (collecte + relevé de l'offre + suivi de 150 annonces) et générer le rapport
python scripts/vinted_veille.py lancer --rapport

# 3. La planifier chaque jour : affiche la commande schtasks / cron à copier
python scripts/vinted_veille.py planifier --heure 08:00

# Autres commandes
python scripts/vinted_veille.py liste
python scripts/vinted_veille.py rapport ralph-tshirts --jours 14
python scripts/vinted_veille.py supprimer ralph-tshirts --donnees
```

Les données sont stockées dans `./veille_vinted/` (modifiable avec `--dossier`) :

```
veille_vinted/
├── veilles.json      # recherches enregistrées (critères + id résolus)
├── veille.db         # SQLite : annonces, observations, offre, exécutions
├── exports/          # nouveautes_<veille>_<date>.csv à chaque passage
└── rapports/         # rapport_<veille>_<date>.md + _segments.csv
```

**Extrait de rapport :**

| Tranche de prix | Annonces | Vérifiées | Parties | Taux de départ | Délai médian | Prix médian parties |
|---|---:|---:|---:|---:|---:|---:|
| 0-5 € | 63 | 24 | 17 | 71 % | 29 h | 5.0 € |
| 5-10 € | 313 | 125 | 80 | 64 % | 29 h | 10.0 € |
| 10-15 € | 204 | 99 | 55 | 56 % | 30 h | 15.0 € |

*(chiffres de démonstration)*

Il faut compter **2 à 3 semaines** de veille quotidienne avant que les tendances soient fiables.

---

## Comment ça marche

Les grandes lignes sont ci-dessous ; le détail est dans [docs/FONCTIONNEMENT.md](docs/FONCTIONNEMENT.md), et la référence technique de l'API dans [references/api-vinted.md](references/api-vinted.md).

- Les scripts appellent la même API JSON que le site (`api.vinted.fr/svc-catalogue/items`), après avoir récupéré les cookies de session en visitant la page d'accueil.
- **Plafond de 960 résultats par recherche** : on découpe la recherche par tranches de prix, chaque tranche ayant son propre plafond.
- **Pas de date de publication publique** : on lit l'âge affiché (« Il y a 3 heures ») d'une annonce par page. Les autres dates sont interpolées grâce aux id d'annonces, qui croissent avec le temps. Les annonces « remontées » sont corrigées.
- **Pas de données de vente publiques** : on déduit les ventes en revérifiant les annonces dans le temps (`can_buy`, `is_reserved`, page disparue).

## Limites

- Les dates de publication sont **estimées** : à l'heure près sur quelques jours, beaucoup plus floues au-delà.
- Une annonce « partie » peut avoir été vendue **ou** retirée par le vendeur. Le prix réellement payé (offres négociées) est inconnu.
- Une recherche ponctuelle très large ne remonte que quelques heures ou quelques jours en arrière. Pour couvrir des mois, il faut la veille.
- Vinted modifie régulièrement son site. Si les scripts renvoient des erreurs 404, voir la section « Si un endpoint change » de [references/api-vinted.md](references/api-vinted.md).
- Seul `vinted.fr` a été vérifié (`--domaine be|es|it…` est prévu mais non testé).

## Structure du dépôt

```
vinted-scraper/
├── SKILL.md                  # instructions pour Claude Code
├── README.md
├── requirements.txt
├── LICENSE
├── scripts/
│   ├── vinted_commun.py      # client HTTP poli, résolution des critères, collecte, dates
│   ├── vinted_recherche.py   # recherche par critères -> CSV
│   └── vinted_veille.py      # veille : ajouter / lancer / rapport / planifier
├── references/
│   └── api-vinted.md         # référence technique de l'API (pour la maintenance)
├── docs/
│   └── FONCTIONNEMENT.md     # explication détaillée de la logique
└── examples/
    └── veilles.exemple.json  # exemple de fichier de veilles
```

## Licence

[MIT](LICENSE)
