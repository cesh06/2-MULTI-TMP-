"""
TMP Multi-Account Bot — Final Production Version
=================================================
Strategy: ONE run per night. All accounts run IN PARALLEL.

• 30-task accounts → all 30 tasks done in ~30 min (one run, one night).
• 5/8-task accounts → finish in ~5–8 min, context closes, done.
• No two-run split. No queueing. No session overlap.

Session safety:
  • Each account gets its own isolated browser context (own cookies, zero bleed).
  • Logins are staggered (5–15 s apart) so TMP never sees 4 hits at the exact same second.
  • asyncio.gather() collects all results even if one account crashes.

Secret format (TMP_ACCOUNTS):
  phone1:pass1:tasks1,phone2:pass2:tasks2,...
  Example:
  794968772:Password1:30,786763840:Password2:30,791377506:Password3:8,732749495:Password4:5
"""

import asyncio
import os
import random
import re
import glob
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()

# ── Environment ───────────────────────────────────────────────────────────────
ACCOUNTS_RAW = os.getenv("TMP_ACCOUNTS")
if not ACCOUNTS_RAW:
    raise ValueError(
        "TMP_ACCOUNTS is not set.\n"
        "Format: phone:pass:tasks,phone:pass:tasks,..."
    )

RUN_LIMIT = int(os.getenv("TMP_RUN_LIMIT", "30"))

TMP_LOGIN_URL   = "https://tmpjob.net/login"
TASK_CENTER_URL = "https://tmpjob.net/index/rotary/index.html"

MAX_CTX_RETRIES  = 3
MAX_TASK_RETRIES = 2

STAGGER_MIN = 5
STAGGER_MAX = 15


def cleanup_old_screenshots() -> None:
    removed = 0
    for f in glob.glob("*.png"):
        try:
            os.remove(f)
            removed += 1
        except OSError:
            pass
    print(f"🧹 Removed {removed} old screenshot(s).\n")


async def get_active_context(page, probe="text=Shaka gahunda"):
    for attempt in range(MAX_CTX_RETRIES):
        try:
            await page.wait_for_selector(probe, timeout=3000)
            return page
        except PlaywrightTimeout:
            pass
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                await frame.wait_for_selector(probe, timeout=2000)
                return frame
            except PlaywrightTimeout:
                continue
        if attempt < MAX_CTX_RETRIES - 1:
            print(f"      🔄 Context probe attempt {attempt + 1}/{MAX_CTX_RETRIES}…")
            await asyncio.sleep(2)
    frames = page.frames
    print("      ⚠️ Probe not found — using fallback context.")
    return frames[1] if len(frames) > 1 else page


async def get_login_context(page):
    try:
        await page.wait_for_selector("input", timeout=10000)
        return page
    except PlaywrightTimeout:
        pass
    iframe_el = await page.wait_for_selector("iframe", timeout=15000)
    return await iframe_el.content_frame()


async def safe_click(ctx, selectors, timeout=8000) -> bool:
    if isinstance(selectors, str):
        selectors = [selectors]
    for sel in selectors:
        try:
            await ctx.wait_for_selector(sel, state="visible", timeout=timeout)
            await ctx.click(sel, force=True)
            await asyncio.sleep(random.uniform(0.4, 0.9))
            return True
        except PlaywrightTimeout:
            continue
    return False


async def wait_idle(page, timeout=12000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeout:
        pass


async def is_logged_in(ctx) -> bool:
    try:
        text = await ctx.inner_text("body")
        phrases = [
            "Iterambere ry'imirimo",
            "Shaka gahunda",
            "Shaka komisiyo uyu munsi",
            "Kubitsa",
            "Impano zidasanzwe",
            "Genda kwakira igihembo",
        ]
        return any(p in text for p in phrases)
    except Exception:
        return False


async def close_any_popup(ctx) -> None:
    popup_groups = [
        ["text=Gufunga", "button:has-text('Gufunga')"],
        ["button:has-text('X')", ".close", "[aria-label='Close']",
         ".modal-close", ".popup-close"],
    ]
    for selectors in popup_groups:
        if await safe_click(ctx, selectors, timeout=3000):
            print("      🔒 Pop-up dismissed.")
            await asyncio.sleep(1)
            return
    print("      ℹ️ No pop-up found.")


async def go_to_task_center(page) -> None:
    clicked = await safe_click(
        page,
        ["text=Inshingano", ".bottom-nav > a:nth-child(2)"],
        timeout=8000,
    )
    if not clicked:
        print("      ⚠️ Nav button not found — loading Task Center URL.")
        await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded", timeout=30000)
    await wait_idle(page)


async def run_single_task(page, task_num: int, target: int, tag: str) -> bool:
    print(f"{tag} 🚀 Task {task_num}/{target}")
    ctx = await get_active_context(page)

    if not await safe_click(
        ctx,
        ["text=Shaka gahunda", 'button:has-text("Shaka gahunda")'],
        timeout=20000,
    ):
        print(f"{tag}    ❌ 'Shaka gahunda' not found.")
        return False

    await wait_idle(page, 8000)

    confirmed = await safe_click(
        ctx, ["text=Nibyo", "button:has-text('Nibyo')"], timeout=7000
    )
    if not confirmed:
        print(f"{tag}    ⚠️ Confirmation button not found — continuing anyway.")
    await asyncio.sleep(random.uniform(1.5, 2.5))

    if not await safe_click(
        ctx,
        ["text=Tanga icyifuzo", "text=Tanga inshingano", ".button-fill"],
        timeout=15000,
    ):
        print(f"{tag}    ❌ Submit button not found.")
        return False

    print(f"{tag}    ⏳ Waiting for 100%…")
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
            print(f"{tag}    …waiting (attempt {attempt + 1}/6)…")
            await asyncio.sleep(4)

    if not reached:
        print(f"{tag}    ⚠️ 100% not reached — aborting task.")
        return False

    await asyncio.sleep(1)
    await safe_click(ctx, ["text=Tanga inshingano", ".button-fill"], timeout=10000)
    print(f"{tag}    ✅ Task {task_num} done.")
    return True


async def run_account(
    browser,
    username: str,
    password: str,
    target_tasks: int,
    stagger_delay: float = 0.0,
) -> dict:
    tag = f"[{username}]"
    stats = {"completed": 0, "failed": 0, "skipped": False}

    if stagger_delay > 0:
        print(f"{tag} ⏱️ Waiting {stagger_delay:.0f}s before login (stagger)…")
        await asyncio.sleep(stagger_delay)

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
        print(f"\n{'─'*55}\n{tag} Target: {target_tasks} tasks\n{'─'*55}")
        await page.goto(TMP_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        login_ctx = await get_login_context(page)
        await login_ctx.locator("input").nth(0).fill(username)
        await login_ctx.locator("input").nth(1).fill(password)
        await safe_click(login_ctx, ["button", ".login-button"])
        print(f"{tag} 👆 Credentials submitted — waiting for dashboard…")

        await wait_idle(page, 30000)
        await asyncio.sleep(4)

        if not await is_logged_in(login_ctx):
            await asyncio.sleep(5)
            if not await is_logged_in(login_ctx):
                print(f"{tag} ❌ LOGIN FAILED — check the password in TMP_ACCOUNTS secret.")
                await page.screenshot(path=f"login_failed_{username}.png")
                return stats

        print(f"{tag} ✅ Logged in successfully.")
        await close_any_popup(login_ctx)

        nav_ok = await safe_click(
            login_ctx,
            ["text=Inshingano", ".bottom-nav > a:nth-child(2)"],
            timeout=15000,
        )
        if not nav_ok:
            await page.goto(TASK_CENTER_URL, wait_until="networkidle", timeout=30000)
        await wait_idle(page)
        await asyncio.sleep(3)

        ctx = await get_active_context(page)
        body = await ctx.inner_text("body")
        match = re.search(rf'(\d+)\s*/\s*{target_tasks}', body)
        done = int(match.group(1)) if match else 0
        print(f"{tag} 📊 Progress: {done}/{target_tasks}")

        if done >= target_tasks:
            print(f"{tag} 🎉 All {target_tasks} tasks already complete — skipping.")
            stats["skipped"] = True
            return stats

        ceiling = min(target_tasks, done + RUN_LIMIT)
        print(f"{tag} 📋 Will complete tasks {done + 1} → {ceiling} (cap={RUN_LIMIT})")

        for i in range(done + 1, ceiling + 1):
            task_ok = False
            for attempt in range(MAX_TASK_RETRIES):
                try:
                    task_ok = await run_single_task(page, i, target_tasks, tag)
                    if task_ok:
                        stats["completed"] += 1
                        break
                except Exception as e:
                    print(f"{tag}    ⚠️ Task {i} raised exception (attempt {attempt + 1}): {e}")
                    await page.screenshot(path=f"err_{username}_t{i}_a{attempt + 1}.png")

                if attempt < MAX_TASK_RETRIES - 1:
                    print(f"{tag}    ↩️ Recovering — going back to Task Center…")
                    await go_to_task_center(page)
                    await asyncio.sleep(3)

            if not task_ok:
                stats["failed"] += 1
                print(f"{tag}    ⛔ Task {i} failed after {MAX_TASK_RETRIES} attempts — skipping.")

            await go_to_task_center(page)
            await asyncio.sleep(2)

        print(f"{tag} 🏁 Finished!  ✅ {stats['completed']} done  ❌ {stats['failed']} failed")

    except Exception as e:
        print(f"{tag} ❌ Unhandled crash: {e}")
        await page.screenshot(path=f"crash_{username}.png")

    finally:
        await context.close()

    return stats


async def main() -> None:
    cleanup_old_screenshots()

    accounts: list[tuple[str, str, int]] = []
    for entry in ACCOUNTS_RAW.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            try:
                accounts.append((parts[0], parts[1], int(parts[2])))
            except ValueError:
                print(f"⚠️ Bad task count in {entry!r} — skipping.")
        else:
            print(f"⚠️ Malformed entry {entry!r} (expected phone:pass:tasks) — skipping.")

    if not accounts:
        print("❌ No valid accounts found. Exiting.")
        return

    print(f"📋 {len(accounts)} account(s) loaded.  RUN_LIMIT = {RUN_LIMIT}")
    print(f"⚡ All accounts will run IN PARALLEL with staggered logins.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        coroutines = []
        cumulative_delay = 0.0
        for username, password, target_tasks in accounts:
            coroutines.append(
                run_account(browser, username, password, target_tasks, cumulative_delay)
            )
            cumulative_delay += random.uniform(STAGGER_MIN, STAGGER_MAX)

        results = await asyncio.gather(*coroutines, return_exceptions=True)

        await browser.close()

    total_completed = 0
    total_failed    = 0
    total_skipped   = 0

    for idx, result in enumerate(results):
        uname = accounts[idx][0]
        if isinstance(result, Exception):
            print(f"❌ Account {uname} crashed: {result}")
            total_failed += 1
        elif isinstance(result, dict):
            total_completed += result.get("completed", 0)
            total_failed    += result.get("failed", 0)
            if result.get("skipped"):
                total_skipped += 1

    print(f"\n{'═'*55}")
    print("🏁 All accounts finished!")
    print(f"   ✅ Tasks completed  : {total_completed}")
    print(f"   ❌ Tasks failed     : {total_failed}")
    print(f"   ⏭️  Accounts skipped : {total_skipped}  (already done or login failed)")
    print(f"{'═'*55}")


if __name__ == "__main__":
    asyncio.run(main())
