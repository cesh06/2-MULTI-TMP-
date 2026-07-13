import asyncio
import os
import random
import re
import glob
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()

# ── Multi‑account secret ────────────────────────────────────────────────────
ACCOUNTS_DATA = os.getenv("TMP_ACCOUNTS")
if not ACCOUNTS_DATA:
    raise ValueError("TMP_ACCOUNTS must be set.\nFormat: phone:pass:tasks,phone:pass:tasks,...")

TMP_LOGIN_URL   = "https://tmpjob.net/login"
TASK_CENTER_URL = "https://tmpjob.net/index/rotary/index.html"

# Stagger logins (seconds) – account 0 starts immediately, each next waits 5‑15 s extra.
STAGGER_MIN = 5
STAGGER_MAX = 15

# ====================== STORAGE CLEANUP ======================
def cleanup_old_screenshots():
    for file in glob.glob("*.png"):
        try:
            os.remove(file)
            print(f"🧹 Cleaned old screenshot: {file}")
        except:
            pass
    print("✅ Storage cleanup completed")
# ============================================================

async def safe_click(page, selectors, timeout=8000):
    if isinstance(selectors, str):
        selectors = [selectors]
    for selector in selectors:
        try:
            await page.wait_for_selector(selector, state="visible", timeout=timeout)
            await page.click(selector, force=True)
            await asyncio.sleep(random.uniform(0.5, 1.0))
            return True
        except PlaywrightTimeout:
            continue
    return False

async def is_logged_in(page_or_frame):
    try:
        text = await page_or_frame.inner_text("body")
        phrases = ["Iterambere ry'imirimo", "Shaka gahunda", "Shaka komisiyo uyu munsi",
                   "Kubitsa", "Gukuramo", "Impano zidasanzwe", "Amahirwe Roulette", "Genda kwakira igihembo"]
        return any(phrase in text for phrase in phrases)
    except:
        return False

async def close_any_popup(main):
    for popup_selectors in [
        ["text=Gufunga", "button:has-text('Gufunga')"],
        ["button:has-text('X')", ".close", "[aria-label='Close']", ".modal-close"]
    ]:
        closed = await safe_click(main, popup_selectors, timeout=3000)
        if closed:
            print("   🔒 Popup closed.")
            break
    else:
        print("   ℹ️ No popup detected, proceeding.")

# ──────────────────────────── PER‑ACCOUNT WORKER ────────────────────────────
async def run_account_worker(phone, password, target_tasks, stagger_delay):
    """
    Exactly the same logic as the reliable single‑account bot,
    but wrapped to be called concurrently with its own context.
    """
    tag = f"[{phone}]"

    # Stagger login
    if stagger_delay > 0:
        print(f"{tag} ⏱️ Waiting {stagger_delay:.0f}s before login (stagger)…")
        await asyncio.sleep(stagger_delay)

    # ── Fresh isolated context ───────────────────────────────────────────
    context = await browser.new_context(
        viewport={"width": 412, "height": 915},
        is_mobile=True,
        user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
    )
    page = await context.new_page()

    try:
        print(f"{tag} ───────────────────────────────────────")
        print(f"{tag} Target: {target_tasks} tasks")
        print(f"{tag} ───────────────────────────────────────")

        # ── LOGIN ─────────────────────────────────────────────────────────
        print(f"{tag} 🔐 Opening TMP login…")
        await page.goto(TMP_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        main = page

        try:
            await page.wait_for_selector("input", timeout=10000)
        except:
            iframe_element = await page.wait_for_selector("iframe", timeout=15000)
            main = await iframe_element.content_frame()

        await main.locator("input").nth(0).fill(phone)
        await main.locator("input").nth(1).fill(password)
        await safe_click(main, ["button", ".login-button"])
        print(f"{tag} 👆 Login submitted.")

        print(f"{tag} ⏳ Waiting for successful login…")
        await asyncio.sleep(10)
        if not await is_logged_in(main):
            await asyncio.sleep(5)
            if not await is_logged_in(main):
                print(f"{tag} ❌ LOGIN FAILED — Check password in secrets")
                await page.screenshot(path=f"login_failed_{phone}.png")
                return

        print(f"{tag} ✅ Login successful!")
        await close_any_popup(main)

        # ── NAVIGATE TO TASK CENTER ───────────────────────────────────────
        print(f"{tag} 🧭 Clicking 'Inshingano' nav item…")
        nav_clicked = await safe_click(main, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=15000)
        if not nav_clicked:
            await page.goto(TASK_CENTER_URL, wait_until="networkidle")
            await asyncio.sleep(5)

        # Ensure we are in the right context (iframe or main)
        try:
            await main.wait_for_selector(":has-text('Iterambere ry\\'imirimo')", timeout=10000)
        except:
            try:
                iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                main = await iframe_el.content_frame()
                print("   🔄 Switched to iframe.")
            except:
                pass

        # ── PROGRESS CHECK ─────────────────────────────────────────────────
        print(f"{tag} 📊 Checking progress…")
        progress_text = await main.inner_text("body")
        match = re.search(rf'(\d+)\s*/\s*{target_tasks}', progress_text)
        done = int(match.group(1)) if match else 0
        print(f"{tag} 📊 Progress: {done}/{target_tasks}")

        if done >= target_tasks:
            print(f"{tag} 🎉 All tasks already completed — skipping.")
            return

        # ── TASK LOOP ─────────────────────────────────────────────────────
        for i in range(done + 1, target_tasks + 1):
            print(f"\n{tag} 🚀 Task {i}/{target_tasks}")

            # Re‑detect context before each task
            try:
                await main.wait_for_selector("text=Shaka gahunda", timeout=5000)
            except:
                try:
                    iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                    main = await iframe_el.content_frame()
                    print("   🔄 Re‑switched to iframe.")
                except:
                    main = page

            if not await safe_click(main, ["text=Shaka gahunda", 'button:has-text("Shaka gahunda")'], timeout=20000):
                print(f"{tag} ⚠️ Shaka gahunda not found. Reloading Task Center…")
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(4)
                continue

            await asyncio.sleep(random.uniform(3, 5))
            await safe_click(main, ["text=Nibyo", "button:has-text('Nibyo')"], timeout=7000)
            await asyncio.sleep(2)

            if not await safe_click(main, ["text=Tanga icyifuzo", "text=Tanga inshingano", ".button-fill"], timeout=15000):
                print(f"{tag} ❌ Could not find submit button. Skipping task.")
                continue

            print(f"{tag} ⏳ Waiting for 100%…")
            success_100 = False
            for attempt in range(5):
                try:
                    await main.wait_for_function("() => document.body.innerText.includes('100%')", timeout=25000)
                    success_100 = True
                    break
                except:
                    print(f"...waiting (attempt {attempt+1})…")
                    await asyncio.sleep(4)

            if not success_100:
                print(f"{tag} ⚠️ 100% not reached. Moving on.")
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                continue

            await asyncio.sleep(2)
            await safe_click(main, ["text=Tanga inshingano", ".button-fill"], timeout=10000)
            print(f"{tag} ✅ Task {i} completed.")

            print(f"{tag} 🔁 Returning to Task Center…")
            clicked_nav = await safe_click(page, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=8000)
            if not clicked_nav:
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(4)
            main = page
            try:
                await page.wait_for_selector("text=Shaka gahunda", timeout=5000)
            except:
                try:
                    iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                    main = await iframe_el.content_frame()
                except:
                    pass

        print(f"{tag} 🏁 All tasks done!")

    except Exception as e:
        print(f"{tag} ❌ Error: {e}")
        await page.screenshot(path=f"error_{phone}.png")
    finally:
        await context.close()


# ──────────────────────────── MAIN ────────────────────────────
async def main():
    cleanup_old_screenshots()

    # Parse TMP_ACCOUNTS: "phone:pass:tasks,phone:pass:tasks,..."
    accounts = []
    for entry in ACCOUNTS_DATA.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            try:
                accounts.append((parts[0], parts[1], int(parts[2])))
            except ValueError:
                print(f"⚠️ Bad task count in {entry!r} — skipping.")
        else:
            print(f"⚠️ Malformed entry {entry!r} — expected phone:pass:tasks — skipping.")

    if not accounts:
        print("❌ No valid accounts. Exiting.")
        return

    print(f"📋 {len(accounts)} account(s) loaded.")
    print(f"⚡ All accounts will run IN PARALLEL with staggered logins.\n")

    global browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        # Build coroutines with cumulative stagger delay
        coros = []
        cumulative_delay = 0.0
        for phone, password, target in accounts:
            coros.append(run_account_worker(phone, password, target, cumulative_delay))
            cumulative_delay += random.uniform(STAGGER_MIN, STAGGER_MAX)

        # Run all concurrently – one account crash doesn't stop the others
        await asyncio.gather(*coros, return_exceptions=True)

        await browser.close()

    print(f"\n{'═'*55}")
    print("🏁 All accounts finished!")
    print(f"{'═'*55}")


if __name__ == "__main__":
    asyncio.run(main())
