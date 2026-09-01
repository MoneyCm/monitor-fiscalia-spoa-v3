from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import DATASETS, METHODOLOGY_NOTE, OFFICIAL_STATS_URL
from .utils import sha256_file


COLORS = {"procesos": "#194b77", "victimas": "#c49a35", "procesados": "#2f7d58"}


def _monthly_chart(indicators: Dict[str, Any]) -> str:
    figure, axes = plt.subplots(1, 3, figsize=(9.2, 3.0))
    for axis, (key, dataset) in zip(axes, indicators["datasets"].items()):
        axis.plot(
            [item["month"] for item in dataset["monthly"]],
            [item["current"] for item in dataset["monthly"]],
            marker="o",
            linewidth=2,
            color=COLORS[key],
        )
        for item in dataset["monthly"]:
            axis.annotate(str(item["current"]), (item["month"], item["current"]), xytext=(0, 5), textcoords="offset points", ha="center", fontsize=7)
        axis.set_title(key.capitalize(), color=COLORS[key], fontsize=11, fontweight="bold")
        axis.set_xlabel("Mes")
        axis.set_ylabel("Únicos", fontsize=8)
        axis.tick_params(labelsize=8)
        axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=150, transparent=False)
    plt.close(figure)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(("html", "xml")),
    )


def render_report_html(context: Dict[str, Any]) -> str:
    enriched = {
        **context,
        "datasets_specs": DATASETS,
        "methodology_note": METHODOLOGY_NOTE,
        "official_stats_url": OFFICIAL_STATS_URL,
        "monthly_chart": _monthly_chart(context["indicators"]),
        "generated_label": datetime.fromisoformat(context["generated_at"]).strftime("%d/%m/%Y %H:%M"),
    }
    return _environment().get_template("bulletin.html.j2").render(**enriched)


def render_email_html(context: Dict[str, Any]) -> str:
    enriched = {
        **context,
        "datasets_specs": DATASETS,
        "methodology_note": METHODOLOGY_NOTE,
    }
    return _environment().get_template("email.html.j2").render(**enriched)


def generate_pdf(html: str, pdf_path: Path, *, base_url: Path | None = None) -> str:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from weasyprint import HTML

        HTML(string=html, base_url=str(base_url or Path.cwd())).write_pdf(str(pdf_path))
    except (ImportError, OSError):
        _generate_pdf_reportlab(html, pdf_path)
    return sha256_file(pdf_path)


def _generate_pdf_reportlab(html: str, pdf_path: Path) -> None:
    """Fallback portable para Windows cuando Pango/GTK no está disponible."""
    from bs4 import BeautifulSoup
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    soup = BeautifulSoup(html, "html.parser")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SiscTitle", parent=styles["Title"], textColor=colors.HexColor("#123c62"), fontSize=20, leading=22))
    styles.add(ParagraphStyle(name="SiscH2", parent=styles["Heading2"], textColor=colors.HexColor("#194b77"), spaceBefore=8, spaceAfter=5))
    styles.add(ParagraphStyle(name="SiscSmall", parent=styles["BodyText"], fontSize=7.5, leading=9, textColor=colors.HexColor("#52677a")))
    styles.add(ParagraphStyle(name="SiscAlert", parent=styles["BodyText"], backColor=colors.HexColor("#fff7e0"), borderColor=colors.HexColor("#c49a35"), borderWidth=1, borderPadding=6, spaceAfter=4))
    styles.add(ParagraphStyle(name="SiscBrand", parent=styles["BodyText"], textColor=colors.HexColor("#194b77"), fontSize=9, leading=11, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="SiscTableHeader", parent=styles["SiscSmall"], textColor=colors.white, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="SiscCard", parent=styles["BodyText"], alignment=TA_CENTER, fontSize=9, leading=13, textColor=colors.HexColor("#123c62")))
    story = []

    def safe_text(element) -> str:
        return element.get_text(" ", strip=True).replace("—", "-").replace("–", "-").replace("‑", "-")

    for element in soup.body.find_all(["h1", "h2", "h3", "p", "div", "table", "img"], recursive=True):
        if element.find_parent(["table"]):
            continue
        classes = element.get("class") or []
        if element.find_parent("div", class_="cards") and "cards" not in classes:
            continue
        if "card" in classes:
            continue
        if element.name == "h1":
            story.extend([Paragraph(safe_text(element), styles["SiscTitle"]), Spacer(1, 3 * mm)])
        elif element.name == "h2":
            if story:
                story.append(Spacer(1, 2 * mm))
            story.append(Paragraph(safe_text(element), styles["SiscH2"]))
        elif element.name == "h3":
            story.append(Paragraph(safe_text(element), styles["Heading3"]))
        elif element.name == "p" and not element.find_parent("div", class_=["alert", "method"]):
            style = styles["SiscSmall"] if "small" in (element.get("class") or []) else styles["BodyText"]
            story.extend([Paragraph(safe_text(element), style), Spacer(1, 1.5 * mm)])
        elif element.name == "div" and "brand" in classes:
            story.append(Paragraph(safe_text(element), styles["SiscBrand"]))
        elif element.name == "div" and "subtitle" in classes:
            story.append(Paragraph(safe_text(element), styles["SiscSmall"]))
        elif element.name == "div" and "cards" in classes:
            cards = [[Paragraph(safe_text(card), styles["SiscCard"]) for card in element.find_all("div", class_="card", recursive=False)]]
            if cards and cards[0]:
                card_table = Table(cards, colWidths=[178 * mm / len(cards[0])] * len(cards[0]))
                card_table.setStyle(TableStyle([
                    ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d7e0e9")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.8, colors.HexColor("#d7e0e9")),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9fbfd")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]))
                story.extend([card_table, Spacer(1, 3 * mm)])
        elif element.name == "div" and ("alert" in (element.get("class") or []) or "method" in (element.get("class") or [])):
            story.append(Paragraph(safe_text(element), styles["SiscAlert"]))
        elif element.name == "img":
            source = element.get("src", "")
            if source.startswith("data:image/png;base64,"):
                raw = base64.b64decode(source.split(",", 1)[1])
                image = Image(io.BytesIO(raw), width=178 * mm, height=62 * mm)
                story.extend([image, Spacer(1, 2 * mm)])
        elif element.name == "table":
            table_rows = []
            for tr in element.find_all("tr"):
                cells = [
                    Paragraph(safe_text(cell), styles["SiscTableHeader"] if cell.name == "th" else styles["SiscSmall"])
                    for cell in tr.find_all(["th", "td"], recursive=False)
                ]
                if cells:
                    table_rows.append(cells)
            if table_rows:
                width = 178 * mm / max(len(row) for row in table_rows)
                has_header = bool(element.find("th"))
                table = Table(
                    table_rows,
                    colWidths=[width] * max(len(row) for row in table_rows),
                    repeatRows=1 if has_header else 0,
                    splitByRow=1,
                )
                commands = [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7e0e9")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
                if has_header:
                    commands.extend([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#194b77")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ])
                table.setStyle(TableStyle(commands))
                story.extend([table, Spacer(1, 2 * mm)])
        if element.name == "div" and "page-break" in (element.get("class") or []):
            story.append(PageBreak())

    def footer(canvas, document):
        canvas.saveState()
        canvas.setTitle("Boletín Fiscalía SPOA V3 - Observatorio del Delito de Jamundí")
        canvas.setAuthor("Secretaría de Seguridad y Convivencia - Alcaldía de Jamundí")
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawCentredString(A4[0] / 2, 8 * mm, f"SISC · Observatorio del Delito de Jamundí · {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(pdf_path), pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=12 * mm, bottomMargin=15 * mm)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
