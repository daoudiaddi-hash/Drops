---
name: vinted-listing
description: Draft a ready-to-paste Vinted listing (title, description, price, every field the Sell form asks for) for one second-hand item, usually clothing, from a few facts or photos, then check it with the bundled linter. Trigger when the user says "write a Vinted listing", "sell this on Vinted", "/vinted-listing", or hands over an item to sell. Drafts by default and posts only when the user asks, through the marketplace-post skill.
---

# Vinted listing

Turns one item into a Vinted listing the user can paste into the Sell form. It never posts unless the user asks, and never messages anyone.

For a whole wardrobe of 20+ items, point the user at the MIT-licensed batch skill at github.com/stefanmanojlovic2/vinted-listing instead. This one is for a single item.

## Inputs to collect (ask once, in one short message, only for what is missing)
1. Brand, item type, colour or pattern
2. Gender or fit and label size
3. Material, from the care label
4. Condition, honestly, flaws named with where they are
5. Price the user wants
6. Rough weight or how it would pack (shirt = small parcel)

If photos are given, read the label photo for brand, size and material, and look at every photo for flaws. Never guess a size or material from a front view.

## Hard rules
- **Every fact not given by the user or shown in a photo goes in as a visible placeholder** like `(TALLA?)`, `(PRECIO?)`, `(MATERIAL?)`. Never invent a size, price, material, flaw or reason for selling. The linter fails while any placeholder is left.
- No links, emails, phone numbers or other apps in the text. Vinted removes them and can restrict accounts.
- Name flaws plainly, or say there are none. Vinted refunds items that are significantly not as described.
- Write in the language of the Vinted site the user sells on (Spanish for vinted.es), casual and plain. No exclamation marks, no sales-copy voice.
- Title format is brand, item, the detail buyers search for, size. Brand and size also feed buyers' filters, so they must be set in the fields too.

## Output format
Write the draft to `listing-vinted.md` in the working folder with exactly these headings so the linter can read it:

```
## Title
(max 100 characters)

## Description
(brand and item first, then size and fit, material, condition and flaws, thanks)

## Price
(one number)

## Fields to set in the form
Category, brand, size, condition, colour, material, parcel size. One line each.
```

Use the exact condition wording the form shows. Vinted's labels differ by country and change, so read them from the live Sell form and tell the user to pick the closest. For parcel size, suggest the smallest size the packed item fits and tell the user to confirm in the form.

## Check it
```
python3 scripts/lint_listing.py vinted listing-vinted.md
```

Exit 0 and PASS means ready to paste. Exit 1 lists what to fix. Report the result in one line.

## Pricing
Do not invent a price. Before asking the user for one, run the price check on the same search a buyer would type, with the label size and the condition:

```
python3 scripts/price_check.py "camisa sandro" --size M --condition "muy bueno"
```

It reads Vinted's public search page and prints the median, the middle half and the range of live asking prices, a breakdown by condition and the five most favourited similar listings. Add `--countries es,fr,it` to compare markets. Tell the user the median and middle half in one line, suggest a price inside the middle half for a quick sale, and put `(PRECIO?)` in the draft until they give a number. These are asking prices, not sale prices. If the script cannot read the page, say so and fall back to Vinted's own price guide in the Sell form.

## Photos (tell the user, do not do it for them)
Natural daylight, whole item first on a plain background, then label, fabric close-up, any flaw. Cover photo decides whether anyone clicks.

## Verified form facts (Spain, 15 Nov 2026)
- Condition options include New with tags and New without tags.
- After the photos upload, Vinted detects category, brand, size and colours. Check the category path (Men > Clothing > Tops & t-shirts > Shirts) and the brand, then set the rest.
- Designer brands show an authenticity pop-up asking for logo, care label and size label photos. Include them when you have them.
- Vinted's price guide for similar items is shown under the price box. Tell the user when their price is far above it.
- Leave the paid Bump unticked unless the user asks.

## Optimise for search
Vinted search matches the title first, so write the title as a buyer would type it, in the language of the site (Spanish on vinted.es), and add a short English line in the description for expats.
- Title order. Item type in Spanish, brand, gender, then size, colour and condition keywords. Example `Vaqueros Emporio Armani hombre W31 L32 burdeos granate nuevos - Jeans Armani`. Up to 100 characters, use them.
- Use both words buyers use for one thing (vaqueros and jeans, camisa and shirt) once each, never stuffed.
- Put the exact label size in the title and description (W31 L32 for jeans, M for shirts), plus the plain-words version (31 de cintura, 32 de largo).
- Use the brand as printed on the label, not the seller's shorthand (Emporio Armani, not Armani).
- Only keywords that are true and known. Never add a model name, fabric, fit or "talla grande" the user or a photo hasn't shown.
- Description order. Spanish sentence with the keywords, condition line, English line, thanks. No hashtags, no exclamation marks, no contact details.
- Apply the same pass to every listing the user has live when they ask for optimising, including ones already published (edit them, do not relist).

## Never reuse another seller's photos (learned 15 Nov 2026)
- Vinted's automated moderation deleted two relistings made from the photos and wording of the original sellers' listings within minutes, with the message "Listing deleted: Duplicate listings". The "Item listed" pop-up still appeared first, so check the profile and the Vinted inbox a few minutes after posting, not just the pop-up.
- Do not try to get round it (cropping, flipping, re-encoding). Ask the user for their own photos of the item instead. Own photos are also the honest option, because the item in hand may not match the original listing.
- Also skip photos that are not of the item: stock or AI model shots, and photos of people.
- A price the user quotes as "the same as the seller's" needs a check. Vinted shows a struck-through original price and a lower current one. Ask which, or state which was used.

## Posting
Drafting only unless the user asks to post. When they say "post it" or "do the ad", follow the `marketplace-post` skill, which drives their own signed-in Chrome through Claude in Chrome. There is no official posting API, so go slowly, one ad at a time. Never message buyers or sellers, and never open paid options.
