---
name: PDF Report Generator
slug: pdf
version: 1.0.0
publisher: platform-team
tags: [pdf, documents, reportlab, pypdf, ocr, reporting]
verified: true
---

# PDF Report Generator

Generate polished, structured PDF reports from data, text, or analysis. Supports multi-page layouts, tables, charts, branded headers/footers, and OCR text extraction from existing PDFs.

## When to use this skill

- You need to produce a formatted PDF report from a dataset or analysis
- You want to extract text or tables from an existing PDF (OCR)
- You need to combine multiple PDFs or append pages
- You want a branded, shareable document (e.g. monthly KPI report, partner statement, compliance export)

## Tools required

- **Python** with `reportlab`, `pypdf`, `pytesseract` (optional for OCR), `pandas`
- **Tesseract** binary installed for OCR (`brew install tesseract` / `apt install tesseract-ocr`)

## Generating a PDF report

### Basic report with ReportLab

```python
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.units import mm

def build_report(output_path: str, title: str, rows: list[dict]) -> str:
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    story = []

    # Title
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 6*mm))

    # Table
    if rows:
        headers = list(rows[0].keys())
        data = [headers] + [[str(r[h]) for h in headers] for r in rows]
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),  # brand accent
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1,-1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FA")]),
            ("GRID",       (0, 0), (-1,-1), 0.5, colors.HexColor("#DEE2E6")),
            ("LEFTPADDING", (0,0),(-1,-1), 6),
            ("RIGHTPADDING",(0,0),(-1,-1), 6),
            ("TOPPADDING",  (0,0),(-1,-1), 4),
            ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ]))
        story.append(t)

    doc.build(story)
    return output_path
```

### Branded header / footer

```python
from reportlab.platypus import SimpleDocTemplate
from reportlab.lib.units import mm

BRAND_NAME = "Acme Corp"
BRAND_COLOR = "#2563EB"

def _on_page(canvas, doc):
    canvas.saveState()
    # Header bar
    canvas.setFillColor(BRAND_COLOR)
    canvas.rect(0, doc.pagesize[1] - 15*mm, doc.pagesize[0], 15*mm, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.setFillColorRGB(1, 1, 1)
    canvas.drawString(20*mm, doc.pagesize[1] - 10*mm, BRAND_NAME)
    # Footer
    canvas.setFillColorRGB(0.4, 0.4, 0.4)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(doc.pagesize[0] - 20*mm, 10*mm, f"Page {doc.page}")
    canvas.restoreState()

doc = SimpleDocTemplate("report.pdf", pagesize=A4)
doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
```

## Extracting text from a PDF (OCR)

```python
import pytesseract
from pdf2image import convert_from_path

def extract_text_from_pdf(pdf_path: str) -> str:
    images = convert_from_path(pdf_path, dpi=200)
    return "\n\n--- PAGE BREAK ---\n\n".join(
        pytesseract.image_to_string(img) for img in images
    )
```

## Merging / splitting PDFs

```python
from pypdf import PdfWriter, PdfReader

def merge_pdfs(paths: list[str], output: str) -> str:
    writer = PdfWriter()
    for p in paths:
        writer.append(p)
    writer.write(output)
    return output

def extract_pages(src: str, pages: list[int], output: str) -> str:
    reader = PdfReader(src)
    writer = PdfWriter()
    for i in pages:
        writer.add_page(reader.pages[i])
    writer.write(output)
    return output
```

## Common patterns

| Task | Approach |
|------|----------|
| Monthly KPI report | Query your data source → `pandas` DataFrame → `build_report()` |
| Partner statement PDF | Jinja2 HTML → `weasyprint` (alternative to ReportLab) |
| Extract invoice data | `pytesseract` OCR → parse with regex |
| Append compliance pages | `pypdf` merge |
| Branded report | Custom `_on_page` callback with your brand accent colour |

## IMPORTANT: Saving files in the workspace

When generating PDF code that will be run inside the workspace, you **must** follow these rules:

1. **Always save to `OUTPUT_DIR`** — this variable is pre-defined by the execution environment.  
   ```python
   import os, secrets
   filename = f"{secrets.token_hex(8)}_report.pdf"
   output_path = os.path.join(OUTPUT_DIR, filename)
   ```
2. **Never hardcode paths** such as `/app/outputs/`, `/tmp/`, `~/`, or any absolute path.
3. After the PDF is saved, print the filename so the user can confirm:  
   ```python
   print(f"PDF saved: {output_path}")
   ```
4. The workspace will **automatically detect and open** the PDF in the viewer once the code runs.

## Notes

- Always use `secrets.token_hex(8)` prefix when saving generated files to avoid path collisions.
- For large tables (>500 rows) use `TableStyle` with `splitByRow=1` to allow page breaks inside tables.
- ReportLab's default font is Helvetica. For Inter/custom fonts, register with `pdfmetrics.registerFont`.
- `weasyprint` is an HTML→PDF alternative that works well when you already have an HTML report.
