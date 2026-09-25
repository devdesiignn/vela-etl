"""Run the Azure Document Intelligence adapter over a range of receipt
images and write one result line per receipt.

Unlike scripts/sweep_receipts.py this calls a paid third-party API and
uploads each photo to Microsoft. It needs AZURE_DOC_INTEL_ENDPOINT and
AZURE_DOC_INTEL_KEY (see .env.example) and it consumes F0 tier quota.

Usage: uv run python scripts/sweep_receipts_azure.py <start> <end>
"""

import os
import sys

from etl.config import get_settings
from etl.extract.adapters.azure_document_intelligence_adapter import (
    AzureDocumentIntelligenceAdapter,
)
from etl.extract.types import ExtractionResult

settings = get_settings()
adapter = AzureDocumentIntelligenceAdapter(
    settings.azure_doc_intel_endpoint, settings.azure_doc_intel_key
)
receipt_dir = r"C:\Users\Muizzz\Desktop\receipt-data"
all_files = sorted(os.listdir(receipt_dir))

start = int(sys.argv[1])
end = int(sys.argv[2])

out_path = f"debug_sweep_azure_{start}_{end}.txt"
with open(out_path, "w", encoding="utf-8") as out:
    for fname in all_files[start:end]:
        path = os.path.join(receipt_dir, fname)
        with open(path, "rb") as handle:
            image = handle.read()
        try:
            result = adapter.extract(image)
        except Exception as e:  # noqa: BLE001
            line = f"{fname}: CRASHED {type(e).__name__}: {e}"
        else:
            if isinstance(result, ExtractionResult):
                receipt = result.candidate.receipt
                item_sum = round(
                    sum(i.line_total for i in result.candidate.line_items), 2
                )
                line = (
                    f"{fname}: OK store={result.candidate.store.name!r} "
                    f"date={receipt.date} total={receipt.total} "
                    f"items={len(result.candidate.line_items)} sum={item_sum}"
                )
            else:
                line = f"{fname}: REVIEW {result.extractor_notes}"
        out.write(line + "\n")
        out.flush()
