from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import httpx
import time
import os
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()


app = FastAPI()

# CORS configuration
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://next.henrybarefoot.com",
    "https://www.henrybarefoot.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory cache: {url: {"data": ..., "timestamp": ...}}
CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_DURATION = 600  # 10 minutes in seconds

class AuditRequest(BaseModel):
    url: str

@app.post("/api/audit")
async def audit_url(request: AuditRequest):
    url = request.url
    
    # Basic URL validation/ensure it calls http/https
    if not url.startswith("http"):
        url = f"https://{url}"

    # Check cache
    now = time.time()
    if url in CACHE:
        entry = CACHE[url]
        if now - entry["timestamp"] < CACHE_DURATION:
            return entry["data"]

    # Google PageSpeed API URL
    # Valid categories: accessibility, best-practices, performance, pwa, seo
    # Strategy: mobile
    api_key = os.getenv("GOOGLE_PAGESPEED_API_KEY", "")
    params = {
        "url": url,
        "strategy": "mobile",
        "category": ["performance", "accessibility", "best-practices", "seo"],
    }
    if api_key:
        params["key"] = api_key

    api_url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

    try:
        if "mock" in url:
            raise Exception("Force mock") # Simple way to trigger mock block for specific keyword
            
        async with httpx.AsyncClient() as client:
            try:
                # Add Referer header to satisfy API key restrictions
                referer = os.getenv("APP_URL", "http://127.0.0.1:8000")
                headers = {"Referer": referer} 
                response = await client.get(api_url, params=params, headers=headers, timeout=60.0)
                if response.status_code == 429:
                    print("Usage limit exceeded, falling back to mock data")
                    raise Exception("Quota exceeded")
                response.raise_for_status()
                data = response.json()
                
                # Extract relevant metrics
                lighthouse = data.get("lighthouseResult", {})
                categories = lighthouse.get("categories", {})
                audits = lighthouse.get("audits", {})

                result = {
                    "url": url,
                    "scores": {
                        "performance": categories.get("performance", {}).get("score", 0),
                        "accessibility": categories.get("accessibility", {}).get("score", 0),
                        "best_practices": categories.get("best-practices", {}).get("score", 0),
                        "seo": categories.get("seo", {}).get("score", 0),
                    },
                    "metrics": {
                        "fcp": audits.get("first-contentful-paint", {}).get("displayValue", "N/A"),
                        "fcp_score": audits.get("first-contentful-paint", {}).get("score", 0),
                    },
                    "is_mock": False
                }
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=e.response.status_code, detail=f"PageSpeed API error: {e.response.text}")

    except Exception as e:
        print(f"Using mock data due to: {str(e)}")
        # Mock Response
        result = {
            "url": url,
            "scores": {
                "performance": 0.72,
                "accessibility": 0.85,
                "best_practices": 0.90,
                "seo": 0.92,
            },
            "metrics": {
                "fcp": "1.2 s",
                "fcp_score": 0.85,
            },
            "is_mock": True
        }

    # Update Cache
    CACHE[url] = {
        "data": result,
        "timestamp": now
    }

    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
