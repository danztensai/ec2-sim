"""Capture the documentation screenshots from the app running on demo data.

    npm run screenshots          # starts Vite, captures, writes ../docs/images/*.png

Needs Python Playwright with Chromium (pip install playwright; playwright install chromium).
"""
import os
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:1420/screenshots.html"
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "images")
os.makedirs(OUT, exist_ok=True)


def shot(page, name):
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(OUT, f"{name}.png"))
    print("wrote", name)


def nav(page, label):
    page.locator("nav .nav-item", has_text=label).click()
    page.wait_for_timeout(400)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
    page.goto(URL)
    page.wait_for_selector("text=Server Running")

    nav(page, "Overview"); shot(page, "overview")
    nav(page, "Instances"); shot(page, "instances")

    page.locator("tr", has_text="web-1").get_by_role("button", name="Connect").click()
    shot(page, "connect")
    page.get_by_role("button", name="Browser").click()
    shot(page, "web-access")
    page.keyboard.press("Escape")

    page.get_by_role("button", name="Launch instance").first.click()
    page.get_by_placeholder("e.g. web-1").fill("web-3")
    page.locator(".modal, [role=dialog]").first.locator("text=Install nginx (Amazon Linux)").click()
    shot(page, "launch")
    page.keyboard.press("Escape")

    nav(page, "AMIs"); shot(page, "amis")
    nav(page, "Key pairs"); shot(page, "key-pairs")

    nav(page, "Run script")
    page.get_by_placeholder("/path/to/repo").fill("/home/me/work/infra")
    page.get_by_placeholder("./build.sh").fill("./build-ami.sh --env staging")
    page.get_by_role("button", name="Add variable").click()
    page.get_by_placeholder("NAME").fill("VAULT_TOKEN")
    page.locator(".var-row input[type=checkbox]").check()
    page.get_by_placeholder("secret value (not saved)").fill("s.demo-not-a-real-token")
    page.evaluate("window.__emitRunOutput()")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    shot(page, "run-script")

    nav(page, "Server log"); shot(page, "server-log")
    nav(page, "Settings"); shot(page, "settings")
    browser.close()
