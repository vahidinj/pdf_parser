# PDF Bank Statement Parser Backend

FastAPI + parsing utilities to extract transactions from bank statement PDFs.

## Features
- Endpoint `POST /parse` accepting a PDF file upload (multipart/form-data field name: `file`).
- Returns JSON: transactions, account info, unparsed sample lines.
- Health probe at `GET /health`.

## Local Development
Create and activate a virtual environment (example using Python 3.11+):

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api:app --reload --port 8000
```

Test with curl:
```
curl -X POST -F "file=@/path/to/statement.pdf" http://localhost:8000/parse | jq
```

## Project Layout
```
backend/
  requirements.txt
  src/
    pdf_parser.py
    api.py
```

## Deploying (GitHub Repository)
1. Initialize git (if not already):
```
cd backend
git init
```
2. Add remote:
```
git remote add origin git@github.com:vahidinj/pdf_parser.git
```
(Or use https remote if preferred):
```
git remote add origin https://github.com/vahidinj/pdf_parser.git
```
3. Commit & push:
```
git add .
git commit -m "Initial backend parser"
git branch -M main
git push -u origin main
```

## Production Run
Use a production ASGI server (e.g. uvicorn with workers or gunicorn + uvicorn workers):
```
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

## Notes
- Streamlit UI is separate (main.py). This backend focuses on API usage.
- For large PDFs consider increasing server timeout.
