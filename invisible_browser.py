"""
Invisible headless=False Playwright Chromium on macOS.

Runs Chromium with headless=False (so sites like Google don't block AI mode)
but ensures the browser window NEVER steals focus, doesn't move active windows,
and stays completely invisible/non-intrusive.

Uses a 3-layer approach:
  1. LSBackgroundOnly Info.plist patch — prevents macOS from ever activating Chrome
  2. Chrome flags — position window far offscreen + disable background throttling
  3. CDP post-launch — reposition window offscreen via DevTools protocol

IMPORTANT CAVEATS:
  - LSBackgroundOnly makes Chrome invisible in Cmd+Tab (ideal for automation)
  - DO NOT use windowState="minimized" — CDP commands freeze when minimized
  - Info.plist patch may invalidate code signature; we re-sign with ad-hoc
  - Offscreen position uses -9999 (not -32000) since macOS may clamp extreme values
"""

import asyncio
import plistlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from playwright.async_api import async_playwright


# ---------------------------------------------------------------------------
# 1. Info.plist patching (macOS-only, most effective layer)
# ---------------------------------------------------------------------------


def _find_app_bundle(executable_path: str) -> Path | None:
    """Walk up from the executable to find the .app bundle root."""
    p = Path(executable_path).resolve()
    while p != p.parent:
        if p.suffix == ".app":
            return p
        p = p.parent
    return None


def patch_info_plist(executable_path: str) -> bool:
    """
    Add LSBackgroundOnly=True to the Chromium .app's Info.plist.
    This tells macOS to treat Chrome as a background-only process —
    no Dock icon, no Cmd+Tab entry, and critically: no focus stealing.

    Returns True if patched (or already patched), False on failure.
    """
    if platform.system() != "Darwin":
        return False

    app_bundle = _find_app_bundle(executable_path)
    if not app_bundle:
        print(f"[invisible_browser] Could not find .app bundle for {executable_path}")
        return False

    plist_path = app_bundle / "Contents" / "Info.plist"
    if not plist_path.exists():
        print(f"[invisible_browser] Info.plist not found at {plist_path}")
        return False

    # Read current plist
    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)

    # Already patched?
    if plist.get("LSBackgroundOnly") is True:
        print("[invisible_browser] Info.plist already patched with LSBackgroundOnly")
        return True

    # Backup original (only if no backup exists yet)
    backup_path = plist_path.with_suffix(".plist.original")
    if not backup_path.exists():
        shutil.copy2(plist_path, backup_path)
        print(f"[invisible_browser] Backed up original Info.plist to {backup_path}")

    # Patch
    plist["LSBackgroundOnly"] = True
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    # Re-sign with ad-hoc signature (modifying plist invalidates code signature)
    try:
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)],
            capture_output=True,
            timeout=30,
        )
        print("[invisible_browser] Re-signed .app bundle with ad-hoc signature")
    except Exception as e:
        print(f"[invisible_browser] Warning: codesign failed ({e}), may still work")

    # Touch the .app to invalidate Launch Services cache
    app_bundle.touch()

    print("[invisible_browser] Patched Info.plist with LSBackgroundOnly=True")
    return True


def restore_info_plist(executable_path: str) -> bool:
    """Restore the original Info.plist from backup."""
    app_bundle = _find_app_bundle(executable_path)
    if not app_bundle:
        return False

    plist_path = app_bundle / "Contents" / "Info.plist"
    backup_path = plist_path.with_suffix(".plist.original")

    if backup_path.exists():
        shutil.copy2(backup_path, plist_path)
        # Re-sign after restore
        try:
            subprocess.run(
                ["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            pass
        print("[invisible_browser] Restored original Info.plist")
        return True
    return False


# ---------------------------------------------------------------------------
# 2. Chrome launch arguments
# ---------------------------------------------------------------------------

INVISIBLE_CHROME_ARGS = [
    # --- OFFSCREEN POSITIONING (primary invisibility mechanism) ---
    "--window-position=-9999,-9999",  # Far offscreen; macOS clips extreme values
    "--window-size=800,600",  # Reasonable size for rendering (not 1x1)
    # --- PREVENT BACKGROUND THROTTLING (critical for automation) ---
    # Without these, Chrome throttles timers/rendering for background tabs,
    # which causes timeouts and stale element errors in Playwright.
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    # --- REDUCE UI NOISE ---
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-popup-blocking",
    "--disable-component-update",
    "--disable-hang-monitor",
    # --- ANTI-DETECTION (so Google doesn't flag as automated) ---
    "--disable-blink-features=AutomationControlled",
]


# ---------------------------------------------------------------------------
# 3. CDP post-launch window positioning
# ---------------------------------------------------------------------------


async def _move_window_offscreen_via_cdp(context, page):
    """
    Belt-and-suspenders: use CDP to reposition the window offscreen.
    MUST keep windowState='normal' — minimized state freezes CDP commands.
    """
    try:
        cdp = await context.new_cdp_session(page)
        result = await cdp.send("Browser.getWindowForTarget")
        window_id = result["windowId"]
        await cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": -9999,
                    "top": -9999,
                    "width": 1920,
                    "height": 1080,
                    "windowState": "normal",  # NEVER "minimized" — CDP freezes!
                },
            },
        )
        await cdp.detach()
        print("[invisible_browser] Moved window offscreen via CDP")
    except Exception as e:
        # Non-fatal — the launch args already handle positioning
        print(f"[invisible_browser] CDP window move skipped: {e}")


# ---------------------------------------------------------------------------
# 4. AppleScript hide (last-resort fallback)
# ---------------------------------------------------------------------------


def _applescript_hide_chrome():
    """Use AppleScript to hide the Chrome process entirely."""
    if platform.system() != "Darwin":
        return
    try:
        # Try both possible process names
        for name in ["Google Chrome for Testing", "Chromium", "Google Chrome"]:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "System Events" to set visible of process "{name}" to false',
                ],
                capture_output=True,
                timeout=5,
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 5. Main API: launch_invisible_browser()
# ---------------------------------------------------------------------------


async def launch_invisible_browser(
    user_data_dir: str = "/tmp/pw-invisible-profile",
    patch_plist: bool = True,
    viewport: dict | None = None,
    extra_args: list[str] | None = None,
    **kwargs,
):
    """
    Launch a Playwright persistent Chromium context that is completely
    invisible / non-intrusive on macOS.

    Args:
        user_data_dir: Path to Chrome user data directory (persistent profile).
        patch_plist:   If True, patch Chromium's Info.plist with LSBackgroundOnly
                       (most effective, but makes Chrome invisible in Cmd+Tab).
        viewport:      Viewport size dict, e.g. {"width": 1920, "height": 1080}.
        extra_args:    Additional Chrome flags to append.
        **kwargs:      Extra kwargs passed to launch_persistent_context().

    Returns:
        Tuple of (playwright_instance, context, page).
        Caller is responsible for cleanup — call close_invisible_browser().
    """
    pw = await async_playwright().start()
    executable_path = pw.chromium.executable_path

    # Layer 1: Patch Info.plist
    if patch_plist:
        patch_info_plist(executable_path)

    # Layer 2: Chrome flags
    args = list(INVISIBLE_CHROME_ARGS)
    if extra_args:
        args.extend(extra_args)

    # Launch
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=False,
        args=args,
        viewport=viewport or {"width": 1920, "height": 1080},
        ignore_default_args=["--enable-automation"],  # Remove automation flag
        **kwargs,
    )

    # Get or create a page
    page = context.pages[0] if context.pages else await context.new_page()

    # Layer 3: CDP offscreen positioning
    await _move_window_offscreen_via_cdp(context, page)

    # Layer 4: AppleScript hide (catches edge cases)
    _applescript_hide_chrome()

    return pw, context, page


async def close_invisible_browser(pw, context, restore_plist: bool = True):
    """Clean shutdown: close context, stop playwright, optionally restore plist."""
    executable_path = pw.chromium.executable_path
    await context.close()
    await pw.stop()
    if restore_plist:
        restore_info_plist(executable_path)


# ---------------------------------------------------------------------------
# Demo / test
# ---------------------------------------------------------------------------


async def main():
    print("Launching invisible browser (headless=False, zero focus steal)...")

    pw, context, page = await launch_invisible_browser()

    await page.goto("https://www.google.com")
    title = await page.title()
    print(f"Page title: {title}")

    # Demonstrate that the browser is fully functional
    content = await page.content()
    print(f"Page HTML length: {len(content)} chars")

    # Take a screenshot to prove rendering works
    await page.screenshot(path="/tmp/invisible_browser_test.png")
    print("Screenshot saved to /tmp/invisible_browser_test.png")

    await close_invisible_browser(pw, context)
    print("Done. Browser closed cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
