#!/usr/bin/env python3
"""
CLI Tool for Google AI Overview (AIO)
Sends inquiries to Google and streams the AI-generated response back to the terminal.
Returns clean markdown output from Google's AI Overview.

Usage:
  python ask_google.py "what happened yesterday?"
"""

import sys
import time
import argparse
from urllib.parse import quote_plus

try:
    from rich.console import Console
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth
except ImportError:
    print("Dependencies missing! Please run:")
    print("  pip install rich playwright playwright-stealth")
    print("  playwright install chromium")
    sys.exit(1)

POLL_MS = 100
MAX_WAIT_S = 30
CAPTCHA_WAIT_S = 45
USER_DATA_DIR = "/tmp/pw_google_aio"

console = Console()

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.7632.6 Safari/537.36"
)

CHROME_ARGS = [
    "--headless=new",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-popup-blocking",
    "--disable-component-update",
    "--disable-hang-monitor",
]

_AIO_INIT_SCRIPT = """
window.getAioState = function() {
    const SKIP_CLASSES = new Set([
        'uJ19be', 'txxDge', 'Fsg96', 'Jd31eb', 'DBd2Wb',
    ]);
    const SKIP_TAGS = new Set([
        'BUTTON', 'SVG', 'IMG', 'STYLE', 'SCRIPT', 'NOSCRIPT',
    ]);

    function shouldSkip(el) {
        if (SKIP_TAGS.has(el.tagName)) return true;
        for (const cls of el.classList) {
            if (SKIP_CLASSES.has(cls)) return true;
        }
        if (el.getAttribute('data-xid') === 'Gd7Hsc') return true;
        const style = el.getAttribute('style') || '';
        if (style.includes('display:none') || style.includes('display: none'))
            return true;
        return false;
    }

    function walk(node) {
        if (node.nodeType === 3) return node.textContent;
        if (node.nodeType !== 1) return '';
        if (shouldSkip(node)) return '';

        const tag = node.tagName;

        if (tag === 'STRONG') {
            const inner = walkChildren(node).trim();
            return inner ? '**' + inner + '**' : '';
        }
        if (tag === 'EM') {
            const inner = walkChildren(node).trim();
            return inner ? '*' + inner + '*' : '';
        }
        if (tag === 'MARK') {
            return walkChildren(node);
        }
        if (tag === 'UL' || tag === 'OL') {
            const items = [];
            for (const child of node.children) {
                if (child.tagName === 'LI') {
                    const text = walk(child).trim();
                    if (text) items.push('- ' + text);
                }
            }
            return items.length ? '\\n' + items.join('\\n') + '\\n' : '';
        }
        if (tag === 'LI') {
            return walkChildren(node);
        }
        if (node.getAttribute('role') === 'heading') {
            const level = parseInt(node.getAttribute('aria-level') || '3', 10);
            const prefix = '#'.repeat(Math.min(level, 6));
            const inner = walkChildren(node).trim();
            return inner ? '\\n' + prefix + ' ' + inner + '\\n' : '';
        }
        if (tag === 'DIV' && node.classList.contains('Y3BBE')) {
            const inner = walkChildren(node).trim();
            return inner ? inner + '\\n\\n' : '';
        }
        return walkChildren(node);
    }

    function walkChildren(node) {
        let out = '';
        for (const child of node.childNodes) {
            out += walk(child);
        }
        return out;
    }

    const containers = document.querySelectorAll('[data-container-id]');
    for (const c of containers) {
        const id = c.getAttribute('data-container-id');
        if (id === 'main-col') continue;
        const rawText = c.innerText?.trim();
        if (rawText && rawText.length > 15) {
            const raw = walkChildren(c);
            const markdown = raw
                .replace(/[ \\t]+/g, ' ')
                .replace(/\\n{3,}/g, '\\n\\n')
                .replace(/<!--[^>]*-->/g, '')
                .replace(/\\[\\d+(?:,\\s*\\d+)*\\]/g, '')
                .trim();
            const allComplete = c.querySelectorAll('[data-complete]');
            const pending = Array.from(allComplete).filter(
                el => el.getAttribute('data-complete') !== 'true'
            );
            return {
                id: id,
                text: markdown,
                len: markdown.length,
                complete: pending.length === 0 && allComplete.length > 0
            };
        }
    }
    return { id: null, text: '', len: 0, complete: false };
};
"""


def build_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}&udm=50&aep=11"


def _get_aio_state(page) -> dict:
    return page.evaluate("() => window.getAioState()")


def _has_captcha(page) -> bool:
    body = page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
    return "unusual traffic" in body.lower() or "captcha" in body.lower()


def _check_no_ai_mode(page) -> bool:
    body = page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
    return (
        "Modo IA no está disponible" in body
        or "AI Mode is not currently available" in body
    )


def _dismiss_cookie_consent(page):
    for label in ["Aceptar todo", "Accept all"]:
        try:
            btn = page.locator(f'button:has-text("{label}")').first
            btn.click(timeout=1500)
            page.wait_for_load_state("domcontentloaded", timeout=3000)
            return
        except Exception:
            continue


def extract_aio(query: str, debug: bool = False):
    url = build_url(query)
    stealth = Stealth()

    with stealth.use_sync(sync_playwright()) as p:
        ctx = None
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=True,
                args=list(CHROME_ARGS),
                viewport={"width": 1280, "height": 900},
                user_agent=USER_AGENT,
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                ignore_default_args=["--enable-automation"],
            )

            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.add_init_script(_AIO_INIT_SCRIPT)

            with console.status(
                f"[bold cyan]Asking Google AI: [white]'{query}'[bold cyan]...",
                spinner="dots",
            ):
                t0 = time.monotonic()
                page.goto(url, wait_until="domcontentloaded")
                time.sleep(0.5)

                _dismiss_cookie_consent(page)

                if _has_captcha(page):
                    console.print(
                        f"[bold yellow]\u26a0\ufe0f CAPTCHA detected.[/bold yellow] "
                        f"Please solve it within {CAPTCHA_WAIT_S} seconds."
                    )
                    deadline = time.monotonic() + CAPTCHA_WAIT_S
                    while time.monotonic() < deadline and _has_captcha(page):
                        time.sleep(1)
                    if _has_captcha(page):
                        console.print(
                            "[bold red]\u274c Timed out waiting for CAPTCHA solve.[/bold red]"
                        )
                        return

                if _check_no_ai_mode(page):
                    console.print(
                        "[bold red]\u274c Google AI Overview is currently unavailable "
                        "for this account/IP (Bot detection triggered).[/bold red]"
                    )
                    return

                deadline = time.monotonic() + MAX_WAIT_S
                first_text_found = False

                while time.monotonic() < deadline:
                    state = _get_aio_state(page)
                    if state["text"]:
                        first_text_found = True
                        break
                    time.sleep(POLL_MS / 1000)

            if not first_text_found:
                console.print(
                    "[bold red]\u274c No AI response generated for this query.[/bold red]"
                )
                return

            console.print("[bold green]Google AI:[/bold green]\n")

            prev_text = ""
            stable_count = 0
            first_text_at = time.monotonic()
            MIN_STABLE_S = 3.0

            while time.monotonic() < deadline:
                state = _get_aio_state(page)
                current_text = state["text"]

                if current_text and current_text != prev_text:
                    delta = current_text[len(prev_text) :]
                    console.print(delta, end="")
                    prev_text = current_text
                    stable_count = 0
                elif current_text:
                    stable_count += 1
                    time_since_first = time.monotonic() - first_text_at
                    is_stable_long_enough = (
                        stable_count > 30 and time_since_first > MIN_STABLE_S
                    )
                    if is_stable_long_enough and state["complete"]:
                        break

                time.sleep(POLL_MS / 1000)

            console.print("\n")

            if debug:
                elapsed = time.monotonic() - t0
                console.print(
                    f"[dim]Finished in {elapsed:.2f}s | "
                    f"Length: {len(prev_text)} chars (markdown)[/dim]"
                )

        finally:
            if ctx:
                try:
                    ctx.close()
                except Exception:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ask Google AI via CLI")
    parser.add_argument("query", nargs="+", help="The question to ask")
    parser.add_argument(
        "--debug", action="store_true", help="Show debug stats at the end"
    )

    args = parser.parse_args()
    query = " ".join(args.query)

    try:
        extract_aio(query, debug=args.debug)
    except KeyboardInterrupt:
        console.print("\n[bold red]Canceled by user.[/bold red]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]An error occurred:[/bold red] {str(e)}")
        sys.exit(1)
