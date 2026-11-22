---
name: depop-to-vinted
description: Cross-lists a user's active Depop shop listings onto Vinted as new, live listings — pulling titles, descriptions, prices, sizes, categories, and photos straight from Depop and rebuilding them on Vinted with Vinted-appropriate categories, brand/size/condition/color fields, and pricing. Use this whenever the user asks to "copy," "cross-list," "transfer," "mirror," or "move" listings from Depop to Vinted, wants to "start selling on Vinted too," or asks to sync/duplicate their resale shop across platforms. Only ever reads from Depop — never edits, deletes, or discounts anything in the Depop account. Requires a browser-automation MCP server (this repo ships with Playwright MCP configured in .mcp.json) driving a real, logged-in Chrome window.
compatibility: "Requires a browser-automation MCP (Playwright MCP is preconfigured in this repo's .mcp.json) exposing navigate/click/type/evaluate-JS/screenshot tools, plus normal shell access (curl/wget) for downloading photos."
---

# Depop → Vinted cross-listing

This skill recreates a seller's live Depop listings as live Vinted listings. It was built by reverse-engineering both sites' actual DOM/behavior, so follow the concrete techniques below rather than improvising — naive approaches (clicking through native file pickers, typing into fields right after the page reflowed, guessing at required fields) all fail or waste huge amounts of effort. The core technique here was tested end-to-end and produced a real, live Vinted listing.

**Golden rule: never touch the Depop side.** Only read from Depop (view pages, scroll, extract text/images). Never click Edit, Delete, discount, or otherwise modify a Depop listing. If the user says "don't change my Depop," that's the default behavior anyway — just don't deviate from it.

**This skill assumes a Playwright-MCP-style tool set** (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_evaluate`, `browser_take_screenshot`, `browser_file_upload`, `browser_tabs`). If a different browser MCP is connected, the same steps apply — just use that MCP's equivalent tools (navigate / click-by-ref-or-selector / type-into-field / run-JS / screenshot).

## Step 0: Get a logged-in browser

Open a browser tab with `browser_navigate` to `https://www.depop.com`. Take a `browser_snapshot` or screenshot to check whether the user is logged in (look for a profile icon / "Log in" button). If not logged in to Depop, or if a later check shows not logged in to Vinted, **stop and tell the user to log in manually in the open browser window** — never type credentials yourself, and never ask the user to paste a password into chat. Because this repo's `.mcp.json` points Playwright at a persistent `--user-data-dir`, once the user logs in once, the session is remembered for future runs.

## Before starting: scope the job with the user

Depop shops are often much bigger than they look — "a few listings" can turn out to be 100+. Each Vinted listing takes on the order of 20-25 individual browser actions (photos, title, description, category, brand, size, condition, color, price, submit, plus handling occasional modals). Before doing bulk work, confirm with the user:

1. **Which listings** — all active listings, or a subset/category?
2. **Publish live or save as drafts** — publishing is an irreversible, public action, so get explicit confirmation of "publish live" before clicking Upload on each one (a one-time confirmation up front covers the whole batch — don't re-ask per item).
3. **Pricing approach** — see "Pricing" below for the default, but confirm if the user wants something else (e.g., match Vinted's price-recommendation tool instead).
4. **Pacing** — if there are more than ~20-30 listings, tell the user up front roughly how long this will take (steps-per-item × item count) and check whether they want everything now, a first batch to review quality, or the work spread over multiple runs. Don't silently grind through hundreds of steps without a checkpoint — surface the real scope once you know the listing count (Step 1 below tells you this almost immediately).

## Step 1: Extract every Depop listing

Go to `https://www.depop.com/sellinghub/selling/active/`. This page shows the count of active items right away ("Active items (N)") — use that number to size the job for the user.

Depop paginates 24 items at a time behind a "Load more" button. Click it repeatedly until it disappears — everything then loads into the DOM at once, which is what makes bulk extraction fast.

**Text fields** (title/description/hashtags, both prices, size, category): once everything is loaded, get the page's text content in one shot (a full-page text extraction, or `browser_snapshot`'s text content) rather than reading item-by-item — it reliably returns the full text for 100+ items without truncation. Each item's block looks like:

```
Footwear
US 9.5
US$180.00       <- original price (crossed out on-page)
US$18.00        <- discounted/sale price
Aime Leon dore new balance 991 collab size 9.5 lightly worn #aimeleondore #ald #newbalance
```

**Use the first (higher) price as the item's real price, not the discounted one**, unless the user says otherwise. Depop sellers commonly run shop-wide "sales" that show a crossed-out original price next to a discounted one — the crossed-out number is the one that reflects what the item is actually worth.

**Photos**: use `browser_evaluate` to run JS in the page that builds a lookup of product-slug → image URL, keyed so it's safe to re-run after each "Load more" (it dedupes):

```js
() => {
  const links = Array.from(document.querySelectorAll('a[href^="/products/edit/"]'));
  function findContainer(link){
    let el = link.parentElement;
    while(el){
      const editLinks = el.querySelectorAll('a[href^="/products/edit/"]');
      const img = el.querySelector('img');
      if (editLinks.length === 1 && img) {
        if (el.parentElement && el.parentElement.querySelectorAll('a[href^="/products/edit/"]').length > 1) {
          return el;
        }
      }
      el = el.parentElement;
    }
    return null;
  }
  window.__allItems = window.__allItems || {};
  links.forEach(link => {
    const slug = link.getAttribute('href').replace('/products/edit/','').split('/?')[0];
    const c = findContainer(link);
    const img = c ? (c.querySelector('img')||{}).src : null;
    window.__allItems[slug] = { slug, img: img ? img.replace('/P2.jpg','/P0.jpg') : null };
  });
  return Object.values(window.__allItems);
}
```
(The `/P2.jpg` → `/P0.jpg` swap upgrades the thumbnail to Depop's full-resolution version of the same photo — same CDN path, same hash, just a different size suffix.)

Because Claude Code runs on the user's own machine (not a sandboxed/allowlisted environment), you can **download these photos directly with `curl`/`wget` in the shell** into a local temp folder, then hand the local file paths to `browser_file_upload` when it's time to add them to Vinted — this is simpler and more robust than trying to move image bytes through the browser MCP. (If shell network access to Depop's CDN is ever blocked in some environment, fall back to fetching the image from *inside* the Vinted tab via `browser_evaluate` — Depop's media CDN serves images with permissive CORS — and constructing a `File`/`DataTransfer` to assign to the file input directly; ask the user or check the MCP's file-upload tool if `browser_file_upload` isn't available.)

Save the extracted item list (title, description, both prices, size, category, local photo paths) to a scratch file as you go — don't try to hold 100+ items only in working memory across many tool calls.

## Step 2: Build each Vinted listing

For each Depop item, navigate to `https://www.vinted.com/items/new` (a fresh, empty form) and do the following. **Use `browser_type` (or the MCP's field-filling tool) for the Title, Description, and Price fields** — set them directly by ref/selector rather than click-then-type, since the page reflows as fields are filled (e.g. selecting a Category adds a whole new Brand/Size/Condition/Colors block above Pricing) and coordinates captured before a reflow will land on the wrong element after it. Category, Brand, Size, Condition, and Colors are custom dropdown widgets, not native `<select>`s — click to open them, then click the option; take a fresh `browser_snapshot` before each click since these are exactly the elements that shift position most often.

### Photos

Upload 2-3 of the item's downloaded photos via `browser_file_upload` on the Photos section's file input. **Upload at least 3 photos for anything priced as "premium"** — Vinted's own tag for higher-value items. If you submit with only 1-2 photos on an item Vinted considers premium, it shows an "Add more photos to this listing" modal and blocks the Upload button until you add more. It's cheap to just grab 2-3 of the item's Depop photos up front rather than dealing with the modal reactively.

### Category and Brand — let Vinted's own AI help

After uploading a photo, click "Select a category" — Vinted analyzes the photo and offers a "Suggested" category at the top of the list (e.g., "Sneakers · Men > Shoes"). Same thing happens for Brand once a category with a brand field is selected — Vinted often pre-fills a "Suggested" brand chip. Prefer these suggestions when they're correct; they're faster than searching and are trained on real listing photos, so they're usually right. Fall back to the "Find a category" / "Search brands" search box when they're not.

### Size, Condition, Colors

- **Size**: dropdown organized by category (e.g., "Men's shoes"); scroll within the dropdown to find the right value.
- **Condition**: map Depop's condition language to Vinted's fixed options (New with tags / New without tags / Very good / Good / Satisfactory). Depop's "excellent condition, lightly worn" maps to Vinted's "Very good," for example.
- **Colors**: despite the "(recommended)"-style label on some fields, **Colors is actually a required field** — Vinted's validation will block submission with "Fill in color to continue" if it's empty. Always set at least one color. Material is genuinely optional/recommended — skip it unless you want extra polish.

### Description and hashtags

Depop listings lean heavily on hashtags for discovery; Vinted's search instead leans on the structured fields (category, brand, size, color) you're already filling in. Don't strip hashtags from the description — they don't hurt — but don't rely on them to do the work either. Prioritize:
1. Getting Category/Brand/Size/Colors right (this is what actually drives Vinted search/browse placement).
2. A natural-language description a human buyer would read, with a handful (3-6) of the most relevant hashtags at the end.
3. Carrying over any condition/flaw/authenticity details from the Depop description — buyers on both platforms care about this.

### Pricing

Default: **list at the item's original (pre-discount) Depop price.** Vinted sellers keep the full listed price — Vinted adds its buyer protection fee on top at checkout, paid by the buyer, not deducted from the seller — so a straight price match is the closest thing to an apples-to-apples transfer. (Vinted will show its own "Bargain / Optimal / Premium" price suggestions based on comparable sold listings once you're filling out the form — these can be dramatically lower than a rare/collab item is actually worth, since the algorithm doesn't know about the collab/rarity. Use your judgment, but default to the user's original price rather than Vinted's algorithmic suggestion unless the user asked you to price competitively.)

### Submitting

Find and click the "Upload" button (this is Vinted's publish action — it's live and public immediately). If a modal appears (e.g., asking for more authenticity photos), handle it — add photos and retry, or close it if the user is fine proceeding without them — then click Upload again. A successful publish shows an "Item listed" confirmation card with the item's photo, size, condition, and price.

## Step 3: Report back

Keep a running list of what got listed (title, price, and ideally the Vinted item URL) so you can give the user a clean summary at the end, or after each batch if you're checkpointing. If any item failed (couldn't find a good category match, image download failed, etc.), note it explicitly rather than silently skipping it — the user should know their count of "N Depop items" vs. "actually listed on Vinted" if those numbers diverge.
