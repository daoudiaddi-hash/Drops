#!/usr/bin/env python3
"""Check a Wallapop or Vinted listing draft before you paste it into the app.

Usage
  python3 lint_listing.py wallapop listing.md
  python3 lint_listing.py vinted listing.md

The listing file is plain markdown with these headings (extra headings are fine)

  ## Title
  ## Description
  ## Price

Exit code 0 means ready to post. Exit code 1 means something must be fixed first.
Warnings never fail the check, they are things worth a second look.

Limits below are the commonly quoted ones and are NOT confirmed against the live
apps. They are marked "believed" in the output. Check the app if one matters.
"""
import re
import sys
from pathlib import Path

PLATFORMS = {
    "wallapop": {"title_max": 50, "desc_max": 640},
    "vinted": {"title_max": 100, "desc_max": 2000},
}

PLACEHOLDER = re.compile(r"\(([^()\n]{1,60}\?)\)")
CONTACT = re.compile(
    r"(https?://|www\.|@[\w.]+\.\w+|\b[\w.+-]+@[\w-]+\.\w+|\b(?:\+?\d[\s.-]?){9,}\b|whatsapp|telegram|instagram|bizum)",
    re.I,
)
FLAW_WORDS = re.compile(
    r"(defecto|mancha|desgaste|roto|descosid|agujero|bolita|sin defectos|impecable|"
    r"flaw|stain|wear|hole|pilling|no flaws|perfect condition|as new|like new|never worn|"
    r"sin estrenar|como nueva?|nueva?|buen estado|muy buen estado)",
    re.I,
)


def sections(text):
    out, name, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^#{1,3}\s+(.*\S)\s*$", line)
        if m:
            if name is not None:
                out[name] = "\n".join(buf).strip()
            name, buf = m.group(1).strip().lower(), []
        elif name is not None:
            buf.append(line)
    if name is not None:
        out[name] = "\n".join(buf).strip()
    return out


def unfence(s):
    return re.sub(r"^```\w*\n|\n```$", "", s.strip()).strip()


def main(argv):
    if len(argv) != 3 or argv[1] not in PLATFORMS:
        print(__doc__)
        return 2
    platform, path = argv[1], Path(argv[2])
    if not path.is_file():
        print(f"FAIL  file not found: {path}")
        return 2
    cfg = PLATFORMS[platform]
    text = path.read_text(encoding="utf-8")
    sec = sections(text)
    fails, warns = [], []

    title = unfence(sec.get("title", ""))
    desc = unfence(sec.get("description", ""))
    price = unfence(sec.get("price", ""))

    for label, val in (("Title", title), ("Description", desc), ("Price", price)):
        if not val:
            fails.append(f"{label} section is missing or empty")

    ph = PLACEHOLDER.findall(text)
    if ph:
        fails.append(
            f"{len(ph)} unfilled placeholder(s), fill or delete before posting: "
            + ", ".join(f"({p})" for p in dict.fromkeys(ph))
        )

    if title:
        if len(title) > cfg["title_max"]:
            fails.append(f"Title is {len(title)} chars, believed limit {cfg['title_max']}")
        if not re.search(r"sandro|brand|marca", title, re.I) and "brand" in sec:
            brand = unfence(sec["brand"])
            if brand and brand.lower() not in title.lower():
                warns.append(f"Brand '{brand}' is not in the title, buyers search by brand")

    if desc:
        if len(desc) > cfg["desc_max"]:
            fails.append(f"Description is {len(desc)} chars, believed limit {cfg['desc_max']}")
        if CONTACT.search(desc) or CONTACT.search(title):
            fails.append("Contact details, links or other apps in the text. Both apps remove these and can suspend the account")
        if not FLAW_WORDS.search(desc):
            warns.append("No mention of condition or flaws. Say 'no flaws' if there are none, buyers ask anyway")
        if re.search(r"[–—]", text):
            warns.append("Long dashes found. Use hyphens or commas")
        if re.search(r"\[[^\]]*\]", desc):
            warns.append("Square brackets in the description")

    if price:
        if not re.search(r"\d", price):
            fails.append("Price has no number")
        elif re.search(r"\d+\s*[-/]\s*\d+", price):
            warns.append("Price looks like a range. The app needs a single number")

    print(f"{platform} listing check for {path.name}")
    print(f"  title {len(title)} chars, description {len(desc)} chars")
    for f in fails:
        print(f"FAIL  {f}")
    for w in warns:
        print(f"WARN  {w}")
    if not fails:
        print("PASS  ready to paste" + (" (with warnings)" if warns else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
