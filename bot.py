import asyncio
import os
import random
import re
import glob
import time
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()

# ── Multi-account secret ─────────────────────────────────────────────────────
ACCOUNTS_DATA = os.getenv("TMP_ACCOUNTS")
if not ACCOUNTS_DATA:
    raise ValueError("TMP_ACCOUNTS must be set.\nFormat: phone:pass:tasks,phone:pass:tasks,...")

TMP_LOGIN_URL   = "https://tmpjob.net/login"
TASK_CENTER_URL = "https://tmpjob.net/index/rotary/index.html"

STAGGER_MIN = 5
STAGGER_MAX = 15

# ── ANSI colors (GitHub Actions supports these) ───────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
BLUE   = "\033[34m"
CYAN   = "\033[36m"
RED    = "\033[31m"
WHITE  = "\033[37m"
GRAY   = "\033[90m"

def ts():
    """Compact HH:MM:SS timestamp for every log line."""
    return f"{GRAY}[{datetime.now().strftime('%H:%M:%S')}]{RESET}"

def tag_color(phone):
    """Rotate a distinct color per account so interleaved lines are scannable."""
    colors = [CYAN, YELLOW, "\033[35m", "\033[32m", "\033[36m"]
    idx = hash(phone) % len(colors)
    return colors[idx]

def log(phone, icon, msg, color=WHITE):
    tc = tag_color(phone)
    print(f"{ts()} {tc}{BOLD}[{phone}]{RESET} {icon}  {color}{msg}{RESET}", flush=True)

# ── Per-account result tracking ───────────────────────────────────────────────
results = {}   # phone -> {"done": int, "target": int, "status": str, "elapsed": float}

# ── GitHub Actions group helpers ──────────────────────────────────────────────
def gh_group(title):
    print(f"\033[0m::group::{title}", flush=True)

def gh_endgroup():
    print("::endgroup::", flush=True)

def gh_notice(msg):
    print(f"::notice ::{msg}", flush=True)

def gh_warning(msg):
    print(f"::warning ::{msg}", flush=True)

def gh_error(msg):
    print(f"::error ::{msg}", flush=True)

# ====================== STORAGE CLEANUP ======================
def cleanup_old_screenshots():
    removed = []
    for file in glob.glob("*.png"):
        try:
            os.remove(file)
            removed.append(file)
        except:
            pass
    if removed:
        print(f"{ts()} {YELLOW}🧹  Cleaned {len(removed)} old screenshot(s){RESET}", flush=True)
    print(f"{ts()} {GREEN}✔   Storage clean{RESET}", flush=True)
# =============================================================

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

async def close_any_popup(main, phone):
    for popup_selectors in [
        ["text=Gufunga", "button:has-text('Gufunga')"],
        ["button:has-text('X')", ".close", "[aria-label='Close']", ".modal-close"]
    ]:
        closed = await safe_click(main, popup_selectors, timeout=3000)
        if closed:
            log(phone, "🔒", "Popup dismissed", GRAY)
            return
    log(phone, "·", "No popup", GRAY)

# ──────────────────────────── PER-ACCOUNT WORKER ────────────────────────────
async def run_account_worker(phone, password, target_tasks, stagger_delay):
    tag  = phone
    t0   = time.time()
    done = 0

    # ── Register in results table ────────────────────────────────────────────
    results[phone] = {"done": 0, "target": target_tasks, "status": "waiting", "elapsed": 0.0}

    if stagger_delay > 0:
        log(phone, "⏳", f"Stagger {stagger_delay:.0f}s …", GRAY)
        await asyncio.sleep(stagger_delay)

    gh_group(f"Account {phone}  (target: {target_tasks} tasks)")

    context = await browser.new_context(
        viewport={"width": 412, "height": 915},
        is_mobile=True,
        user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
    )
    page = await context.new_page()

    try:
        # ── LOGIN ─────────────────────────────────────────────────────────────
        results[phone]["status"] = "logging in"
        log(phone, "🔐", "Logging in …", BLUE)

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

        await asyncio.sleep(10)
        if not await is_logged_in(main):
            await asyncio.sleep(5)
            if not await is_logged_in(main):
                log(phone, "✖", "LOGIN FAILED — check secret", RED)
                results[phone]["status"] = "login failed"
                gh_error(f"{phone}: login failed — check TMP_ACCOUNTS secret")
                await page.screenshot(path=f"login_failed_{phone}.png")
                return

        log(phone, "✔", "Logged in", GREEN)
        results[phone]["status"] = "running"
        await close_any_popup(main, phone)

        # ── NAVIGATE TO TASK CENTER ───────────────────────────────────────────
        nav_clicked = await safe_click(
            main, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=15000
        )
        if not nav_clicked:
            await page.goto(TASK_CENTER_URL, wait_until="networkidle")
            await asyncio.sleep(5)

        try:
            await main.wait_for_selector(":has-text('Iterambere ry\\'imirimo')", timeout=10000)
        except:
            try:
                iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                main = await iframe_el.content_frame()
                log(phone, "↺", "Switched to iframe", GRAY)
            except:
                pass

        # ── PROGRESS CHECK ────────────────────────────────────────────────────
        progress_text = await main.inner_text("body")
        match = re.search(rf'(\d+)\s*/\s*{target_tasks}', progress_text)
        done = int(match.group(1)) if match else 0
        results[phone]["done"] = done

        bar_filled = int((done / target_tasks) * 20)
        bar = f"[{'█' * bar_filled}{'░' * (20 - bar_filled)}]"
        log(phone, "📊", f"Progress: {done}/{target_tasks}  {bar}", CYAN)

        if done >= target_tasks:
            log(phone, "🎉", "All tasks already done — skipping", GREEN)
            results[phone]["status"] = "already complete"
            return

        # ── TASK LOOP ─────────────────────────────────────────────────────────
        for i in range(done + 1, target_tasks + 1):
            pct    = int((i - 1) / target_tasks * 100)
            filled = int(pct / 5)
            mini   = f"[{'█' * filled}{'░' * (20 - filled)}] {pct:3d}%"
            log(phone, "▶", f"Task {i:>2}/{target_tasks}  {mini}", BLUE)

            try:
                await main.wait_for_selector("text=Shaka gahunda", timeout=5000)
            except:
                try:
                    iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                    main = await iframe_el.content_frame()
                    log(phone, "↺", "Re-switched to iframe", GRAY)
                except:
                    main = page

            if not await safe_click(
                main, ["text=Shaka gahunda", 'button:has-text("Shaka gahunda")'], timeout=20000
            ):
                log(phone, "⚠", "Shaka gahunda not found — reloading", YELLOW)
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(4)
                continue

            await asyncio.sleep(random.uniform(3, 5))
            await safe_click(main, ["text=Nibyo", "button:has-text('Nibyo')"], timeout=7000)
            await asyncio.sleep(2)

            if not await safe_click(
                main, ["text=Tanga icyifuzo", "text=Tanga inshingano", ".button-fill"], timeout=15000
            ):
                log(phone, "✖", f"Task {i} — submit button not found, skipping", RED)
                continue

            # Wait for 100 %
            success_100 = False
            for attempt in range(5):
                try:
                    await main.wait_for_function(
                        "() => document.body.innerText.includes('100%')", timeout=25000
                    )
                    success_100 = True
                    break
                except:
                    log(phone, "·", f"Waiting for 100% (attempt {attempt + 1}/5)", GRAY)
                    await asyncio.sleep(4)

            if not success_100:
                log(phone, "⚠", f"Task {i} — 100% not reached, moving on", YELLOW)
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                continue

            await asyncio.sleep(2)
            await safe_click(main, ["text=Tanga inshingano", ".button-fill"], timeout=10000)

            done = i
            results[phone]["done"] = done
            log(phone, "✔", f"Task {i}/{target_tasks} done", GREEN)

            # Return to Task Center
            clicked_nav = await safe_click(
                page, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=8000
            )
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

        results[phone]["status"] = "complete"
        log(phone, "🏁", f"All {target_tasks} tasks finished!", GREEN)
        gh_notice(f"{phone}: {target_tasks}/{target_tasks} tasks complete")

    except Exception as e:
        results[phone]["status"] = "error"
        log(phone, "✖", f"Unexpected error: {e}", RED)
        gh_error(f"{phone}: {e}")
        await page.screenshot(path=f"error_{phone}.png")
    finally:
        results[phone]["elapsed"] = time.time() - t0
        await context.close()
        gh_endgroup()


# ──────────────────────────── SUMMARY TABLE ──────────────────────────────────
def print_summary():
    SEP  = "─" * 62
    W    = 62

    print(f"\n{BOLD}{CYAN}{'═' * W}{RESET}", flush=True)
    print(f"{BOLD}{CYAN}  RUN SUMMARY{RESET}", flush=True)
    print(f"{BOLD}{CYAN}{'═' * W}{RESET}", flush=True)

    # Header
    print(
        f"  {BOLD}{'ACCOUNT':<16} {'PROGRESS':>12}  {'BAR':>22}  {'TIME':>6}{RESET}",
        flush=True
    )
    print(f"  {GRAY}{SEP}{RESET}", flush=True)

    total_done   = 0
    total_target = 0
    all_ok       = True

    for phone, r in results.items():
        done   = r["done"]
        target = r["target"]
        status = r["status"]
        secs   = r["elapsed"]

        total_done   += done
        total_target += target

        filled = int((done / target) * 20) if target else 0
        bar    = f"{'█' * filled}{'░' * (20 - filled)}"

        if status == "complete" or status == "already complete":
            s_color = GREEN
            icon    = "✔"
        elif status in ("login failed", "error"):
            s_color = RED
            icon    = "✖"
            all_ok  = False
        else:
            s_color = YELLOW
            icon    = "⚠"
            all_ok  = False

        mins = int(secs // 60)
        sec  = int(secs % 60)
        elapsed_str = f"{mins}m{sec:02d}s"

        print(
            f"  {tag_color(phone)}{BOLD}{phone:<16}{RESET}"
            f"  {s_color}{icon} {done:>3}/{target:<3}{RESET}"
            f"  {s_color}{bar}{RESET}"
            f"  {GRAY}{elapsed_str:>6}{RESET}",
            flush=True
        )

    print(f"  {GRAY}{SEP}{RESET}", flush=True)

    # Totals row
    pct = int(total_done / total_target * 100) if total_target else 0
    filled = int(pct / 5)
    total_bar = f"{'█' * filled}{'░' * (20 - filled)}"
    overall_color = GREEN if all_ok else YELLOW

    print(
        f"  {BOLD}{'TOTAL':<16}{RESET}"
        f"  {overall_color}{BOLD}{total_done:>3}/{total_target:<3}{RESET}"
        f"  {overall_color}{total_bar}{RESET}"
        f"  {GRAY}{pct:>5}%{RESET}",
        flush=True
    )
    print(f"{BOLD}{CYAN}{'═' * W}{RESET}\n", flush=True)

    if all_ok:
        gh_notice(f"All accounts complete — {total_done}/{total_target} tasks")
    else:
        failed = [p for p, r in results.items() if r["status"] in ("login failed", "error")]
        gh_warning(f"Some accounts had issues: {', '.join(failed)}")


# ──────────────────────────── MAIN ───────────────────────────────────────────
async def main():
    t_start = time.time()

    # ── Banner ────────────────────────────────────────────────────────────────
    W = 62
    print(f"\n{BOLD}{BLUE}{'═' * W}{RESET}", flush=True)
    print(f"{BOLD}{BLUE}  TMP BOT  ·  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}{RESET}", flush=True)
    print(f"{BOLD}{BLUE}{'═' * W}{RESET}\n", flush=True)

    cleanup_old_screenshots()

    # ── Parse accounts ────────────────────────────────────────────────────────
    accounts = []
    for entry in ACCOUNTS_DATA.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            try:
                accounts.append((parts[0], parts[1], int(parts[2])))
            except ValueError:
                print(f"{YELLOW}⚠  Bad task count in {entry!r} — skipping{RESET}", flush=True)
        else:
            print(f"{YELLOW}⚠  Malformed entry {entry!r} — expected phone:pass:tasks{RESET}", flush=True)

    if not accounts:
        print(f"{RED}✖  No valid accounts. Exiting.{RESET}", flush=True)
        return

    # ── Account overview table ────────────────────────────────────────────────
    print(f"  {BOLD}{'ACCOUNT':<16} {'TARGET':>6}  {'STAGGER':>8}{RESET}", flush=True)
    print(f"  {GRAY}{'─' * 36}{RESET}", flush=True)

    coros             = []
    cumulative_delay  = 0.0
    stagger_map       = {}

    for phone, password, target in accounts:
        stagger_map[phone] = cumulative_delay
        delay_str = "immediate" if cumulative_delay == 0 else f"+{cumulative_delay:.0f}s"
        print(
            f"  {tag_color(phone)}{BOLD}{phone:<16}{RESET}"
            f"  {CYAN}{target:>6} tasks{RESET}"
            f"  {GRAY}{delay_str:>8}{RESET}",
            flush=True
        )
        coros.append(run_account_worker(phone, password, target, cumulative_delay))
        cumulative_delay += random.uniform(STAGGER_MIN, STAGGER_MAX)

    print(f"  {GRAY}{'─' * 36}{RESET}", flush=True)
    print(f"\n  {BOLD}{len(accounts)} account(s) · parallel execution · staggered logins{RESET}\n", flush=True)

    # ── Run ───────────────────────────────────────────────────────────────────
    global browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        await asyncio.gather(*coros, return_exceptions=True)
        await browser.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    total_secs = time.time() - t_start
    print_summary()
    total_mins = int(total_secs // 60)
    total_sec  = int(total_secs % 60)
    print(
        f"  {GRAY}Total wall-clock time: {total_mins}m {total_sec:02d}s{RESET}\n",
        flush=True
    )


if __name__ == "__main__":
    asyncio.run(main())
