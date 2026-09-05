#!/usr/bin/env python3
"""Download the provider crest cache into static/clubs/, so the site serves
its own images.

Hotlinking 169 distinct crests from a third-party CDN is a dependency the
site does not control: a rate limit or a moved file breaks every card at
once, and a traffic spike is exactly when that happens.  Serving them from
our own origin removes the dependency and cuts 280-odd cross-origin requests
off the front page.

This changes nothing about rights.  Club crests stay protected marks; the
display is the act, not the origin server, and `render.club_mark` already
treats a local file as the deliberate one.  Anything downloaded here is
replaceable in place by a licensed asset pack under the same name.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config, crests  # noqa: E402
from proofodds.render import slugify  # noqa: E402

# The bytes a provider returns are trusted only as far as their content type:
# an image extension is chosen from what arrived, never from the URL, so a
# provider serving HTML into an .svg path cannot plant markup in static/.
TYPES = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
}

# Enough of a signature that a provider can identify and contact us rather
# than silently blocking an anonymous scraper.
AGENT = f"ProofOdds/1.0 (+{config.SITE_URL}; one-time crest vendoring)"

MAX_BYTES = 512 * 1024

# The largest a crest is ever painted is the 76px match-page mark, so anything
# past 160px is bytes nobody sees.  Pillow is a build-time convenience, not a
# runtime dependency — it is deliberately absent from requirements.txt, and
# without it the crests are simply stored at whatever size the provider sent.
MAX_PIXELS = 160

try:
    from PIL import Image
except ImportError:
    Image = None


def shrink(path: Path, limit: int) -> None:
    """Downscale in place, preserving transparency. Never upscales."""
    if Image is None or limit <= 0 or path.suffix == ".svg":
        return
    try:
        with Image.open(path) as image:
            image = image.convert("RGBA")
            if max(image.size) > limit:
                image.thumbnail((limit, limit), Image.LANCZOS)
            # A crest is flat colour and a few gradients, so a 256-entry
            # palette is visually identical and about a third of the bytes.
            # Transparency survives because the quantiser keeps an alpha
            # index; RGBA is kept if anything about that fails.
            try:
                palette = image.quantize(colors=256, method=Image.FASTOCTREE)
                palette.save(path, "PNG", optimize=True)
                return
            except (OSError, ValueError):
                pass
            image.save(path, "PNG", optimize=True)
    except OSError as exc:
        # A crest we cannot re-encode is still a crest; keep the original.
        print(f"    kept original size ({exc})")


def existing(slug: str) -> Path | None:
    for suffix in (".svg", ".png", ".webp", ".jpg"):
        candidate = config.STATIC_DIR / "clubs" / f"{slug}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def fetch(url: str) -> tuple[bytes, str] | None:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            kind = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            if kind not in TYPES:
                print(f"    refused: content-type {kind!r}")
                return None
            body = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"    failed: {exc}")
        return None
    if len(body) > MAX_BYTES:
        print(f"    refused: larger than {MAX_BYTES} bytes")
        return None
    if not body:
        print("    refused: empty response")
        return None
    return body, TYPES[kind]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="re-download crests already present")
    parser.add_argument("--delay", type=float, default=0.25,
                        help="seconds between requests (default 0.25)")
    parser.add_argument("--max-px", type=int, default=MAX_PIXELS,
                        help=f"longest edge to keep (default {MAX_PIXELS}, 0 to disable)")
    args = parser.parse_args()

    out = config.STATIC_DIR / "clubs"
    out.mkdir(parents=True, exist_ok=True)

    # One club can appear in several divisions (promotion, or the same name in
    # a cup file). Deduplicate on the slug the renderer will actually look up.
    wanted: dict[str, str] = {}
    for league, clubs in crests.load().get("clubs", {}).items():
        for club, entry in clubs.items():
            url = crests.safe_url((entry or {}).get("url"))
            if url:
                wanted.setdefault(slugify(club), url)

    saved = skipped = failed = 0
    for slug, url in sorted(wanted.items()):
        present = existing(slug)
        if present and not args.force:
            shrink(present, args.max_px)
            skipped += 1
            continue
        print(f"  {slug} <- {url}")
        result = fetch(url)
        if result is None:
            failed += 1
            continue
        body, suffix = result
        if present and present.suffix != suffix:
            present.unlink()
        destination = out / f"{slug}{suffix}"
        destination.write_bytes(body)
        shrink(destination, args.max_px)
        saved += 1
        time.sleep(args.delay)

    print(f"\n{len(wanted)} crests known — "
          f"{saved} saved, {skipped} already present, {failed} failed.")
    # A failure is not fatal: club_mark falls back to the provider URL and
    # then to the monogram, so a partial vendoring still renders.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
