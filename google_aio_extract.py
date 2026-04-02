"""
Google AI Overview Progressive Text Extractor

Extracts Google AI Overview (AIO) text progressively as it streams into the DOM.

Architecture:
  Google's AIO renders inside div[data-container-id] elements. The answer text
  arrives via DOM mutations in batches of SPAN elements within paragraph
  containers. The attribute data-complete="true" is set on each section after
  its content finishes streaming. The overall flow is:

    1. Page loads -> navigation chrome renders
    2. "Searching" indicator appears (~0-1s)
    3. Answer text streams into div[data-container-id="N"] in batches (~1-3s)
    4. data-complete="true" propagates up the tree as sections finish
    5. Source citations and follow-up suggestions render last

  The container ID ("N") is dynamic per page load; we identify the AIO
  container by finding the first non-"main-col" data-container-id whose
  innerText exceeds a threshold.

Two extraction strategies are provided:
  - Polling (simple, robust): polls innerText every POLL_MS and diffs
  - MutationObserver (fine-grained): captures individual SPAN/text insertions

Requirements:
  pip install playwright playwright-stealth
  playwright install chromium

Usage:
  python google_aio_extract.py "your search query here"
  python google_aio_extract.py  # defaults to "what is the capital of france"
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

POLL_MS = 100
MAX_WAIT_S = 30
CAPTCHA_WAIT_S = 45
USER_DATA_DIR = "/tmp/pw_google_aio"


@dataclass
class AIOResult:
    query: str
    full_text: str = ""
    chunks: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0
    container_id: Optional[str] = None


def build_url(query: str) -> str:
    from urllib.parse import quote_plus

    return f"https://www.google.com/search?q={quote_plus(query)}&udm=50&aep=11"


def _inject_mutation_tracker(page):
    """Inject a MutationObserver that streams text chunks back via page.expose_function."""
    chunks = []
    page.expose_function("_aioChunk", lambda data: chunks.append(json.loads(data)))

    page.add_init_script("""
        let _aioStart = 0;
        document.addEventListener('DOMContentLoaded', () => {
            _aioStart = performance.now();
            const obs = new MutationObserver((mutations) => {
                const ms = Math.round(performance.now() - _aioStart);
                for (const m of mutations) {
                    if (m.type !== 'childList') continue;
                    for (const node of m.addedNodes) {
                        const container = node.parentElement?.closest?.('[data-container-id]');
                        const cid = container?.getAttribute('data-container-id');
                        if (!cid || cid === 'main-col') continue;

                        let text = '';
                        if (node.nodeType === 3) {
                            text = node.textContent.trim();
                        } else if (node.nodeType === 1) {
                            text = node.innerText?.trim() || '';
                        }
                        if (text.length > 0) {
                            window._aioChunk(JSON.stringify({
                                ms, text, cid,
                                tag: node.nodeType === 1 ? node.tagName : '#text',
                            }));
                        }
                    }
                }
            });
            obs.observe(document.body, { childList: true, subtree: true });
        });
    """)
    return chunks


def _get_aio_text(page) -> dict:
    """Extract current AI Overview text from the page."""
    return page.evaluate("""() => {
        const containers = document.querySelectorAll('[data-container-id]');
        for (const c of containers) {
            const id = c.getAttribute('data-container-id');
            if (id === 'main-col') continue;
            const text = c.innerText?.trim();
            if (text && text.length > 15) {
                const allComplete = c.querySelectorAll('[data-complete]');
                const pending = Array.from(allComplete).filter(
                    el => el.getAttribute('data-complete') !== 'true'
                );
                return {
                    id: id,
                    text: text,
                    len: text.length,
                    complete: pending.length === 0 && allComplete.length > 0,
                    completedCount: allComplete.length - pending.length,
                    pendingCount: pending.length,
                };
            }
        }
        return { id: null, text: '', len: 0, complete: false, completedCount: 0, pendingCount: 0 };
    }""")


def _has_captcha(page) -> bool:
    body = page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
    return "unusual traffic" in body.lower() or "captcha" in body.lower()


def extract_aio(
    query: str,
    headless: bool = False,
    verbose: bool = True,
) -> AIOResult:
    """
    Navigate to Google AI Overview and extract text progressively.

    Args:
        query:    The search query.
        headless: Run browser headless (will likely hit CAPTCHA).
        verbose:  Print streaming text to stdout in real-time.

    Returns:
        AIOResult with full text, chunks, and timing.
    """
    url = build_url(query)
    result = AIOResult(query=query)
    stealth = Stealth()

    with stealth.use_sync(sync_playwright()) as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = ctx.new_page()
        mutation_chunks = _inject_mutation_tracker(page)

        t0 = time.monotonic()
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(0.5)

        if _has_captcha(page):
            if verbose:
                print(
                    f"[CAPTCHA] Google is showing a CAPTCHA. "
                    f"Solve it in the browser window within {CAPTCHA_WAIT_S}s...",
                    file=sys.stderr,
                )
            deadline = time.monotonic() + CAPTCHA_WAIT_S
            while time.monotonic() < deadline and _has_captcha(page):
                time.sleep(1)
            if _has_captcha(page):
                if verbose:
                    print(
                        "[CAPTCHA] Timed out waiting for CAPTCHA solve.",
                        file=sys.stderr,
                    )
                ctx.close()
                return result

        prev_text = ""
        stable_count = 0
        first_text_at = 0.0
        deadline = time.monotonic() + MAX_WAIT_S
        MIN_STABLE_S = 3.0

        while time.monotonic() < deadline:
            time.sleep(POLL_MS / 1000)
            state = _get_aio_text(page)

            current_text = state["text"]
            if current_text and current_text != prev_text:
                delta = current_text[len(prev_text) :]
                elapsed = int((time.monotonic() - t0) * 1000)

                if not first_text_at:
                    first_text_at = time.monotonic()

                result.chunks.append(
                    {
                        "ms": elapsed,
                        "delta": delta,
                        "total_len": state["len"],
                    }
                )
                result.container_id = state["id"]

                if verbose:
                    sys.stdout.write(delta)
                    sys.stdout.flush()

                prev_text = current_text
                stable_count = 0
            elif current_text:
                stable_count += 1
                time_since_first = (
                    time.monotonic() - first_text_at if first_text_at else 0
                )
                is_stable_long_enough = (
                    stable_count > 30 and time_since_first > MIN_STABLE_S
                )
                if is_stable_long_enough and state["complete"]:
                    break

        if verbose:
            sys.stdout.write("\n")
            sys.stdout.flush()

        result.full_text = prev_text
        result.elapsed_ms = int((time.monotonic() - t0) * 1000)

        if verbose:
            print("--- Extraction stats ---", file=sys.stderr)
            print(f"Query:        {query}", file=sys.stderr)
            print(
                f'Container:    data-container-id="{result.container_id}"',
                file=sys.stderr,
            )
            print(f"Total chars:  {len(result.full_text)}", file=sys.stderr)
            print(f"Chunks:       {len(result.chunks)}", file=sys.stderr)
            print(f"Elapsed:      {result.elapsed_ms}ms", file=sys.stderr)
            print(f"DOM mutations: {len(mutation_chunks)}", file=sys.stderr)
            for i, ch in enumerate(result.chunks):
                preview = ch["delta"][:60].replace("\n", "\\n")
                print(
                    f'  chunk[{i}] +{ch["ms"]}ms +{len(ch["delta"])}chars "{preview}..."',
                    file=sys.stderr,
                )

        ctx.close()

    return result


if __name__ == "__main__":
    query = (
        " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "what is the capital of france"
    )
    result = extract_aio(query, headless=False, verbose=True)
