from fastapi import FastAPI, UploadFile, File
from pdf_parser import parse_bank_statement
import io

app = FastAPI(title="PDF Bank Statement Parser", version="1.0.0")

@app.post("/parse")
async def parse_pdf(file: UploadFile = File(...)):
    # Read uploaded bytes into a BytesIO for pdfplumber
    data = await file.read()
    buf = io.BytesIO(data)
    df, unparsed, _ = parse_bank_statement(buf)
    if df.empty:
        return {"transactions": [], "unparsed_sample": unparsed[:50], "total_unparsed": len(unparsed)}
    # Convert DataFrame to JSON-serializable structure
    records = []
    for row in df.to_dict(orient="records"):
        # Ensure dates are strings
        d = row.get("date")
        if d is not None:
            row["date"] = str(d)
        records.append(row)
    return {
        "transactions": records,
        "transaction_count": len(records),
        "accounts": sorted({(r.get("account_number"), r.get("account_type")) for r in records}),
        "unparsed_sample": unparsed[:50],
        "total_unparsed": len(unparsed),
    }

@app.get("/health")
async def health():
    return {"status": "ok"}
