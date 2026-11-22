"""
Recherche Vinted par critères -> CSV trié par prix.

Exemples :
  # T-shirts homme Ralph Lauren, très bon état ou mieux, taille M ou L, <= 30 €, 3 mois
  python vinted_recherche.py --marque "Ralph Lauren" --marque "Polo Ralph Lauren" \
      --categorie "Hommes > T-shirts" --etat "Très bon état" --etat "Neuf sans étiquette" \
      --taille M --taille L --prix-max 30 --mois 3

  # Recherche texte simple avec filtre anti-bruit
  python vinted_recherche.py --recherche "levis 501" --prix-max 25 --filtre levi

  # Explorer les valeurs possibles d'un critère
  python vinted_recherche.py --lister categories --contient t-shirt
  python vinted_recherche.py --lister taille --categorie "Hommes > T-shirts"
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timedelta

import httpx

from vinted_commun import (CHAMPS_CSV, CRITERES_FACETTES, FUSEAU, BlocageVinted, ClientVinted,
                           EstimateurDates, Resolveur, collecter, configurer_logs,
                           construire_params, log, normaliser, passe_le_filtre, slug,
                           tranches_prix, vers_ligne)

SEUIL_RESULTATS_MAIGRES = 200   # en dessous, on lance les variantes de mots-clés


def ajouter_criteres(p):
    """Arguments de critères, partagés avec vinted_veille.py."""
    g = p.add_argument_group("critères (répétables : plusieurs valeurs = OU)")
    g.add_argument("--recherche", help="texte de recherche libre")
    g.add_argument("--marque", action="append", default=[], help="nom ou id de marque")
    g.add_argument("--categorie", action="append", default=[],
                   help="nom, chemin ('Hommes > T-shirts') ou id de catégorie")
    g.add_argument("--taille", action="append", default=[], help="ex. M, 42, 6 ans / 116 cm")
    g.add_argument("--etat", action="append", default=[],
                   help="Neuf avec étiquette, Neuf sans étiquette, Très bon état, Bon état, Satisfaisant")
    g.add_argument("--couleur", action="append", default=[], help="ex. Noir, Bleu marine")
    g.add_argument("--matiere", action="append", default=[], help="ex. Coton, Lin")
    g.add_argument("--motif", action="append", default=[], help="ex. Rayures, Uni")
    g.add_argument("--prix-min", type=float, default=0)
    g.add_argument("--prix-max", type=float)
    g.add_argument("--filtre", action="append", default=[],
                   help="mot que le titre ou la marque doit contenir (anti-bruit, OU)")
    g.add_argument("--tranches", type=int, default=6,
                   help="nombre de tranches de prix pour dépasser le plafond de 960 (nécessite --prix-max)")


def ajouter_options_techniques(p):
    p.add_argument("--domaine", default="fr", help="extension du site : fr, be, es... (fr vérifié)")
    p.add_argument("--delai", type=float, default=1.5, help="secondes entre requêtes (>= 1,5)")


def criteres_depuis(args):
    return {"recherche": args.recherche, "marques": args.marque, "categories": args.categorie,
            **{c: getattr(args, c) for c in CRITERES_FACETTES}}


def lister(client, resolveur, args):
    """Affiche les valeurs possibles d'un critère, pour choisir ses filtres."""
    quoi = args.lister
    if quoi == "categories":
        for i, chemin in resolveur.categories():
            if not args.contient or normaliser(args.contient) in normaliser(chemin):
                print(f"{i:>6}  {chemin}")
    elif quoi == "marque":
        r = client.get(client.base + "/api/v2/brands", params={"keyword": args.contient or ""},
                       headers={"Accept": "application/json"})
        for m in r.json().get("brands", []):
            print(f"{m['id']:>8}  {m['title']}  ({m.get('pretty_item_count', '?')} annonces)")
    else:
        contexte = {k: v for k, v in construire_params(resolveur, {
            "recherche": args.recherche, "categories": args.categorie, "marques": args.marque}).items()}
        for i, titre, groupe, nb in resolveur.options(CRITERES_FACETTES[quoi], contexte):
            if not args.contient or normaliser(args.contient) in normaliser(titre):
                print(f"{i:>8}  {titre}" + (f"  [{groupe}]" if groupe else "") + (f"  ({nb})" if nb else ""))


def main():
    p = argparse.ArgumentParser(description="Recherche Vinted par critères (annonces publiques) -> CSV")
    ajouter_criteres(p)
    p.add_argument("--variante", action="append", default=[],
                   help="texte de recherche de secours si résultats filtrés maigres")
    p.add_argument("--mois", type=float, default=3, help="ancienneté maximale en mois")
    p.add_argument("--sortie", help="chemin du CSV (défaut : vinted_<critères>_<date>.csv)")
    p.add_argument("--lister", choices=["categories", "marque", *CRITERES_FACETTES],
                   help="affiche les valeurs possibles d'un critère puis quitte")
    p.add_argument("--contient", help="avec --lister : ne garder que les libellés contenant ce texte")
    ajouter_options_techniques(p)
    args = p.parse_args()
    configurer_logs()

    client = ClientVinted(args.domaine, args.delai)
    try:
        client.ouvrir_session()
        resolveur = Resolveur(client)
        if args.lister:
            lister(client, resolveur, args)
            client.fermer()
            return
        criteres = criteres_depuis(args)
        if not any(criteres.values()):
            p.error("indique au moins un critère (--recherche, --marque, --categorie...)")
        params = construire_params(resolveur, criteres)
    except (ValueError, BlocageVinted) as e:
        log.error("%s", e)
        client.fermer()
        sys.exit(2)

    maintenant = datetime.now(FUSEAU)
    date_limite = maintenant - timedelta(days=30 * args.mois)
    nom = slug(args.recherche or " ".join(args.marque + args.categorie)) or "recherche"
    chemin = args.sortie or os.path.join(os.getcwd(), f"vinted_{nom[:50]}_{maintenant:%Y-%m-%d}.csv")
    chemin_partiel = re.sub(r"\.csv$", "", chemin) + ".partiel.csv"
    if args.prix_max is None and args.tranches > 1:
        log.warning("Sans --prix-max, pas de découpage en tranches : 960 annonces maximum")
    tranches = tranches_prix(args.prix_min, args.prix_max, args.tranches)

    estimateur = EstimateurDates()
    annonces = {}
    stats = {"pages": 0, "brutes": 0}

    # Sauvegarde progressive : chaque page est ajoutée au fichier partiel
    f_partiel = open(chemin_partiel, "w", newline="", encoding="utf-8-sig")
    w_partiel = csv.DictWriter(f_partiel, fieldnames=CHAMPS_CSV)
    w_partiel.writeheader()

    def sauvegarde(items):
        for it in items:
            w_partiel.writerow(vers_ligne(it, estimateur, client.base))
        f_partiel.flush()

    code_sortie = 0
    try:
        collecter(client, params, tranches, date_limite, estimateur, annonces, sauvegarde, stats)
        nb = sum(passe_le_filtre(vers_ligne(it, estimateur, client.base), args.filtre) for it in annonces.values())
        if args.variante and nb < SEUIL_RESULTATS_MAIGRES:
            log.info("Résultats maigres (%d), ajout des variantes", nb)
            for v in args.variante:
                collecter(client, {**params, "search_text": v}, tranches, date_limite,
                          estimateur, annonces, sauvegarde, stats)
    except BlocageVinted as e:
        log.error("BLOCAGE : %s. Arrêt propre, sauvegarde de ce qui a été récupéré.", e)
        code_sortie = 2
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.error("Erreur inattendue : %r. Sauvegarde de ce qui a été récupéré.", e)
        code_sortie = 1
    finally:
        f_partiel.close()
        client.fermer()
        # Export final : dates recalculées avec tous les repères, filtres, fenêtre, tri par prix
        lignes = []
        for it in annonces.values():
            ligne = vers_ligne(it, estimateur, client.base)
            prix = float(ligne["prix"])
            if not passe_le_filtre(ligne, args.filtre) or prix < args.prix_min:
                continue
            if args.prix_max is not None and prix > args.prix_max:
                continue
            date = estimateur.estimer(it["id"])
            if date and date < date_limite:
                continue
            lignes.append(ligne)
        lignes.sort(key=lambda l: float(l["prix"]))
        with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CHAMPS_CSV)
            w.writeheader()
            w.writerows(lignes)
        os.remove(chemin_partiel)

        print("\n===== RÉSULTAT =====")
        print(f"Pages parcourues      : {stats['pages']}")
        print(f"Requêtes HTTP         : {client.nb_requetes}")
        print(f"Annonces brutes       : {stats['brutes']} ({len(annonces)} uniques)")
        print(f"Annonces après filtre : {len(lignes)}")
        nb_corriges = sum(1 for i, d in estimateur.reperes_corriges() if d != estimateur.reperes[i])
        print(f"Repères de date       : {len(estimateur.reperes)} ({nb_corriges} corrigés car remontés)")
        dates = [d for d in (estimateur.estimer(i) for i in annonces) if d]
        if dates:
            print(f"Période couverte      : {min(dates):%Y-%m-%d %H:%M} -> {max(dates):%Y-%m-%d %H:%M}")
        print(f"CSV                   : {chemin}")
    sys.exit(code_sortie)


if __name__ == "__main__":
    main()
