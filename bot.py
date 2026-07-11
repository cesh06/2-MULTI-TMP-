"""
TMP Multi-Account Bot — Two-Run Strategy
=========================================
Run 1 (00:00 Kigali):
  • 30-task accounts  → complete tasks  1–15
  •  5-task accounts  → complete tasks  1– 5  (fully done, skipped in run 2)
  •  8-task accounts  → complete tasks  1– 8  (fully done, skipped in run 2)

Run 2 (00:30 Kigali — queued, starts only after run 1 finishes):
  • 30-task accounts  → complete tasks 16–30
  •  5/8-task accounts→ done >= target  → skipped automatically

Session safety: each account gets its own browser context (fresh cookies).
Concurrency guard in the workflow prevents two runs from being active at once,
so TMP never sees the same account logged in from two sessions simultaneously.
"""

import asyncio
import os
import random
import re
import glob
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()

# ── Secrets ──────────────────────────────────────────────────────────────────
ACCOUNTS_RAW = os.getenv("TMP_ACCOUNTS")
if not ACCOUNTS_RAW:
    raise ValueError("TMP_ACCOUNTS must be set.\nFormat: phone:pass:tasks,phone:pass:tasks,...")

# How many tasks each account can do in ONE run (both runs use the same limit).
# Default 15 → 2 × 15 = 30 tasks total for accounts with target=30.
RUN_LIMIT = int(os.getenv("TMP_RUN_LIMIT", "15"))

TMP_LOGIN_URL   = "https://tmpjob.net/login"
TASK_CENTER_URL = "https://tmpjob.net/index/rotary/index.html"

MAX_CTX_RETRIES  = 3   # retries for iframe/context probe
MAX_TASK_RETRIES = 2   # retries per individual task


# ─────────────────────────────────────────────────────────────────────────────
# STORAGE CLEANUP
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_old_screenshots() -> None:
    removed = 0
    for f in glob.glob("*.png"):
        try:
            os.remove(f)
            removed += 1
        except OSError:
            pass
    print(f"🧹 Removed {removed} old screenshot(s).")


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT / IFRAME DETECTION
# ─────────────────────────────────────────────────────────────────────────────

async def get_active_context(page, probe="text=Shaka gahunda", retries=MAX_CTX_RETRIES):
    """
    Return the frame (main page or any iframe) that contains `probe`.
    Walks ALL frames on the page; retries up to `retries` times.
    Falls back to first available iframe or the main page.
    """
    for attempt in range(retries):
        # 1. Check main page
        try:
            await page.wait_for_selector(probe, timeout=3000)
            return page
        except PlaywrightTimeout:
            pass

        # 2. Walk every iframe
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                await frame.wait_for_selector(probe, timeout=2000)
                return frame
            except PlaywrightTimeout:
                continue

        if attempt < retries - 1:
            print(f"   🔄 Context probe attempt {attempt + 1}/{retries} — retrying…")
            await asyncio.sleep(2)

    # Fallback
    frames = page.frames
    ctx = frames[1] if len(frames) > 1 else page
    print("   ⚠️ Probe selector not found — using fallback context.")
    return ctx


async def get_login_context(page):
    """Return the frame that contains login inputs (main page or iframe)."""
    try:
        await page.wait_for_selector("input", timeout=10000)
        return page
    except PlaywrightTimeout:
        pass
    iframe_el = await page.wait_for_selector("iframe", timeout=15000)
    return await iframe_el.content_frame()


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def safe_click(ctx, selectors, timeout=8000) -> bool:
    """Click the first visible selector. Returns True on success."""
    if isinstance(selectors, str):
        selectors = [selectors]
    for sel in selectors:
        try:
            await ctx.wait_for_selector(sel, state="visible", timeout=timeout)
            await ctx.click(sel, force=True)
            await asyncio.sleep(random.uniform(0.4, 0.9))  # human-like pause
            return True
        except PlaywrightTimeout:
            continue
    return False


async def wait_idle(page, timeout=12000) -> None:
    """Best-effort networkidle wait — never raises."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeout:
        pass


async def is_logged_in(ctx) -> bool:
    """Check for any Kinyarwanda phrase that only appears post-login."""
    try:
        text = await ctx.inner_text("body")
        phrases = [
            "Iterambere ry'imirimo", "Shaka gahunda",
            "Shaka komisiyo uyu munsi", "Kubitsa",
            "Impano zidasanzwe", "Genda kwakira igihembo",
        ]
        return any(p in text for p in phrases)
    except Exception:
        return False


async def close_any_popup(ctx) -> None:
    """Dismiss known TMP pop-ups (announcements, generic modals)."""
    popup_groups = [
        ["text=Gufunga", "button:has-text('Gufunga')"],                         # Itangazo
        ["button:has-text('X')", ".close", "[aria-label='Close']",
         ".modal-close", ".popup-close"],                                        # Generic
    ]
    for selectors in popup_groups:
        if await safe_click(ctx, selectors, timeout=3000):
            print("   🔒 Pop-up dismissed.")
            await asyncio.sleep(1)
            return
    print("   ℹ️ No pop-up found.")


async def go_to_task_center(page) -> None:
    """Navigate back to the Task Center (nav button → URL fallback)."""
    clicked = await safe_click(
        page,
        ["text=Inshingano", ".bottom-nav > a:nth-child(2)"],
        timeout=8000,
    )
    if not clicked:
        print("   ⚠️ Nav button not found — loading Task Center URL.")
        await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded", timeout=30000)
    await wait_idle(page)


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE TASK EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

async def run_single_task(page, task_num: int, target: int) -> bool:
    """
    Execute one task cycle. Returns True on success.
    Does NOT navigate back to the Task Center — caller handles that.
    """
    print(f"   🚀 Task {task_num}/{target}")
    ctx = await get_active_context(page)

    # Step 1 — Find a task
    if not await safe_click(
        ctx,
        ["text=Shaka gahunda", 'button:has-text("Shaka gahunda")'],
        timeout=20000,
    ):
        print("      ❌ 'Shaka gahunda' not found.")
        return False

    await wait_idle(page, 8000)

    # Step 2 — Confirm
    await safe_click(ctx, ["text=Nibyo", "button:has-text('Nibyo')"], timeout=7000)
    await asyncio.sleep(random.uniform(1.5, 2.5))

    # Step 3 — Submit
    if not await safe_click(
        ctx,
        ["text=Tanga icyifuzo", "text=Tanga inshingano", ".button-fill"],
        timeout=15000,
    ):
        print("      ❌ Submit button not found.")
        return False

    # Step 4 — Wait for 100 %
    print("      ⏳ Waiting for 100%…")
    reached = False
    for attempt in range(6):
        try:
            await ctx.wait_for_function(
                "() => document.body.innerText.includes('100%')",
                timeout=20000,
            )
            reached = True
            break
        except Exception:
            print(f"      …waiting (attempt {attempt + 1}/6)…")
            await asyncio.sleep(4)

    if not reached:
        print("      ⚠️ 100% not reached — aborting this task.")
        return False

    # Step 5 — Final confirm
    await asyncio.sleep(1)
    await safe_click(ctx, ["text=Tanga inshingano", ".button-fill"], timeout=10000)
    print(f"      ✅ Task {task_num} complete.")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# PER-ACCOUNT RUN
# ─────────────────────────────────────────────────────────────────────────────

async def run_account(
    browser, username: str, password: str, target_tasks: int
) -> dict:
    """
    Open a FRESH browser context for this account (own cookies, no bleed).
    Login, read progress, run up to RUN_LIMIT tasks, close context.
    Returns {"completed": int, "failed": int, "skipped": bool}.
    """
    stats = {"completed": 0, "failed": 0, "skipped": False}

    # Fresh context = fresh session; TMP cannot see any previous account's cookies
    context = await browser.new_context(
        viewport={"width": 412, "height": 915},
        is_mobile=True,
        user_agent=(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/116.0.0.0 Mobile Safari/537.36"
        ),
    )
    page = await context.new_page()

    try:
        # ── LOGIN ─────────────────────────────────────────────────────────────
        print(f"🔐 Logging in as {username}…")
        await page.goto(TMP_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        login_ctx = await get_login_context(page)
        await login_ctx.locator("input").nth(0).fill(username)
        await login_ctx.locator("input").nth(1).fill(password)
        await safe_click(login_ctx, ["button", ".login-button"])
        print("   👆 Credentials submitted — waiting for dashboard…")

        await wait_idle(page, 30000)
        await asyncio.sleep(4)  # SPA boot buffer

        # Verify login
        if not await is_logged_in(login_ctx):
            await asyncio.sleep(5)
            if not await is_logged_in(login_ctx):
                print(f"   ❌ LOGIN FAILED for {username} — check the secret password.")
                await page.screenshot(path=f"login_failed_{username}.png")
                return stats

        print("   ✅ Logged in successfully.")
        await close_any_popup(login_ctx)

        # ── NAVIGATE TO TASK CENTER ───────────────────────────────────────────
        print("   🧭 Opening Task Center…")
        nav_ok = await safe_click(
            login_ctx,
            ["text=Inshingano", ".bottom-nav > a:nth-child(2)"],
            timeout=15000,
        )
        if not nav_ok:
            await page.goto(TASK_CENTER_URL, wait_until="networkidle", timeout=30000)
        await wait_idle(page)
        await asyncio.sleep(3)

        # ── READ CURRENT PROGRESS ─────────────────────────────────────────────
        ctx = await get_active_context(page)
        body = await ctx.inner_text("body")
        match = re.search(rf'(\d+)\s*/\s*{target_tasks}', body)
        done = int(match.group(1)) if match else 0
        print(f"   📊 Progress: {done}/{target_tasks}")

        # Already finished? Skip entirely (works for 5/8-task accounts in run 2)
        if done >= target_tasks:
            print(f"   🎉 All {target_tasks} tasks already done — skipping {username}.")
            stats["skipped"] = True
            return stats

        # ── CALCULATE THIS RUN'S CEILING ──────────────────────────────────────
        # e.g. done=0, RUN_LIMIT=15, target=30  → ceiling=15  (tasks 1–15)
        # e.g. done=15, RUN_LIMIT=15, target=30 → ceiling=30  (tasks 16–30)
        # e.g. done=0, RUN_LIMIT=15, target=5   → ceiling=5   (tasks 1–5, all done)
        ceiling = min(target_tasks, done + RUN_LIMIT)
        print(f"   📋 This run: tasks {done + 1} → {ceiling}  (cap={RUN_LIMIT})")

        # ── TASK LOOP ─────────────────────────────────────────────────────────
        for i in range(done + 1, ceiling + 1):
            task_ok = False
            for attempt in range(MAX_TASK_RETRIES):
                try:
                    task_ok = await run_single_task(page, i, target_tasks)
                    if task_ok:
                        stats["completed"] += 1
                        break
                except Exception as e:
                    print(f"      ⚠️ Task {i} raised exception (attempt {attempt + 1}): {e}")
                    await page.screenshot(
                        path=f"err_{username}_task{i}_attempt{attempt + 1}.png"
                    )

                # Recover before next attempt
                if attempt < MAX_TASK_RETRIES - 1:
                    print("      ↩️ Recovering — going back to Task Center…")
                    await go_to_task_center(page)
                    await asyncio.sleep(3)

            if not task_ok:
                stats["failed"] += 1
                print(f"      ⛔ Task {i} failed after {MAX_TASK_RETRIES} attempts.")

            # Always return to Task Center before the next task
            await go_to_task_center(page)
            await asyncio.sleep(2)

    except Exception as e:
        print(f"❌ Unhandled error for account {username}: {e}")
        await page.screenshot(path=f"error_{username}.png")

    finally:
        # Close context — TMP session is now completely gone before next account
        await context.close()

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    cleanup_old_screenshots()

    # Parse TMP_ACCOUNTS: "phone1:pass1:tasks1,phone2:pass2:tasks2,..."
    accounts = []
    for entry in ACCOUNTS_RAW.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            try:
                accounts.append((parts[0], parts[1], int(parts[2])))
            except ValueError:
                print(f"⚠️ Bad task count in entry {entry!r} — skipping.")
        else:
            print(f"⚠️ Malformed entry {entry!r} — expected phone:pass:tasks — skipping.")

    print(f"📋 {len(accounts)} account(s) found.  RUN_LIMIT = {RUN_LIMIT}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        total_completed = 0
        total_failed    = 0
        total_skipped   = 0

        for idx, (username, password, target_tasks) in enumerate(accounts, 1):
            print(f"\n{'─' * 55}")
            print(f"[{idx}/{len(accounts)}]  {username}  |  Target: {target_tasks} tasks")
            print(f"{'─' * 55}")

            stats = await run_account(browser, username, password, target_tasks)

            total_completed += stats["completed"]
            total_failed    += stats["failed"]
            if stats["skipped"]:
                total_skipped += 1

            # Buffer between accounts — avoids rate-limiting / detection
            if idx < len(accounts):
                await asyncio.sleep(6)

        await browser.close()

    # ── FINAL SUMMARY ─────────────────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print("🏁 Run complete!")
    print(f"   ✅ Tasks completed  : {total_completed}")
    print(f"   ❌ Tasks failed     : {total_failed}")
    print(f"   ⏭️  Accounts skipped : {total_skipped} (already done or login failed)")
    print(f"{'═' * 55}")


if __name__ == "__main__":
    asyncio.run(main())
