"""
Briques communes aux scripts : client HTTP poli, résolution des critères (noms -> id
Vinted), collecte paginée, estimation des dates et extraction des champs publics.

Règles appliquées partout :
- au moins 1,5 s entre deux requêtes, backoff exponentiel sur 429 / 503 ;
- arrêt immédiat (exception BlocageVinted) sur captcha, challenge ou 403 ;
- aucune donnée vendeur n'est extraite (l'objet `user` de l'API est ignoré).
"""

import json
import logging
import random
import re
import time
import unicodedata
from bisect import bisect_left
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

# ============================================================================
# CONSTANTES
# ============================================================================
DELAI_MIN = 1.5               # secondes minimum entre deux requêtes (non négociable)
MAX_TENTATIVES = 5            # backoff exponentiel sur 429 / 503
PAGES_MAX = 10                # plafond Vinted : 10 pages x 96 = 960 résultats
PAR_PAGE = 96                 # maximum accepté par l'API
FUSEAU = ZoneInfo("Europe/Paris")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

CHAMPS_CSV = ["id", "titre", "prix", "devise", "taille", "etat", "marque",
              "date_publication", "url", "photo_url", "favoris"]

# Critère CLI -> code de filtre Vinted (options obtenues via svc-filters/filters/facets)
CRITERES_FACETTES = {"taille": "size", "etat": "status", "couleur": "color",
                     "matiere": "material", "motif": "patterns"}

log = logging.getLogger("vinted")


def configurer_logs():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)


class BlocageVinted(Exception):
    """Captcha, challenge Cloudflare ou refus d'accès : on arrête tout."""


def normaliser(texte):
    """Minuscules, sans accents, espaces simples : pour comparer des libellés."""
    t = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def slug(texte):
    return re.sub(r"[^a-z0-9]+", "_", normaliser(texte)).strip("_")


# ============================================================================
# CLIENT HTTP (rythme lent, backoff, détection de blocage)
# ============================================================================
class ClientVinted:
    def __init__(self, domaine="fr", delai=DELAI_MIN):
        self.base = f"https://www.vinted.{domaine}"
        self.api = f"https://api.vinted.{domaine}"
        self.delai = max(delai, DELAI_MIN)
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
            follow_redirects=True, timeout=30)
        self.derniere_requete = 0.0
        self.nb_requetes = 0
        self.html_accueil = ""

    def _attendre(self):
        ecart = self.delai + random.uniform(0, 0.5) - (time.monotonic() - self.derniere_requete)
        if ecart > 0:
            time.sleep(ecart)

    @staticmethod
    def _verifier_blocage(r):
        texte = r.text[:5000].lower()
        if r.headers.get("cf-mitigated") or "captcha" in texte or "challenge-platform" in texte:
            raise BlocageVinted(f"captcha/challenge détecté ({r.status_code} sur {r.url})")
        if r.status_code == 403:
            raise BlocageVinted(f"accès refusé 403 sur {r.url}")

    def get(self, url, **kwargs):
        for tentative in range(MAX_TENTATIVES):
            self._attendre()
            r = self.client.get(url, **kwargs)
            self.derniere_requete = time.monotonic()
            self.nb_requetes += 1
            if r.status_code in (429, 503):
                attente = float(r.headers.get("Retry-After") or self.delai * 2 ** (tentative + 1))
                log.warning("HTTP %s, nouvel essai dans %.0f s", r.status_code, attente)
                time.sleep(attente)
                continue
            if r.status_code == 401 and tentative == 0:
                # Jeton de session expiré : on le renouvelle via la page d'accueil
                log.info("Session expirée, renouvellement des cookies")
                self.ouvrir_session()
                continue
            self._verifier_blocage(r)
            return r
        raise BlocageVinted(f"trop d'erreurs 429/503 sur {url}")

    def ouvrir_session(self):
        # La page d'accueil pose les cookies exigés par l'API et contient l'arbre des catégories
        r = self.get(self.base + "/")
        r.raise_for_status()
        if "access_token_web" not in self.client.cookies:
            raise BlocageVinted("pas de cookie de session après la page d'accueil")
        self.html_accueil = r.text

    def api_json(self, chemin, params, referer=None):
        r = self.get(self.api + chemin, params=params,
                     headers={"Accept": "application/json, text/plain, */*",
                              "Referer": referer or self.base + "/catalog", "Origin": self.base})
        if r.status_code == 200 and "json" not in r.headers.get("content-type", ""):
            raise BlocageVinted(f"réponse non JSON sur {chemin}")
        return r

    def fermer(self):
        self.client.close()


# ============================================================================
# RÉSOLUTION DES CRITÈRES (noms lisibles -> id Vinted)
# ============================================================================
def _arbre_categories(html):
    """Extrait l'arbre complet des catégories embarqué dans le HTML de la page d'accueil."""
    t = html.replace('\\"', '"')
    meilleur, taille_max = [], 0

    def compter(noeuds):
        return sum(1 + compter(n.get("catalogs") or []) for n in noeuds)

    for m in re.finditer(r'\[\{"id":\d+,"title":"[^"]+","url":"/catalog/', t):
        try:
            obj, _ = json.JSONDecoder().raw_decode(t, m.start())
        except ValueError:
            continue
        n = compter(obj)
        if n > taille_max:
            meilleur, taille_max = obj, n
    return meilleur


def _aplatir_categories(noeuds, chemin=()):
    for n in noeuds:
        c = chemin + (n["title"],)
        yield n["id"], " > ".join(c)
        yield from _aplatir_categories(n.get("catalogs") or [], c)


def _aplatir_options(options, groupe=""):
    for o in options or []:
        if o.get("options"):   # groupe (ex. tailles hommes / femmes)
            yield from _aplatir_options(o["options"], o.get("title", ""))
        else:
            yield str(o["id"]), o.get("title", ""), groupe, o.get("items_count")


class Resolveur:
    """Traduit 'Ralph Lauren', 'Hommes > T-shirts', 'Très bon état'... en id Vinted."""

    def __init__(self, client):
        self.client = client
        self._categories = None
        self._options = {}

    def categories(self):
        if self._categories is None:
            self._categories = list(_aplatir_categories(_arbre_categories(self.client.html_accueil)))
            if not self._categories:
                raise ValueError("arbre des catégories introuvable dans la page d'accueil")
        return self._categories

    def categorie(self, valeur):
        if str(valeur).isdigit():
            return int(valeur)
        segments = [normaliser(s) for s in str(valeur).split(">") if s.strip()]

        def correspond(chemin):
            parties = [normaliser(p) for p in chemin.split(" > ")]
            if parties[-1] != segments[-1]:
                return False
            # les segments précédents doivent apparaître dans l'ordre dans le chemin
            i = 0
            for p in parties[:-1]:
                if i < len(segments) - 1 and p == segments[i]:
                    i += 1
            return i == len(segments) - 1

        candidats = [(i, c) for i, c in self.categories() if correspond(c)]
        if len(candidats) == 1:
            log.info("Catégorie « %s » -> %s (%s)", valeur, candidats[0][0], candidats[0][1])
            return candidats[0][0]
        if not candidats:
            raise ValueError(f"catégorie introuvable : « {valeur} » (voir --lister categories)")
        liste = "\n  ".join(f"{i} : {c}" for i, c in candidats[:15])
        raise ValueError(f"catégorie ambiguë « {valeur} », précise le chemin "
                         f"(ex. 'Hommes > T-shirts') ou l'id :\n  {liste}")

    def marque(self, valeur):
        if str(valeur).isdigit():
            return int(valeur)
        r = self.client.get(self.client.base + "/api/v2/brands", params={"keyword": valeur},
                            headers={"Accept": "application/json"})
        marques = r.json().get("brands", []) if r.status_code == 200 else []
        if not marques:
            raise ValueError(f"marque introuvable : « {valeur} »")
        exactes = [m for m in marques if normaliser(m["title"]) == normaliser(valeur)]
        choix = (exactes or marques)[0]
        log.info("Marque « %s » -> %s (%s)", valeur, choix["id"], choix["title"])
        return choix["id"]

    def options(self, code, contexte):
        """Options d'un filtre (taille, état...) avec leur nombre d'annonces actives."""
        cle = (code, tuple(sorted(contexte.items())))
        if cle not in self._options:
            r = self.client.api_json("/svc-filters/filters/facets", {**contexte, "filter_code": code})
            r.raise_for_status()
            self._options[cle] = list(_aplatir_options(r.json().get("options")))
        return self._options[cle]

    def facette(self, code, valeur, contexte):
        if str(valeur).isdigit():
            return [str(valeur)]
        trouves = [o for o in self.options(code, contexte) if normaliser(o[1]) == normaliser(valeur)]
        if not trouves:
            dispo = ", ".join(sorted({o[1] for o in self.options(code, contexte)})[:40])
            raise ValueError(f"valeur « {valeur} » inconnue pour {code}. Disponibles : {dispo}")
        log.info("%s « %s » -> %s", code, valeur, ",".join(o[0] for o in trouves))
        return [o[0] for o in trouves]


def construire_params(resolveur, criteres):
    """criteres : dict avec recherche, marques, categories, taille, etat, couleur, matiere,
    motif (listes). Renvoie les paramètres de l'API svc-catalogue (valeurs séparées par
    des virgules : répéter le paramètre désactive le filtre)."""
    params = {}
    if criteres.get("recherche"):
        params["search_text"] = criteres["recherche"]
    if criteres.get("categories"):
        params["attribute_ids[catalog]"] = ",".join(
            str(resolveur.categorie(c)) for c in criteres["categories"])
    if criteres.get("marques"):
        params["attribute_ids[brand]"] = ",".join(
            str(resolveur.marque(m)) for m in criteres["marques"])
    contexte = dict(params)   # les options de taille dépendent de la catégorie
    for critere, code in CRITERES_FACETTES.items():
        valeurs = criteres.get(critere) or []
        if valeurs:
            ids = [i for v in valeurs for i in resolveur.facette(code, v, contexte)]
            params[f"attribute_ids[{code}]"] = ",".join(dict.fromkeys(ids))
    return params


# ============================================================================
# DATES : âge relatif -> date absolue, puis interpolation par id
# ============================================================================
_UNITES = {"minute": "minutes", "heure": "hours", "jour": "days", "semaine": "weeks"}


def age_vers_date(texte, maintenant):
    """Convertit 'Il y a 3 jours', 'A l'instant', 'Il y a un mois'... en datetime."""
    t = texte.lower().strip()
    if "instant" in t:
        return maintenant
    m = re.search(r"(\d+|une?)\s+(minute|heure|jour|semaine|mois|an)", t)
    if not m:
        return None
    n = 1 if m.group(1) in ("un", "une") else int(m.group(1))
    unite = m.group(2)
    if unite == "mois":
        return maintenant - timedelta(days=30 * n)
    if unite == "an":
        return maintenant - timedelta(days=365 * n)
    return maintenant - timedelta(**{_UNITES[unite]: n})


class EstimateurDates:
    """Repères (id, date affichée) et interpolation linéaire entre eux.

    Une annonce remontée affiche une date plus récente que sa création réelle. Comme les
    id croissent avec la date de création, on corrige chaque repère par l'enveloppe
    inférieure : date(id) <= date de tout repère d'id plus grand.
    """

    def __init__(self):
        self.reperes = {}  # id -> datetime affichée
        self._cache = None

    def ajouter(self, item_id, date):
        self.reperes[item_id] = date
        self._cache = None

    def reperes_corriges(self):
        if self._cache is None:
            res, mini = [], None
            for item_id, date in sorted(self.reperes.items(), reverse=True):
                mini = date if mini is None else min(mini, date)
                res.append((item_id, mini))
            self._cache = res[::-1]   # id croissant
        return self._cache

    def estimer(self, item_id):
        pts = self.reperes_corriges()
        if not pts:
            return None
        if len(pts) == 1:
            return pts[0][1]
        (premier, d_premier), (dernier, d_dernier) = pts[0], pts[-1]
        if item_id < premier or item_id > dernier:
            # Hors bornes : extrapolation au rythme moyen de création des id sur tous les
            # repères (le segment le plus proche est souvent plat à cause des remontées)
            vitesse = (d_dernier - d_premier) / (dernier - premier)
            ref, d_ref = (premier, d_premier) if item_id < premier else (dernier, d_dernier)
            return min(d_ref + vitesse * (item_id - ref), datetime.now(FUSEAU))
        ids = [p[0] for p in pts]
        i = min(max(bisect_left(ids, item_id), 1), len(ids) - 1)
        (a, da), (b, db) = pts[i - 1], pts[i]
        return min(da + (db - da) * ((item_id - a) / (b - a)), datetime.now(FUSEAU))


# ============================================================================
# PAGE D'UNE ANNONCE : âge affiché et statut de vente
# ============================================================================
def page_annonce(client, url):
    return client.get(url, headers={"Accept": "text/html"})


def lire_age(html):
    m = re.search(r'(?i)itemprop="upload_date"><span[^>]*>([^<]+)<', html)
    return m.group(1) if m else None


def lire_statut(reponse, item_id):
    """Statut public d'une annonce : actif, reserve, indisponible ou disparu.

    'indisponible' (can_buy=false ou is_hidden=true) et 'disparu' (404 ou redirection hors
    de l'annonce) signifient probablement « vendu », sans distinction possible avec une
    suppression par le vendeur. Les indicateurs bruts sont renvoyés pour garder la trace.
    """
    brut = {"http": reponse.status_code, "can_buy": None, "is_reserved": None, "is_hidden": None}
    if reponse.status_code in (404, 410) or f"/items/{item_id}" not in str(reponse.url):
        return "disparu", brut
    t = reponse.text.replace('\\"', '"')
    # Le bloc d'achat de l'annonce principale porte son item_id (la page contient aussi
    # des annonces similaires, d'où le ciblage). Plusieurs blocs portent l'item_id : on
    # prend celui qui contient can_buy, quel que soit l'ordre des clés.
    m = (re.search(r'\{"data":\{([^{}]*"can_buy":[^{}]*"item_id":"%s"[^{}]*)\}' % item_id, t)
         or re.search(r'\{"data":\{([^{}]*"item_id":"%s"[^{}]*"can_buy":[^{}]*)\}' % item_id, t))
    bloc = m.group(1) if m else ""
    for cle in ("can_buy", "is_reserved", "is_hidden"):
        v = re.search(r'"%s":(true|false)' % cle, bloc)
        brut[cle] = (v.group(1) == "true") if v else None
    if brut["is_reserved"]:
        return "reserve", brut
    if brut["can_buy"] is False or brut["is_hidden"]:
        return "indisponible", brut
    if brut["can_buy"] is None:
        return "inconnu", brut
    return "actif", brut


# ============================================================================
# EXTRACTION DES CHAMPS PUBLICS (aucune donnée vendeur)
# ============================================================================
def extraire_attributs(item):
    """Marque / État / Taille ne sont que dans le libellé d'accessibilité de la liste."""
    box = item.get("item_box") or {}
    label = box.get("accessibility_label") or ""
    attrs = {}
    for m in re.finditer(r"(Marque|État|Taille)\s*:\s*(.*?)(?=, (?:Marque|État|Taille)\s*:|, \d[\d.,]*\s*€|$)", label):
        attrs[m.group(1)] = m.group(2).strip()
    return attrs.get("Marque") or box.get("first_line") or "", attrs.get("Taille", ""), attrs.get("État", "")


def vers_ligne(item, estimateur, base):
    marque, taille, etat = extraire_attributs(item)
    date = estimateur.estimer(item["id"])
    url = item.get("url") or ""
    return {
        "id": item["id"],
        "titre": item.get("title", ""),
        "prix": item["price"]["amount"],
        "devise": item["price"]["currency_code"],
        "taille": taille,
        "etat": etat,
        "marque": marque,
        "date_publication": date.strftime("%Y-%m-%d %H:%M") if date else "",
        "url": url if url.startswith("http") else base + url,
        "photo_url": (item.get("photo") or {}).get("url", ""),
        "favoris": item.get("favourite_count", ""),
    }


def passe_le_filtre(ligne, mots):
    if not mots:
        return True
    texte = normaliser(f"{ligne['titre']} {ligne['marque']}")
    return any(normaliser(m) in texte for m in mots)


# ============================================================================
# COLLECTE PAGINÉE
# ============================================================================
def tranches_prix(prix_min, prix_max, nb):
    """Découpe [prix_min, prix_max] en nb tranches ; sans prix max, une seule recherche."""
    if prix_max is None:
        return [(prix_min, None)]
    pas = (prix_max - prix_min) / max(nb, 1)
    bornes = [round(prix_min + pas * i, 2) for i in range(max(nb, 1))] + [prix_max]
    return list(zip(bornes, bornes[1:]))


def collecter(client, params, tranches, date_limite, estimateur, annonces,
              sauvegarde=None, stats=None, ids_connus=frozenset()):
    """Parcourt chaque tranche de prix, page par page, jusqu'à : page vide, dernière page,
    date limite dépassée, ou page entièrement déjà connue (veille)."""
    stats = stats if stats is not None else {"pages": 0, "brutes": 0}
    for prix_min, prix_max in tranches:
        p = {**params, "order": "newest_first"}
        if prix_min:
            p["price_from"] = prix_min
        if prix_max is not None:
            p["price_to"] = prix_max
        log.info("Recherche %s", {k: v for k, v in p.items() if k != "order"})
        referer = f"{client.base}/catalog?{urlencode(p)}"
        for page in range(1, PAGES_MAX + 1):
            r = client.api_json("/svc-catalogue/items", {**p, "page": page, "per_page": PAR_PAGE}, referer)
            if r.status_code == 400:   # au-delà du plafond de pages
                break
            r.raise_for_status()
            donnees = r.json()
            items = donnees.get("items") or []
            stats["pages"] += 1
            if not items:
                break

            # Repère de date : la dernière annonce de la page dans l'ordre du tri, plus la
            # première en page 1. Une seule visite de page par repère.
            maintenant = datetime.now(FUSEAU)
            frontiere = None
            for it in [items[-1]] + ([items[0]] if page == 1 else []):
                age = lire_age(page_annonce(client, client.base + it["url"]).text)
                date = age_vers_date(age, maintenant) if age else None
                if date:
                    estimateur.ajouter(it["id"], date)
                    if it is items[-1]:
                        frontiere = date
                else:
                    log.warning("  âge illisible pour l'annonce %s", it["id"])

            nouvelles = [it for it in items if it["id"] not in annonces]
            stats["brutes"] += len(items)
            for it in nouvelles:
                annonces[it["id"]] = it
            if sauvegarde:
                sauvegarde(nouvelles)

            plus_ancienne = frontiere or estimateur.estimer(items[-1]["id"])
            total_pages = (donnees.get("pagination") or {}).get("total_pages", PAGES_MAX)
            inedites = sum(it["id"] not in ids_connus for it in nouvelles)
            log.info("  page %d/%d : %d annonces (%d nouvelles), dernière ≈ %s", page, total_pages,
                     len(items), inedites, f"{plus_ancienne:%Y-%m-%d %H:%M}" if plus_ancienne else "?")
            if plus_ancienne and plus_ancienne < date_limite:
                break
            if ids_connus and all(it["id"] in ids_connus for it in items):
                log.info("  page entièrement déjà vue, fin")
                break
            if page >= total_pages:
                break
    return stats
