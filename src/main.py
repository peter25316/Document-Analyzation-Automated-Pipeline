
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

# -------- PDF text extraction (pdfminer) --------
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer

# -------- Optional OCR imports (lazy) --------
def _lazy_import_ocr():
    try:
        import pytesseract  # requires system tesseract-ocr
    except Exception as e:
        raise RuntimeError("pytesseract not available. Install tesseract-ocr system package and 'pip install pytesseract'.") from e
    # Prefer pypdfium2 to rasterize pages (fast, cross-platform)
    try:
        import pypdfium2 as pdfium
        return pytesseract, pdfium
    except Exception:
        # Fallback to pdf2image if pypdfium2 not available
        try:
            from pdf2image import convert_from_path
            return pytesseract, convert_from_path
        except Exception as e:
            raise RuntimeError("No PDF rasterizer available. Install 'pypdfium2' or 'pdf2image' + system poppler.") from e

# -------- Helpers --------
def list_pdfs(input_dir: Path) -> List[Path]:
    return sorted([p for p in input_dir.rglob("*.pdf") if p.is_file()])

def extract_pdf_text_per_page(pdf_path: Path) -> Dict[str, Any]:
    """Return {'pages': [{'page_number': int, 'text': str}, ...]} or {'error': str}"""
    pages = []
    try:
        for pageno, page_layout in enumerate(extract_pages(str(pdf_path))):
            lines = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    lines.append(element.get_text())
            page_text = "".join(lines)
            pages.append({"page_number": pageno+1, "text": page_text})
    except Exception as e:
        return {"error": f"pdfminer parse failed: {e}"}
    return {"pages": pages}

def ocr_pdf_pages(pdf_path: Path) -> List[str]:
    """OCR all pages -> list of strings (one per page). Only used if --ocr passed."""
    pytesseract, raster = _lazy_import_ocr()
    texts = []
    # Prefer pypdfium2 path
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(pdf_path))
        for i in range(len(pdf)):
            page = pdf.get_page(i)
            pil = page.render(scale=2).to_pil()  # 2x scale for better OCR
            txt = pytesseract.image_to_string(pil)
            texts.append(txt)
        return texts
    except Exception:
        pass
    # Fallback: pdf2image
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), dpi=300)
        for img in images:
            txt = pytesseract.image_to_string(img)
            texts.append(txt)
        return texts
    except Exception as e:
        raise RuntimeError(f"OCR rasterization failed: {e}")

def find_candidate_blocks(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Identify pages likely to contain CUP/solar content."""
    cands = []
    for p in pages:
        txt = p.get("text") or ""
        if not txt.strip():
            continue
        t = re.sub(r"[ \t]+", " ", txt)
        if re.search(r"(Conditional\s+Use\s+Permit|Special\s+Use\s+Permit|Solar\b|Photovoltaic|2232)", t, re.I):
            cands.append({"page": p["page_number"], "text": t})
    return cands

def extract_fields(text: str) -> Dict[str, Any]:
    """Extract structured fields from a candidate text block."""
    fields = {}
    # Applicant / Project
    m = re.search(r"\b(Applicant|Application|Project)\s*[:\-]\s*([A-Z0-9\-\&\.,' ]{5,120})", text, re.I)
    if m:
        fields["project_or_applicant"] = m.group(2).strip()
    # Capacity (MW)
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*MW\b", text, re.I)
    if m:
        fields["mw"] = m.group(1)
    # Acres
    m = re.search(r"(\d{1,4}(?:\.\d+)?)\s*acre[s]?\b", text, re.I)
    if m:
        fields["acres"] = m.group(1)
    # Address / Parcel / PIN
    m = re.search(r"\b(Location|Address|Parcel|Tax\s*Map|GPIN|PIN)\s*[:\-]\s*([^\n]{5,160})", text, re.I)
    if m:
        fields["location"] = m.group(2).strip()
    # Outcome phrases
    m = re.search(r"\b(approved|denied|recommend(?:ed)?\s+approval|recommend(?:ed)?\s+denial)\b", text, re.I)
    if m:
        fields["outcome_phrase"] = m.group(0)
    # Vote lines
    v = re.search(r"(roll\s*call\s*vote|vote\s*(?:was\s*)?(?:taken)?(?:\s*and\s*the\s*result[s]?\s*were)?)\s*[:\-]?\s*[^\n]{0,140}", text, re.I)
    if v:
        fields["vote_line"] = v.group(0).strip()
    # Ayes/Nays lists
    ayes = re.search(r"\b(Ayes?|Yeas?)\s*[:\-]\s*([^\n]+)", text, re.I)
    nays = re.search(r"\b(Nays?|Nos?)\s*[:\-]\s*([^\n]+)", text, re.I)
    if ayes:
        fields["ayes"] = ayes.group(2).strip()
    if nays:
        fields["nays"] = nays.group(2).strip()
    # Reasons / concerns
    reasons = []
    for m in re.finditer(r"([^.]{0,140}\b(concern|because|due to|reason|finding|finding[s]? of fact)[^.]{0,140})\.", text, re.I):
        reasons.append(m.group(0).strip())
        if len(reasons) >= 3:
            break
    if reasons:
        fields["decision_factor_snippets"] = reasons
    return fields

def guess_meeting_date_from_name(name: str) -> str:
    m = re.search(r"(\d{4}[-_/\.]\d{1,2}[-_/\.]\d{1,2})", name)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2}[-_/\.]\d{1,2}[-_/\.]\d{2,4})", name)
    if m:
        return m.group(1)
    return ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", required=True, help="Directory containing PDFs (recursively processed)")
    ap.add_argument("--out_csv", required=True, help="Output CSV path")
    ap.add_argument("--out_snippets", required=True, help="Output JSONL path with snippets for audit")
    ap.add_argument("--ocr", action="store_true", help="Enable OCR fallback for pages with little/no text")
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    out_csv = Path(args.out_csv)
    out_snippets = Path(args.out_snippets)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_snippets.parent.mkdir(parents=True, exist_ok=True)

    pdf_paths = list_pdfs(input_dir)
    if not pdf_paths:
        print("No PDFs found.", file=sys.stderr)
        sys.exit(1)

    # Lazy-load OCR pieces if requested
    if args.ocr:
        try:
            _ = _lazy_import_ocr()
            print("[OCR] OCR modules available.")
        except Exception as e:
            print(f"[OCR] {e}", file=sys.stderr)
            sys.exit(2)

    rows = []
    with out_snippets.open("w", encoding="utf-8") as snipf:
        for idx, pdf_path in enumerate(pdf_paths, start=1):
            try:
                res = extract_pdf_text_per_page(pdf_path)
                pages = res.get("pages", [])
                # OCR fallback per page if requested and text is weak
                if args.ocr and (not pages or sum(len(p.get('text') or "") for p in pages) < 500):
                    try:
                        ocr_texts = ocr_pdf_pages(pdf_path)
                        pages = [{"page_number": i+1, "text": ocr_texts[i] if i < len(ocr_texts) else ""} for i in range(len(ocr_texts))]
                    except Exception as e:
                        # keep existing pages if OCR fails
                        pass

                cands = find_candidate_blocks(pages)
                if not cands:
                    continue

                meeting_date = guess_meeting_date_from_name(pdf_path.name)
                for blk in cands:
                    fields = extract_fields(blk["text"] or "")
                    if not fields:
                        continue
                    record = {
                        "filename": pdf_path.name,
                        "relative_path": str(pdf_path.relative_to(input_dir)),
                        "meeting_date_guess": meeting_date,
                        "page": blk["page"],
                    }
                    record.update(fields)
                    rows.append(record)
                    # snippets
                    snip = {
                        "filename": pdf_path.name,
                        "page": blk["page"],
                        "text_snippet": (blk["text"] or "")[:1000]
                    }
                    snipf.write(json.dumps(snip) + "\n")

            except Exception as e:
                # continue with next file
                continue

    import pandas as pd
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    print(f"Wrote {len(rows)} records to {out_csv}")
    print(f"Snippets written to {out_snippets}")

if __name__ == "__main__":
    main()
