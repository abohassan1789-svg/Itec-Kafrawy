"""مولّد ملف خطة التنفيذ — خطوة واحدة.

بيقرا docs/plan/plan_data.json ← يطلّع docs/plan/execution_plan.html ← يطبعه PDF بـ Edge
في docs/plan/خطة_التنفيذ.pdf

التشغيل:
    py -3.11 docs/plan/build_plan.py

أول ما بند يخلص: في plan_data.json غيّر "status" لـ "done" واكتب "date"، وشغّل السكريبت.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "plan_data.json"
HTML_FILE = HERE / "execution_plan.html"
PDF_FILE = HERE / "خطة_التنفيذ.pdf"

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

# ألوان Hr GAD (app/ui/common/theme.py)
GREEN = "#137A38"
GREEN_DARK = "#0F6B30"
TEXT = "#111827"
TEXT_MUTED = "#64748B"
BORDER = "#D9E2EC"
BG_CONTENT = "#F1F5F9"

STATUS = {
    #  المفتاح: (الكلام، لون الكلام، الخلفية، الحد)
    "done": ("تم التنفيذ ✅", "#087443", "#ECFDF3", "#B7E4C7"),
    "doing": ("شغالين فيه 🔧", "#1D4ED8", "#EFF6FF", "#BFDBFE"),
    "next": ("الخطوة الجاية 👉", "#9A3412", "#FFF7ED", "#FED7AA"),
    "todo": ("لسه ⏳", "#64748B", "#F8FAFC", "#E2E8F0"),
}


def esc(text: str | None) -> str:
    return html.escape(text or "")


# أي كلام إنجليزي/أكواد جوه جملة عربي بيتلف في span اتجاهه LTR معزول،
# عشان مايتقلبش (زي 0002_security أو %LOCALAPPDATA%\ITEC\logs أو transaction()).
_WORD = r"(?:[A-Za-z0-9_%.\\/:+#-]|\(\))+"
_LTR_RUN = re.compile(rf"{_WORD}(?:[ ,]+{_WORD})*")


def rich(text: str | None) -> str:
    """زي esc بس بيعزل الكلام الإنجليزي."""
    out, pos = [], 0
    text = text or ""
    edge = " ,.:+-/#"  # علامات على الأطراف بتفضل برّه العزل (زي «+ SESSION»)
    for m in _LTR_RUN.finditer(text):
        raw = m.group(0)
        lead = len(raw) - len(raw.lstrip(edge.replace(".", "")))  # «.env» تفضل بنقطتها
        run = raw[lead:].rstrip(edge)
        if not re.search(r"[A-Za-z_\\]", run):
            continue  # الأرقام والنسب (14% / 0%) بتتقرا صح يمين لشمال من غير عزل
        start = m.start() + lead
        cls = "ltr nw" if len(run) <= 24 else "ltr"  # الكلام القصير مايتقسمش على سطرين
        out.append(html.escape(text[pos:start]))
        out.append(f'<span class="{cls}">{html.escape(run)}</span>')
        pos = start + len(run)
    out.append(html.escape(text[pos:]))
    return "".join(out)


def load() -> dict:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    errors = []
    for ph in data["phases"]:
        for it in ph["items"]:
            st = it.get("status")
            if st not in STATUS:
                errors.append(f"المرحلة {ph['id']} / «{it['name']}»: حالة غلط «{st}»")
            if st == "done" and not it.get("date"):
                errors.append(f"المرحلة {ph['id']} / «{it['name']}»: خلصت من غير تاريخ")
    if errors:
        sys.exit("فيه أخطاء في plan_data.json:\n" + "\n".join(errors))
    return data


def phase_state(ph: dict) -> tuple[str, str]:
    """(حالة المرحلة، تاريخ آخر بند لو خلصت)."""
    sts = [it["status"] for it in ph["items"]]
    if all(s == "done" for s in sts):
        return "done", max(it["date"] for it in ph["items"])
    if "doing" in sts or ("done" in sts):
        return "doing", ""
    if "next" in sts:
        return "next", ""
    return "todo", ""


def chip(status: str, date: str = "", big: bool = False) -> str:
    label, fg, bg, bd = STATUS[status]
    date_html = f'<span class="chip-date">{esc(date)}</span>' if date else ""
    cls = "chip big" if big else "chip"
    return (f'<span class="{cls}" style="color:{fg};background:{bg};border-color:{bd}">'
            f'{label}{date_html}</span>')


def bar(pct: float, color: str) -> str:
    return (f'<div class="bar"><div class="bar-fill" style="width:{pct:.1f}%;background:{color}">'
            f'</div></div>')


def render(data: dict) -> str:
    phases = data["phases"]
    all_items = [it for ph in phases for it in ph["items"]]
    total = len(all_items)
    counts = {k: sum(1 for it in all_items if it["status"] == k) for k in STATUS}
    pct = 100.0 * counts["done"] / total if total else 0.0
    phases_done = sum(1 for ph in phases if phase_state(ph)[0] == "done")

    next_items = [(ph, it) for ph in phases for it in ph["items"] if it["status"] in ("doing", "next")]
    if next_items:
        ph, it = next_items[0]
        next_html = (f'المرحلة <b>{esc(ph["id"])}</b> ({esc(ph["title"])}) ← '
                     f'<b>{rich(it["name"])}</b>'
                     + (f' — {rich(it.get("note"))}' if it.get("note") else ""))
    else:
        next_html = "مفيش — كل البنود خلصت 🎉"

    # ---- جدول نظرة عامة على المراحل ----
    overview_rows = []
    for ph in phases:
        n_done = sum(1 for it in ph["items"] if it["status"] == "done")
        n = len(ph["items"])
        st, date = phase_state(ph)
        overview_rows.append(
            "<tr>"
            f'<td><span class="ph-badge" style="background:{ph["color"]}">{esc(ph["id"])}</span></td>'
            f'<td class="ph-name">{esc(ph["title"])}</td>'
            f'<td class="num">{n_done} / {n}</td>'
            f'<td class="bar-cell">{bar(100.0 * n_done / n if n else 0, ph["color"])}</td>'
            f"<td>{chip(st, date)}</td>"
            "</tr>"
        )

    # ---- المراحل بالتفصيل ----
    sections = []
    for ph in phases:
        color = ph["color"]
        n_done = sum(1 for it in ph["items"] if it["status"] == "done")
        n = len(ph["items"])
        st, date = phase_state(ph)
        rows = []
        for i, it in enumerate(ph["items"], 1):
            note = f'<div class="note">📌 {rich(it["note"])}</div>' if it.get("note") else ""
            detail = f'<div class="detail">{rich(it.get("detail"))}</div>' if it.get("detail") else ""
            row_cls = f'row-{it["status"]}'
            rows.append(
                f'<tr class="{row_cls}">'
                f'<td class="idx" style="color:{color}">{i}</td>'
                f'<td><div class="item-name">{rich(it["name"])}</div>{detail}{note}</td>'
                f'<td class="st">{chip(it["status"], it.get("date", ""))}</td>'
                "</tr>"
            )
        sections.append(f"""
<section class="phase" style="--c:{color}">
  <table class="items">
    <thead>
    <tr><th colspan="3" class="head-cell">
  <div class="phase-head" style="background:{color}">
    <div class="phase-num">{esc(ph["id"])}</div>
    <div class="phase-title">
      <div class="t">المرحلة {esc(ph["id"])}: {esc(ph["title"])}</div>
      <div class="g">🎯 {rich(ph.get("goal"))}</div>
    </div>
    <div class="phase-prog">
      <div class="pp-num">{n_done} / {n}</div>
      <div class="pp-bar"><div style="width:{100.0 * n_done / n if n else 0:.1f}%"></div></div>
    </div>
  </div>
  <div class="phase-state">حالة المرحلة: {chip(st, date)}</div>
    </th></tr>
    <tr class="cols"><th style="width:26px">#</th><th>البند</th><th style="width:150px">الحالة</th></tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</section>""")

    legend = " ".join(chip(k) for k in STATUS)

    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>{esc(data["title"])}</title>
<style>
  @page {{
    size: A4;
    margin: 12mm 11mm 14mm 11mm;
    @bottom-center {{
      content: "{esc(data["brand"])}  ·  صفحة " counter(page) " من " counter(pages);
      font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
      font-size: 8.5pt; color: {TEXT_MUTED};
    }}
  }}
  * {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  html, body {{ margin: 0; padding: 0; background: #FFFFFF; }}
  body {{ font-family: 'Segoe UI', Tahoma, Arial, sans-serif; color: {TEXT}; font-size: 10pt; line-height: 1.55; }}

  .cover {{ background: linear-gradient(135deg, {GREEN_DARK}, {GREEN}); color: #fff;
           border-radius: 12px; padding: 16px 20px; display: flex; align-items: center; gap: 16px; }}
  .cover .ttl {{ flex: 1; }}
  .cover h1 {{ margin: 0; font-size: 20pt; font-weight: 800; }}
  .cover .sub {{ opacity: .92; font-size: 9.5pt; margin-top: 4px; }}
  .cover .brand {{ text-align: center; border-right: 1px solid rgba(255,255,255,.35); padding-right: 16px; }}
  .cover .brand b {{ display: block; font-size: 15pt; letter-spacing: 1px; direction: ltr; }}
  .cover .brand span {{ font-size: 8.5pt; opacity: .9; }}

  .summary {{ display: flex; gap: 12px; margin-top: 12px; align-items: stretch; }}
  .ring {{ width: 128px; height: 128px; border-radius: 50%; flex: none; align-self: center;
          background: conic-gradient({GREEN} 0 {pct:.1f}%, #E2E8F0 {pct:.1f}% 100%);
          display: flex; align-items: center; justify-content: center; }}
  .ring-in {{ width: 96px; height: 96px; border-radius: 50%; background: #fff; text-align: center;
             display: flex; flex-direction: column; align-items: center; justify-content: center; }}
  .ring-in b {{ font-size: 21pt; color: {GREEN}; line-height: 1; direction: ltr; }}
  .ring-in span {{ font-size: 8pt; color: {TEXT_MUTED}; margin-top: 4px; }}
  .tiles {{ flex: 1; display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }}
  .tile {{ border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 10px; background: #fff; }}
  .tile .k {{ font-size: 8.5pt; color: {TEXT_MUTED}; }}
  .tile .v {{ font-size: 17pt; font-weight: 800; line-height: 1.2; }}
  .next {{ grid-column: 1 / -1; border: 1px solid #FED7AA; background: #FFF7ED; color: #9A3412;
          border-radius: 10px; padding: 8px 12px; font-size: 10pt; }}

  h2 {{ font-size: 12.5pt; color: {GREEN_DARK}; margin: 16px 0 6px; padding-bottom: 4px;
       border-bottom: 2px solid {GREEN}; }}
  .legend {{ font-size: 9pt; color: {TEXT_MUTED}; margin: 2px 0 8px; }}
  .legend .chip {{ margin-left: 4px; }}

  table {{ width: 100%; border-collapse: collapse; }}
  .overview td {{ padding: 4px 6px; border-bottom: 1px solid #EEF2F6; vertical-align: middle; font-size: 9.5pt; }}
  .overview tr:nth-child(even) td {{ background: #F8FAFC; }}
  .overview tr {{ break-inside: avoid; }}
  .ph-badge {{ display: inline-block; min-width: 34px; text-align: center; color: #fff; font-weight: 800;
              border-radius: 6px; padding: 1px 6px; font-size: 9.5pt; }}
  .ph-name {{ font-weight: 700; }}
  .num {{ white-space: nowrap; color: {TEXT_MUTED}; text-align: center; direction: ltr; }}
  .bar-cell {{ width: 30%; }}
  .bar {{ height: 9px; border-radius: 6px; background: #E2E8F0; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 6px; }}

  .chip {{ display: inline-block; border: 1px solid; border-radius: 999px; padding: 1px 9px;
          font-size: 8.5pt; font-weight: 700; white-space: nowrap; }}
  .chip-date {{ font-weight: 600; margin-right: 6px; direction: ltr; unicode-bidi: isolate; }}

  .phase {{ margin-top: 14px; border: 1px solid var(--c); border-radius: 12px; overflow: hidden;
           background: #fff; }}
  .phase-head {{ color: #fff; display: flex; align-items: center; gap: 12px; padding: 9px 14px;
                break-after: avoid; }}
  .phase-num {{ background: rgba(255,255,255,.2); border: 1.5px solid rgba(255,255,255,.7);
               border-radius: 9px; min-width: 44px; height: 40px; display: flex; align-items: center;
               justify-content: center; font-size: 15pt; font-weight: 800; padding: 0 6px; }}
  .phase-title {{ flex: 1; }}
  .phase-title .t {{ font-size: 13pt; font-weight: 800; }}
  .phase-title .g {{ font-size: 9pt; opacity: .93; }}
  .phase-prog {{ width: 120px; text-align: center; }}
  .pp-num {{ font-size: 10pt; font-weight: 800; direction: ltr; }}
  .pp-bar {{ height: 7px; border-radius: 5px; background: rgba(255,255,255,.3); overflow: hidden; margin-top: 3px; }}
  .pp-bar div {{ height: 100%; background: #fff; border-radius: 5px; }}
  .phase-state {{ padding: 6px 14px; font-size: 9pt; color: {TEXT_MUTED};
                 background: color-mix(in srgb, var(--c) 7%, #fff); border-bottom: 1px solid #EEF2F6; }}

  .items .head-cell {{ padding: 0; border: none; text-align: right; font-weight: normal; }}
  .nw {{ white-space: nowrap; }}
  h2.details {{ break-before: page; }}
  .ltr {{ direction: ltr; unicode-bidi: isolate; }}
  .items .cols th {{ text-align: right; font-size: 8.5pt; color: {TEXT_MUTED}; font-weight: 700;
              padding: 4px 8px; border-bottom: 1px solid {BORDER}; }}
  .items td {{ padding: 6px 8px; border-bottom: 1px solid #EEF2F6; vertical-align: top; }}
  .items tr {{ break-inside: avoid; }}
  .items tbody tr:last-child td {{ border-bottom: none; }}
  .items .idx {{ font-weight: 800; text-align: center; }}
  .items .st {{ text-align: center; }}
  .item-name {{ font-weight: 700; font-size: 10pt; }}
  .detail {{ font-size: 8.8pt; color: {TEXT_MUTED}; }}
  .note {{ font-size: 8.8pt; color: #9A3412; margin-top: 2px; }}
  .row-done td {{ background: #F6FDF8; }}
  .row-next td {{ background: #FFFBF5; }}
  .row-doing td {{ background: #F5F9FF; }}

  .foot {{ margin-top: 14px; font-size: 8.5pt; color: {TEXT_MUTED}; border-top: 1px solid {BORDER};
          padding-top: 6px; }}
  code {{ direction: ltr; unicode-bidi: isolate; font-family: Consolas, monospace; font-size: 8.5pt;
         background: {BG_CONTENT}; padding: 0 4px; border-radius: 4px; }}
</style>
</head>
<body>

<div class="cover">
  <div class="ttl">
    <h1>{esc(data["title"])}</h1>
    <div class="sub">{rich(data.get("intro"))}</div>
    <div class="sub">آخر تحديث: <b style="direction:ltr;unicode-bidi:isolate">{esc(data["updated"])}</b></div>
  </div>
  <div class="brand"><b>{esc(data["brand"])}</b><span>للحلول البرمجية</span></div>
</div>

<div class="summary">
  <div class="ring"><div class="ring-in"><b>{pct:.0f}%</b><span>نسبة التقدّم</span></div></div>
  <div class="tiles">
    <div class="tile"><div class="k">البنود اللي خلصت</div><div class="v" style="color:#087443">{counts["done"]} <small style="font-size:10pt;color:{TEXT_MUTED}">من {total}</small></div></div>
    <div class="tile"><div class="k">المراحل اللي خلصت</div><div class="v" style="color:{GREEN}">{phases_done} <small style="font-size:10pt;color:{TEXT_MUTED}">من {len(phases)}</small></div></div>
    <div class="tile"><div class="k">شغالين فيه / الجاية</div><div class="v" style="color:#9A3412">{counts["doing"] + counts["next"]}</div></div>
    <div class="tile"><div class="k">لسه</div><div class="v" style="color:{TEXT_MUTED}">{counts["todo"]}</div></div>
    <div class="next">👉 <b>الخطوة الجاية:</b> {next_html}</div>
  </div>
</div>

<h2>نظرة سريعة على المراحل</h2>
<div class="legend">الحالات: {legend}</div>
<table class="overview"><tbody>{''.join(overview_rows)}</tbody></table>

<h2 class="details">المراحل بالتفصيل</h2>
{''.join(sections)}

<div class="foot">
  الملف ده بيتولّد من <code>docs/plan/plan_data.json</code> بالسكريبت <code>docs/plan/build_plan.py</code>،
  وبيتحدّث أول بأول: أول ما بند يخلص بيتكتب قصاده «تم التنفيذ ✅» والتاريخ.
</div>

</body>
</html>
"""


def find_edge() -> Path:
    for p in EDGE_CANDIDATES:
        if p.exists():
            return p
    found = shutil.which("msedge")
    if found:
        return Path(found)
    sys.exit("مش لاقي Microsoft Edge عشان يطبع الـ PDF.")


def print_pdf(html_path: Path, pdf_path: Path) -> None:
    edge = find_edge()
    with tempfile.TemporaryDirectory(prefix="itec_plan_") as tmp:
        tmp_pdf = Path(tmp) / "plan.pdf"  # اسم إنجليزي مؤقت، وبعدين ننقله للاسم العربي
        cmd = [
            str(edge), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-pdf-header-footer", f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--print-to-pdf={tmp_pdf}", html_path.as_uri(),
        ]
        subprocess.run(cmd, check=False, capture_output=True, timeout=120)
        if not tmp_pdf.exists() or tmp_pdf.stat().st_size == 0:
            sys.exit("Edge ماطلّعش الـ PDF.")
        shutil.copyfile(tmp_pdf, pdf_path)


def main() -> None:
    data = load()
    HTML_FILE.write_text(render(data), encoding="utf-8")
    print_pdf(HTML_FILE, PDF_FILE)
    items = [it for ph in data["phases"] for it in ph["items"]]
    done = sum(1 for it in items if it["status"] == "done")
    print(f"تمام: {HTML_FILE.name} + {PDF_FILE.name} — {done}/{len(items)} بند خلصوا "
          f"({100 * done / len(items):.0f}%)")


if __name__ == "__main__":
    main()
