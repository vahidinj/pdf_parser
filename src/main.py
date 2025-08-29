import streamlit as st

from pdf_parser import parse_bank_statement, compute_balance_mismatches


def main():
    "main"

    st.title("Bank Statement PDF → CSV")
    st.caption(
        "Drop a bank statement PDF. Dates without year are inferred. Trailing '-' means negative."
    )
    uploaded = st.file_uploader("Upload PDF", type=["pdf"])
    debug = st.checkbox("Debug mode")
    if uploaded and st.button("Parse PDF"):
        with st.spinner("Parsing..."):
            df, unparsed, raw_lines = parse_bank_statement(uploaded)
        if df.empty:
            st.error("No transactions parsed.")
        else:
            st.success(
                f"Parsed {len(df)} transactions across {df['account_number'].nunique()} account(s)."
            )
            base_cols = [
                "date",
                "account_type" if "account_type" in df.columns else None,
                "account_name",
                "account_number",
                "description",
                "amount",
                "balance",
            ]
            display_cols = [c for c in base_cols if c]
            st.dataframe(df[display_cols])
            st.download_button(
                "Download CSV",
                data=df[display_cols].to_csv(index=False).encode("utf-8"),
                file_name="statement.csv",
                mime="text/csv",
            )
        if debug:
            st.subheader("Unparsed candidate lines")
            if unparsed:
                st.code("\n".join(unparsed[:200]))
            else:
                st.write("None.")
            if not df.empty:
                mismatches = compute_balance_mismatches(df)
                st.subheader("Balance mismatches")
                if mismatches:
                    st.dataframe(mismatches)
                else:
                    st.write("None.")
            st.subheader("Sample raw lines")
            st.code(
                "\n".join(
                    f"{i + 1:04d}: {line_text}"
                    for i, (_, line_text) in enumerate(raw_lines[:250])
                )
            )


if __name__ == "__main__":
    main()
