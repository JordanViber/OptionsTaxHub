import os
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io

# Load environment variables from .env.local or .env
load_dotenv(".env.local")
load_dotenv(".env")

app = FastAPI()

# Get environment variables
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
DATABASE_URL = os.environ.get("DATABASE_URL")
API_KEY_SECRET = os.environ.get("API_KEY_SECRET")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)