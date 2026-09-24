import os
import sys

from etl.extract.adapters.rapidocr_adapter import RapidOcrAdapter
from etl.extract.types import ExtractionResult

adapter = RapidOcrAdapter()
receipt_dir = r"C:\Users\Muizzz\Desktop\receipt-data"
all_files = sorted(os.listdir(receipt_dir))

start = int(sys.argv[1])
end = int(sys.argv[2])
batch = all_files[start:end]

out_path = f"debug_sweep_batch_{start}_{end}.txt"
with open(out_path, "w", encoding="utf-8") as out:
    for fname in batch:
        path = os.path.join(receipt_dir, fname)
        with open(path, "rb") as f:
            image = f.read()
        try:
            result = adapter.extract(image)
        except Exception as e:  # noqa: BLE001
            line = f"{fname}: CRASHED {type(e).__name__}: {e}"
        else:
            if isinstance(result, ExtractionResult):
                r = result.candidate.receipt
                line = (
                    f"{fname}: OK store={result.candidate.store.name!r} "
                    f"date={r.date} total={r.total} "
                    f"items={len(result.candidate.line_items)}"
                )
            else:
                line = f"{fname}: REVIEW {result.extractor_notes}"
        out.write(line + "\n")
        out.flush()
