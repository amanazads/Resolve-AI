import csv
import io
import logging
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

class FileParser:
    """
    Robust parser for CSV and XLSX files.
    Tolerates diverse encodings, delimiter variations, empty rows, and malformed structures.
    """

    @staticmethod
    def parse_file(content: bytes, filename: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        Parses raw bytes from CSV or XLSX and returns (headers, rows).
        Each row contains raw header -> raw value mapping plus '_source_row'.
        """
        lower_name = filename.lower()
        if lower_name.endswith(".xlsx") or lower_name.endswith(".xlsm") or lower_name.endswith(".xltx"):
            return FileParser.parse_xlsx(content)
        return FileParser.parse_csv(content)

    @staticmethod
    def parse_csv(content: bytes) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        Parses CSV with encoding detection and delimiter sniffing.
        """
        text = None
        for enc in ["utf-8-sig", "utf-8", "latin-1", "cp1252"]:
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            text = content.decode("utf-8", errors="replace")

        # Strip null bytes if present
        text = text.replace("\x00", "")

        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return [], []

        # Sniff delimiter
        sample = "\n".join(lines[:15])
        delimiter = ","
        try:
            sniffer = csv.Sniffer()
            dialect = sniffer.sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except Exception:
            # Fallback delimiter inference
            first_line = lines[0]
            counts = {d: first_line.count(d) for d in [",", ";", "\t", "|"]}
            best = max(counts, key=counts.get)
            if counts[best] > 0:
                delimiter = best

        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        raw_rows = []
        for row in reader:
            if any(cell.strip() for cell in row):
                raw_rows.append([cell.strip() for cell in row])

        if not raw_rows:
            return [], []

        raw_headers = raw_rows[0]
        headers = [h if h else f"col_{i+1}" for i, h in enumerate(raw_headers)]

        rows = []
        for idx, row in enumerate(raw_rows[1:], start=2):
            row_dict = {}
            for col_idx, col_name in enumerate(headers):
                val = row[col_idx] if col_idx < len(row) else ""
                row_dict[col_name] = val
            row_dict["_source_row"] = idx
            rows.append(row_dict)

        logger.info(f"Parsed CSV: {len(headers)} columns, {len(rows)} data rows.")
        return headers, rows

    @staticmethod
    def parse_xlsx(content: bytes) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        Parses XLSX workbooks using openpyxl streaming reader.
        """
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = wb.active
        if not sheet:
            return [], []

        raw_rows = []
        for row in sheet.iter_rows(values_only=True):
            # Check if non-empty
            if row and any(cell is not None and str(cell).strip() != "" for cell in row):
                cleaned_cells = []
                for cell in row:
                    if cell is None:
                        cleaned_cells.append("")
                    elif isinstance(cell, float) and cell.is_integer():
                        cleaned_cells.append(str(int(cell)))
                    else:
                        cleaned_cells.append(str(cell).strip())
                raw_rows.append(cleaned_cells)

        wb.close()

        if not raw_rows:
            return [], []

        raw_headers = raw_rows[0]
        headers = [h if h else f"col_{i+1}" for i, h in enumerate(raw_headers)]

        rows = []
        for idx, row in enumerate(raw_rows[1:], start=2):
            row_dict = {}
            for col_idx, col_name in enumerate(headers):
                val = row[col_idx] if col_idx < len(row) else ""
                row_dict[col_name] = val
            row_dict["_source_row"] = idx
            rows.append(row_dict)

        logger.info(f"Parsed XLSX: {len(headers)} columns, {len(rows)} data rows.")
        return headers, rows
