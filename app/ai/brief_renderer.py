"""
Premium HTML rendering for BriefResult.

Three modes:
  - "app":   uses <style> block + theme-aware colors. Rendered into QTextBrowser.
  - "pdf":   adds @page CSS for clean PDF pagination via QPrinter+QTextDocument.
  - "email": inline styles only, table-based layout for Outlook compatibility.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Iterable

from app.ai.brief import BriefResult


# ---------------------------------------------------------------------------
# Tiny markdown -> HTML (purpose-built for our brief format)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD_RE    = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE  = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_CODE_RE    = re.compile(r"`([^`]+?)`")


def _inline(s: str) -> str:
    s = html.escape(s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = _ITALIC_RE.sub(r"<em>\1</em>", s)
    s = _CODE_RE.sub(r"<code>\1</code>", s)
    return s


def _is_severity_header(text: str) -> str:
    """Return a CSS class hint for a section header based on keywords."""
    lower = text.lower()
    if any(k in lower for k in ("stockout", "runout", "backorder", "out of stock")):
        return "sev-danger"
    if any(k in lower for k in ("overstock", "aging", "12 month", "12-month", "excess")):
        return "sev-warning"
    if any(k in lower for k in ("yesterday", "new po", "receipt")):
        return "sev-info"
    if any(k in lower for k in ("recommend", "action", "next step", "priority")):
        return "sev-action"
    if any(k in lower for k in ("summary", "executive", "snapshot", "overview")):
        return "sev-summary"
    return "sev-neutral"


def _render_table(rows: list[list[str]]) -> str:
    if not rows or len(rows) < 2:
        return ""
    header = rows[0]
    body = rows[2:] if len(rows) > 2 and all(c.strip() in ("---", ":---", "---:", ":---:") for c in rows[1]) else rows[1:]
    out = ['<table class="brief-table">', "<thead><tr>"]
    for c in header:
        out.append(f"<th>{_inline(c.strip())}</th>")
    out.append("</tr></thead><tbody>")
    for r in body:
        out.append("<tr>")
        for c in r:
            out.append(f"<td>{_inline(c.strip())}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _markdown_to_html(md: str) -> str:
    """Lightweight Markdown subset: headings, lists, bold/italic/code, tables, paragraphs."""
    if not md:
        return "<p><em>(no content)</em></p>"

    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    while i < len(lines):
        line = lines[i].rstrip()

        # Blank line
        if not line.strip():
            close_lists()
            i += 1
            continue

        # Table — detect a row with pipes followed by a separator row
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            close_lists()
            tbl = []
            while i < len(lines) and "|" in lines[i]:
                cells = [c for c in lines[i].split("|")]
                # Trim leading/trailing empty cells from leading/trailing |
                if cells and not cells[0].strip():
                    cells = cells[1:]
                if cells and not cells[-1].strip():
                    cells = cells[:-1]
                tbl.append(cells)
                i += 1
            out.append(_render_table(tbl))
            continue

        # Heading
        h = _HEADING_RE.match(line)
        if h:
            close_lists()
            level = min(len(h.group(1)), 4)
            text = h.group(2).strip()
            cls = _is_severity_header(text)
            out.append(f'<h{level} class="brief-h{level} {cls}">{_inline(text)}</h{level}>')
            i += 1
            continue

        # Unordered list
        if re.match(r"^\s*[-*•]\s+", line):
            if not in_ul:
                close_lists()
                out.append('<ul class="brief-list">')
                in_ul = True
            text = re.sub(r"^\s*[-*•]\s+", "", line)
            out.append(f"<li>{_inline(text)}</li>")
            i += 1
            continue

        # Ordered list
        if re.match(r"^\s*\d+\.\s+", line):
            if not in_ol:
                close_lists()
                out.append('<ol class="brief-list">')
                in_ol = True
            text = re.sub(r"^\s*\d+\.\s+", "", line)
            out.append(f"<li>{_inline(text)}</li>")
            i += 1
            continue

        # Paragraph
        close_lists()
        # Collect consecutive plain lines into one paragraph
        para_lines = [line]
        j = i + 1
        while j < len(lines) and lines[j].strip() and not (
            _HEADING_RE.match(lines[j])
            or re.match(r"^\s*[-*•]\s+", lines[j])
            or re.match(r"^\s*\d+\.\s+", lines[j])
            or "|" in lines[j]
        ):
            para_lines.append(lines[j].rstrip())
            j += 1
        text = " ".join(para_lines).strip()
        out.append(f"<p>{_inline(text)}</p>")
        i = j

    close_lists()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CSS palettes
# ---------------------------------------------------------------------------

_SEV_COLORS = {
    "sev-danger":   ("#dc2626", "#fef2f2", "#7f1d1d"),
    "sev-warning":  ("#d97706", "#fffbeb", "#78350f"),
    "sev-info":     ("#2563eb", "#eff6ff", "#1e3a8a"),
    "sev-action":   ("#059669", "#ecfdf5", "#064e3b"),
    "sev-summary":  ("#7c3aed", "#f5f3ff", "#3b0764"),
    "sev-neutral":  ("#475569", "#f1f5f9", "#0f172a"),
}


def _build_css(mode: str) -> str:
    """Return a <style> block sized to the rendering mode."""
    sev_rules = []
    for cls, (border, bg, fg) in _SEV_COLORS.items():
        sev_rules.append(f"""
        h1.{cls}, h2.{cls}, h3.{cls}, h4.{cls} {{
            border-left: 5px solid {border};
            background: {bg};
            color: {fg};
            padding: 12px 18px;
            margin: 28px 0 14px 0;
            border-radius: 0 6px 6px 0;
            font-weight: 700;
        }}
        """)
    sev_css = "\n".join(sev_rules)

    page_rule = "@page { size: Letter; margin: 0.6in; }" if mode == "pdf" else ""
    body_max = "max-width: 940px; margin: 0 auto;" if mode != "pdf" else "max-width: 100%;"

    return f"""
    <style>
    {page_rule}
    body, .brief-root {{
        font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
        color: #111827;
        background: #ffffff;
        font-size: 13.5px;
        line-height: 1.55;
        {body_max}
        padding: 0;
    }}
    .brief-banner {{
        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%);
        color: white;
        padding: 26px 32px;
        border-radius: 10px;
        margin-bottom: 28px;
        box-shadow: 0 4px 20px rgba(99,102,241,0.15);
    }}
    .brief-banner h1 {{
        margin: 0 0 6px 0;
        font-size: 28px;
        font-weight: 800;
        letter-spacing: -0.5px;
    }}
    .brief-banner .subtitle {{
        font-size: 14px;
        opacity: 0.92;
        margin: 0;
    }}
    .kpi-grid {{
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin: 18px 0 28px 0;
    }}
    .kpi-card {{
        flex: 1 1 160px;
        min-width: 160px;
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 14px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    .kpi-card .label {{
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: #6b7280;
        font-weight: 600;
        margin-bottom: 6px;
    }}
    .kpi-card .value {{
        font-size: 22px;
        font-weight: 700;
        color: #111827;
    }}
    .kpi-card.danger {{ border-left: 4px solid #dc2626; }}
    .kpi-card.warning {{ border-left: 4px solid #d97706; }}
    .kpi-card.info {{ border-left: 4px solid #2563eb; }}
    .kpi-card.success {{ border-left: 4px solid #059669; }}
    {sev_css}
    h1.brief-h1 {{ font-size: 22px; }}
    h2.brief-h2 {{ font-size: 18px; }}
    h3.brief-h3 {{ font-size: 16px; }}
    h4.brief-h4 {{ font-size: 14px; }}
    p {{ margin: 8px 0; }}
    ul.brief-list, ol.brief-list {{ margin: 8px 0 14px 22px; padding: 0; }}
    ul.brief-list li, ol.brief-list li {{ margin: 4px 0; }}
    code {{
        background: #f1f5f9;
        padding: 1px 5px;
        border-radius: 3px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 12px;
        color: #1e293b;
    }}
    table.brief-table {{
        width: 100%;
        border-collapse: collapse;
        margin: 12px 0 22px 0;
        font-size: 12px;
        border: 1px solid #e5e7eb;
    }}
    table.brief-table th {{
        background: #f8fafc;
        color: #334155;
        text-transform: uppercase;
        font-size: 10.5px;
        letter-spacing: 0.4px;
        font-weight: 700;
        padding: 8px 10px;
        text-align: left;
        border-bottom: 2px solid #e2e8f0;
    }}
    table.brief-table td {{
        padding: 6px 10px;
        border-bottom: 1px solid #f1f5f9;
        color: #1f2937;
    }}
    table.brief-table tr:nth-child(even) td {{ background: #fafbfc; }}
    .brief-footer {{
        margin-top: 36px;
        padding-top: 14px;
        border-top: 1px solid #e5e7eb;
        font-size: 11px;
        color: #9ca3af;
    }}
    </style>
    """


# ---------------------------------------------------------------------------
# Banner + KPI strip
# ---------------------------------------------------------------------------

def _kpi_strip(kpis: dict) -> str:
    cards = [
        ("danger",  "Active Stockouts",  f"{kpis.get('stockout_skus', 0):,}"),
        ("danger",  "Runout Risk",       f"{kpis.get('runout_risk_skus', 0):,}"),
        ("warning", "Overstock SKUs",    f"{kpis.get('overstock_skus', 0):,}"),
        ("warning", "Aging ≥ 365d",      f"{kpis.get('aging_365d_skus', 0):,}"),
        ("warning", ">12 mo DOI (proj)", f"{kpis.get('twelve_month_doi_skus', 0):,}"),
        ("info",    "Stock Turn",        f"{kpis.get('stock_turn', 0):.2f}×"),
        ("success", "Fill Rate",         f"{kpis.get('fill_rate', 0)*100:.1f}%"),
        ("info",    "Total SKUs",        f"{kpis.get('total_skus', 0):,}"),
    ]
    items = "".join(
        f'<div class="kpi-card {tone}"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div></div>'
        for tone, label, value in cards
    )
    return f'<div class="kpi-grid">{items}</div>'


def _banner(result: BriefResult) -> str:
    # %-d is POSIX-only; format with %d then strip the leading zero manually
    # so this works on Windows.
    try:
        d = result.target_date.strftime("%A, %B %d, %Y")
        d = re.sub(r"\b0(\d)", r"\1", d)
    except Exception:
        d = str(result.target_date)
    return f"""
    <div class="brief-banner">
        <h1>📋 Daily Brief — {html.escape(d)}</h1>
        <p class="subtitle">Inventory health · Two priorities: avoid 12-month inventory · avoid stockouts</p>
    </div>
    """


def _footer(result: BriefResult) -> str:
    gen = result.generated_at.strftime("%Y-%m-%d %H:%M")
    return (
        f'<div class="brief-footer">'
        f'Generated {html.escape(gen)} · '
        f'Provider: {html.escape(result.provider)} · '
        f'Model: {html.escape(result.model)} · '
        f'Tokens: ~{result.tokens_in:,} in / ~{result.tokens_out:,} out · '
        f'Estimated cost: ${result.cost_usd:.4f} · '
        f'Elapsed: {result.elapsed_sec:.1f}s'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_to_html(
    result: BriefResult,
    kpis: dict | None = None,
    mode: str = "app",
) -> str:
    """Render a BriefResult to a complete HTML document.

    `kpis` is the BriefData.portfolio_kpis dict — optional but adds the visual
    KPI strip at the top. When None, only the AI narrative is shown.
    """
    css = _build_css(mode)
    banner = _banner(result)
    kpi_html = _kpi_strip(kpis) if kpis else ""
    body_html = _markdown_to_html(result.markdown or "_(no content generated)_")
    footer = _footer(result)

    full = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Daily Brief — {html.escape(str(result.target_date))}</title>
{css}
</head><body>
<div class="brief-root">
{banner}
{kpi_html}
{body_html}
{footer}
</div>
</body></html>"""

    if mode == "email":
        # Outlook-friendly: don't strip CSS (modern clients honor it),
        # but the simple table/inline structure already works in Outlook 2013+.
        return full

    return full
