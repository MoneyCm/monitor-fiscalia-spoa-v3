from __future__ import annotations

import base64
import io
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import DATASETS, METHODOLOGY_NOTE, OFFICIAL_STATS_URL
from .utils import sha256_file


# Paleta institucional alineada con plataforma-seguridad (NewsletterPreview):
# azul #0033A0, amarillo #FFC000, verde éxito #00A34F, rojo alerta #E53E3E.
COLORS = {"procesos": "#0033A0", "victimas": "#B7791F", "procesados": "#007A3D"}
COLOR_AZUL = "#0033A0"
COLOR_AMARILLO = "#FFC000"
COLOR_VERDE = "#00A34F"
COLOR_ROJO = "#E53E3E"


@lru_cache(maxsize=1)
def _escudo_data_uri() -> str:
    """Escudo de Jamundí embebido para que el HTML/PDF sea autocontenido."""
    candidates = [
        Path(__file__).parent / "assets" / "escudo_jamundi_small.png",
        Path(__file__).parent / "assets" / "escudo_jamundi.png",
    ]
    for path in candidates:
        try:
            return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
    return ""


def _monthly_chart(indicators: Dict[str, Any]) -> str:
    figure, axes = plt.subplots(1, 3, figsize=(9.2, 3.0))
    figure.patch.set_facecolor("white")
    for axis, (key, dataset) in zip(axes, indicators["datasets"].items()):
        axis.set_facecolor("white")
        axis.plot(
            [item["month"] for item in dataset["monthly"]],
            [item["current"] for item in dataset["monthly"]],
            marker="o",
            linewidth=2.5,
            markersize=4,
            color=COLORS[key],
        )
        for item in dataset["monthly"]:
            axis.annotate(str(item["current"]), (item["month"], item["current"]), xytext=(0, 5), textcoords="offset points", ha="center", fontsize=7)
        axis.set_title(key.capitalize(), color=COLORS[key], fontsize=11, fontweight="bold")
        axis.set_xlabel("Mes", fontsize=8)
        axis.set_ylabel("Únicos", fontsize=8)
        axis.tick_params(labelsize=8, colors="#4A5568")
        axis.grid(axis="y", alpha=0.25, color="#E2E8F0")
        for spine in axis.spines.values():
            spine.set_color("#E2E8F0")
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
        "escudo_data_uri": _escudo_data_uri(),
        "color_azul": COLOR_AZUL,
        "color_amarillo": COLOR_AMARILLO,
        "color_verde": COLOR_VERDE,
        "color_rojo": COLOR_ROJO,
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
    styles.add(ParagraphStyle(name="SiscTitle", parent=styles["Title"], textColor=colors.HexColor("#0033A0"), fontSize=20, leading=22))
    styles.add(ParagraphStyle(name="SiscH2", parent=styles["Heading2"], textColor=colors.HexColor("#0033A0"), spaceBefore=8, spaceAfter=5))
    styles.add(ParagraphStyle(name="SiscSmall", parent=styles["BodyText"], fontSize=7.5, leading=9, textColor=colors.HexColor("#52677a")))
    styles.add(ParagraphStyle(name="SiscAlert", parent=styles["BodyText"], backColor=colors.HexColor("#FFFAF0"), borderColor=colors.HexColor("#FFC000"), borderWidth=1, borderPadding=6, spaceAfter=4))
    styles.add(ParagraphStyle(name="SiscBrand", parent=styles["BodyText"], textColor=colors.HexColor("#0033A0"), fontSize=9, leading=11, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="SiscTableHeader", parent=styles["SiscSmall"], textColor=colors.white, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="SiscCard", parent=styles["BodyText"], alignment=TA_CENTER, fontSize=9, leading=13, textColor=colors.HexColor("#0033A0")))
    styles.add(ParagraphStyle(name="SiscHeadTitle", parent=styles["BodyText"], textColor=colors.HexColor("#0033A0"), fontSize=13, leading=14, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="SiscHeadSub", parent=styles["BodyText"], textColor=colors.HexColor("#718096"), fontSize=7, leading=9, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="SiscHeadRight", parent=styles["BodyText"], textColor=colors.HexColor("#4A5568"), fontSize=7.5, leading=10, alignment=2))
    story = []

    def safe_text(element) -> str:
        return element.get_text(" ", strip=True).replace("—", "-").replace("–", "-").replace("‑", "-")

    def _data_image(source: str):
        """Construye Image reportlab con tamaño según contenido.

        El escudo embebido (~120px) se renderiza pequeño; la gráfica mensual
        (ancha) ocupa el ancho útil. Antes todo se dibujaba a 178mm y el
        escudo salía gigante y pixelado en el fallback de Windows.
        """
        from PIL import Image as _PILImage

        raw = base64.b64decode(source.split(",", 1)[1])
        try:
            with _PILImage.open(io.BytesIO(raw)) as probe:
                width_px, height_px = probe.size
        except OSError:
            width_px, height_px = (800, 400)
        if max(width_px, height_px) <= 260 and height_px:
            target_w = 26 * mm
            target_h = target_w * height_px / width_px
            return Image(io.BytesIO(raw), width=target_w, height=target_h)
        return Image(io.BytesIO(raw), width=178 * mm, height=58 * mm)

    for element in soup.body.find_all(["h1", "h2", "h3", "p", "div", "table", "img", "header"], recursive=True):
        if element.find_parent(["table"]):
            continue
        classes = element.get("class") or []
        if element.name != "header" and element.find_parent("header"):
            continue
        if element.name == "header":
            left = element.select_one(".head-left")
            right = element.select_one(".head-right")
            left_flow = []
            if left is not None:
                image = left.find("img")
                if image is not None and (image.get("src", "") or "").startswith("data:image"):
                    try:
                        left_flow.append(_data_image(image.get("src", "")))
                    except (ValueError, OSError):
                        pass
                title = left.select_one(".head-title")
                if title is not None:
                    left_flow.append(Paragraph(safe_text(title), styles["SiscHeadTitle"]))
                subtitle = left.select_one(".head-sub")
                if subtitle is not None:
                    left_flow.append(Paragraph(safe_text(subtitle), styles["SiscHeadSub"]))
            right_flow = []
            if right is not None:
                for selector in (".sec", ".obs", ".cut"):
                    node = right.select_one(selector)
                    if node is not None:
                        right_flow.append(Paragraph(safe_text(node), styles["SiscHeadRight"]))
            header_table = Table(
                [[left_flow or [Paragraph("", styles["BodyText"])], right_flow or [Paragraph("", styles["BodyText"])]]],
                colWidths=[110 * mm, 68 * mm],
            )
            header_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, 0), 3, colors.HexColor("#FFC000")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ]))
            story.extend([header_table, Spacer(1, 3 * mm)])
            continue
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
                    ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#E2E8F0")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.8, colors.HexColor("#E2E8F0")),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
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
                try:
                    story.extend([_data_image(source), Spacer(1, 2 * mm)])
                except (ValueError, OSError):
                    continue
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
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
                if has_header:
                    commands.extend([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0033A0")),
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
