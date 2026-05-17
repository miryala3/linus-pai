#!/usr/bin/env python3
"""
web_scraper.py — AI-guided web scraper
Describe what data you want in English; PAI generates the scraping code,
runs it locally, and returns structured JSON/CSV — all without cloud APIs.

Usage:
    python samples/web_scraper.py --url https://example.com --want "all product names and prices"
    python samples/web_scraper.py --url https://news.ycombinator.com --want "top 10 story titles and URLs"
    python samples/web_scraper.py --url https://api.github.com/repos/owner/repo --want "star count and description"
    python samples/web_scraper.py --sitemap https://example.com --want "all page titles"  # crawl
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

SCRAPE_PROMPT = """You are an expert web scraping engineer.

## Target URL
{url}

## Page content (first 6000 chars)
```html
{html}
```

## What to extract
{want}

Write Python code that:
1. Fetches {url} using `requests` or `urllib`
2. Parses HTML with `BeautifulSoup4` (bs4)
3. Extracts exactly what was asked for
4. Prints results as JSON to stdout

Rules:
- Handle errors gracefully (404, timeout, encoding)
- Set a realistic User-Agent header
- Do NOT use selenium/playwright (headless only)
- Output ONLY valid Python — no markdown, no explanation

```python
# Your code here
```
"""

CRAWL_PROMPT = """Write Python code that:
1. Fetches the sitemap at {url}/sitemap.xml (or discovers it from robots.txt)
2. Visits up to 50 pages
3. For each page, extracts: {want}
4. Saves results to a list and prints as JSON at the end

Use requests + BeautifulSoup4. Handle errors gracefully.
Output ONLY valid Python.
"""


def _call_pai(prompt: str) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sudo",
        "max_tokens": 2000, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["response"]


def _fetch_page(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LinusPAI-Scraper/1.0; +https://github.com/miryala3/linus-pai)",
        "Accept": "text/html,application/xhtml+xml,application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(50_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return f"<!-- FETCH ERROR: {exc} -->"


def _extract_code(response: str) -> str:
    if "```python" in response:
        return response.split("```python", 1)[-1].split("```")[0].strip()
    if "```" in response:
        return response.split("```", 1)[-1].split("```")[0].strip()
    return response.strip()


def _ensure_deps() -> None:
    try:
        import requests
        import bs4
    except ImportError:
        print("  Installing requests and beautifulsoup4…")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "requests", "beautifulsoup4", "-q"])


def _run_code(code: str, out_file: str | None = None) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".py")
    try:
        os.write(fd, code.encode())
        os.close(fd)
        print("\n" + "="*60 + " Generated scraper:")
        print(code[:600] + ("…" if len(code) > 600 else ""))
        print("="*60 + " Output:")
        result = subprocess.run([sys.executable, tmp],
                                capture_output=True, text=True, timeout=60)
        output = result.stdout or result.stderr
        print(output[:3000])

        if out_file and result.stdout:
            Path(out_file).write_text(result.stdout)
            print(f"\n✓ Saved to {out_file}")
    except subprocess.TimeoutExpired:
        print("[!] Scraper timed out after 60s.")
    finally:
        os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser(description="PAI Web Scraper")
    ap.add_argument("--url",     metavar="URL",  help="Target URL to scrape")
    ap.add_argument("--sitemap", metavar="URL",  help="Root URL to crawl via sitemap")
    ap.add_argument("--want",    metavar="DESC", help="What data to extract (natural language)")
    ap.add_argument("--out",     metavar="FILE", help="Save output JSON/CSV to file")
    ap.add_argument("--code-only",action="store_true", help="Print generated code, don't run")
    args = ap.parse_args()

    if not (args.url or args.sitemap) or not args.want:
        ap.print_help()
        return

    _ensure_deps()

    if args.sitemap:
        prompt = CRAWL_PROMPT.format(url=args.sitemap, want=args.want)
        print(f"  Generating crawler for {args.sitemap}…", end="", flush=True)
    else:
        print(f"  Fetching {args.url}…", end="", flush=True)
        html = _fetch_page(args.url)
        print(f" {len(html):,} chars")
        prompt = SCRAPE_PROMPT.format(url=args.url, html=html[:6000], want=args.want)
        print("  Generating scraper…", end="", flush=True)

    code_raw = _call_pai(prompt)
    code     = _extract_code(code_raw)
    print(" done.")

    if args.code_only:
        print(code)
        return

    _run_code(code, args.out)


if __name__ == "__main__":
    main()
