---
name: announcement-card
description: >-
  Generate a polished, shareable PNG announcement card from plain text. Use for team announcements, milestones, release notes, product updates, or any internal/external message where someone pastes text and asks for an image, card, visual, or something to post or share. Renders a styled 680px card via Playwright screenshot of a self-contained HTML file, supports light and dark themes, and composes sections (metrics, flow-comparison, steps, bullets, phase, timeline, callout) based on the content. Do NOT use for slide decks, long-form documents, or multi-page reports -- this skill produces a single card image. Do NOT use when the caller only wants raw HTML or Markdown output -- the skill's purpose is the PNG. Triggers on: "announcement card", "make this into an image", "create a card for this", "generate a visual", "turn this into a post", "announcement image", "Slack card", "team update visual", pasted announcement text with a share-to-channel intent.
version: 1.1.0
author: DE Team
tags: [announcements, image, comms, card, png]
---

# Announcement Card Generator

Turns any plain-text announcement into a styled, downloadable PNG card. Supports light and dark themes. Sections are detected automatically from the content — the card adapts to whatever sections are present.

---

## When NOT to use

- **Slide decks or multi-page reports** — this skill produces a single card image, not a presentation.
- **HTML or Markdown output only** — if the caller explicitly wants raw markup rather than a PNG, skip this skill.
- **Real-time charts or dashboards** — dynamic data rendering is out of scope.

---

## Step 1. Clarify theme

If the user has not specified a theme, ask:

> "Light or dark card?"

Default to **light** if they do not answer or say they do not mind.

---

## Step 2. Parse the announcement

Read the announcement text and extract the following. Not all fields will be present — only extract what is actually there.

| Field | What to look for |
|---|---|
| `title` | The main headline of the announcement |
| `eyebrow` | Team name, department, or category label (e.g. "DE Team · Platform Milestone") |
| `subtitle` | One or two sentence summary of the key message |
| `metrics` | Any 2–4 key numbers worth calling out (counts, percentages, time saved, etc.) |
| `sections` | All body sections — see Section Types below |
| `footer` | A closing line, e.g. "More soon · DE Team" — use a short version if not stated |

If the announcement is too vague to extract a title or subtitle, ask the user to clarify before proceeding.

---

## Step 3. Map content to section types

Identify which section types apply based on the content. A single announcement can have any combination. Read `references/section-types.md` for the full HTML snippet for each type.

| Section type | Use when the announcement contains... |
|---|---|
| `metrics` | Key numbers: counts, percentages, time durations, zero-something |
| `flow-comparison` | A before/after process or pipeline change |
| `steps` | A numbered or bulleted "how we did it" or "how it works" |
| `bullets` | A list of key points, features, or outcomes without a before/after structure |
| `phase` | An upcoming phase, next steps, or "what's next" block |
| `timeline` | A sequence of events with dates or ordered stages |
| `callout` | A single key insight or quote worth highlighting on its own |

Pick only the section types that match the content. Do not force sections that are not implied by the announcement.

---

## Step 4. Build the HTML file

Build a single self-contained HTML file at `./announcement_card.html`.

> **Path compatibility:** on Claude.ai sandbox, write to `/home/claude/announcement_card.html` instead. On Claude Code / API, `./announcement_card.html` in the current working directory is correct.

### Base structure

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  /* Paste the full CSS block for the chosen theme from references/theming.md */
</style>
</head>
<body>
<div class="card">
  <!-- HEADER — always present; see references/section-types.md -->
  <!-- METRICS — if metrics were found; see references/section-types.md -->
  <!-- BODY SECTIONS — in the order they appear in the announcement -->
  <!-- FOOTER — always present; see references/section-types.md -->
</div>
</body>
</html>
```

Read `references/theming.md` for the full CSS for both themes. Use only the theme the user selected.

Read `references/section-types.md` for the HTML snippet for each section type, including the header and footer.

### Rules when composing the HTML

- **Wrap all body sections in** `<div class="section">` with a `border-bottom` unless it is the last section before the footer.
- **Section labels** (the small uppercase label at the top of a section) should be short: 2–4 words max.
- **Flow steps** should be short: 2–4 words per box. Trim if needed.
- **Metric numbers** must be actual numbers or symbols (1,000+, 51, 0, 80%, ~3 weeks). Do not put descriptive text as a number.
- **Metric labels** should be 3–5 words.
- **Steps** grid: use 2 columns for 3–4 steps, 1 column for 1–2 steps.
- **Phase / next section** always uses the purple accent stripe at the top.
- Keep the card width at exactly **680px**.
- Do not add sections that were not in the original announcement.

---

## Step 5. Screenshot to PNG

> **First-time setup:** if Playwright is not installed, run `pip install playwright && playwright install chromium` once before running the script.

Run the following Python script via bash to produce a 2x retina PNG:

```python
from playwright.sync_api import sync_playwright
import os

HTML_PATH = os.environ.get('CARD_HTML', './announcement_card.html')
PNG_PATH  = os.environ.get('CARD_PNG',  './announcement_card.png')

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 760, "height": 1600}, device_scale_factor=2)
    page.goto(f"file://{os.path.abspath(HTML_PATH)}")
    page.wait_for_timeout(500)
    card = page.locator('.card')
    try:
        card.screenshot(path=PNG_PATH)
    except Exception:
        page.screenshot(path=PNG_PATH, full_page=True)
    browser.close()
    print(PNG_PATH)
```

On Claude.ai sandbox: set `CARD_HTML=/home/claude/announcement_card.html` and `CARD_PNG=/mnt/user-data/outputs/announcement_card.png` before running.

---

## Step 6. Present the file

Report the output path to the user. On Claude.ai sandbox, also call `present_files` with the PNG path so it attaches inline. In Claude Code / API, simply state the path — the user's client handles display.

Then offer:
- "Want me to switch to dark/light theme?"
- "Want to adjust any section or wording?"

---

## Trigger Tests

### Should trigger
- generate an announcement card for this release note
- turn this team update into an image I can post
- create a card visual for this Slack announcement
- make this into a shareable PNG
- I need a visual for this milestone post

### Should NOT trigger
- build a slide deck from this — out of scope, different tool
- give me the HTML only — this skill produces a PNG, not raw markup
- draft the announcement text itself — this skill renders text, it doesn't write it

---

## Notes

- If the announcement has no metrics and no before/after, the card will just have a header, one or two body sections, and a footer — that is fine.
- If the user pastes a very short announcement (2–3 sentences), produce a minimal card: header + one callout section + footer.
- Never invent content. If something is ambiguous, use the closest reasonable interpretation or ask.
- The skill works best when the user pastes the full announcement text, not just a title.

---

## References

- `references/section-types.md` — HTML snippets for header, footer, and each body section type
- `references/theming.md` — full CSS block for light and dark themes
