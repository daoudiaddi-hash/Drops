"""
Veille Vinted : recherches enregistrées, suivi quotidien et analyse des tendances.

Vinted ne publie pas ses ventes. On les déduit en observant les annonces dans le temps :
  1. collecte des nouvelles annonces de chaque veille (même moteur que vinted_recherche.py) ;
  2. relevé de l'offre (nombre d'annonces actives par taille, état, couleur, marque) ;
  3. suivi d'un échantillon d'annonces déjà vues : encore en vente, réservée, partie ? ;
  4. rapport : ce qui part le plus vite, à quel prix, et comment l'offre évolue.

Commandes :
  python vinted_veille.py ajouter ralph-tshirts --marque "Ralph Lauren" --categorie "Hommes > T-shirts" --prix-max 30
  python vinted_veille.py liste
  python vinted_veille.py lancer                 # toutes les veilles (à planifier 1x/jour)
  python vinted_veille.py rapport ralph-tshirts --jours 14
  python vinted_veille.py planifier --heure 08:00 # affiche la commande de planification
  python vinted_veille.py supprimer ralph-tshirts

Toutes les données sont dans --dossier (défaut : ./veille_vinted) : veilles.json,
veille.db (SQLite), exports/ (nouveautés CSV) et rapports/ (Markdown).
"""

import argparse
import csv
import json
import math
import os
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import httpx

from vinted_commun import (CHAMPS_CSV, CRITERES_FACETTES, FUSEAU, BlocageVinted, ClientVinted,
                           EstimateurDates, Resolveur, collecter, configurer_logs,
                           construire_params, lire_statut, log, page_annonce, passe_le_filtre,
                           tranches_prix, vers_ligne)
from vinted_recherche import ajouter_criteres, ajouter_options_techniques, criteres_depuis

STATUTS_PARTIS = ("indisponible", "disparu", "reserve")   # « parti » = probablement vendu
FENETRE_PREMIERE_COLLECTE_JOURS = 30   # 1re exécution : plafonné de fait par les 960 résultats
MARGE_RECOUVREMENT = timedelta(hours=2)  # recouvrement entre deux exécutions
AGE_MIN_SUIVI = timedelta(hours=20)      # on ne vérifie pas une annonce vue il y a moins de 20 h
ECHANTILLON_MIN = 10                     # en dessous, un segment est signalé comme peu fiable
FILTRES_OFFRE = ["status", "size", "color", "brand"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS annonces (
    id INTEGER, veille TEXT, titre TEXT, prix REAL, devise TEXT, taille TEXT, etat TEXT,
    marque TEXT, date_publication TEXT, url TEXT, photo_url TEXT,
    favoris_initial INTEGER, favoris_dernier INTEGER,
    premiere_vue TEXT, derniere_vue TEXT,
    statut TEXT DEFAULT 'actif', date_statut TEXT, derniere_verif TEXT,
    PRIMARY KEY (id, veille));
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER, veille TEXT, date TEXT, statut TEXT, http INTEGER,
    can_buy INTEGER, is_reserved INTEGER, is_hidden INTEGER);
CREATE TABLE IF NOT EXISTS offre (
    veille TEXT, date TEXT, filtre TEXT, option_id TEXT, libelle TEXT, groupe TEXT, nb_annonces INTEGER);
CREATE TABLE IF NOT EXISTS executions (
    veille TEXT, debut TEXT, fin TEXT, nouvelles INTEGER, verifiees INTEGER,
    requetes INTEGER, code INTEGER);
"""


def maintenant():
    return datetime.now(FUSEAU)


def iso(d):
    return d.isoformat(timespec="minutes") if d else None


def depuis_iso(s):
    return datetime.fromisoformat(s) if s else None


# ============================================================================
# STOCKAGE
# ============================================================================
class Stockage:
    def __init__(self, dossier):
        self.dossier = dossier
        os.makedirs(os.path.join(dossier, "exports"), exist_ok=True)
        os.makedirs(os.path.join(dossier, "rapports"), exist_ok=True)
        self.chemin_config = os.path.join(dossier, "veilles.json")
        self.db = sqlite3.connect(os.path.join(dossier, "veille.db"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def veilles(self):
        if not os.path.exists(self.chemin_config):
            return {}
        with open(self.chemin_config, encoding="utf-8") as f:
            return json.load(f)

    def enregistrer_veilles(self, veilles):
        with open(self.chemin_config, "w", encoding="utf-8") as f:
            json.dump(veilles, f, ensure_ascii=False, indent=2)

    def ids_connus(self, veille):
        return {r[0] for r in self.db.execute("SELECT id FROM annonces WHERE veille=?", (veille,))}

    def derniere_execution(self, veille):
        r = self.db.execute("SELECT MAX(debut) FROM executions WHERE veille=? AND code=0", (veille,)).fetchone()
        return depuis_iso(r[0])

    def enregistrer_annonces(self, veille, lignes, vu_le):
        """Insère les nouvelles annonces ; pour les connues, met à jour la dernière vue
        (vue dans la recherche = encore en vente) et les favoris."""
        nouvelles = []
        for l in lignes:
            cur = self.db.execute(
                "UPDATE annonces SET derniere_vue=?, favoris_dernier=?, prix=? WHERE id=? AND veille=?",
                (vu_le, l["favoris"] or 0, float(l["prix"]), l["id"], veille))
            if cur.rowcount == 0:
                self.db.execute(
                    "INSERT INTO annonces (id, veille, titre, prix, devise, taille, etat, marque, "
                    "date_publication, url, photo_url, favoris_initial, favoris_dernier, premiere_vue, derniere_vue) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (l["id"], veille, l["titre"], float(l["prix"]), l["devise"], l["taille"], l["etat"],
                     l["marque"], l["date_publication"], l["url"], l["photo_url"],
                     l["favoris"] or 0, l["favoris"] or 0, vu_le, vu_le))
                nouvelles.append(l)
        self.db.commit()
        return nouvelles


# ============================================================================
# EXÉCUTION D'UNE VEILLE
# ============================================================================
def releve_offre(client, resolveur, stock, nom, params, quand):
    """Comptages Vinted par valeur de filtre : un indice de l'offre du jour. Leurs valeurs
    absolues sont incohérentes (vérifié), seule leur évolution pour une même requête a du
    sens. Un filtre déjà posé par la veille (ex. marque) n'est pas relevé : sa facette
    ignore la sélection et listerait les autres marques."""
    for code in FILTRES_OFFRE:
        if f"attribute_ids[{code}]" in params:
            continue
        try:
            options = resolveur.options(code, params)
        except httpx.HTTPError as e:
            log.warning("Relevé d'offre %s impossible : %r", code, e)
            continue
        stock.db.executemany(
            "INSERT INTO offre VALUES (?,?,?,?,?,?,?)",
            [(nom, quand, code, i, titre, groupe, nb) for i, titre, groupe, nb in options if nb is not None])
    stock.db.commit()


def suivre(client, stock, nom, nb, debut):
    """Vérifie un échantillon d'annonces déjà vues, en priorité jamais ou anciennement
    vérifiées. Celles revues dans la recherche du jour sont forcément encore en vente."""
    seuil_age = iso(maintenant() - AGE_MIN_SUIVI)
    candidates = stock.db.execute(
        "SELECT id, url FROM annonces WHERE veille=? AND statut IN ('actif','inconnu','reserve') "
        "AND premiere_vue <= ? AND derniere_vue < ? "
        "ORDER BY derniere_verif IS NOT NULL, derniere_verif, RANDOM() LIMIT ?",
        (nom, seuil_age, debut, nb)).fetchall()
    verifiees = 0
    for row in candidates:
        r = page_annonce(client, row["url"])
        statut, brut = lire_statut(r, row["id"])
        quand = iso(maintenant())
        stock.db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",
                         (row["id"], nom, quand, statut, brut["http"], brut["can_buy"],
                          brut["is_reserved"], brut["is_hidden"]))
        stock.db.execute(
            "UPDATE annonces SET statut=?, derniere_verif=?, "
            "date_statut=CASE WHEN statut=? THEN date_statut ELSE ? END WHERE id=? AND veille=?",
            (statut, quand, statut, quand, row["id"], nom))
        verifiees += 1
        if verifiees % 25 == 0:
            stock.db.commit()
            log.info("  suivi : %d/%d vérifiées", verifiees, len(candidates))
    stock.db.commit()
    return verifiees


def exporter_nouveautes(stock, nom, nouvelles, quand):
    if not nouvelles:
        return None
    chemin = os.path.join(stock.dossier, "exports", f"nouveautes_{nom}_{quand:%Y-%m-%d_%H%M}.csv")
    with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CHAMPS_CSV)
        w.writeheader()
        w.writerows(sorted(nouvelles, key=lambda l: float(l["prix"])))
    return chemin


def lancer_veille(client, resolveur, stock, nom, conf, nb_suivi):
    debut = maintenant()
    precedente = stock.derniere_execution(nom)
    date_limite = (precedente - MARGE_RECOUVREMENT) if precedente else \
        debut - timedelta(days=FENETRE_PREMIERE_COLLECTE_JOURS)
    log.info("=== Veille « %s » (annonces depuis %s) ===", nom, f"{date_limite:%Y-%m-%d %H:%M}")

    params = conf["params"]
    tranches = tranches_prix(conf.get("prix_min") or 0, conf.get("prix_max"), conf.get("tranches", 6))
    estimateur, annonces, stats = EstimateurDates(), {}, {"pages": 0, "brutes": 0}
    requetes_avant = client.nb_requetes
    code, nouvelles, verifiees = 0, [], 0
    try:
        collecter(client, params, tranches, date_limite, estimateur, annonces, stats=stats,
                  ids_connus=stock.ids_connus(nom))
        lignes = [vers_ligne(it, estimateur, client.base) for it in annonces.values()]
        lignes = [l for l in lignes if passe_le_filtre(l, conf.get("filtre"))
                  and float(l["prix"]) >= (conf.get("prix_min") or 0)
                  and (conf.get("prix_max") is None or float(l["prix"]) <= conf["prix_max"])]
        nouvelles = stock.enregistrer_annonces(nom, lignes, iso(debut))
        chemin = exporter_nouveautes(stock, nom, nouvelles, debut)
        log.info("%d nouvelles annonces%s", len(nouvelles), f" -> {chemin}" if chemin else "")
        releve_offre(client, resolveur, stock, nom, params, iso(debut))
        if nb_suivi:
            verifiees = suivre(client, stock, nom, nb_suivi, iso(debut))
            log.info("%d annonces vérifiées", verifiees)
    except BlocageVinted:
        code = 2
        raise
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.error("Erreur pendant la veille « %s » : %r", nom, e)
        code = 1
    finally:
        stock.db.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?)",
                         (nom, iso(debut), iso(maintenant()), len(nouvelles), verifiees,
                          client.nb_requetes - requetes_avant, code))
        stock.db.commit()
    return code


# ============================================================================
# RAPPORT DE TENDANCES
# ============================================================================
def tranche_prix_libelle(prix, pas=5):
    bas = max(0, math.ceil(prix / pas) - 1) * pas   # 30 € tombe dans 25-30 €, pas 30-35 €
    return f"{bas:>3}-{bas + pas} €"


def est_verifiee(r):
    return bool(r["derniere_verif"]) and r["statut"] != "inconnu"


def heures_entre(a, b):
    if not a or not b:
        return None
    return (depuis_iso(b) - depuis_iso(a)).total_seconds() / 3600


def mediane(valeurs):
    v = [x for x in valeurs if x is not None]
    return statistics.median(v) if v else None


def fmt(x, suffixe="", dec=1):
    return "–" if x is None else f"{x:.{dec}f}{suffixe}"


def segmenter(rows, cle, tri_par_segment=False):
    groupes = defaultdict(list)
    for r in rows:
        groupes[cle(r) or "(non renseigné)"].append(r)
    lignes = []
    for valeur, g in groupes.items():
        verif = [r for r in g if est_verifiee(r)]
        partis = [r for r in verif if r["statut"] in STATUTS_PARTIS]
        restants = [r for r in g if r["statut"] not in STATUTS_PARTIS]
        lignes.append({
            "segment": valeur, "annonces": len(g), "verifiees": len(verif), "parties": len(partis),
            "taux_depart": (100 * len(partis) / len(verif)) if verif else None,
            "delai_median_h": mediane(heures_entre(r["premiere_vue"], r["date_statut"]) for r in partis),
            "prix_median_parties": mediane(r["prix"] for r in partis),
            "prix_median_restantes": mediane(r["prix"] for r in restants),
            "favoris_moyens": statistics.mean(r["favoris_dernier"] or 0 for r in g),
            "fiable": len(verif) >= ECHANTILLON_MIN,
        })
    if tri_par_segment:
        return sorted(lignes, key=lambda l: l["segment"])
    return sorted(lignes, key=lambda l: (l["fiable"], l["taux_depart"] or 0), reverse=True)


def tableau(lignes, titre_segment, limite=15):
    out = [f"| {titre_segment} | Annonces | Vérifiées | Parties | Taux de départ | Délai médian | "
           f"Prix médian parties | Prix médian restantes | Favoris moy. |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for l in lignes[:limite]:
        alerte = "" if l["fiable"] else " ⚠️"
        delai = l["delai_median_h"]
        delai_txt = "–" if delai is None else (f"{delai:.0f} h" if delai < 48 else f"{delai / 24:.1f} j")
        out.append(f"| {str(l['segment']).strip()}{alerte} | {l['annonces']} | {l['verifiees']} | {l['parties']} | "
                   f"{fmt(l['taux_depart'], ' %', 0)} | {delai_txt} | {fmt(l['prix_median_parties'], ' €')} | "
                   f"{fmt(l['prix_median_restantes'], ' €')} | {fmt(l['favoris_moyens'])} |")
    return "\n".join(out)


def evolution_offre(stock, nom, depuis):
    """Variation du nombre d'annonces actives par valeur de filtre sur la période."""
    rows = stock.db.execute(
        "SELECT filtre, libelle, groupe, date, nb_annonces FROM offre WHERE veille=? AND date>=? "
        "ORDER BY date", (nom, depuis)).fetchall()
    series = defaultdict(list)
    for r in rows:
        series[(r["filtre"], r["libelle"], r["groupe"])].append(r["nb_annonces"])
    res = []
    for (filtre, libelle, groupe), v in series.items():
        if len(v) >= 2 and v[0]:
            res.append((filtre, libelle + (f" [{groupe}]" if groupe else ""), v[0], v[-1], 100 * (v[-1] - v[0]) / v[0]))
    return res


def rapport(stock, nom, conf, jours):
    fin = maintenant()
    depuis = iso(fin - timedelta(days=jours))
    rows = stock.db.execute("SELECT * FROM annonces WHERE veille=? AND premiere_vue>=?", (nom, depuis)).fetchall()
    execs = stock.db.execute("SELECT * FROM executions WHERE veille=? AND debut>=? ORDER BY debut",
                             (nom, depuis)).fetchall()
    verif = [r for r in rows if est_verifiee(r)]
    partis = [r for r in verif if r["statut"] in STATUTS_PARTIS]
    nb_jours_collecte = len({e["debut"][:10] for e in execs}) or 1

    md = [f"# Rapport de veille Vinted : {nom}",
          f"*Période : {jours} derniers jours (du {fin - timedelta(days=jours):%d/%m/%Y} au {fin:%d/%m/%Y}), "
          f"généré le {fin:%d/%m/%Y à %H:%M}.*", "",
          f"**Critères :** `{json.dumps(conf.get('criteres'), ensure_ascii=False)}`", "",
          "## Vue d'ensemble", "",
          f"- Exécutions : {len(execs)} sur {nb_jours_collecte} jour(s), {sum(e['requetes'] or 0 for e in execs)} requêtes",
          f"- Annonces collectées : {len(rows)} (≈ {len(rows) / nb_jours_collecte:.0f} nouvelles / jour)",
          f"- Annonces vérifiées : {len(verif)} ; parties (probablement vendues) : {len(partis)}"
          + (f" soit **{100 * len(partis) / len(verif):.0f} %**" if verif else ""),
          f"- Délai médian avant départ : {fmt(mediane(heures_entre(r['premiere_vue'], r['date_statut']) for r in partis), ' h', 0)}",
          f"- Prix médian des annonces parties : {fmt(mediane(r['prix'] for r in partis), ' €')} "
          f"(restantes : {fmt(mediane(r['prix'] for r in rows if r['statut'] not in STATUTS_PARTIS), ' €')})", ""]

    if len(verif) < 3 * ECHANTILLON_MIN:
        md += ["> ⚠️ **Pas encore assez de données.** Il faut en général 2 à 3 semaines de veille "
               "quotidienne pour des tendances fiables. Les chiffres ci-dessous sont indicatifs.", ""]

    segments = {
        "Taille": lambda r: r["taille"], "État": lambda r: r["etat"], "Marque": lambda r: r["marque"],
        "Tranche de prix": lambda r: tranche_prix_libelle(r["prix"]),
    }
    md += ["## Ce qui part le plus vite", "",
           "Taux de départ = part des annonces vérifiées qui ne sont plus en vente (vendues, réservées "
           "ou retirées). ⚠️ = moins de %d annonces vérifiées, peu fiable." % ECHANTILLON_MIN, ""]
    toutes = []
    for titre, cle in segments.items():
        lignes = segmenter(rows, cle, tri_par_segment=(titre == "Tranche de prix"))
        toutes += [{**l, "dimension": titre} for l in lignes]
        md += [f"### Par {titre.lower()}", "", tableau(lignes, titre), ""]

    fiables = sorted((l for l in toutes if l["fiable"] and l["taux_depart"] is not None),
                     key=lambda l: l["taux_depart"], reverse=True)
    if fiables:
        md += ["## Top 10 des segments qui se vendent le mieux", ""]
        md += [f"{i}. **{l['dimension']} : {str(l['segment']).strip()}** : {l['taux_depart']:.0f} % de départ, "
               f"prix médian vendu {fmt(l['prix_median_parties'], ' €')}"
               for i, l in enumerate(fiables[:10], 1)]
        md.append("")

    evo = evolution_offre(stock, nom, depuis)
    if evo:
        md += ["## Évolution de l'offre (indice)", "",
               "Comptages renvoyés par les filtres Vinted. Leurs valeurs absolues ne sont pas "
               "fiables, seule la variation sur la période compte.", "",
               "| Filtre | Valeur | Début | Fin | Variation |", "|---|---|---:|---:|---:|"]
        evo.sort(key=lambda x: x[4])
        extremes = evo if len(evo) <= 20 else evo[:10] + evo[-10:]   # 10 plus fortes baisses et hausses
        for filtre, libelle, a, b, pct in extremes:
            md.append(f"| {filtre} | {libelle} | {a} | {b} | {pct:+.1f} % |")
        md += ["", "Une offre qui baisse alors que le taux de départ reste élevé signale une demande "
               "supérieure à l'offre : c'est là qu'il y a des opportunités.", ""]

    md += ["## Limites", "",
           "- « Parti » ne distingue pas une vente d'un retrait par le vendeur.",
           "- Le prix réellement payé est inconnu (offres négociées) : seul le prix affiché est connu.",
           "- Le suivi porte sur un échantillon : les petits segments ont une forte marge d'erreur.",
           "- Les dates de publication sont estimées (Vinted n'affiche qu'un âge relatif).", ""]

    chemin = os.path.join(stock.dossier, "rapports", f"rapport_{nom}_{fin:%Y-%m-%d}.md")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    chemin_csv = chemin.replace(".md", "_segments.csv")
    with open(chemin_csv, "w", newline="", encoding="utf-8-sig") as f:
        champs = ["dimension", "segment", "annonces", "verifiees", "parties", "taux_depart",
                  "delai_median_h", "prix_median_parties", "prix_median_restantes", "favoris_moyens", "fiable"]
        w = csv.DictWriter(f, fieldnames=champs)
        w.writeheader()
        w.writerows(toutes)
    return chemin, chemin_csv


# ============================================================================
# COMMANDES
# ============================================================================
def cmd_ajouter(args, stock):
    veilles = stock.veilles()
    if args.nom in veilles and not args.remplacer:
        sys.exit(f"La veille « {args.nom} » existe déjà (--remplacer pour l'écraser).")
    criteres = criteres_depuis(args)
    if not any(criteres.values()):
        sys.exit("Indique au moins un critère (--recherche, --marque, --categorie...).")
    client = ClientVinted(args.domaine, args.delai)
    try:
        client.ouvrir_session()
        params = construire_params(Resolveur(client), criteres)   # noms -> id, une fois pour toutes
        r = client.api_json("/svc-catalogue/items", {**params, "per_page": 1})
        total = (r.json().get("pagination") or {}).get("total_entries") if r.status_code == 200 else None
    except (ValueError, BlocageVinted) as e:
        sys.exit(f"Erreur : {e}")
    finally:
        client.fermer()
    veilles[args.nom] = {"criteres": {k: v for k, v in criteres.items() if v}, "params": params,
                         "prix_min": args.prix_min or 0, "prix_max": args.prix_max,
                         "tranches": args.tranches, "filtre": args.filtre, "domaine": args.domaine,
                         "creee_le": iso(maintenant())}
    stock.enregistrer_veilles(veilles)
    print(f"Veille « {args.nom} » enregistrée : {params}")
    if total is not None:
        print(f"Annonces correspondantes actuellement : {total}{'+ (plafond de la recherche)' if total >= 960 else ''}")


def cmd_liste(args, stock):
    veilles = stock.veilles()
    if not veilles:
        print("Aucune veille. Ajoute-en une avec : vinted_veille.py ajouter NOM --marque ...")
    for nom, conf in veilles.items():
        n = stock.db.execute("SELECT COUNT(*) FROM annonces WHERE veille=?", (nom,)).fetchone()[0]
        der = stock.derniere_execution(nom)
        print(f"- {nom} : {json.dumps(conf['criteres'], ensure_ascii=False)} | prix {conf.get('prix_min') or 0}"
              f"-{conf.get('prix_max') or '∞'} € | {n} annonces | dernière exécution : {iso(der) or 'jamais'}")


def cmd_supprimer(args, stock):
    veilles = stock.veilles()
    if veilles.pop(args.nom, None) is None:
        sys.exit(f"Veille « {args.nom} » inconnue.")
    stock.enregistrer_veilles(veilles)
    if args.donnees:
        for table in ("annonces", "observations", "offre", "executions"):
            stock.db.execute(f"DELETE FROM {table} WHERE veille=?", (args.nom,))
        stock.db.commit()
    print(f"Veille « {args.nom} » supprimée" + (" avec ses données." if args.donnees else " (données conservées)."))


def cmd_lancer(args, stock):
    veilles = stock.veilles()
    noms = args.noms or list(veilles)
    if not noms:
        sys.exit("Aucune veille à lancer.")
    par_domaine = defaultdict(list)
    for nom in noms:
        if nom not in veilles:
            sys.exit(f"Veille « {nom} » inconnue.")
        par_domaine[veilles[nom].get("domaine", "fr")].append(nom)
    code_global = 0
    for domaine, noms_dom in par_domaine.items():
        client = ClientVinted(domaine, args.delai)
        try:
            client.ouvrir_session()
            resolveur = Resolveur(client)
            for nom in noms_dom:
                code_global = max(code_global, lancer_veille(client, resolveur, stock, nom,
                                                             veilles[nom], 0 if args.sans_suivi else args.suivi))
        except BlocageVinted as e:
            log.error("BLOCAGE : %s. Arrêt propre, les données déjà collectées sont en base.", e)
            code_global = 2
            break
        finally:
            client.fermer()
        print(f"Requêtes HTTP ({domaine}) : {client.nb_requetes}")
    if args.rapport:
        for nom in noms:
            print("Rapport :", rapport(stock, nom, veilles[nom], args.jours)[0])
    sys.exit(code_global)


def cmd_rapport(args, stock):
    veilles = stock.veilles()
    for nom in args.noms or list(veilles):
        if nom not in veilles:
            sys.exit(f"Veille « {nom} » inconnue.")
        md, csv_ = rapport(stock, nom, veilles[nom], args.jours)
        print(f"Rapport : {md}\nSegments : {csv_}")


def cmd_planifier(args, stock):
    script = os.path.abspath(__file__)
    dossier = os.path.abspath(stock.dossier)
    python = sys.executable
    if os.name == "nt":
        tr = f'\\"{python}\\" \\"{script}\\" lancer --dossier \\"{dossier}\\" --rapport'
        print("Windows (Planificateur de tâches), à coller dans un terminal :\n")
        print(f'schtasks /Create /SC DAILY /ST {args.heure} /TN "Veille Vinted" /TR "{tr}" /F')
        print("\nPour supprimer : schtasks /Delete /TN \"Veille Vinted\" /F")
    else:
        h, m = args.heure.split(":")
        print("Linux / macOS (crontab -e), ajouter la ligne :\n")
        print(f'{int(m)} {int(h)} * * * cd "{dossier}" && "{python}" "{script}" lancer --dossier "{dossier}" '
              f'--rapport >> "{dossier}/veille.log" 2>&1')


def main():
    p = argparse.ArgumentParser(description="Veille Vinted : suivi quotidien et tendances de vente")
    p.add_argument("--dossier", default=os.path.join(os.getcwd(), "veille_vinted"),
                   help="dossier des données de veille (défaut : ./veille_vinted)")
    sous = p.add_subparsers(dest="commande", required=True)

    a = sous.add_parser("ajouter", help="enregistrer une nouvelle veille")
    a.add_argument("nom")
    ajouter_criteres(a)
    ajouter_options_techniques(a)
    a.add_argument("--remplacer", action="store_true")
    a.set_defaults(func=cmd_ajouter)

    sous.add_parser("liste", help="lister les veilles").set_defaults(func=cmd_liste)

    s = sous.add_parser("supprimer", help="supprimer une veille")
    s.add_argument("nom")
    s.add_argument("--donnees", action="store_true", help="supprimer aussi ses données en base")
    s.set_defaults(func=cmd_supprimer)

    l = sous.add_parser("lancer", help="exécuter les veilles (à planifier une fois par jour)")
    l.add_argument("noms", nargs="*", help="veilles à lancer (défaut : toutes)")
    l.add_argument("--suivi", type=int, default=150, help="annonces à revérifier par veille (défaut 150)")
    l.add_argument("--sans-suivi", action="store_true", help="collecte seule, sans vérification")
    l.add_argument("--rapport", action="store_true", help="générer le rapport après l'exécution")
    l.add_argument("--jours", type=int, default=14, help="période du rapport")
    l.add_argument("--delai", type=float, default=1.5)
    l.set_defaults(func=cmd_lancer)

    r = sous.add_parser("rapport", help="générer le rapport de tendances")
    r.add_argument("noms", nargs="*")
    r.add_argument("--jours", type=int, default=14)
    r.set_defaults(func=cmd_rapport)

    pl = sous.add_parser("planifier", help="afficher la commande pour lancer la veille chaque jour")
    pl.add_argument("--heure", default="08:00")
    pl.set_defaults(func=cmd_planifier)

    args = p.parse_args()
    configurer_logs()
    random.seed()
    args.func(args, Stockage(args.dossier))


if __name__ == "__main__":
    main()
