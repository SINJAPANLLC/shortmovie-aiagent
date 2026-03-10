import os
import json
import logging
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)

TIKTOK_COOKIES_FILE = "tiktok_cookies.json"
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def save_tiktok_cookies(cookies_data):
    if isinstance(cookies_data, str):
        cookies_data = json.loads(cookies_data)
    with open(TIKTOK_COOKIES_FILE, "w") as f:
        json.dump(cookies_data, f, ensure_ascii=False)
    logger.info(f"TikTok cookies saved: {len(cookies_data)} cookies")


def load_tiktok_cookies():
    if not os.path.exists(TIKTOK_COOKIES_FILE):
        return None
    try:
        with open(TIKTOK_COOKIES_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load TikTok cookies: {e}")
        return None


def is_tiktok_rpa_connected() -> bool:
    cookies = load_tiktok_cookies()
    if not cookies:
        return False
    has_session = any(
        c.get("name") in ("sessionid", "sid_tt", "sessionid_ss")
        for c in cookies
    )
    return has_session


def clear_tiktok_cookies():
    if os.path.exists(TIKTOK_COOKIES_FILE):
        os.remove(TIKTOK_COOKIES_FILE)
        logger.info("TikTok cookies cleared")


def _convert_cookies_for_playwright(cookies):
    pw_cookies = []
    for c in cookies:
        cookie = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ".tiktok.com"),
            "path": c.get("path", "/"),
        }
        if c.get("expirationDate"):
            cookie["expires"] = float(c["expirationDate"])
        if c.get("secure") is not None:
            cookie["secure"] = bool(c["secure"])
        if c.get("sameSite"):
            same_site_map = {
                "no_restriction": "None",
                "lax": "Lax",
                "strict": "Strict",
                "unspecified": "Lax",
            }
            cookie["sameSite"] = same_site_map.get(
                c["sameSite"].lower(), "Lax"
            )
        pw_cookies.append(cookie)
    return pw_cookies


async def upload_to_tiktok_rpa(video_path: str, title: str, description: str = "", tags: list = None) -> str:
    cookies = load_tiktok_cookies()
    if not cookies:
        logger.warning("TikTok cookies not found, skipping RPA upload")
        return None

    if not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return None

    video_path = os.path.abspath(video_path)

    hashtags = ""
    if tags:
        hashtags = " ".join([f"#{t}" for t in tags[:10]])
    caption_text = f"{title} {hashtags}".strip()[:2200]

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )

            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="ja-JP",
            )

            pw_cookies = _convert_cookies_for_playwright(cookies)
            await context.add_cookies(pw_cookies)

            page = await context.new_page()

            logger.info("Navigating to TikTok Studio upload page...")
            await page.goto(TIKTOK_UPLOAD_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)

            current_url = page.url
            if "login" in current_url.lower():
                logger.error("TikTok session expired - cookies need refresh")
                await browser.close()
                return None

            logger.info(f"Upload page loaded: {current_url}")

            file_input = await page.query_selector('input[type="file"][accept*="video"]')
            if not file_input:
                file_input = await page.query_selector('input[type="file"]')

            if file_input:
                await file_input.set_input_files(video_path)
                logger.info(f"Video file selected: {video_path}")
            else:
                logger.error("Could not find file input on TikTok upload page")
                await page.screenshot(path="tiktok_upload_debug.png")
                await browser.close()
                return None

            await page.wait_for_timeout(5000)

            logger.info("Waiting for video to process...")
            for _ in range(60):
                await page.wait_for_timeout(2000)
                progress_el = await page.query_selector('[class*="progress"]')
                upload_complete = await page.query_selector('[class*="upload-complete"], [class*="success"]')
                if upload_complete:
                    break
                replace_btn = await page.query_selector('button:has-text("変更"), button:has-text("Replace"), button:has-text("Edit video")')
                if replace_btn:
                    break
            else:
                logger.warning("Upload processing timeout, attempting to continue...")

            await page.wait_for_timeout(2000)

            caption_selectors = [
                '[data-contents="true"] [data-text="true"]',
                '.public-DraftEditor-content',
                '[contenteditable="true"]',
                'div[role="textbox"]',
                '.caption-editor',
                '.DraftEditor-root',
            ]

            caption_filled = False
            for sel in caption_selectors:
                try:
                    editor = await page.query_selector(sel)
                    if editor:
                        await editor.click()
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Delete")
                        await page.keyboard.type(caption_text, delay=20)
                        caption_filled = True
                        logger.info(f"Caption filled via selector: {sel}")
                        break
                except Exception as e:
                    logger.debug(f"Caption selector {sel} failed: {e}")
                    continue

            if not caption_filled:
                logger.warning("Could not fill caption, continuing without it")

            await page.wait_for_timeout(2000)

            post_selectors = [
                'button:has-text("投稿")',
                'button:has-text("Post")',
                'button:has-text("Publish")',
                'button[data-e2e="post_video_button"]',
                'div.btn-post button',
            ]

            posted = False
            for sel in post_selectors:
                try:
                    post_btn = await page.query_selector(sel)
                    if post_btn:
                        is_disabled = await post_btn.get_attribute("disabled")
                        if is_disabled:
                            logger.info("Post button is disabled, waiting...")
                            await page.wait_for_timeout(5000)

                        await post_btn.click()
                        posted = True
                        logger.info(f"Post button clicked via selector: {sel}")
                        break
                except Exception as e:
                    logger.debug(f"Post button selector {sel} failed: {e}")
                    continue

            if not posted:
                logger.error("Could not find post button")
                await page.screenshot(path="tiktok_post_debug.png")
                await browser.close()
                return None

            await page.wait_for_timeout(10000)

            success_selectors = [
                'div:has-text("投稿されました")',
                'div:has-text("Your video has been")',
                'div:has-text("uploaded")',
                '[class*="success"]',
                'div:has-text("Video published")',
            ]

            upload_success = False
            for sel in success_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        upload_success = True
                        break
                except Exception:
                    continue

            final_url = page.url
            await browser.close()

            if upload_success or "manage" in final_url.lower() or "upload" not in final_url.lower():
                logger.info(f"TikTok RPA upload completed successfully")
                return f"rpa_upload_{int(asyncio.get_event_loop().time())}"
            else:
                logger.warning(f"TikTok RPA upload may have failed, final URL: {final_url}")
                return f"rpa_upload_uncertain_{int(asyncio.get_event_loop().time())}"

    except Exception as e:
        logger.error(f"TikTok RPA upload error: {e}", exc_info=True)
        return None


def upload_to_tiktok_rpa_sync(video_path: str, title: str, description: str = "", tags: list = None) -> str:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    upload_to_tiktok_rpa(video_path, title, description, tags)
                )
                return future.result(timeout=300)
        else:
            return loop.run_until_complete(
                upload_to_tiktok_rpa(video_path, title, description, tags)
            )
    except Exception as e:
        logger.error(f"TikTok RPA sync upload error: {e}")
        return None
