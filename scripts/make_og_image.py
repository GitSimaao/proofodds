#!/usr/bin/env python3
"""Draw static/og.png — the card X, Reddit, Slack and LinkedIn show for a link.

Deliberately carries no live figure.  The social platforms cache a card for
weeks and key it on the URL, so a card built from the current scorecard would
be shared, cached, and then quietly wrong — on a site whose entire argument is
that its numbers reconcile.  What is on it instead is the one claim that
cannot go stale, because it is true whichever way the record moves.

Run it by hand after a brand change; it is not part of the daily build:

    python scripts/make_og_image.py

Pillow is a build-time convenience and is deliberately absent from
requirements.txt — the daily job never needs it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from proofodds import config  # noqa: E402

W, H = 1200, 630

INK       = "#F8FAFC"
INK_SOFT  = "#A3ACB4"
GROUND    = "#0B1628"
SURFACE   = "#121C2E"
RULE      = "#22334B"
ACCENT    = "#2869D8"
FLAG      = "#E8834F"

FONTS = Path("/usr/share/fonts/truetype/dejavu")
SERIF      = FONTS / "DejaVuSerif.ttf"
SERIF_BOLD = FONTS / "DejaVuSerif-Bold.ttf"
MONO       = FONTS / "DejaVuSansMono.ttf"
MONO_BOLD  = FONTS / "DejaVuSansMono-Bold.ttf"


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.is_file():
        raise SystemExit(f"missing font {path} — install fonts-dejavu-core")
    return ImageFont.truetype(str(path), size)


def run(draw: ImageDraw.ImageDraw, x, y, parts, fnt, gap=0):
    """Draw coloured runs on one baseline and return the width used."""
    for text, colour in parts:
        draw.text((x, y), text, font=fnt, fill=colour)
        x += draw.textlength(text, font=fnt) + gap
    return x


def main() -> int:
    image = Image.new("RGB", (W, H), GROUND)
    draw = ImageDraw.Draw(image)

    # A single accent rule across the top: the site's one piece of chrome.
    draw.rectangle([0, 0, W, 6], fill=ACCENT)

    pad = 78

    # ---- brand ---------------------------------------------------------- #
    tile = 60
    ty = 74
    draw.rounded_rectangle([pad, ty, pad + tile, ty + tile], radius=14,
                           fill=SURFACE, outline=RULE, width=2)
    po = font(SERIF_BOLD, 27)
    box = draw.textbbox((0, 0), "PO", font=po)
    draw.text((pad + tile / 2 - (box[2] - box[0]) / 2 - box[0],
               ty + tile / 2 - (box[3] - box[1]) / 2 - box[1]),
              "PO", font=po, fill=INK)

    word = font(SERIF_BOLD, 33)
    draw.text((pad + tile + 22, ty + 13), "ProofOdds", font=word, fill=INK)

    label = font(MONO, 17)
    draw.text((pad + tile + 22 + draw.textlength("ProofOdds", font=word) + 20,
               ty + 22), "11 divisions", font=label, fill=INK_SOFT)

    # ---- the claim ------------------------------------------------------ #
    # Two sentences doing different jobs: the admission, then the reply.
    # Same weight for both would read as one four-line block and the turn —
    # which is the whole hook — would be lost.
    head = font(SERIF_BOLD, 62)
    y = 196
    run(draw, pad, y, [("Our model ", INK), ("loses", FLAG),
                       (" to the", INK)], head)
    y += 78
    draw.text((pad, y), "closing line.", font=head, fill=INK)

    reply = font(SERIF, 44)
    y += 122
    draw.text((pad, y), "We publish the score anyway.", font=reply,
              fill=INK_SOFT)

    # ---- footer --------------------------------------------------------- #
    fy = 540
    draw.rectangle([pad, fy, W - pad, fy + 1], fill=RULE)

    foot = font(MONO_BOLD, 21)
    soft = font(MONO, 21)
    x = run(draw, pad, fy + 30, [("proofodds.com", INK)], foot)
    run(draw, x, fy + 30,
        [("   sealed before kickoff · scored after", INK_SOFT)], soft)

    out = config.STATIC_DIR / "og.png"
    image.save(out, "PNG", optimize=True)
    print(f"wrote {out} — {out.stat().st_size // 1024}KB, {W}x{H}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
