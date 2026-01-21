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
                
                # DEBUG: Print available audit keys and LCP structure
                print("DEBUG: Available Audit Keys:", list(audits.keys()))
                print("DEBUG: LCP Audit Raw:", audits.get("largest-contentful-paint"))

                # Helper to get display value or score
                def get_metric(key, field="displayValue"):
                    return audits.get(key, {}).get(field, "N/A")
                
                def get_score(key):
                     return audits.get(key, {}).get("score", 0)

                # Extract Opportunities (audits with score < 1 and type 'opportunity')
                opportunities = []
                for key, audit in audits.items():
                    if audit.get("details", {}).get("type") == "opportunity" and audit.get("score", 1) < 0.9:
                        opportunities.append({
                            "id": key,
                            "title": audit.get("title"),
                            "description": audit.get("description"),
                            "score": audit.get("score"),
                            "saving": audit.get("details", {}).get("overallSavingsMs", 0)
                        })
                
                # Sort opportunities by estimated savings (descending) and take top 5
                opportunities.sort(key=lambda x: x.get("saving", 0), reverse=True)
                opportunities = opportunities[:5]

                result = {
                    "url": url,
                    "scores": {
                        "performance": categories.get("performance", {}).get("score", 0),
                        "accessibility": categories.get("accessibility", {}).get("score", 0),
                        "best_practices": categories.get("best-practices", {}).get("score", 0),
                        "seo": categories.get("seo", {}).get("score", 0),
                    },
                    "metrics": {
                        "fcp": get_metric("first-contentful-paint"),
                        "fcp_score": get_score("first-contentful-paint"),
                        "lcp": get_metric("largest-contentful-paint"),
                        "lcp_score": get_score("largest-contentful-paint"),
                        "cls": get_metric("cumulative-layout-shift"),
                        "cls_score": get_score("cumulative-layout-shift"),
                        "tbt": get_metric("total-blocking-time"),
                        "tbt_score": get_score("total-blocking-time"),
                        "si": get_metric("speed-index"),
                        "si_score": get_score("speed-index"),
                    },
                    "opportunities": opportunities,
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
                "lcp": "2.1 s",
                "lcp_score": 0.88,
                "cls": "0.05",
                "cls_score": 0.95,
                "tbt": "120 ms",
                "tbt_score": 0.90,
                "si": "1.8 s",
                "si_score": 0.82
            },
            "opportunities": [
                {
                    "id": "unused-javascript",
                    "title": "Reduce unused JavaScript",
                    "description": "Remove unused JavaScript to reduce bytes consumed by network activity.",
                    "score": 0.65,
                    "saving": 350
                },
                {
                    "id": "modern-image-formats",
                    "title": "Serve images in next-gen formats",
                    "description": "Image formats like WebP and AVIF often provide better compression than PNG or JPEG.",
                    "score": 0.70,
                    "saving": 200
                }
            ],
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
