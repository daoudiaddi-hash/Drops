#!/usr/bin/env python3
"""Check what similar items are listed for on Vinted before you set a price.

Reads Vinted's public search page (the same page a buyer sees) and summarises the
asking prices of live listings. These are asking prices, not sale prices, so treat
the median as a ceiling for a quick sale rather than a guarantee.

Usage
  python3 price_check.py "camisa sandro"
  python3 price_check.py "camisa sandro" --size M --condition "muy bueno"
  python3 price_check.py "levis 501" --countries es,fr,it,de

Options
  --country CODE     one market, default es
  --countries LIST   compare several markets side by side (es,fr,it,de,nl,be,pt,pl,uk...)
  --size TEXT        keep only listings whose size starts with this (M, L, 42, W31...)
  --condition TEXT   keep only listings whose condition contains this text, in the
                     site's language (es: "nuevo con etiquetas", "muy bueno", "bueno")
  --pages N          search pages to read per market, 1 to 3, default 1 (about 96 items each)
  --json             print raw rows as JSON instead of the summary

Standard library only. One request for cookies plus one per page, with a pause
between pages. Idea from DataKazKN's vinted-mcp-server (MIT), whose free npm
version no longer runs (checked 16 Nov 2026), so this is a fresh, smaller take.
"""
import argparse
import http.cookiejar
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request

DOMAINS = {
    "es": "www.vinted.es", "fr": "www.vinted.fr", "it": "www.vinted.it", "de": "www.vinted.de",
    "nl": "www.vinted.nl", "be": "www.vinted.be", "pt": "www.vinted.pt", "pl": "www.vinted.pl",
    "uk": "www.vinted.co.uk", "at": "www.vinted.at", "lt": "www.vinted.lt", "cz": "www.vinted.cz",
    "sk": "www.vinted.sk", "hu": "www.vinted.hu", "ro": "www.vinted.ro", "hr": "www.vinted.hr",
    "fi": "www.vinted.fi", "dk": "www.vinted.dk", "se": "www.vinted.se", "lu": "www.vinted.lu",
    "ie": "www.vinted.ie", "gr": "www.vinted.gr",
}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

ITEM = re.compile(
    r'"productItem":\{"id":(?P<id>\d+),"title":"(?P<title>.*?)","url":"(?P<url>[^"]*)"'
    r'.*?"price":\{"amount":"(?P<price>[\d.]+)","currencyCode":"(?P<cur>[A-Z]+)"\}'
    r'.*?"itemBox":\{"firstLine":"(?P<brand>[^"]*)","secondLine":"(?P<second>[^"]*)"',
    re.S,
)
FAVS = re.compile(r'"favouriteCount":(\d+)')


def fetch_items(country, query, pages):
    host = DOMAINS.get(country)
    if not host:
        sys.exit(f"Unknown country '{country}'. Known: {', '.join(sorted(DOMAINS))}")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", UA), ("Accept-Language", "en,es;q=0.8")]
    opener.open(f"https://{host}/", timeout=30).read()
    rows, seen = [], set()
    for page in range(1, pages + 1):
        qs = urllib.parse.urlencode({"search_text": query, "page": page})
        html = opener.open(f"https://{host}/catalog?{qs}", timeout=30).read().decode("utf-8", "replace")
        text = html.replace('\\"', '"')
        found = 0
        for chunk in text.split('"productItem":{')[1:]:
            m = ITEM.match('"productItem":{' + chunk)
            if not m or m["id"] in seen:
                continue
            seen.add(m["id"])
            found += 1
            size, _, cond = m["second"].partition(" · ")
            if not cond:  # no size shown, e.g. accessories
                size, cond = "", m["second"]
            fav = FAVS.search(chunk)
            rows.append({
                "id": int(m["id"]), "title": m["title"], "brand": m["brand"], "size": size.strip(),
                "condition": cond.strip(), "price": float(m["price"]), "currency": m["cur"],
                "favourites": int(fav.group(1)) if fav else 0, "url": f"https://{host}{m['url']}",
            })
        if found == 0:
            break
        if page < pages:
            time.sleep(1.5)
    return rows


def keep(rows, size, condition):
    out = rows
    if size:
        out = [r for r in out if r["size"].lower().startswith(size.lower())]
    if condition:
        out = [r for r in out if condition.lower() in r["condition"].lower()]
    return out


def summary(rows):
    prices = sorted(r["price"] for r in rows)
    if not prices:
        return None
    q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
    return {"n": len(prices), "min": prices[0], "p25": q[0], "median": statistics.median(prices),
            "p75": q[2], "max": prices[-1], "currency": rows[0]["currency"]}


def fmt(v):
    return f"{v:.0f}" if v >= 10 else f"{v:.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query")
    ap.add_argument("--country", default="es")
    ap.add_argument("--countries")
    ap.add_argument("--size")
    ap.add_argument("--condition")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    pages = max(1, min(a.pages, 3))
    markets = [c.strip().lower() for c in a.countries.split(",")] if a.countries else [a.country.lower()]

    results = {}
    for i, c in enumerate(markets):
        try:
            results[c] = keep(fetch_items(c, a.query, pages), a.size, a.condition)
        except Exception as e:  # network or layout change: say so, never guess
            print(f"{c}: could not read Vinted ({e.__class__.__name__}: {e})", file=sys.stderr)
            results[c] = None
        if i < len(markets) - 1:
            time.sleep(1.5)

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    filters = ", ".join(f for f in [a.size and f"size {a.size}", a.condition and f"condition '{a.condition}'"] if f)
    print(f'Vinted asking prices for "{a.query}"' + (f" ({filters})" if filters else ""))
    print("Live listings only, asking prices not sale prices.\n")
    for c, rows in results.items():
        if rows is None:
            print(f"{c.upper():<4} unreadable (Vinted may have changed its page)")
            continue
        s = summary(rows)
        if not s:
            print(f"{c.upper():<4} no matching listings")
            continue
        print(f"{c.upper():<4} {s['n']:>3} listings  median {fmt(s['median'])} {s['currency']}  "
              f"middle half {fmt(s['p25'])} to {fmt(s['p75'])}  range {fmt(s['min'])} to {fmt(s['max'])}")

    if len(markets) == 1 and results[markets[0]]:
        rows = results[markets[0]]
        by_cond = {}
        for r in rows:
            by_cond.setdefault(r["condition"] or "(not shown)", []).append(r["price"])
        print("\nBy condition")
        for cond, ps in sorted(by_cond.items(), key=lambda kv: -len(kv[1])):
            print(f"  {cond:<28} {len(ps):>3}  median {fmt(statistics.median(ps))}")
        print("\nMost favourited (what buyers want)")
        for r in sorted(rows, key=lambda r: -r["favourites"])[:5]:
            print(f"  {fmt(r['price']):>5} {r['currency']}  {r['favourites']:>3} favs  {r['size'] or '-':<6} "
                  f"{r['condition']:<20} {r['title'][:50]}  {r['url']}")


if __name__ == "__main__":
    main()
