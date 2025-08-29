import re
from datetime import datetime, date
from typing import List, Tuple, Union
import pandas as pd
import pdfplumber
from collections import Counter


STATEMENT_PERIOD_RX = re.compile(
    r"""
    (?:
        Statement \s+ Period .*?
    )?
    (\d{1,2}[/-]\d{1,2}[/-](\d{2,4}))
    \s*-\s*
    (\d{1,2}[/-]\d{1,2}[/-](\d{2,4}))
    """,
    re.IGNORECASE | re.VERBOSE,
)

DATE_START_RX = re.compile(
    r"""
    ^(?P<date>
        \d{1,2} [/-] \d{1,2}
        (?: [/-] \d{2,4} )?
    )
    (?!\s*-\s*\d{1,2}[/-]\d{1,2})
    \b
    """,
    re.VERBOSE,
)

AMOUNT_TOKEN_RX = re.compile(
    r"""
    ^ 
    (?:
        \( (?P<num_paren> \$? (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})? ) \)
        |
        (?P<sign>-)? \$? (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})? (?P<trail_minus>-)?
    )$
    """,
    re.VERBOSE,
)

YEAR_IN_RANGE_RX = re.compile(
    r"""
    \b
    \d{1,2} [/-] \d{1,2} [/-] (\d{2,4})
    \b
    """,
    re.VERBOSE,
)

ACCOUNT_HEADER_RX = re.compile(
    r"""
    ^
    (?P<name>
        [A-Za-z&'./-]+
        (?:\s+[A-Za-z&'./-]+)*
    )
    \s* - \s*
    (?P<number>\d{6,})\b
    """,
    re.VERBOSE,
)


ACCOUNT_HEADER_INLINE_RX = re.compile(
    r"""
    (?P<name>
        [A-Za-z&'./-]+
        (?:\s+[A-Za-z&'./-]+)*
    )
    \s* - \s*
    (?P<number>\d{6,})\b
    """,
    re.VERBOSE,
)

DATE_RANGE_RX = re.compile(
    r"""
    ^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}
    \s*-\s*
    \d{1,2}[/-]\d{1,2}[/-]\d{2,4}$
    """,
    re.VERBOSE,
)

HEADER_FOOTER_PATTERNS_RX = [
    re.compile(r"^Page \s+\d+ \s+ of \s+ \d+$", re.IGNORECASE | re.VERBOSE),
    re.compile(r"^Statement \s+ Period$", re.IGNORECASE | re.VERBOSE),
    re.compile(r"^Statement \s+ of \s+ Account$", re.IGNORECASE | re.VERBOSE),
]


SKIP_DESC = ["Beginning Balance", "Ending Balance"]
SKIP_CONTAINS = ["Average Daily Balance", "Beginning Balnce", "Ending Balance"]


def classify_account(name: str | None) -> str | None:
    """Classify an account name into a broad type.

    Returns one of:
      - "checking"
      - "savings" (includes generic savings, share accounts)
      - "money_market_savings" (money market / MM / MMSA variants)
      - None if no classification.

    Heuristics are credit-union friendly ("Share Draft" = checking, "Share" alone = savings).
    """
    if not name:
        return None
    n = name.lower()
    n_norm = re.sub(r"[^a-z0-9 ]+", " ", n)
    if "money market" in n_norm or re.search(r"\bmm(sa)?\b", n_norm):
        return "money_market_savings"
    if (
        "checking" in n_norm
        or re.search(r"\bchk\b", n_norm)
        or "share draft" in n_norm
        or re.search(r"\bdraft\b", n_norm)
    ):
        return "checking"
    if (
        "savings" in n_norm
        or "saving" in n_norm
        or ("share" in n_norm and "draft" not in n_norm)
    ):
        return "savings"
    return None


def infer_year(all_lines: list[str]) -> int | None:
    years: list[int] = []
    period_years: list[int] = []
    for line in all_lines:
        for m in YEAR_IN_RANGE_RX.finditer(line):
            y = m.group(1)
            y_full = int(("20" + y) if len(y) == 2 else y)
            years.append(y_full)
        for pm in STATEMENT_PERIOD_RX.finditer(line):
            y1_raw = pm.group(2)
            y2_raw = pm.group(4)
            for y_raw in (y1_raw, y2_raw):
                y_full = int(("20" + y_raw) if len(y_raw) == 2 else y_raw)
                period_years.append(y_full)
    if not years:
        return None
    counter = Counter(years)
    if len(counter) == 2:
        y_sorted = sorted(counter.keys())
        if abs(y_sorted[0] - y_sorted[1]) == 1 and period_years:
            period_counts = [(y, counter[y]) for y in set(period_years) if y in counter]
            if period_counts:
                period_counts.sort(key=lambda t: t[1], reverse=True)
                return period_counts[0][0]
    return counter.most_common(1)[0][0]


def normalize_number(raw: str | None) -> float | None:
    """Parse a currency-like token into a float with sign.

    Heuristics to reduce false positives (e.g. giant reference strings):
    - Allow optional $, commas, parentheses, trailing minus.
    - Require at most 2 decimal places when a decimal point is present.
    - Reject pure integer tokens longer than 7 digits (likely IDs) unless they contain commas.
    - Reject tokens whose numeric part exceeds 1e9 (configurable cutoff) to avoid absurd values.
    """
    if not raw:
        return None
    token = raw.strip()
    neg = False
    # Trailing minus form: 123.45-
    if token.endswith("-") and token.count("-") == 1:
        neg = True
        token = token[:-1]
    # Parentheses form: (123.45)
    if token.startswith("(") and token.endswith(")"):
        neg = True
        token = token[1:-1]
    # Remove currency symbol and grouping
    token_nosym = token.replace("$", "").replace(",", "")
    # Leading sign
    if token_nosym.startswith("-"):
        neg = True
        token_nosym = token_nosym[1:]
    core = token_nosym
    # Basic numeric pattern
    if not re.fullmatch(r"\d+(?:\.\d+)?", core):
        return None
    # Reject overly long integer without decimal (likely an ID)
    if "." not in core and len(core) > 7:
        return None
    # If decimal, must have exactly two places for currency
    if "." in core:
        int_part, frac_part = core.split(".", 1)
        if not (
            1 <= len(frac_part) <= 2
        ):  # allow 1 or 2 (some statements omit trailing 0)
            return None
        if len(frac_part) == 1:  # normalize one decimal place by padding
            core = int_part + "." + frac_part + "0"
    try:
        v = float(core)
    except ValueError:
        return None
    # Absurd cutoff (1 billion) — treat as non-amount
    if v > 1_000_000_000:
        return None
    return -v if neg else v


def parse_date(
    raw: str, default_year: int | None, date_order: str | None
) -> Union[date, str]:
    """
    Parse date strings like:
    - 07-23 (infer year & ordering)
    - 07/23/25
    - 23/07/2025
    Returns date | original raw on failure.
    """
    parts = re.split(r"[/-]", raw)
    if len(parts) == 2 and default_year:
        try:
            a = int(parts[0])
            b = int(parts[1])
        except ValueError:
            return raw
        if a > 12 and b <= 12:
            day, month = a, b
        elif b > 12 and a <= 12:
            month, day = a, b
        else:
            if date_order == "DM":
                day, month = a, b
            else:
                month, day = a, b
        try:
            return datetime(default_year, month, day).date()
        except ValueError:
            return raw
    for fmt in (
        "%m-%d-%Y",
        "%m-%d-%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d/%m/%y",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return raw


def _normalize_space(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s.replace("\u00a0", " ")).strip()


def extract_raw_lines(
    pdf_file,
    mode: str = "raw",
    merge_wrapped: bool = True,
    include_coords: bool = False,
    drop_header_footer: bool = True,
) -> List[Tuple[int, str]]:
    """
    Extract lines from a PDF.

    Parameters:
        mode: "raw" (use extract_text lines) or "words" (reconstruct via word boxes)
        merge_wrapped: attempt to merge continuation lines (long descriptions)
        include_coords: if True and mode="words", attaches coords internally (still returns (page, text) outward)
        drop_header_footer: drop lines matching known header/footer regexes

    Returns:
        List[(page_number, line_text)]
    """
    lines: List[Tuple[int, str]] = []
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for p_idx, page in enumerate(pdf.pages, start=1):
                if mode == "raw":
                    text = page.extract_text() or ""
                    for raw in text.splitlines():
                        s = raw.rstrip()
                        if not s:
                            continue
                        s_norm = _normalize_space(s)
                        if not s_norm:
                            continue
                        if drop_header_footer and any(
                            rx.match(s_norm) for rx in HEADER_FOOTER_PATTERNS_RX
                        ):
                            continue
                        lines.append((p_idx, s_norm))
                else:
                    words = page.extract_words() or []
                    grouped = []
                    y_tol = 3
                    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                        if not grouped:
                            grouped.append([w])
                            continue
                        last_line = grouped[-1]
                        if abs(w["top"] - last_line[0]["top"]) <= y_tol:
                            last_line.append(w)
                        else:
                            grouped.append([w])
                    for group in grouped:
                        group_sorted = sorted(group, key=lambda w: w["x0"])
                        text_line = " ".join(g["text"] for g in group_sorted)
                        s_norm = _normalize_space(text_line)
                        if not s_norm:
                            continue
                        if drop_header_footer and any(
                            rx.match(s_norm) for rx in HEADER_FOOTER_PATTERNS_RX
                        ):
                            continue
                        lines.append((p_idx, s_norm))
    except Exception:
        return []

    if merge_wrapped and lines:
        merged: List[Tuple[int, str]] = []
        date_prefix = re.compile(r"^\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
        for pg, text in lines:
            if not merged:
                merged.append((pg, text))
                continue
            prev_pg, prev_text = merged[-1]
            prev_starts_with_date = bool(date_prefix.match(prev_text))
            curr_starts_with_date = bool(date_prefix.match(text))
            # Detect account headers inline; don't merge across them
            prev_has_header = bool(ACCOUNT_HEADER_INLINE_RX.search(prev_text))
            curr_has_header = bool(ACCOUNT_HEADER_INLINE_RX.search(text))
            if (
                pg == prev_pg
                and not prev_starts_with_date
                and not curr_starts_with_date
                and not prev_has_header
                and not curr_has_header
            ):
                merged[-1] = (prev_pg, prev_text + " " + text)
            else:
                merged.append((pg, text))
        lines = merged

    return lines


def should_skip_desc(desc: str) -> bool:
    if desc in SKIP_DESC:
        return True
    for frag in SKIP_CONTAINS:
        if frag in desc:
            return True
    return False


def parse_line(
    line: str,
    default_year: int | None,
    account_name: str | None,
    account_number: str | None,
    account_type: str | None = None,
    date_order: str | None = None,
):
    m = DATE_START_RX.match(line)
    if not m:
        return None
    date_raw = m.group("date")
    rest = line[m.end() :].strip()
    tokens = rest.split()
    if not tokens:
        return None

    trailing: list[str] = []
    while tokens and AMOUNT_TOKEN_RX.match(tokens[-1]) and len(trailing) < 3:
        trailing.append(tokens.pop())
    trailing.reverse()

    description = " ".join(tokens).strip()
    if not description:
        return None
    # Skip balance marker lines entirely per user request
    if description in {"Beginning Balance", "Ending Balance"}:
        return None

    amount = balance = None
    debit = credit = None

    if len(trailing) == 3:
        # Heuristic: if first token looks like a reference/check number (no decimal, length>=5)
        # and the next two tokens look like monetary values (have decimal OR parentheses OR trailing minus),
        # treat the first token as part of the description instead of an amount.
        ref_candidate = trailing[0]
        monetary_tail = trailing[1:]

        def _looks_money(tok: str) -> bool:
            return bool(re.search(r"[().-]", tok)) or "." in tok

        if (
            ref_candidate.isdigit()
            and len(ref_candidate) >= 5
            and "." not in ref_candidate
            and any(_looks_money(t) for t in monetary_tail)
        ):
            # push reference back into description tokens
            description = (description + " " + ref_candidate).strip()
            trailing = trailing[1:]

    if len(trailing) == 3:  # Re-evaluate if still 3 after potential adjustment
        a1 = normalize_number(trailing[0])
        a2 = normalize_number(trailing[1])
        b = normalize_number(trailing[2])
        if a1 is not None and a2 is not None and b is not None:
            if (a1 < 0 < a2) or (a2 < 0 < a1):
                debit = abs(a1) if a1 < 0 else abs(a2) if a2 < 0 else None
                credit = a1 if a1 > 0 else a2 if a2 > 0 else None
                amount = (credit or 0) - (debit or 0)
                balance = b
            else:
                amount = a1
                balance = b
        elif a1 is not None and b is not None:
            amount = a1
            balance = b
    elif len(trailing) == 2:
        # Possible patterns:
        #  (ref, amount)  e.g., 2100002 120.47-
        #  (amount, balance) normal
        #  (amount, ?) ambiguous
        t0, t1 = trailing
        looks_ref = t0.isdigit() and len(t0) >= 5 and "." not in t0
        looks_money = bool(re.search(r"[().-]", t1) or "." in t1)
        # If first looks like a reference and second like money, push reference into description
        if looks_ref and looks_money:
            description = (description + " " + t0).strip()
            trailing = [t1]
            # Re-handle as single token
            a1 = normalize_number(trailing[0])
            if a1 is not None and not (trailing[0].isdigit() and len(trailing[0]) > 8):
                amount = a1
        else:
            a1 = normalize_number(t0)
            a2 = normalize_number(t1)
            if a1 is not None and a2 is not None:
                if (abs(a2) >= abs(a1)) or ("," in t1 and "," not in t0):
                    amount = a1
                    balance = a2
                else:
                    amount = a1
            elif a1 is not None:
                amount = a1
            elif a2 is not None:
                amount = a2
    elif len(trailing) == 1:
        a1 = normalize_number(trailing[0])
        if a1 is not None and not (trailing[0].isdigit() and len(trailing[0]) > 8):
            amount = a1
        else:
            if trailing[0]:
                description = (description + " " + trailing[0]).strip()

    if amount is not None and debit is None and credit is None:
        if amount < 0:
            debit = -amount
        elif amount > 0:
            credit = amount

    if all(v is None for v in (amount, balance, debit, credit)):
        return None

    line_type = "transaction"

    return {
        "date": parse_date(date_raw, default_year, date_order),
        "date_raw": date_raw,
        "description": description,
        "amount": amount,
        "debit": debit,
        "credit": credit,
        "balance": balance,
        "account_name": account_name,
        "account_number": account_number,
        "account_type": account_type,
        "line_type": line_type,
        "raw_line": line,
    }


def parse_bank_statement(
    pdf_file,
) -> tuple[pd.DataFrame, list[str], List[Tuple[int, str]]]:
    raw_lines = extract_raw_lines(pdf_file)
    default_year = infer_year([line_text for _, line_text in raw_lines])
    rows = []
    unparsed: list[str] = []
    account_name = account_number = account_type = None

    for pg, line in raw_lines:
        hdr = ACCOUNT_HEADER_RX.match(line)
        if not hdr:
            hdr = ACCOUNT_HEADER_INLINE_RX.search(line)
        if hdr:
            account_name = hdr.group("name").strip()
            account_number = hdr.group("number")
            account_type = classify_account(account_name)
        rec = parse_line(line, default_year, account_name, account_number, account_type)
        if rec:
            rows.append(rec)
        else:
            if re.match(r"^\d{1,2}[/-]\d{1,2}\b", line):
                unparsed.append(f"[p{pg}] {line}")
    df = pd.DataFrame(rows)
    if not df.empty:
        try:
            df["_d"] = pd.to_datetime(df["date"], errors="coerce")
            sort_cols = [
                c for c in ["account_type", "account_number", "_d"] if c in df.columns
            ]
            df = df.sort_values(sort_cols).drop(columns=["_d"])
            if "amount" in df.columns:
                amt_series = df["amount"].dropna()
                if not amt_series.empty:
                    med = amt_series.abs().median()
                    if med > 0:
                        cutoff = med * 50  # generous multiplier
                        df.loc[
                            df["amount"].abs() > cutoff, ["amount", "debit", "credit"]
                        ] = None
            mask_all_none = (
                df[["amount", "debit", "credit", "balance"]].isna().all(axis=1)
            )
            if mask_all_none.any():
                df = df[~mask_all_none]
        except Exception:
            pass
    return df, unparsed, raw_lines


def compute_balance_mismatches(df: pd.DataFrame, tolerance: float = 0.01) -> list[dict]:
    """Return list of mismatches where provided balance != prior balance + amount.

    Requires columns: date, amount, balance, account_number, line_type.
    """
    mismatches: list[dict] = []
    if df.empty:
        return mismatches
    for acct, g in df.sort_values(["account_number", "date_raw"]).groupby(
        "account_number", dropna=False
    ):
        last_balance = None
        for idx, row in g.iterrows():
            if row.get("line_type") == "marker":
                if row.get("balance") is not None:
                    last_balance = row["balance"]
                continue
            amount = row.get("amount")
            bal = row.get("balance")
            if amount is not None and bal is not None and last_balance is not None:
                expected = round(last_balance + amount, 2)
                provided = round(bal, 2)
                if abs(expected - provided) > tolerance:
                    mismatches.append(
                        {
                            "index": idx,
                            "account_number": acct,
                            "date": row.get("date"),
                            "description": row.get("description"),
                            "amount": amount,
                            "prev_balance": last_balance,
                            "expected_balance": expected,
                            "provided_balance": provided,
                            "delta": round(provided - expected, 2),
                        }
                    )
            if bal is not None:
                last_balance = bal
    return mismatches
