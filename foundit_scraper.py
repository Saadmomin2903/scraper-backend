import sys
import time
import json
import logging
from typing import List, Dict, Optional
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    ElementClickInterceptedException, 
    StaleElementReferenceException,
    TimeoutException,
    NoSuchElementException
)
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.responses import JSONResponse
import uvicorn
from dataclasses import dataclass
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Foundit Job Scraper", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@dataclass
class JobListing:
    job_id: str
    title: str
    company_name: str
    location: str
    experience: str
    posted_time: str
    job_description: str
    skills: List[str]
    industry: str
    job_type: str
    current_url: str

class FounditScraper:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        self.wait = None
        
    def setup_driver(self) -> webdriver.Chrome:
        """Setup Chrome driver with optimized options"""
        options = Options()
        if self.headless:
            options.add_argument('--headless=new')
        
        # Anti-detection measures
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # Performance optimizations
        options.add_argument('--disable-images')
        options.add_argument('--incognito')  # Use incognito mode
        options.page_load_strategy = 'eager'  # Don't wait for all resources
        
        # Added window size argument
        options.add_argument('--window-size=1920,1080')
        
        service = Service('./chromedriver')
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        driver.delete_all_cookies()  # Clear cookies before each run
        
        return driver

    def handle_cookie_banner(self):
        """Handle cookie banner with multiple selectors and strategies (optimized for speed)"""
        # Only valid CSS selectors
        cookie_selectors = [
            "#acceptAll",
            "#cookieBanner #acceptAll",
            ".cookie-accept",
            ".accept-cookies",
            "[data-testid='accept-all']",
            ".cookie-banner button:first-child",
            ".consent-accept",
            "#onetrust-accept-btn-handler"
        ]
        
        for selector in cookie_selectors:
            try:
                # Wait a short time for the banner to appear
                element = WebDriverWait(self.driver, 1).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                if element.is_displayed():
                    element.click()
                    logger.info(f"Clicked cookie accept button using selector: {selector}")
                    time.sleep(0.5)
                    logger.info(f"Cookie accepted using selector: {selector}")
                    return True
            except TimeoutException:
                continue
            except Exception as e:
                logger.warning(f"Failed to click cookie button with selector {selector}: {e}")
                continue
        
        # Try JavaScript click as fallback
        try:
            self.driver.execute_script("""
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var text = buttons[i].textContent.toLowerCase();
                    if (text.includes('accept') || text.includes('allow') || text.includes('continue')) {
                        buttons[i].click();
                        return true;
                    }
                }
                return false;
            """)
            logger.info("Attempted JavaScript cookie acceptance")
            time.sleep(0.5)
            logger.info("Cookie accepted using JavaScript fallback")
            return True
        except Exception as e:
            logger.warning(f"JavaScript cookie handling failed: {e}")
        
        return False

    def safe_find_element(self, parent_element, selector: str, attribute: str = 'text') -> Optional[str]:
        """Safely find element and return text or attribute"""
        try:
            element = parent_element.find_element(By.CSS_SELECTOR, selector)
            if attribute == 'text':
                return element.text.strip()
            else:
                return element.get_attribute(attribute)
        except NoSuchElementException:
            return None

    def extract_job_card_info(self, card) -> Dict:
        """Extract basic info from job card"""
        job_id = card.get_attribute('id')
        title = self.safe_find_element(card, '.jobTitle')
        company = self.safe_find_element(card, '.companyName p')
        
        # Handle multiple experience formats
        experience = (
            self.safe_find_element(card, '.experienceSalary .bodyRow .details') or
            self.safe_find_element(card, '.bodyRow:has(.mqfisrp-briefcase-job) .details')
        )
        
        location = self.safe_find_element(card, '.details.location')
        posted_time = self.safe_find_element(card, '.timeText')
        
        return {
            'job_id': job_id,
            'title': title,
            'company': company,
            'experience': experience,
            'location': location,
            'posted_time': posted_time
        }

    def extract_job_details(self) -> Dict:
        """Extract detailed job information from right panel"""
        details = {}
        
        # Job description
        details['job_description'] = self.safe_find_element(
            self.driver, '.jobDescInfoNew'
        )
        
        # Skills
        skills = []
        try:
            skill_elements = self.driver.find_elements(By.CSS_SELECTOR, '.pillsContainer .pillItem')
            skills = [elem.text.strip() for elem in skill_elements if elem.text.strip()]
        except:
            pass
        details['skills'] = skills
        
        # Additional info
        try:
            info_elements = self.driver.find_elements(By.CSS_SELECTOR, '.infoContainer p')
            for elem in info_elements:
                text = elem.text
                if 'Industry:' in text:
                    details['industry'] = text.split('Industry:')[-1].strip()
                elif 'Job Type:' in text:
                    details['job_type'] = text.split('Job Type:')[-1].strip()
        except:
            details['industry'] = None
            details['job_type'] = None
            
        return details

    def click_job_card(self, card, index: int) -> bool:
        """Safely click on job card with multiple retry strategies"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # Check for cookie banner before clicking
                self.handle_cookie_banner()
                
                # Scroll card into view
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
                time.sleep(0.5)
                
                # Try regular click first
                card.click()
                time.sleep(1)
                # Scroll the right panel into view after clicking
                try:
                    details_panel = self.driver.find_element(By.CSS_SELECTOR, '.jobDescInfoNew')
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", details_panel)
                except Exception as e:
                    logger.warning(f"Could not scroll job details panel: {e}")
                return True
                
            except ElementClickInterceptedException:
                # Handle overlays/modals
                self.close_overlays()
                
                # Try JavaScript click
                try:
                    self.driver.execute_script("arguments[0].click();", card)
                    time.sleep(1)
                    return True
                except:
                    pass
                    
            except StaleElementReferenceException:
                # Re-locate the card
                try:
                    fresh_cards = self.driver.find_elements(By.CSS_SELECTOR, 'div.cardContainer')
                    if index < len(fresh_cards):
                        card = fresh_cards[index]
                        continue
                except:
                    return False
                    
            except Exception as e:
                logger.warning(f"Click attempt {attempt + 1} failed: {e}")
                
            time.sleep(1)
            
        return False

    def close_overlays(self):
        """Close any overlays or modals that might be blocking clicks"""
        overlay_selectors = [
            '.modal-close', '.close', '.overlay-close', 
            '.quickApplyFilter', '[data-dismiss="modal"]',
            '.popup-close', '.dialog-close',
            '.cookie-banner .close', '.cookie-banner .dismiss'
        ]
        
        for selector in overlay_selectors:
            try:
                overlay = self.driver.find_element(By.CSS_SELECTOR, selector)
                if overlay.is_displayed():
                    overlay.click()
                    time.sleep(0.5)
            except:
                continue

    def wait_for_job_details(self) -> bool:
        """Wait for job details to load in right panel"""
        try:
            WebDriverWait(self.driver, 7).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, '.jobDescInfoNew'))
            )
            time.sleep(0.5)
            return True
        except TimeoutException:
            logger.warning("Job details failed to load")
            return False

    def go_to_next_page(self, current_page: int) -> bool:
        """Click the right arrow to go to the next page if enabled. Returns True if successful."""
        try:
            # Handle cookie banner before pagination
            self.handle_cookie_banner()
            
            right_arrow = self.driver.find_element(By.CSS_SELECTOR, '.arrow.arrow-right:not(.disabled)')
            right_arrow.click()
            # Wait for the active page number to increment
            WebDriverWait(self.driver, 10).until(
                lambda d: int(d.find_element(By.CSS_SELECTOR, '.number.activePage').text) == current_page + 1
            )
            time.sleep(1)
            return True
        except Exception as e:
            logger.info(f"No more pages or failed to go to next page: {e}")
            return False

    def scrape_jobs(self, job_title: str, location: str, num_jobs: int = 5) -> Dict:
        """Main scraping method with pagination support"""
        self.driver = self.setup_driver()
        self.wait = WebDriverWait(self.driver, 3)
        jobs = []
        
        try:
            # Visit homepage first to accept cookies
            homepage_url = "https://www.foundit.in/"
            logger.info(f"Loading homepage URL: {homepage_url}")
            self.driver.get(homepage_url)
            self.driver.delete_all_cookies()
            self.handle_cookie_banner()
            time.sleep(0.5)
            
            search_url = (
                f"https://www.foundit.in/srp/results?"
                f"query={job_title.replace(' ', '+')}&"
                f"location={location.replace(' ', '+')}"
            )
            logger.info(f"Loading search URL: {search_url}")
            logger.info("About to load search URL...")
            start_time = time.time()
            self.driver.get(search_url)
            logger.info(f"Search URL loaded in {time.time() - start_time:.2f} seconds")
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div.cardContainer'))
                )
                logger.info("Job cards appeared early, proceeding immediately.")
            except TimeoutException:
                logger.info("Waited 5 seconds, proceeding even if job cards not found yet.")
            self.driver.delete_all_cookies()
            
            start_wait = time.time()
            # Wait for job cards to load
            try:
                self.wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div.cardContainer')))
                logger.info(f"Job cards loaded in {time.time() - start_wait:.2f} seconds")
                logger.info("Job cards loaded successfully")
            except TimeoutException:
                logger.error("No job cards found")
                return {'scraped_jobs': [], 'error': 'No job cards found'}
            
            total_jobs = 0
            scraped_count = 0
            current_page = 1
            
            while len(jobs) < num_jobs:
                # Handle cookie banner on each page
                self.handle_cookie_banner()
                
                # Scrape jobs on the current page
                job_cards = self.driver.find_elements(By.CSS_SELECTOR, 'div.cardContainer')
                total_jobs += len(job_cards)
                jobs_to_scrape = min(num_jobs - len(jobs), len(job_cards))
                
                logger.info(f"Scraping {jobs_to_scrape} jobs from page {current_page}")
                
                for idx in range(jobs_to_scrape):
                    try:
                        # Always re-fetch job cards to avoid stale references
                        job_cards = self.driver.find_elements(By.CSS_SELECTOR, 'div.cardContainer')
                        if idx >= len(job_cards):
                            break
                            
                        card = job_cards[idx]
                        basic_info = self.extract_job_card_info(card)
                        
                        logger.info(f"Processing job {len(jobs)+1}: {basic_info.get('title', 'Unknown')}")
                        
                        if not self.click_job_card(card, idx):
                            logger.warning(f"Failed to click job card {idx}")
                            continue
                            
                        if not self.wait_for_job_details():
                            logger.warning(f"Job details failed to load for job {idx}")
                            continue
                            
                        detailed_info = self.extract_job_details()
                        
                        job_data = {
                            'jobId': basic_info['job_id'],
                            'title': basic_info['title'],
                            'companyName': basic_info['company'],
                            'location': basic_info['location'],
                            'experience': basic_info['experience'],
                            'postedTime': basic_info['posted_time'],
                            'jobDescription': detailed_info.get('job_description'),
                            'skills': detailed_info.get('skills', []),
                            'industry': detailed_info.get('industry'),
                            'jobType': detailed_info.get('job_type'),
                            'currentURL': self.driver.current_url
                        }
                        
                        jobs.append(job_data)
                        
                        if len(jobs) >= num_jobs:
                            break
                            
                    except Exception as e:
                        logger.error(f"Error processing job {idx}: {e}")
                        continue
                
                if len(jobs) >= num_jobs:
                    break
                
                # Try to go to next page if needed
                try:
                    active_page_elem = self.driver.find_element(By.CSS_SELECTOR, '.number.activePage')
                    current_page = int(active_page_elem.text)
                except Exception:
                    current_page += 1
                    
                if not self.go_to_next_page(current_page):
                    break  # No more pages
            
            return {
                'scraped_jobs': jobs,
                'total_found': total_jobs,
                'scraped_count': len(jobs)
            }
            
        except Exception as e:
            logger.error(f"Fatal error during scraping: {e}")
            return {'scraped_jobs': [], 'error': str(e)}
            
        finally:
            if self.driver:
                self.driver.quit()

# Request model for POST endpoint
class FounditRequest(BaseModel):
    job_title: str
    location: str
    num_jobs: int = 5

# Global scraper instance
scraper = FounditScraper()

@app.get("/scrape_foundit")
async def scrape_foundit_api(
    job_title: str = Query(..., description="Job title to search for"),
    location: str = Query(..., description="Location to search in"),
    num_jobs: int = Query(5, ge=1, le=50, description="Number of jobs to scrape")
):
    """API endpoint to scrape Foundit jobs (GET)"""
    try:
        result = scraper.scrape_jobs(job_title, location, num_jobs)
        
        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['error'])
            
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scrape_foundit")
async def scrape_foundit_post_api(request: FounditRequest):
    """API endpoint to scrape Foundit jobs (POST)"""
    try:
        result = scraper.scrape_jobs(request.job_title, request.location, request.num_jobs)
        
        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['error'])
            
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.options("/scrape_foundit")
async def options_scrape_foundit():
    return {"message": "OK"}

@app.options("/scrape_foundit")
async def options_scrape_foundit_post():
    return {"message": "OK"}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        uvicorn.run("foundit_scraper:app", host="0.0.0.0", port=8002, reload=True)
    else:
        # CLI usage
        job_title = sys.argv[1] if len(sys.argv) > 1 else "Software Engineer"
        location = sys.argv[2] if len(sys.argv) > 2 else "India"
        num_jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        
        scraper = FounditScraper()
        result = scraper.scrape_jobs(job_title, location, num_jobs)
        print(json.dumps(result, indent=2))