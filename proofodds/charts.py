"""
Inline SVG charts.

Hand-built rather than matplotlib, for three reasons: the output is crisp at
any zoom, it inherits the page's CSS custom properties so light and dark themes
are correct by construction, and the build has no image pipeline.

Colour roles are fixed across the whole site:
    model  -> --c-model    (blue)
    market -> the baseline. The market is the benchmark, not a peer series,
              so it is drawn as a reference rule, never as a second line.
"""

from __future__ import annotations

import html


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def cumulative_gap(points: list[dict], width: int = 720, height: int = 220) -> str:
    """
    Running total of (model loss − market loss), match by match.

    Above the zero rule the model is paying for what it does not know; a flat
    line means it is keeping pace. The slope says far more than any single
    summary number, which is why this is the chart at the top of the scorecard.
    """
    if len(points) < 2:
        return _empty(width, height, "Not enough graded matches yet")

    pad_l, pad_r, pad_t, pad_b = 44, 12, 14, 26
    values = [p["value"] for p in points]
    lo, hi = min(min(values), 0.0), max(max(values), 0.0)
    span = (hi - lo) or 1.0
    lo -= span * 0.12
    hi += span * 0.12
    span = hi - lo

    def x(i):
        return pad_l + (width - pad_l - pad_r) * (i / (len(points) - 1))

    def y(v):
        return pad_t + (height - pad_t - pad_b) * (1 - (v - lo) / span)

    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    area = f"{x(0):.1f},{y(0):.1f} " + line + f" {x(len(values)-1):.1f},{y(0):.1f}"

    ticks = []
    for frac in (0, 0.5, 1):
        v = lo + span * frac
        ticks.append(
            f'<line x1="{pad_l}" y1="{y(v):.1f}" x2="{width - pad_r}" y2="{y(v):.1f}" '
            f'class="grid"/>'
            f'<text x="{pad_l - 8}" y="{y(v) + 3.5:.1f}" class="tick" '
            f'text-anchor="end">{v:+.1f}</text>')

    first, last = points[0]["date"], points[-1]["date"]

    return f'''<figure class="chart">
<svg viewBox="0 0 {width} {height}" role="img" preserveAspectRatio="xMidYMid meet"
     aria-label="Cumulative log loss against the closing line, {_esc(first)} to {_esc(last)}">
  {''.join(ticks)}
  <polygon points="{area}" class="area"/>
  <line x1="{pad_l}" y1="{y(0):.1f}" x2="{width - pad_r}" y2="{y(0):.1f}" class="zero"/>
  <polyline points="{line}" class="series"/>
  <circle cx="{x(len(values)-1):.1f}" cy="{y(values[-1]):.1f}" r="4" class="endpoint"/>
  <text x="{pad_l}" y="{height - 8}" class="tick">{_esc(first)}</text>
  <text x="{width - pad_r}" y="{height - 8}" class="tick" text-anchor="end">{_esc(last)}</text>
</svg>
<figcaption>Cumulative log loss against the market-average closing line.
Above the rule the model is behind; below it, ahead.</figcaption>
</figure>'''


def calibration(bins: list[dict], size: int = 300) -> str:
    """Reliability diagram: stated probability against what actually happened."""
    if len(bins) < 3:
        return ""

    pad = 34
    inner = size - pad - 14

    def px(v):
        return pad + inner * v

    def py(v):
        return pad + inner * (1 - v)

    dots = "".join(
        f'<circle cx="{px(b["predicted"]):.1f}" cy="{py(b["observed"]):.1f}" '
        f'r="{3 + min(5, b["n"] ** 0.5 / 6):.1f}" class="dot"><title>'
        f'said {b["predicted"]:.0%}, happened {b["observed"]:.0%} '
        f'({b["n"]} outcomes)</title></circle>'
        for b in bins)

    path = " ".join(f'{px(b["predicted"]):.1f},{py(b["observed"]):.1f}' for b in bins)

    return f'''<figure class="chart chart--square">
<svg viewBox="0 0 {size} {size}" role="img" preserveAspectRatio="xMidYMid meet"
     aria-label="Reliability diagram of stated probability against observed frequency">
  <rect x="{pad}" y="{pad}" width="{inner}" height="{inner}" class="plot"/>
  <line x1="{px(0)}" y1="{py(0)}" x2="{px(1)}" y2="{py(1)}" class="zero"/>
  <polyline points="{path}" class="series thin"/>
  {dots}
  <text x="{pad}" y="{size - 6}" class="tick">0%</text>
  <text x="{px(1):.0f}" y="{size - 6}" class="tick" text-anchor="end">100%</text>
  <text x="{pad - 8}" y="{py(1) + 4:.0f}" class="tick" text-anchor="end">100%</text>
</svg>
<figcaption>Where the dots sit on the diagonal, a stated probability means what
it says. Dot size is the number of outcomes in the bin.</figcaption>
</figure>'''


def outcome_bar(p_home: float, p_draw: float, p_away: float) -> str:
    """
    The three-way split for one fixture.

    A 2px gap between segments keeps them reading as three quantities rather
    than one continuous strip.
    """
    total = p_home + p_draw + p_away or 1.0
    h, d, a = (100 * p_home / total, 100 * p_draw / total, 100 * p_away / total)
    return (
        f'<div class="obar" role="img" aria-label="'
        f'home {p_home:.0%}, draw {p_draw:.0%}, away {p_away:.0%}">'
        f'<span class="obar-h" style="flex-basis:{h:.2f}%"></span>'
        f'<span class="obar-d" style="flex-basis:{d:.2f}%"></span>'
        f'<span class="obar-a" style="flex-basis:{a:.2f}%"></span>'
        f'</div>')


def _empty(width: int, height: int, message: str) -> str:
    return (f'<figure class="chart chart--empty">'
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(message)}">'
            f'<text x="{width/2}" y="{height/2}" text-anchor="middle" '
            f'class="tick">{_esc(message)}</text></svg></figure>')
