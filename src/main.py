"""Streamlit UI entry point for the PDF bank / credit card statement parser.

Features:
  - Upload a PDF and parse transactions (checking / savings / credit card).
  - Basic filtering (account type, search description substring).
  - Download full or filtered CSV.
  - Optional debug panels: unparsed lines, balance mismatches, raw line sample.
  - Caching of parse results per file content to speed iterative inspection.
"""

from __future__ import annotations

import io
import hashlib
from typing import Tuple

import streamlit as st

from pdf_parser import parse_bank_statement, compute_balance_mismatches


# ----------------------------- Utility Layer ----------------------------- #


def _file_hash(file_like) -> str:
    """Return a short hash of the uploaded file bytes for cache keying."""
    pos = file_like.tell()
    file_like.seek(0)
    data = file_like.read()
    file_like.seek(pos)
    return hashlib.sha256(data).hexdigest()[:16]


@st.cache_data(show_spinner=False)
def _cached_parse(content_bytes: bytes) -> Tuple:
    """Cache wrapper around parse_bank_statement.

    We pass raw bytes to keep the cache key independent of filename.
    """
    bio = io.BytesIO(content_bytes)
    return parse_bank_statement(bio)


# ----------------------------- UI Components ----------------------------- #


def _render_metrics(df):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Transactions", len(df))
    with col2:
        st.metric(
            "Accounts",
            df["account_number"].nunique() if "account_number" in df.columns else "-",
        )
    with col3:
        if "amount" in df.columns and not df["amount"].dropna().empty:
            st.metric("Net Amount", f"{df['amount'].sum():,.2f}")
        else:
            st.metric("Net Amount", "-")
    with col4:
        if "account_type" in df.columns:
            st.metric("Types", ", ".join(sorted(df["account_type"].dropna().unique())))
        else:
            st.metric("Types", "-")


def _filter_dataframe(df):
    if df.empty:
        return df
    f_df = df.copy()
    if "account_type" in f_df.columns:
        types = sorted(f_df["account_type"].dropna().unique())
        chosen = st.multiselect(
            "Account types", types, default=types, key="filter_types"
        )
        if chosen:
            f_df = f_df[f_df["account_type"].isin(chosen)]
    q = st.text_input(
        "Description contains", placeholder="e.g. AMAZON", key="filter_desc"
    )
    if q:
        f_df = f_df[f_df["description"].str.contains(q, case=False, na=False)]
    return f_df


def _debug_panels(df, unparsed, raw_lines):
    """Render expandable debug information panels."""
    with st.expander("Unparsed candidate lines", expanded=False):
        if unparsed:
            st.code("\n".join(unparsed[:300]))
            if len(unparsed) > 300:
                st.caption(f"Showing first 300 of {len(unparsed)}")
        else:
            st.write("None.")
    with st.expander("Balance mismatches", expanded=False):
        if df is not None and not df.empty:
            mismatches = compute_balance_mismatches(df)
            if mismatches:
                st.dataframe(mismatches, use_container_width=True)
            else:
                st.write("None detected.")
    with st.expander("Sample raw lines", expanded=False):
        if raw_lines:
            st.code(
                "\n".join(
                    f"{i + 1:04d}: {line_text}"
                    for i, (_, line_text) in enumerate(raw_lines[:400])
                )
            )
        else:
            st.write("No raw lines captured.")


def main():
    st.set_page_config(page_title="Statement Parser", page_icon="📄", layout="wide")
    st.title("PDF Statement Parser")
    st.caption(
        "Upload a bank or credit card statement PDF. The parser infers missing years, normalizes account types, and labels spending polarity."
    )

    with st.sidebar:
        st.header("Upload & Options")
        uploaded = st.file_uploader(
            "PDF statement", type=["pdf"], accept_multiple_files=False, key="uploader"
        )
        parse_btn = st.button(
            "Parse / Refresh", type="primary", use_container_width=True
        )
        show_debug = st.toggle("Show debug panels", value=False, key="debug_toggle")
        show_full_columns = st.checkbox(
            "Show all columns", value=False, key="full_cols"
        )

    if not uploaded:
        st.info("Upload a PDF to begin.")
        return

    ss = st.session_state
    file_bytes = uploaded.getvalue()
    file_hash = _file_hash(io.BytesIO(file_bytes))
    need_parse = (
        parse_btn
        or ("_parsed_hash" not in ss)
        or (ss.get("_parsed_hash") != file_hash)
        or ("parsed_df" not in ss)
    )

    if need_parse:
        with st.spinner("Parsing PDF ..."):
            df, unparsed, raw_lines = _cached_parse(file_bytes)
        ss.update(
            {
                "parsed_df": df,
                "unparsed_lines": unparsed,
                "raw_lines": raw_lines,
                "_parsed_hash": file_hash,
            }
        )
    else:
        df = ss["parsed_df"]
        unparsed = ss.get("unparsed_lines", [])
        raw_lines = ss.get("raw_lines", [])

    if df is None or df.empty:
        st.error("No transactions parsed from this document.")
        if show_debug:
            _debug_panels(df, unparsed, raw_lines)
        return

    st.success(
        f"Parsed {len(df)} transactions across {df['account_number'].nunique() if 'account_number' in df.columns else '?'} account(s). Hash {file_hash}."
    )
    _render_metrics(df)

    st.subheader("Transactions")
    filtered_df = _filter_dataframe(df)
    base_cols = [
        "date",
        "post_date" if "post_date" in df.columns else None,
        "account_type" if "account_type" in df.columns else None,
        "account_name" if "account_name" in df.columns else None,
        "account_number" if "account_number" in df.columns else None,
        "description",
        "amount",
        "debit" if "debit" in df.columns else None,
        "credit" if "credit" in df.columns else None,
        "balance" if "balance" in df.columns else None,
    ]
    core_cols = [c for c in base_cols if c]
    show_full_columns = st.session_state.get("full_cols", False)
    display_cols = (
        core_cols
        if show_full_columns
        else [c for c in core_cols if c not in {"debit", "credit"}]
    )
    st.dataframe(filtered_df[display_cols], use_container_width=True, hide_index=True)

    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            "Download (filtered CSV)",
            data=filtered_df[display_cols].to_csv(index=False).encode("utf-8"),
            file_name="statement_filtered.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_dl2:
        st.download_button(
            "Download (full CSV)",
            data=df[core_cols].to_csv(index=False).encode("utf-8"),
            file_name="statement_full.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if show_debug:
        st.divider()
        _debug_panels(df, unparsed, raw_lines)


if __name__ == "__main__":
    main()
