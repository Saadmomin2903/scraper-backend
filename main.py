from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any
from jobspy import scrape_jobs

# Import the FastAPI app objects from the other scrapers
from foundit_scraper import app as foundit_app
from new_glassdoor import app as glassdoor_app
from new_simplyhired import app as simplyhired_app
from new_ziprecruiter import app as ziprecruiter_app

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Add CORS middleware to all mounted apps
foundit_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

glassdoor_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

simplyhired_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

ziprecruiter_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Mount the other apps under their own prefixes
app.mount("/foundit", foundit_app)
app.mount("/glassdoor", glassdoor_app)
app.mount("/simplyhired", simplyhired_app)
app.mount("/ziprecruiter", ziprecruiter_app)

class JobPortalRequest(BaseModel):
    site_name: str
    search_term: Optional[str] = None
    google_search_term: Optional[str] = None
    location: Optional[str] = None
    results_wanted: Optional[int] = 5
    country_indeed: Optional[str] = "india"
    hours_old: Optional[int] = 72
    job_type: Optional[str] = None
    is_remote: Optional[bool] = None
    easy_apply: Optional[bool] = None
    description_format: Optional[str] = None
    offset: Optional[int] = 0
    verbose: Optional[int] = 2
    linkedin_fetch_description: Optional[bool] = None
    proxies: Optional[List[str]] = None


def run_scraper(params: dict) -> Any:
    try:
        jobs = scrape_jobs(**params)
        if jobs.empty:
            raise HTTPException(status_code=404, detail="No jobs found.")
        cleaned_jobs = jobs.replace([float("inf"), float("-inf")], None).fillna("")
        job_list = cleaned_jobs.to_dict(orient="records")
        return {"message": f"Found {len(job_list)} jobs", "jobs": job_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scrape-linkedin/")
async def scrape_linkedin(request: JobPortalRequest):
    params = request.dict(exclude_none=True)
    params["site_name"] = ["linkedin"]
    return run_scraper(params)

@app.post("/scrape-indeed/")
async def scrape_indeed(request: JobPortalRequest):
    params = request.dict(exclude_none=True)
    params["site_name"] = ["indeed"]
    return run_scraper(params)

@app.post("/scrape-naukri/")
async def scrape_naukri(request: JobPortalRequest):
    params = request.dict(exclude_none=True)
    params["site_name"] = ["naukri"]
    return run_scraper(params)

# Add explicit OPTIONS handlers for CORS preflight requests
@app.options("/scrape-linkedin/")
async def options_linkedin():
    return {"message": "OK"}

@app.options("/scrape-indeed/")
async def options_indeed():
    return {"message": "OK"}

@app.options("/scrape-naukri/")
async def options_naukri():
    return {"message": "OK"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": "2025-07-02T00:19:00Z",
        "endpoints": [
            "/foundit/scrape_foundit",
            "/glassdoor/scrape_jobs", 
            "/simplyhired/scrape_simplyhired",
            "/ziprecruiter/scrape_ziprecruiter",
            "/scrape-linkedin/",
            "/scrape-indeed/",
            "/scrape-naukri/"
        ]
    } 