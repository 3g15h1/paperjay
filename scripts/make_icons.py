"""Render PaperJay's app icons from the vector artwork (run after changing it; the PNGs are committed).

    paperjay/static/icons/icon.svg        the app icon (home screen, install, sidebar on the sign-in page)
    paperjay/static/icons/icon-small.svg  bolder strokes for tiny sizes (the browser tab)

Needs Playwright with Chromium (a development tool, not a PaperJay dependency):
    python -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/playwright install chromium
    /tmp/pw/bin/python scripts/make_icons.py
The maskable icon (Android) keeps the mark inside the middle 80 %, so round and squircle masks don't crop it.
"""
import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright

ICONS = Path(__file__).resolve().parent.parent / "paperjay" / "static" / "icons"


def maskable(svg: str) -> str:
    """The same icon with its artwork scaled to 80 % around the centre (the background still fills the tile)."""
    head, rest = svg.split('<rect width="1024" height="1024" fill="url(#bg)"/>', 1)
    body = rest[:rest.rindex("</svg>")]
    return (head + '<rect width="1024" height="1024" fill="url(#bg)"/>\n'
            '<g transform="translate(512 512) scale(0.8) translate(-512 -512)">' + body + "</g></svg>")


async def render(page, svg: str, size: int, out: Path) -> None:
    await page.set_viewport_size({"width": size, "height": size})
    svg = re.sub(r"<svg ", f'<svg width="{size}" height="{size}" ', svg, count=1)
    await page.set_content(f'<html><body style="margin:0">{svg}</body></html>')
    await page.screenshot(path=str(out), omit_background=True)


async def main() -> None:
    icon = (ICONS / "icon.svg").read_text()
    small = (ICONS / "icon-small.svg").read_text()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        for size, name in ((180, "icon-180"), (192, "icon-192"), (512, "icon-512")):
            await render(page, icon, size, ICONS / f"{name}.png")
        await render(page, maskable(icon), 512, ICONS / "maskable-512.png")
        await render(page, small, 64, ICONS / "favicon.png")
        await browser.close()
    print("icons written to", ICONS)


if __name__ == "__main__":
    asyncio.run(main())
