---
name: wallapop-listing
description: Draft a ready-to-paste Wallapop ad (title, description, price, field values) for one second-hand item, usually clothing, from a few facts or photos, then check it with the bundled linter. Trigger when the user says "write a Wallapop ad", "sell this on Wallapop", "/wallapop-listing", or hands over an item to sell in Spain. Drafts by default and posts only when the user asks, through the marketplace-post skill.
---

# Wallapop listing

Turns one item into a Wallapop ad the user can paste into the app. It never posts unless the user asks, and never messages anyone.

## Inputs to collect (ask once, in one short message, only for what is missing)
1. Brand, item, colour or pattern
2. Gender or fit (hombre, mujer, unisex) and label size
3. Material, from the care label
4. Condition, honestly. Flaws named with where they are
5. Price the user wants
6. Handover (hand delivery in which area, shipping, or both)

If photos are given, read the label photo for brand, size and material, and look at every photo for flaws. Never guess a size or material from a front view.

## Hard rules
- **Every fact the user has not given or a photo has not shown goes in as a visible placeholder** like `(TALLA?)`, `(PRECIO?)`, `(ESTADO?)`. Never invent a size, price, material, flaw, reason for selling, handover area or shipping promise. The linter fails while any placeholder is left, which is the point.
- No phone numbers, emails, links, WhatsApp, Instagram or "Bizum" in the text. Wallapop removes them and can suspend accounts. Keep the chat inside the app.
- Name flaws plainly, or write "sin defectos" if there are none. Honest wording avoids disputes and returns.
- Write in natural, casual Castilian Spanish, the way a person texts. No opening inverted question or exclamation marks, no sales-copy voice. Add a short English line only if the area has many English speakers (Barcelona does).
- No invented urgency ("ultima oportunidad", "solo hoy").

## Output format
Write the draft to a file called `listing-wallapop.md` in the working folder, using exactly these headings so the linter can read it:

```
## Title
(max 50 characters, brand + item + the detail buyers search for)

## Description
(short paragraphs, brand and item first, then size and fit, material, condition and flaws, handover, thanks)

## Price
(one number in euros)

## Fields to set in the app
Category, brand, size, condition, colour. One line each.
```

Use the exact condition wording the app shows. Wallapop's labels have changed over time, so read them from the live form in the app rather than from memory, and tell the user to pick the closest one.

## Check it
Run the linter bundled in this skill folder (the installer puts it in `scripts/`):

```
python3 scripts/lint_listing.py wallapop listing-wallapop.md
```

Exit 0 and PASS means ready to paste. Exit 1 lists what to fix. Report the result to the user in one line. Warnings are advice, not blockers.

## Pricing
Do not invent a price. Wallapop has no public price guide, so use Vinted Spain's live asking prices as the yardstick. The script is bundled in this skill folder:

```
python3 scripts/price_check.py "camisa sandro" --size M
```

Give the user the median and the middle half in one line and keep `(PRECIO?)` until they pick a number. Wallapop buyers haggle, so a price a little above the Vinted median leaves room.

## Photos (tell the user, do not do it for them)
First photo is the search thumbnail, so make it the whole item on a plain background. Then label, close-up of the fabric or pattern, and any flaw. Natural daylight. Up to 10 photos.

## Verified form facts (Spain, 15 Nov 2026)
- Title limit 50, description limit 640. Category path Moda y accesorios > Hombre > Ropa > Camisas.
- Condition options Sin estrenar (keeps the tag), Nuevo (never used), Como nuevo, En buen estado, En condiciones aceptables, Lo ha dado todo. New without tags is **Nuevo**.
- After the photos upload, Wallapop auto-fills brand, size, colour and writes its own title and description. Check brand, size and colour, then overwrite the title and description with the checked draft.
- Shipping is on by default with the 0 to 1 kg band, which suits clothing. Decline the paid "Destacar" promotion.

## Optimise for search
Wallapop searches the 50 character title, so lead with item, brand, size and the colour or pattern buyers type, in Spanish. Add one short English line in the description. Use only known facts, never invented model names or fabrics. Never reuse another seller's photos, use the user's own (Vinted deleted such relistings as duplicates).

## Posting
Drafting only unless the user asks to post. When they say "post it" or "do the ad", follow the `marketplace-post` skill, which drives their own signed-in Chrome through Claude in Chrome. There is no official posting API, so go slowly, one ad at a time. Never message buyers or sellers, and never accept paid promotion.
