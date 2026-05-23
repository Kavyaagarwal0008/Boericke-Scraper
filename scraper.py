"""
Boericke's Homoeopathic Materia Medica Scraper
================================================
Scrapes http://homeoint.org/books/boericmm/ and produces a structured JSON dataset.

Usage:
    python scraper.py                  # Scrape all A-Z remedies
    python scraper.py --letters A B C  # Scrape specific letters only
    python scraper.py --resume         # Skip already-scraped URLs (auto-enabled if output exists)
"""

import json
import logging
import time
import argparse
import re
import sys
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "http://homeoint.org/books/boericmm"
OUTPUT_FILE = Path("boericke_remedies.json")
FAILED_FILE = Path("failed_urls.txt")
SAMPLE_FILE = Path("sample_output.json")

REQUEST_DELAY = 0.75          # seconds between requests (polite crawling)
REQUEST_TIMEOUT = 30          # seconds before a request times out
MAX_RETRIES = 3               # retry count for transient HTTP errors
RETRY_BACKOFF = 2.0           # seconds; doubles on each retry

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BoerickeResearchScraper/1.0; "
        "+https://jarvis.care)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_session() -> requests.Session:
    """Create a re-usable requests Session with default headers."""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


SESSION = _get_session()


def fetch_page(url: str) -> Optional[str]:
    """
    Fetch a URL and return its HTML text, with retry logic.

    Retries up to MAX_RETRIES times on connection errors or 5xx responses.
    Returns None and logs to failed_urls.txt if all attempts fail.

    Args:
        url: The fully-qualified URL to fetch.

    Returns:
        HTML string on success, or None on failure.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if status and int(status) < 500:
                # Client errors (404 etc.) — no point retrying
                logger.warning("HTTP %s for %s", status, url)
                _log_failed_url(url, f"HTTP {status}")
                return None
            last_exc = exc
        except requests.exceptions.RequestException as exc:
            last_exc = exc

        wait = RETRY_BACKOFF * attempt
        logger.warning(
            "Attempt %d/%d failed for %s — retrying in %.1fs",
            attempt, MAX_RETRIES, url, wait,
        )
        time.sleep(wait)

    logger.error("Giving up on %s after %d attempts: %s", url, MAX_RETRIES, last_exc)
    _log_failed_url(url, str(last_exc))
    return None


def _log_failed_url(url: str, reason: str) -> None:
    """Append a failed URL with its reason to failed_urls.txt."""
    with FAILED_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"{url}\t{reason}\n")


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------

def fetch_letter_index(letter: str) -> Optional[str]:
    """
    Fetch the index page for a given letter.

    Args:
        letter: Single uppercase letter, e.g. 'A'.

    Returns:
        HTML string of the letter index page, or None on failure.
    """
    url = f"{BASE_URL}/{letter.lower()}.htm"
    return fetch_page(url)


def parse_remedy_links(html: str, letter: str) -> list[dict]:
    """
    Parse a letter index page and extract remedy abbreviations + URLs.

    The index page has a <blockquote> containing <a> tags whose link text
    is the remedy abbreviation (e.g. 'ABIES-C') and whose href points to
    the individual remedy page.

    Args:
        html:   Raw HTML of the letter index page.
        letter: The letter being scraped (used for logging).

    Returns:
        List of dicts with keys 'abbreviation' and 'url'.
    """
    soup = BeautifulSoup(html, "html.parser")
    remedies: list[dict] = []

    # Remedy links live in <blockquote> tags
    for blockquote in soup.find_all("blockquote"):
        for anchor in blockquote.find_all("a", href=True):
            href: str = anchor["href"].strip()
            abbrev: str = anchor.get_text(strip=True).upper()

            # Skip empty or navigation links
            if not abbrev or not href:
                continue
            # Skip links that are clearly not remedy pages
            if href.startswith(("http://", "https://", "mailto:")):
                full_url = href
            else:
                full_url = f"{BASE_URL}/{href.lstrip('/')}"

            # Only include links that point into the letter sub-directory
            letter_path = f"/books/boericmm/{letter.lower()}/"
            if letter_path not in full_url:
                continue

            remedies.append({"abbreviation": abbrev, "url": full_url})

    # De-duplicate while preserving order
    seen: set[str] = set()
    unique: list[dict] = []
    for r in remedies:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique


# ---------------------------------------------------------------------------
# Remedy page parsing
# ---------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    """
    Normalise whitespace in a string extracted from HTML.

    - Collapses runs of whitespace / newlines to a single space.
    - Strips leading / trailing whitespace.

    Args:
        raw: Unsanitised text.

    Returns:
        Cleaned text string.
    """
    return re.sub(r"\s+", " ", raw).strip()


def _extract_full_name_and_common(soup: BeautifulSoup) -> tuple[str, Optional[str]]:
    """
    Extract the full Latin remedy name and optional common name from a page.

    Strategy:
    1. Look for the largest / most prominent heading (h1 > h2 > h3).
    2. Fall back to the first <b> tag or <center> tag containing all-caps text.
    3. Common name is typically parenthesised or on a separate sub-line.

    Args:
        soup: Parsed BeautifulSoup object of the remedy page.

    Returns:
        Tuple of (full_name, common_name).  common_name may be None.
    """
    full_name: str = ""
    common_name: Optional[str] = None

    # Try standard heading tags first
    for tag_name in ("h1", "h2", "h3", "h4"):
        heading = soup.find(tag_name)
        if heading:
            text = _clean_text(heading.get_text())
            if text:
                full_name = text
                break

    # Fall back: first <center> block that looks like a title
    if not full_name:
        for center in soup.find_all("center"):
            text = _clean_text(center.get_text())
            if text and len(text) > 3:
                full_name = text
                break

    # Fall back: first prominent <b> tag
    if not full_name:
        for b_tag in soup.find_all("b"):
            text = _clean_text(b_tag.get_text())
            if text and len(text) > 4 and text == text.upper():
                full_name = text
                break

    # Split common name out of parens, e.g. "ARNICA MONTANA (Leopard's Bane)"
    paren_match = re.search(r"\(([^)]+)\)", full_name)
    if paren_match:
        common_name = _clean_text(paren_match.group(1))
        full_name = _clean_text(full_name[: paren_match.start()])

    # Sometimes common name is on its own line right after the Latin name
    if not common_name:
        # Look for italic / small text immediately under the heading area
        for tag in soup.find_all(["i", "em", "small"]):
            text = _clean_text(tag.get_text())
            # A common name won't be very long and won't look like a sentence
            if text and 1 < len(text.split()) <= 6 and not text.endswith("."):
                common_name = text
                break

    return full_name.strip(), common_name



def _split_sections_linearly(container: Tag) -> tuple[str, dict[str, str], Optional[str]]:
    """
    Linear section extractor that handles inline <b> section headings.

    On the Boericke site, sections are marked with inline bold tags inside
    paragraphs, e.g.:  <p><b>Head.--</b>Fullness, heavy, pulsating...</p>

    Strategy:
    1. Collect all text-bearing nodes (p, td, li, plain text).
    2. For each node, check if it contains a <b> heading as its FIRST child.
    3. If so, start a new section; the text AFTER the heading stays in that section.
    4. Everything before the first heading is "general".

    Args:
        container: BeautifulSoup Tag to parse (usually <body>).

    Returns:
        Tuple of (general, sections, relationships).
    """

    def _parse_heading(tag: Tag) -> Optional[str]:
        """
        Return the section title if this <b> tag is a section heading.
        Matches: 'Head.--', 'Stomach.--', 'Relationships.--', 'Dose.--', etc.
        """
        if tag.name != "b":
            return None
        text = _clean_text(tag.get_text())
        m = re.match(r"^([A-Z][A-Za-z][A-Za-z\s/\-]*?)(\.[-\u2013\u2014\-]+)?$", text)
        if m and 2 < len(text) < 60:
            return _clean_text(m.group(1))
        return None

    def _node_text_after_heading(node: Tag) -> tuple[Optional[str], str]:
        """
        Split a node into (heading_title, text_after_heading).
        If the node starts with a <b> section heading, return (title, rest_text).
        Otherwise return (None, full_text).
        """
        children = list(node.children)
        if not children:
            return None, _clean_text(node.get_text())

        # Check if first meaningful child is a <b> heading
        first_tag = next(
            (c for c in children if isinstance(c, Tag)),
            None,
        )
        if first_tag:
            heading = _parse_heading(first_tag)
            if heading:
                # Collect text from everything AFTER the heading tag
                after_parts: list[str] = []
                past_heading = False
                for child in children:
                    if child is first_tag:
                        past_heading = True
                        continue
                    if past_heading:
                        if isinstance(child, Tag):
                            after_parts.append(child.get_text())
                        else:
                            after_parts.append(str(child))
                return heading, _clean_text(" ".join(after_parts))

        return None, _clean_text(node.get_text())

    # Gather all block-level content nodes
    block_nodes = container.find_all(["p", "td", "li", "blockquote", "div"])
    # If no block nodes, treat whole container as one block
    if not block_nodes:
        block_nodes = [container]

    general_parts: list[str] = []
    sections: dict[str, str] = {}
    current_section: Optional[str] = None
    current_parts: list[str] = []

    for node in block_nodes:
        heading, text = _node_text_after_heading(node)

        if heading:
            # Save previous section (or flush to general if first section)
            if current_section is not None:
                combined = _clean_text(" ".join(current_parts))
                if combined:
                    sections[current_section] = combined
            # Don't flush general_parts here — they were collected before any heading
            current_section = heading
            current_parts = [text] if text else []
        else:
            if not text:
                continue
            if current_section is None:
                general_parts.append(text)
            else:
                current_parts.append(text)

    # Save the last open section
    if current_section is not None:
        combined = _clean_text(" ".join(current_parts))
        if combined:
            sections[current_section] = combined

    general = _clean_text(" ".join(general_parts))

    # Pull out Relationships section
    relationships: Optional[str] = None
    rel_keys = [k for k in sections if "relation" in k.lower()]
    if rel_keys:
        relationships = sections.pop(rel_keys[0])

    # Fallback: detect Compare/Complementary lines that weren't headed sections
    if not relationships:
        compare_match = re.search(
            r"(Compare\s*:|Complementary\s*:|Antidote\s*:).+",
            general,
            re.IGNORECASE,
        )
        if compare_match:
            relationships = _clean_text(general[compare_match.start():])
            general = _clean_text(general[: compare_match.start()])

    return general, sections, relationships


def scrape_remedy_page(
    url: str,
    abbreviation: str,
    letter: str,
) -> Optional[dict]:
    """
    Fetch and parse a single remedy page.

    Extracts: full_name, common_name, general, sections, relationships.

    Args:
        url:          Full URL of the remedy page.
        abbreviation: Remedy abbreviation (e.g. 'ABIES-C').
        letter:       Single uppercase letter for the remedy.

    Returns:
        A dict matching the output schema, or None if the page could not be fetched.
    """
    html = fetch_page(url)
    if html is None:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # --- Name extraction ---
    full_name, common_name = _extract_full_name_and_common(soup)
    if not full_name:
        full_name = abbreviation  # last resort

    # --- Body content ---
    body = soup.find("body") or soup
    general, sections, relationships = _split_sections_linearly(body)

    # If general is empty, try pulling from the first <p> tags directly
    if not general:
        paragraphs = soup.find_all("p")
        intro_parts: list[str] = []
        for p in paragraphs:
            # Stop at first paragraph that looks like a section heading
            p_text = _clean_text(p.get_text())
            if re.match(r"^[A-Z][a-z]+\.\-\-", p_text):
                break
            if p_text:
                intro_parts.append(p_text)
        general = _clean_text(" ".join(intro_parts))

    return {
        "abbreviation": abbreviation,
        "full_name": full_name,
        "common_name": common_name if common_name else None,
        "source_url": url,
        "letter": letter.upper(),
        "general": general,
        "sections": sections,
        "relationships": relationships,
    }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def load_existing_output() -> tuple[list[dict], set[str]]:
    """
    Load an existing output file if present, for resume support.

    Returns:
        Tuple of (list of already-scraped remedy dicts, set of scraped URLs).
    """
    if not OUTPUT_FILE.exists():
        return [], set()

    try:
        with OUTPUT_FILE.open("r", encoding="utf-8") as fh:
            data: list[dict] = json.load(fh)
        scraped_urls = {r["source_url"] for r in data}
        logger.info(
            "Resuming: found %d already-scraped remedies in %s",
            len(data), OUTPUT_FILE,
        )
        return data, scraped_urls
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Could not read existing output (%s) — starting fresh.", exc)
        return [], set()


def save_output(remedies: list[dict]) -> None:
    """
    Atomically write the full remedies list to boericke_remedies.json.

    Writes to a temp file first, then renames to avoid corruption on interrupt.

    Args:
        remedies: List of remedy dicts to serialise.
    """
    tmp_path = OUTPUT_FILE.with_suffix(".tmp.json")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(remedies, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(OUTPUT_FILE)


def save_sample(remedies: list[dict], n: int = 5) -> None:
    """
    Write the first n remedies to sample_output.json.

    Args:
        remedies: Full list of scraped remedies.
        n:        Number of sample entries (default 5).
    """
    with SAMPLE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(remedies[:n], fh, ensure_ascii=False, indent=2)
    logger.info("Sample written to %s", SAMPLE_FILE)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def scrape_letter(
    letter: str,
    scraped_urls: set[str],
    all_remedies: list[dict],
) -> int:
    """
    Scrape all remedies for a single letter and append results to all_remedies.

    Skips URLs that are already in scraped_urls (resume support).
    Saves incrementally after each remedy.

    Args:
        letter:       Single uppercase letter.
        scraped_urls: Set of already-scraped source_url values.
        all_remedies: Mutable list to append new remedy dicts into.

    Returns:
        Number of newly scraped remedies for this letter.
    """
    logger.info("=== Letter %s ===", letter)

    index_html = fetch_letter_index(letter)
    if index_html is None:
        logger.error("Could not fetch index for letter %s — skipping.", letter)
        return 0

    links = parse_remedy_links(index_html, letter)
    if not links:
        logger.warning("No remedy links found for letter %s.", letter)
        return 0

    total = len(links)
    scraped_this_letter = 0

    for i, entry in enumerate(links, start=1):
        abbrev = entry["abbreviation"]
        url = entry["url"]

        if url in scraped_urls:
            logger.info("[%s] Skipping (already scraped): %s", letter, abbrev)
            continue

        print(f"[{letter}] Scraping {i}/{total} - {abbrev}", flush=True)

        remedy = scrape_remedy_page(url, abbrev, letter)
        if remedy:
            all_remedies.append(remedy)
            scraped_urls.add(url)
            scraped_this_letter += 1
            # Incremental save — protect progress on interruption
            save_output(all_remedies)

        time.sleep(REQUEST_DELAY)

    logger.info(
        "[%s] Done — %d/%d remedies scraped.", letter, scraped_this_letter, total
    )
    return scraped_this_letter


def main() -> None:
    """Entry point: parse args, then scrape the requested letters."""
    parser = argparse.ArgumentParser(
        description="Scrape Boericke's Homoeopathic Materia Medica."
    )
    parser.add_argument(
        "--letters",
        nargs="+",
        metavar="LETTER",
        help="Scrape only specific letters (e.g. --letters A B C). Default: all A-Z.",
    )
    args = parser.parse_args()

    letters_to_scrape: list[str] = (
        [l.upper() for l in args.letters]
        if args.letters
        else list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    )

    # Resume support
    all_remedies, scraped_urls = load_existing_output()

    total_new = 0
    for letter in letters_to_scrape:
        try:
            count = scrape_letter(letter, scraped_urls, all_remedies)
            total_new += count
        except KeyboardInterrupt:
            logger.warning("Interrupted! Saving progress…")
            save_output(all_remedies)
            sys.exit(0)

    # Final save + sample
    save_output(all_remedies)
    save_sample(all_remedies)

    logger.info(
        "Finished. Total remedies in output: %d (%d new this run).",
        len(all_remedies), total_new,
    )
    if FAILED_FILE.exists() and FAILED_FILE.stat().st_size > 0:
        logger.warning("Some URLs failed — see %s", FAILED_FILE)


if __name__ == "__main__":
    main()
