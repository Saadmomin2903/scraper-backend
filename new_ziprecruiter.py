import sys
import time
import json
import re
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.responses import JSONResponse
import uvicorn
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus
import random
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="ZipRecruiter Job Scraper", version="2.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@dataclass
class JobData:
    """Data class for job information with proper typing"""
    title: Optional[str] = None
    jobId: Optional[str] = None
    companyName: Optional[str] = None
    location: Optional[str] = None
    datePosted: Optional[str] = None
    employmentType: Optional[str] = None
    salary: Optional[str] = None
    reference: Optional[str] = None
    companyDescription: Optional[str] = None
    jobDescription: Optional[str] = None
    responsibilities: Optional[str] = None
    qualifications: Optional[str] = None
    keyElements: Optional[str] = None
    additionalInformation: Optional[str] = None
    jdURL: Optional[str] = None

class ZipRecruiterScraper:
    """Improved ZipRecruiter scraper with better error handling and structure"""
    
    def __init__(self, headless: bool = True, timeout: int = 30):
        self.headless = headless
        self.timeout = timeout
        self.driver = None
        self.wait = None
        
        # Improved selectors - more robust and specific
        self.selectors = {
            'job_cards': 'article.job_result, div[data-testid="job-card"], .job-listing',
            'job_links': 'a[data-testid="job-title"], a.jobList-title, h2 a',
            'job_title': 'h1[data-testid="job-title"], h1.u-textH2, .job-title h1',
            'company_name': '[data-testid="company-name"], .text-primary.text-large strong, .company-name',
            'location': '[data-testid="job-location"], .fa-map-marker-alt + span, .location',
            'date_posted': '[data-testid="posted-date"], .text-muted span, .posted-date',
            'employment_type': '[data-testid="employment-type"], .fa-hourglass + span, .employment-type',
            'salary': '[data-testid="salary"], .salary-range, .compensation',
            'job_description': '.job-description, .job-body, [data-testid="job-description"]',
            'next_page': 'a[aria-label="Next page"], .pagination-next, .fa-chevron-right'
        }
    
    def setup_driver(self) -> uc.Chrome:
        """Setup Chrome driver with optimized options"""
        try:
            options = uc.ChromeOptions()
            
            # Essential options for scraping
            if self.headless:
                options.add_argument('--headless=new')
                options.add_argument('--window-size=1920,1080')
            
            # Anti-detection options
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            
            # Performance options
            options.add_argument('--disable-images')
            options.add_argument('--disable-extensions')
            options.add_argument('--disable-plugins')
            
            # User agent rotation
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
            options.add_argument(f'--user-agent={random.choice(user_agents)}')
            
            self.driver = uc.Chrome(options=options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self.wait = WebDriverWait(self.driver, self.timeout)
            
            logger.info("Chrome driver initialized successfully")
            return self.driver
            
        except Exception as e:
            logger.error(f"Failed to setup driver: {e}")
            raise WebDriverException(f"Driver setup failed: {e}")
    
    def random_delay(self, min_seconds: float = 1, max_seconds: float = 3):
        """Add random delay to avoid detection"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)
    
    def safe_find_element(self, by: By, value: str, context=None) -> Optional[str]:
        """Safely find element and return text content"""
        try:
            element_context = context or self.driver
            element = element_context.find_element(by, value)
            return element.text.strip() if element.text else None
        except NoSuchElementException:
            return None
        except Exception as e:
            logger.warning(f"Error finding element {value}: {e}")
            return None
    
    def extract_job_id_from_url(self, url: str) -> Optional[str]:
        """Extract job ID from URL using regex"""
        patterns = [
            r'/jobs/(\w+)-',
            r'jobkey=([^&]+)',
            r'/job/([^/]+)',
            r'id=(\w+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    def extract_sections(self, soup):
        job_body = soup.find('div', class_='job-body')
        sections = {
            'companyDescription': None,
            'jobDescription': None,
            'responsibilities': None,
            'qualifications': None,
            'keyElements': None
        }
        if not job_body:
            return sections

        def get_texts(tags):
            return "\n".join(tag.get_text(" ", strip=True) for tag in tags if tag)

        # Company Description
        about = job_body.find('b', string=lambda s: s and 'About' in s)
        if about:
            company_desc_divs = []
            for sib in about.parent.find_next_siblings('div'):
                if sib.find('b', string=lambda s: s and 'Role Overview' in s):
                    break
                company_desc_divs.append(sib)
            sections['companyDescription'] = get_texts(company_desc_divs)

        # Job Description
        role = job_body.find('b', string=lambda s: s and 'Role Overview' in s)
        if role:
            job_desc_divs = []
            for sib in role.parent.find_next_siblings('div'):
                if sib.find('h3', string='Key Responsibilities'):
                    break
                job_desc_divs.append(sib)
            sections['jobDescription'] = get_texts(job_desc_divs)

        # Responsibilities
        resp_h3 = job_body.find('h3', string='Key Responsibilities')
        if resp_h3:
            ul = resp_h3.find_next('ul')
            if ul:
                sections['responsibilities'] = get_texts(ul.find_all('li'))

        # Qualifications
        qual_h3 = job_body.find('h3', string='What are we looking for')
        if qual_h3:
            ul = qual_h3.find_next('ul')
            if ul:
                sections['qualifications'] = get_texts(ul.find_all('li'))

        # Key Elements
        key_h3 = job_body.find('h3', string='Key elements needed to succeed in this role')
        if key_h3:
            ul = key_h3.find_next('ul')
            if ul:
                sections['keyElements'] = get_texts(ul.find_all('li'))

        # Fallback: If jobDescription is still None, get all <p> tags as jobDescription
        if not sections['jobDescription']:
            p_tags = job_body.find_all('p')
            if p_tags:
                sections['jobDescription'] = get_texts(p_tags)
            else:
                # If no <p>, get all text
                sections['jobDescription'] = job_body.get_text(" ", strip=True)

        # --- Post-process and extract from jobDescription if needed ---
        def clean_text(text):
            if not text:
                return None
            # Remove repeated headers and excessive whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            cleaned = []
            seen_headers = set()
            for line in lines:
                # Remove lines that are just headers (e.g., 'Job Responsibilities:')
                if re.match(r'^(Job Responsibilities|Responsibilities|Qualifications|Key Elements|Preferred Qualifications|Minimum Qualifications|Technical Expertise|Soft Skills)[:\-]?$', line, re.I):
                    if line.lower() in seen_headers:
                        continue
                    seen_headers.add(line.lower())
                    continue
                cleaned.append(line)
            return '\n'.join(cleaned).strip()

        # Clean up jobDescription
        sections['jobDescription'] = clean_text(sections['jobDescription'])

        # Try to extract responsibilities, qualifications, keyElements from jobDescription if not found
        jd = sections['jobDescription'] or ''
        def extract_section_from_text(text, keywords):
            # Find section by keyword, return text until next keyword or end
            pattern = r'(' + '|'.join(re.escape(k) for k in keywords) + r')[:\-]?\s*\n?(.+?)(?=\n(?:' + '|'.join(re.escape(k) for k in keywords) + r')[:\-]?|$)'
            matches = re.findall(pattern, text, re.I | re.S)
            result = {}
            for match in matches:
                key = match[0].lower()
                val = match[1].strip()
                result[key] = val
            return result

        keywords = [
            'Job Responsibilities', 'Responsibilities', 'Qualifications', 'Key Elements',
            'Preferred Qualifications', 'Minimum Qualifications', 'Technical Expertise', 'Soft Skills'
        ]
        extracted = extract_section_from_text(jd, keywords)
        # Map extracted sections to fields if not already set
        if not sections['responsibilities']:
            for k in ['job responsibilities', 'responsibilities']:
                if k in extracted:
                    sections['responsibilities'] = clean_text(extracted[k])
                    break
        if not sections['qualifications']:
            for k in ['qualifications', 'preferred qualifications', 'minimum qualifications']:
                if k in extracted:
                    sections['qualifications'] = clean_text(extracted[k])
                    break
        if not sections['keyElements']:
            for k in ['key elements', 'technical expertise', 'soft skills']:
                if k in extracted:
                    sections['keyElements'] = clean_text(extracted[k])
                    break

        # Clean up other fields
        for field in ['responsibilities', 'qualifications', 'keyElements', 'companyDescription']:
            if sections[field]:
                sections[field] = clean_text(sections[field])

        return sections

    def scrape_job_details(self, job_url: str) -> JobData:
        """Scrape detailed job information from job page with improved section extraction"""
        job = JobData(jdURL=job_url)
        try:
            logger.info(f"Scraping job details from: {job_url}")
            self.driver.get(job_url)
            self.random_delay(2, 4)
            # Wait for job description to be visible
            self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '.job-description, .job-body, [data-testid="job-description"]')))
            # Scroll the job description panel into view
            try:
                details_panel = self.driver.find_element(By.CSS_SELECTOR, '.job-description, .job-body, [data-testid="job-description"]')
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", details_panel)
            except Exception as e:
                logger.warning(f"Could not scroll job details panel: {e}")
            # Optional: Screenshot for debugging
            if self.headless:
                self.driver.save_screenshot('zip_headless_debug.png')
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # Top-level fields using specific selectors
            job.title = self.safe_find_element(By.CSS_SELECTOR, 'h1.u-textH2')
            job.companyName = self.safe_find_element(By.CSS_SELECTOR, '.text-primary.text-large strong')
            job.location = self.safe_find_element(By.CSS_SELECTOR, '.fa-map-marker-alt + span')
            job.datePosted = self.safe_find_element(By.CSS_SELECTOR, '.text-muted span')
            job.employmentType = self.safe_find_element(By.CSS_SELECTOR, '.fa-hourglass + span')
            ref_elem = soup.select_one('.job-posting-reference')
            if ref_elem:
                job.reference = ref_elem.get_text(strip=True).replace('Reference:', '').strip()
            job.jobId = self.extract_job_id_from_url(job_url)

            # Improved section extraction
            sections = self.extract_sections(soup)
            job.companyDescription = sections['companyDescription']
            job.jobDescription = sections['jobDescription']
            job.responsibilities = sections['responsibilities']
            job.qualifications = sections['qualifications']
            job.keyElements = sections['keyElements']

            logger.info(f"Successfully scraped job: {job.title}")
        except TimeoutException:
            logger.error(f"Timeout while scraping job: {job_url}")
        except Exception as e:
            logger.error(f"Error scraping job details from {job_url}: {e}")
        return job
    
    def scrape_jobs(self, job_title: str, location: str, num_jobs: int = 5) -> Dict[str, List[Dict]]:
        """Main scraping function with improved error handling"""
        if not self.driver:
            self.setup_driver()
        
        jobs = []
        scraped_count = 0
        page_num = 1
        
        try:
            # Build search URL with proper encoding
            encoded_location = quote_plus(location)
            encoded_job_title = quote_plus(job_title)
            search_url = f"https://www.ziprecruiter.in/jobs/search?l={encoded_location}&q={encoded_job_title}"
            
            logger.info(f"Starting scrape for '{job_title}' in '{location}' - Target: {num_jobs} jobs")
            
            while scraped_count < num_jobs and page_num <= 10:  # Limit to 10 pages max
                logger.info(f"Scraping page {page_num}...")
                
                try:
                    self.driver.get(search_url)
                    self.random_delay(2, 4)
                    
                    # Wait for job listings to load
                    job_cards = self.wait.until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, self.selectors['job_cards']))
                    )
                    
                    # Get job links from current page
                    job_links = []
                    link_elements = self.driver.find_elements(By.CSS_SELECTOR, self.selectors['job_links'])
                    
                    for elem in link_elements:
                        try:
                            href = elem.get_attribute("href")
                            if href:
                                # Handle relative URLs
                                if href.startswith("/"):
                                    href = urljoin("https://www.ziprecruiter.in", href)
                                job_links.append(href)
                        except Exception as e:
                            logger.warning(f"Error getting job link: {e}")
                            continue
                    
                    logger.info(f"Found {len(job_links)} job links on page {page_num}")
                    
                    # Scrape individual jobs
                    for i, job_url in enumerate(job_links):
                        if scraped_count >= num_jobs:
                            break
                        
                        try:
                            job_data = self.scrape_job_details(job_url)
                            if job_data.title:  # Only add if we got meaningful data
                                jobs.append(asdict(job_data))
                                scraped_count += 1
                                logger.info(f"Scraped job {scraped_count}/{num_jobs}: {job_data.title}")
                            
                            # Go back to search results
                            self.driver.back()
                            self.random_delay(1, 2)
                            
                        except Exception as e:
                            logger.error(f"Error processing job {i+1}: {e}")
                            continue
                    
                    # Try to navigate to next page
                    if scraped_count < num_jobs:
                        try:
                            next_button = self.driver.find_element(By.CSS_SELECTOR, self.selectors['next_page'])
                            next_url = next_button.get_attribute('href')
                            
                            if next_url and next_url != search_url:
                                search_url = next_url
                                page_num += 1
                                self.random_delay(2, 3)
                            else:
                                logger.info("No more pages available")
                                break
                                
                        except NoSuchElementException:
                            logger.info("Next page button not found - end of results")
                            break
                    
                except TimeoutException:
                    logger.error(f"Timeout on page {page_num}")
                    break
                except Exception as e:
                    logger.error(f"Error on page {page_num}: {e}")
                    break
            
            logger.info(f"Scraping completed. Total jobs scraped: {len(jobs)}")
            return {'scraped_jobs': jobs, 'total_scraped': len(jobs), 'requested': num_jobs}
            
        except Exception as e:
            logger.error(f"Fatal error during scraping: {e}")
            return {'scraped_jobs': jobs, 'total_scraped': len(jobs), 'error': str(e)}
        
        finally:
            if self.driver:
                self.driver.quit()
                logger.info("Driver closed")

# API Endpoints
@app.get("/")
def root():
    return {"message": "ZipRecruiter Job Scraper API", "version": "2.0"}

# Request model for POST endpoint
class ZipRecruiterRequest(BaseModel):
    job_title: str
    location: str
    num_jobs: int = 5
    headless: bool = True

@app.get("/scrape_ziprecruiter")
def scrape_ziprecruiter_api(
    job_title: str = Query(..., description="Job title to search for"),
    location: str = Query(..., description="Location to search in"),
    num_jobs: int = Query(5, ge=1, le=50, description="Number of jobs to scrape (1-50)"),
    headless: bool = Query(True, description="Run browser in headless mode")
):
    """
    Scrape job listings from ZipRecruiter (GET)
    """
    try:
        scraper = ZipRecruiterScraper(headless=headless)
        result = scraper.scrape_jobs(job_title, location, num_jobs)
        return JSONResponse(content=result)
    
    except Exception as e:
        logger.error(f"API error: {e}")
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")

@app.post("/scrape_ziprecruiter")
def scrape_ziprecruiter_post_api(request: ZipRecruiterRequest):
    """
    Scrape job listings from ZipRecruiter (POST)
    """
    try:
        scraper = ZipRecruiterScraper(headless=request.headless)
        result = scraper.scrape_jobs(request.job_title, request.location, request.num_jobs)
        return JSONResponse(content=result)
    
    except Exception as e:
        logger.error(f"API error: {e}")
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.options("/scrape_ziprecruiter")
async def options_scrape_ziprecruiter():
    return {"message": "OK"}

@app.options("/scrape_ziprecruiter")
async def options_scrape_ziprecruiter_post():
    return {"message": "OK"}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        uvicorn.run(app, host="0.0.0.0", port=8001, reload=True)
    else:
        # Command line usage
        job_title = sys.argv[1] if len(sys.argv) > 1 else "Data Analyst"
        location = sys.argv[2] if len(sys.argv) > 2 else "India"
        num_jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        
        scraper = ZipRecruiterScraper(headless=False)  # Visible browser for debugging
        result = scraper.scrape_jobs(job_title, location, num_jobs)
        print(json.dumps(result, indent=2))