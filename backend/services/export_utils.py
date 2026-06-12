"""
DataExportUtil — Convert standardised product dict lists to download-ready
byte streams (JSON / Excel).

Usage::

    from services.export_utils import DataExportUtil

    buf = DataExportUtil.export_to_excel_bytes(products)
    # buf is a BytesIO instance ready for send_file(..., as_attachment=True)
"""

import io
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class DataExportUtil:
    """Collection of static helpers that transform product data."""

    @staticmethod
    def export_to_json_bytes(data: list[dict[str, Any]]) -> io.BytesIO:
        """Serialize *data* as pretty-printed UTF-8 JSON in a BytesIO buffer."""
        buf = io.BytesIO()
        try:
            encoded = json.dumps(data, ensure_ascii=False, indent=2)
            buf.write(encoded.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            logger.error("JSON serialization failed: %s", exc)
            raise ValueError(f"无法生成 JSON 文件: {exc}") from exc
        buf.seek(0)
        return buf

    @staticmethod
    def export_to_excel_bytes(
        data: list[dict[str, Any]],
        sheet_name: str = "Bestsellers",
    ) -> io.BytesIO:
        """Convert *data* to an in-memory Excel workbook (`.xlsx`).

        Requires ``pandas`` and ``openpyxl`` to be installed.
        """
        try:
            import pandas  # noqa: F811 – uses pandas for DataFrame
        except ImportError:
            raise ImportError(
                "pandas is required for Excel export. Install with: pip install pandas openpyxl"
            )

        if not data:
            # Return an empty workbook with just headers
            df = pandas.DataFrame(
                columns=[
                    "category",
                    "rank",
                    "product_name",
                    "price",
                    "identity_code",
                    "rating_score",
                    "review_count",
                    "source_url",
                ]
            )
        else:
            df = pandas.DataFrame(data)
            # Ensure column order
            columns = [
                "category",
                "rank",
                "product_name",
                "price",
                "identity_code",
                "rating_score",
                "review_count",
                "source_url",
            ]
            df = df.reindex(columns=columns)

        # Rename columns to Chinese for the Excel header row
        df.columns = [
            "行业大类",
            "畅销榜排名",
            "产品名称",
            "价格",
            "UPC/ASIN",
            "评分",
            "评论数",
            "来源链接",
        ]

        buf = io.BytesIO()
        with pandas.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)

        buf.seek(0)
        return buf
