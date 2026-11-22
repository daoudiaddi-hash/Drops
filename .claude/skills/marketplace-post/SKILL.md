---
name: marketplace-post
description: Post a finished Vinted and/or Wallapop listing from the user's own signed-in Chrome, through Claude in Chrome. Use when the user says "post it", "do the ad", "put it on Vinted and Wallapop", "use my Chrome" after a listing draft exists (see vinted-listing and wallapop-listing for the drafting). Covers the exact click path, the traps found on 15 Nov 2026, and the checks afterwards.
---

# Post to Vinted and Wallapop through your own Chrome

Learned live on 15 Nov 2026 (a motorbike jacket, posted to both apps in about 25 tool rounds). Follow this path and it should take far fewer. Needs Claude Code with the Claude in Chrome extension, and the user already signed in to Vinted and Wallapop in that Chrome.

## When it applies
The user has asked to post ("do the ad", "post it", "use my Chrome"). A request to draft is not a request to post. Draft first with `vinted-listing` and `wallapop-listing` (their linters must PASS), then post with this skill. Never message buyers or sellers from this skill.

## Before touching the browser
1. Have `listing-vinted.md` and `listing-wallapop.md` linted, with price and photo order decided.
2. Resize photos. The Chrome upload tool refuses more than 10.4 MB per call, and phone photos are 3 to 5 MB each.
   `mkdir -p upload && for f in photo-*.jpg; do sips -Z 1600 "$f" --out "upload/$f" >/dev/null; done`
   Upload from `upload/`, in post order (cover first).
3. Load the Chrome tools in ONE ToolSearch call (tabs_context_mcp, navigate, computer, find, form_input, file_upload, browser_batch). Call `tabs_context_mcp` with createIfEmpty and work in a new tab. The user's Chrome must already be signed in to both apps.
4. Use `browser_batch` for sequences (click, type, wait, screenshot). Take 0.6 scale screenshots, coordinates stay in the full 1399x702 frame.

## Vinted (vinted.es, labels below are from the English interface)
1. Navigate to `https://www.vinted.es/items/new`.
2. `find` "file input for photo upload", then `file_upload` all photos to that ref. Vinted then auto-guesses category, brand and colour.
3. `form_input` the Title and Description refs (find them by label). Newlines work.
4. Category first, brand second. Changing the category clears the brand.
   - Category dropdown, type in the "Find a category" box. For motorbike jackets search `motorcycle` and pick "Biker & racer jackets (Men > Clothing > Outerwear > Jackets)".
5. Brand. Vinted's guess is often wrong (it chose Engelbert Strauss). Click Brand, click the "Search brands" box, type the brand. If it is not in the list pick `Use "<brand>" as brand`.
6. Size (suggested size appears first), Condition (labels in English: New with tags, New without tags, Very good, Good), Colours (up to 2, the list scrolls, Yellow is near the bottom). Material is optional, leave blank unless the care label is known.
7. Price: click the field and type the number.
8. Parcel size is pre-selected with a Recommended badge. Keep it. A padded jacket was Large, not Medium.
9. Leave the Bump unticked. Click Upload. Dismiss the "Item listed" pop-up with Later.
10. Check the user's own Vinted profile a few minutes later that the listing is still active. Vinted deletes duplicates made from other sellers' photos. Own photos are fine.

## Wallapop (es.wallapop.com)
1. Navigate to `https://es.wallapop.com/app/catalog/upload`, click "Algo que ya no necesito".
2. Step 1 asks for a product summary (max 50 characters). Type the title, click Continuar.
3. Photos step. `find` "photo file input" and take the element named "File Upload" (hidden), then `file_upload` all photos. Click Continuar.
4. The next page is pre-filled by Wallapop (its own title, description and category). Overwrite with `form_input` on the Título, Descripción and Precio refs. The category it picks is usually right (for a motorbike jacket "Motor y accesorios > Recambios de motos > Vestimenta y protección").
5. Estado is a custom dropdown and `form_input` does NOT set it. Click it. The list opens upward and is clipped by the header, so scroll the page first so the field sits low on screen, then click the option. Options are Nuevo, Como nuevo, En buen estado, En condiciones aceptables, Lo ha dado todo. Check the field shows the value before publishing.
6. Shipping weight band has a Recommended default. Keep it. Location defaults to the account's postcode.
7. Scroll to the bottom and click Publicar. Decline the paid promotion with "Ahora no, gracias".
8. Confirm the ad appears under Tu Catálogo (https://es.wallapop.com/app/catalog/published) with the right price.

## After posting
Report both listings, price, photos used, and anything left blank (material). Update any sales tracker only if the user keeps one. Close any tab opened for this.

## Traps to avoid
- Neither app offers an official posting API, and their terms may not allow automated use. This drives a real browser slowly, one ad at a time, at the user's own risk.
- Never reuse another seller's photos or wording (Vinted deletes duplicates).
- Do not click paid options (Bump, Destacar).
- If a page shows a login wall, CAPTCHA or verification, stop and tell the user. Do not try to get past it.
- Slow, human-paced clicks. No bulk or repeated posting.
