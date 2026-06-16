import asyncio
import os
import random
import re
import glob
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()

ACCOUNTS_DATA = os.getenv("TMP_ACCOUNTS")
if not ACCOUNTS_DATA:
    raise ValueError("TMP_ACCOUNTS must be set in GitHub Secrets")

TMP_LOGIN_URL = "https://tmpjob.net/login"
TASK_CENTER_URL = "https://tmpjob.net/index/rotary/index.html"

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
        ["text=Gufunga", "button:has-text('Gufunga')"],                              # Type 1: Announcement modal (Itangazo)
        ["button:has-text('X')", ".close", "[aria-label='Close']", ".modal-close"]  # Type 2: Generic popup
    ]:
        closed = await safe_click(main, popup_selectors, timeout=3000)
        if closed:
            print("🔒 Popup closed.")
            break
    else:
        print("ℹ️ No popup detected, proceeding.")

async def run_bot_for_user(page, username, password, target_tasks):
    try:
        print(f"🔐 Opening TMP login for {username}...")
        await page.goto(TMP_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        main = page

        try:
            await page.wait_for_selector("input", timeout=10000)
        except:
            iframe_element = await page.wait_for_selector("iframe", timeout=15000)
            main = await iframe_element.content_frame()

        await main.locator("input").nth(0).fill(username)
        await main.locator("input").nth(1).fill(password)
        await safe_click(main, ["button", ".login-button"])
        print(f"👆 Login submitted for {username}.")

        print("⏳ Waiting for successful login...")
        await asyncio.sleep(10)
        if not await is_logged_in(main):
            await asyncio.sleep(5)
            if not await is_logged_in(main):
                print(f"❌ LOGIN FAILED for {username} — Check password in secrets")
                await page.screenshot(path=f"login_failed_{username}.png")
                return False

        print("✅ Login successful!")

        # Close any post-login popup (checks both known types)
        await close_any_popup(main)

        print("🧭 Clicking 'Inshingano' nav item...")
        nav_clicked = await safe_click(main, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=15000)
        if not nav_clicked:
            await page.goto(TASK_CENTER_URL, wait_until="networkidle")
            await asyncio.sleep(5)

        # Ensure right context
        try:
            await main.wait_for_selector(":has-text('Iterambere ry\\'imirimo')", timeout=10000)
        except:
            try:
                iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                main = await iframe_el.content_frame()
                print(" 🔄 Switched to iframe.")
            except:
                pass

        print("📊 Checking progress...")
        progress_text = await main.inner_text("body")
        match = re.search(r'(\d+)\s*/\s*15', progress_text)
        done = int(match.group(1)) if match else 0
        print(f"📊 Progress: {done}/{target_tasks} requested.")

        if done >= target_tasks:
            print(f"🎉 All requested tasks completed for {username}!")
            return True

        for i in range(done + 1, target_tasks + 1):
            print(f"\n🚀 Task {i}/{target_tasks}")

            # Context check
            try:
                await main.wait_for_selector("text=Shaka gahunda", timeout=5000)
            except:
                try:
                    iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                    main = await iframe_el.content_frame()
                    print(" 🔄 Re-switched to iframe.")
                except:
                    main = page

            if not await safe_click(main, ["text=Shaka gahunda", 'button:has-text("Shaka gahunda")'], timeout=20000):
                print("⚠️ Shaka gahunda not found. Reloading Task Center...")
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(4)
                continue

            await asyncio.sleep(random.uniform(3, 5))
            await safe_click(main, ["text=Nibyo", "button:has-text('Nibyo')"], timeout=7000)
            await asyncio.sleep(2)

            if not await safe_click(main, ["text=Tanga icyifuzo", "text=Tanga inshingano", ".button-fill"], timeout=15000):
                print("❌ Could not find submit button. Skipping task.")
                continue

            print("⏳ Waiting for 100%...")
            success_100 = False
            for attempt in range(5):
                try:
                    await main.wait_for_function("() => document.body.innerText.includes('100%')", timeout=25000)
                    success_100 = True
                    break
                except:
                    print(f"...waiting (attempt {attempt+1})...")
                    await asyncio.sleep(4)

            if not success_100:
                print("⚠️ 100% not reached. Taking screenshot and moving on.")
                await page.screenshot(path=f"debug_100_{username}_task{i}.png")
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                continue

            await asyncio.sleep(2)
            await safe_click(main, ["text=Tanga inshingano", ".button-fill"], timeout=10000)
            print(f"✅ Task {i} completed.")

            print("🔁 Returning to Task Center...")
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

        print(f"\n🏁 Done with {username}!")
        return True

    except Exception as e:
        print(f"❌ Error with account {username}: {e}")
        await page.screenshot(path=f"error_{username}.png")
        return False

async def main():
    cleanup_old_screenshots()
    accounts = []
    for acc in ACCOUNTS_DATA.split(","):
        parts = acc.split(":")
        if len(parts) == 3:
            accounts.append((parts[0], parts[1], int(parts[2])))

    print(f"Found {len(accounts)} accounts to process.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        for username, password, target_tasks in accounts:
            print(f"\n--- Starting run for {username} (Target: {target_tasks} tasks) ---")
            context = await browser.new_context(
                viewport={"width": 412, "height": 915},
                is_mobile=True,
                user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
            )
            page = await context.new_page()
            await run_bot_for_user(page, username, password, target_tasks)
            await context.close()
            await asyncio.sleep(6)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())import asyncio
import os
import random
import re
import glob
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()

ACCOUNTS_DATA = os.getenv("TMP_ACCOUNTS")
if not ACCOUNTS_DATA:
    raise ValueError("TMP_ACCOUNTS must be set in GitHub Secrets")

TMP_LOGIN_URL = "https://tmpjob.net/login"
TASK_CENTER_URL = "https://tmpjob.net/index/rotary/index.html"

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
            await asyncio.sleep(random.uniform(0.5, 1.0))  # small human-like delay
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

async def run_bot_for_user(page, username, password, target_tasks):
    try:
        print(f"🔐 Opening TMP login for {username}...")
        await page.goto(TMP_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        main = page

        try:
            await page.wait_for_selector("input", timeout=10000)
        except:
            iframe_element = await page.wait_for_selector("iframe", timeout=15000)
            main = await iframe_element.content_frame()

        await main.locator("input").nth(0).fill(username)
        await main.locator("input").nth(1).fill(password)
        await safe_click(main, ["button", ".login-button"])
        print(f"👆 Login submitted for {username}.")

        print("⏳ Waiting for successful login...")
        await asyncio.sleep(10)  # reduced
        if not await is_logged_in(main):
            await asyncio.sleep(5)
            if not await is_logged_in(main):
                print(f"❌ LOGIN FAILED for {username} — Check password in secrets")
                await page.screenshot(path=f"login_failed_{username}.png")
                return False

        print("✅ Login successful!")

        # Close any post-login popup
        await safe_click(main, ["button:has-text('X')", ".close", "[aria-label='Close']", ".modal-close"], timeout=3000)

        print("🧭 Clicking 'Inshingano' nav item...")
        nav_clicked = await safe_click(main, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=15000)
        if not nav_clicked:
            await page.goto(TASK_CENTER_URL, wait_until="networkidle")
            await asyncio.sleep(5)

        # Ensure right context
        try:
            await main.wait_for_selector(":has-text('Iterambere ry\\'imirimo')", timeout=10000)
        except:
            try:
                iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                main = await iframe_el.content_frame()
                print(" 🔄 Switched to iframe.")
            except:
                pass

        # ================== PROGRESS CHECK (DYNAMIC TARGET) ==================
        print("📊 Checking progress...")
        progress_text = await main.inner_text("body")
        # The FIX: use actual target_tasks instead of hardcoded 15
        match = re.search(rf'(\d+)\s*/\s*{target_tasks}', progress_text)
        done = int(match.group(1)) if match else 0
        print(f"📊 Progress: {done}/{target_tasks} requested.")

        if done >= target_tasks:
            print(f"🎉 All requested tasks completed for {username}!")
            return True

        for i in range(done + 1, target_tasks + 1):
            print(f"\n🚀 Task {i}/{target_tasks}")

            # Context check
            try:
                await main.wait_for_selector("text=Shaka gahunda", timeout=5000)
            except:
                try:
                    iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                    main = await iframe_el.content_frame()
                    print(" 🔄 Re-switched to iframe.")
                except:
                    main = page

            if not await safe_click(main, ["text=Shaka gahunda", 'button:has-text("Shaka gahunda")'], timeout=20000):
                print("⚠️ Shaka gahunda not found. Reloading Task Center...")
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(4)
                continue

            await asyncio.sleep(random.uniform(3, 5))
            await safe_click(main, ["text=Nibyo", "button:has-text('Nibyo')"], timeout=7000)
            await asyncio.sleep(2)

            if not await safe_click(main, ["text=Tanga icyifuzo", "text=Tanga inshingano", ".button-fill"], timeout=15000):
                print("❌ Could not find submit button. Skipping task.")
                continue

            # Wait for 100%
            print("⏳ Waiting for 100%...")
            success_100 = False
            for attempt in range(5):
                try:
                    await main.wait_for_function("() => document.body.innerText.includes('100%')", timeout=25000)
                    success_100 = True
                    break
                except:
                    print(f"...waiting (attempt {attempt+1})...")
                    await asyncio.sleep(4)

            if not success_100:
                print("⚠️ 100% not reached. Taking screenshot and moving on.")
                await page.screenshot(path=f"debug_100_{username}_task{i}.png")
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                continue

            await asyncio.sleep(2)
            await safe_click(main, ["text=Tanga inshingano", ".button-fill"], timeout=10000)
            print(f"✅ Task {i} completed.")

            print("🔁 Returning to Task Center...")
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

        print(f"\n🏁 Done with {username}!")
        return True

    except Exception as e:
        print(f"❌ Error with account {username}: {e}")
        await page.screenshot(path=f"error_{username}.png")
        return False

async def main():
    cleanup_old_screenshots()
    accounts = []
    for acc in ACCOUNTS_DATA.split(","):
        parts = acc.split(":")
        if len(parts) == 3:
            accounts.append((parts[0], parts[1], int(parts[2])))

    print(f"Found {len(accounts)} accounts to process.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        for username, password, target_tasks in accounts:
            print(f"\n--- Starting run for {username} (Target: {target_tasks} tasks) ---")
            context = await browser.new_context(
                viewport={"width": 412, "height": 915},
                is_mobile=True,
                user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
            )
            page = await context.new_page()
            await run_bot_for_user(page, username, password, target_tasks)
            await context.close()
            await asyncio.sleep(6)  # shorter cooldown

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
