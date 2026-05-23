# Boericke's Materia Medica Scraper

A production-ready Python scraper for [Boericke's Homoeopathic Materia Medica](http://homeoint.org/books/boericmm/index.htm).

Produces a clean, structured JSON dataset of all A–Z remedies for use in clinical search, repertorization engines, and AI case analysis.

---

## Output

| File | Description |
|---|---|
| `boericke_remedies.json` | Full dataset — all ~600 remedies |
| `sample_output.json` | First 5 remedies (for review) |
| `failed_urls.txt` | Any URLs that could not be fetched |

### Schema

```json
{
  "abbreviation": "ABIES-C",
  "full_name": "ABIES CANADENSIS-PINUS CANADENSIS",
  "common_name": "Hemlock Spruce",
  "source_url": "http://homeoint.org/books/boericmm/a/abies-c.htm",
  "letter": "A",
  "general": "Mucous membranes are affected...",
  "sections": {
    "Head": "Feels light-headed...",
    "Stomach": "Canine hunger...",
    "Dose": "First to third potency."
  },
  "relationships": "Compare: Abies-n, Puls..."
}
```

---

## Setup

**Requirements:** Python 3.9+

```bash
# 1. Clone the repo
git clone https://github.com/Kavyaagarwal0008/Boericke-Scraper.git
cd boericke-scraper

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        
# Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Usage

### Scrape everything (A–Z)

```bash
python scraper.py
```

Runs through all 26 letter index pages, ~600 individual remedy pages. Estimated time: **20–30 minutes** (polite crawling with 0.75 s delay).

### Scrape specific letters

```bash
python scraper.py --letters A B C
```

### Resume an interrupted run

Just run the same command again. The scraper checks `boericke_remedies.json` and automatically skips any URL it has already scraped:

```bash
python scraper.py          # safe to re-run at any time
```

---

## Progress output

```
10:05:32 [INFO] === Letter A ===
[A] Scraping 1/87 - ABIES-C
[A] Scraping 2/87 - ABIES-N
[A] Scraping 3/87 - ABROMA-A
...
10:07:14 [INFO] [A] Done — 87/87 remedies scraped.
10:07:14 [INFO] === Letter B ===
```

---

## Error handling

- **HTTP 4xx errors** (e.g. 404): logged to `failed_urls.txt`, scraper continues.
- **HTTP 5xx / network errors**: retried up to 3 times with exponential backoff, then logged to `failed_urls.txt`.
- **Keyboard interrupt** (`Ctrl+C`): progress is saved immediately before exit.
- **Incremental saves**: output is written after every single remedy, so a crash never loses more than one entry.

---

## Design notes

### Parsing strategy

Each remedy page is parsed in two passes:

1. **Name extraction** — looks for the largest heading tag (`h1` → `h2` → `h3` → `center` → first `<b>` in all-caps). Parenthesised common name is split out with a regex.

2. **Section splitting** — iterates over all `<b>` tags and uses the pattern `Head.--`, `Stomach.--` etc. as section boundary markers. Everything before the first heading becomes `general`; the `Relationships` section is extracted separately.

Because the site's HTML is inconsistent across years of edits, the parser is written defensively and falls back gracefully.

### Politeness

- 0.75 s delay between every request
- Descriptive `User-Agent` identifying the scraper and project URL
- No parallel requests

---

## Project structure

```
boericke-scraper/
├── scraper.py               # Main scraper
├── requirements.txt         # Pinned dependencies
├── README.md                # This file
├── boericke_remedies.json   # Full output (generated)
├── sample_output.json       # 5-remedy sample (generated)
└── failed_urls.txt          # Failed URLs (generated, may be empty)
```

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `requests` | 2.31.0 | HTTP client |
| `beautifulsoup4` | 4.12.3 | HTML parsing |
| `lxml` | 5.2.2 | Fast HTML parser backend for BS4 |

---

*Built for jarvis.care — AI-powered clinical assistant for homeopathic practitioners.*
#
