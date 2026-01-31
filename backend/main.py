from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io

app = FastAPI()

# Enable CORS for frontend development (localhost:3000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    # MVP: Read uploaded CSV in-memory, parse with pandas, return first 5 rows as JSON
    # This supports the portfolio rebalancer MVP by allowing users to upload CSV exports (e.g., from Robinhood)
    # for initial parsing and tax-loss harvesting suggestions (to be added later)
    contents = await file.read()
    df = pd.read_csv(io.StringIO(contents.decode("utf-8")))
    # Return first 5 rows as dict for simplicity (no persistent storage yet)
    return df.head(5).to_dict(orient="records")