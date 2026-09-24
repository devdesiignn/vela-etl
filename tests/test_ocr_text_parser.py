from etl.extract.adapters.ocr_text_parser import parse

CLEAN_RECEIPT = """\
Supreme Pharmacy
12 Ikorodu Rd, Lagos
Tel: 0803-123-4567
Date: 2026-01-15
Invoice #INV-2026-0042

Paracetamol 500mg 2 x 150.00 = 300.00
Vitamin C 1 x 500.00 = 500.00

Subtotal 800.00
VAT 40.00
Total 840.00
"""

NO_EQUALS_RECEIPT = """\
Corner Store
5 Allen Ave, Ikeja
15/01/2026
Ref: TXN998877

Bread 1 x 200.00
Milk 2 x 350.00

Total 900.00
"""

MESSY_RECEIPT = """\
GROCERY MART
44 Awolowo Way, Lagos
www.grocerymart.example
info@grocerymart.example
Jan 15, 2026

Rice 5kg 1 x 3500.00 = 3500.00
Beans 2 x 800.00 = 1600.00
Cooking Oil 1 x 1200.00 = 1200.00

Sub-total 6300.00
Discount 300.00
Total 6000.00
"""

NO_DATE_NO_ITEMS = """\
some illegible garbage
????###
"""

SUBTOTAL_ONLY_RECEIPT = """\
Momrota Pharmacy
Ilorin, Kwara State
2025-09-12

Astyfer Cap 2 x 2700.00 = 5400.00
Folic Acid 1 x 950.00 = 950.00

SUB TOTAL 8350.00
"""

TOTAL_WITH_ITEM_COUNT_RECEIPT = """\
Momrota Pharmacy
Ilorin, Kwara State
2025-09-12

Astyfer Cap 2 x 2700.00 = 5400.00
Folic Acid 1 x 950.00 = 950.00

SUB TOTAL 2.00 6350.00
"""

NO_QTY_ITEM_RECEIPT = """\
Home Goods Store
9 Marina Rd, Lagos
2026-01-15

1 TONGUE SCRAPER 500.00
Sponge 250.00

Total 750.00
"""


def test_parses_clean_receipt_header_and_totals():
    result = parse(CLEAN_RECEIPT)

    assert result.store_name == "Supreme Pharmacy"
    assert result.store_address == "12 Ikorodu Rd, Lagos"
    assert result.phone is not None
    assert result.transaction_ref == "INV-2026-0042"
    assert result.date is not None
    assert result.date.isoformat() == "2026-01-15"
    assert result.subtotal == 800.00
    assert result.tax == 40.00
    assert result.total == 840.00


def test_parses_clean_receipt_line_items():
    result = parse(CLEAN_RECEIPT)

    assert len(result.line_items) == 2
    first = result.line_items[0]
    assert first.description == "Paracetamol 500mg"
    assert first.quantity == 2
    assert first.unit_price == 150.00
    assert first.line_total == 300.00


def test_parses_line_items_without_explicit_line_total():
    result = parse(NO_EQUALS_RECEIPT)

    assert result.total == 900.00
    assert len(result.line_items) == 2
    assert result.line_items[0].description == "Bread"
    assert result.line_items[0].line_total == 200.00
    assert result.date is not None
    assert result.date.isoformat() == "2026-01-15"


def test_parses_messy_receipt_with_discount_and_alt_date_format():
    result = parse(MESSY_RECEIPT)

    assert result.store_name == "GROCERY MART"
    assert result.website is not None
    assert result.email == "info@grocerymart.example"
    assert result.date is not None
    assert result.date.isoformat() == "2026-01-15"
    assert result.subtotal == 6300.00
    assert result.discount == 300.00
    assert result.total == 6000.00
    assert len(result.line_items) == 3


def test_parses_garbage_text_without_crashing():
    result = parse(NO_DATE_NO_ITEMS)

    assert result.date is None
    assert result.total is None
    assert result.line_items == []


def test_treats_subtotal_as_total_when_no_separate_total_line_exists():
    """Some real receipts print only "SUB TOTAL", with no separate "TOTAL"
    line and no tax/discount line adjusting it further — confirmed against
    a real receipt. That subtotal IS the grand total, not a partial figure
    still awaiting a total line that never comes."""
    result = parse(SUBTOTAL_ONLY_RECEIPT)

    assert result.subtotal == 8350.00
    assert result.total == 8350.00


def test_parses_dd_mmm_yy_date_format():
    """Real receipt: '03-Jan-26 at 8:10:00 PM' — day, abbreviated month,
    2-digit year, hyphen-separated. Not covered by the numeric DD/MM/YYYY
    patterns (month isn't numeric) or the full-month-name patterns (year
    isn't 4 digits)."""
    result = parse("Some Store\n03-Jan-26 at 8:10:00 PM\nTotal 100.00\n")

    assert result.date is not None
    assert result.date.isoformat() == "2026-01-03"


def test_uses_rightmost_money_value_when_a_totals_line_also_has_an_item_count():
    """Real receipts sometimes print an item count before the actual money
    amount on the same totals line (e.g. "SUB TOTAL 2.00 6350.00" — 2 items,
    then the total) — confirmed against a real receipt. The parser must
    pick the rightmost money-shaped value, not the first one it finds."""
    result = parse(TOTAL_WITH_ITEM_COUNT_RECEIPT)

    assert result.subtotal == 6350.00
    assert result.total == 6350.00


def test_parses_line_items_with_no_quantity_marker():
    """A line with no `x`/`@` quantity marker, just a description followed
    by a price, must recover the price into unit_price/line_total — not the
    description text itself (regression: _ITEM_LINE_NO_QTY previously read
    the wrong capture group, raising ValueError on a real receipt)."""
    result = parse(NO_QTY_ITEM_RECEIPT)

    assert result.total == 750.00
    assert len(result.line_items) == 2
    first = result.line_items[0]
    assert first.description == "1 TONGUE SCRAPER"
    assert first.quantity == 1.0
    assert first.unit_price == 500.00
    assert first.line_total == 500.00


def test_parse_empty_string():
    result = parse("")

    assert result.store_name is None
    assert result.total is None
    assert result.line_items == []
