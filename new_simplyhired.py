import sys
import time
import json
import logging
import random
import csv
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from fastapi.middleware.cors import CORSMiddleware
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    ElementClickInterceptedException, StaleElementReferenceException
)
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Body
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from urllib.parse import urljoin, urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Enhanced logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('simplyhired_scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SimplyHired Job Scraper Pro",
    version="2.0.0",
    description="Advanced SimplyHired job scraper with enhanced features"
)

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
    job_id: Optional[str] = None
    title: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = None
    company_rating: Optional[str] = None
    company_logo: Optional[str] = None
    job_description_snippet: Optional[str] = None
    full_job_description: Optional[str] = None
    posted_date: Optional[str] = None
    job_url: Optional[str] = None
    apply_url: Optional[str] = None
    benefits: Optional[List[str]] = None
    qualifications: Optional[List[str]] = None
    salary_range: Optional[str] = None
    job_type: Optional[str] = None
    experience_level: Optional[str] = None
    scraped_at: Optional[str] = None

    def __post_init__(self):
        if self.scraped_at is None:
            self.scraped_at = datetime.now().isoformat()
        if self.benefits is None:
            self.benefits = []
        if self.qualifications is None:
            self.qualifications = []

class SimplyHiredScraper:
    def __init__(self, headless: bool = True, timeout: int = 20, max_retries: int = 3):
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.driver = None
        self.wait = None
        self.session = self._setup_session()
        self._lock = threading.Lock()

    def _setup_session(self) -> requests.Session:
        """Setup requests session with retry strategy"""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def setup_driver(self) -> webdriver.Chrome:
        """Enhanced driver setup with better stealth options"""
        options = Options()
        options.add_argument('--headless=new')
        
        # Enhanced stealth options
        stealth_options = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--disable-images',
            '--disable-extensions',
            '--disable-plugins',
            '--disable-web-security',
            '--disable-features=VizDisplayCompositor',
            '--disable-gpu',
            '--disable-logging',
            '--disable-dev-tools',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-default-apps',
            '--disable-popup-blocking',
            '--ignore-certificate-errors',
            '--ignore-ssl-errors',
            '--ignore-certificate-errors-spki-list'
        ]
        
        for option in stealth_options:
            options.add_argument(option)

        # Randomize user agent
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        ]
        options.add_argument(f'--user-agent={random.choice(user_agents)}')
        
        # Set window size
        options.add_argument('--window-size=1920,1080')
        
        try:
            service = Service('./chromedriver')
            driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            # Try without custom service path
            driver = webdriver.Chrome(options=options)
        
        # Enhanced stealth JavaScript
        stealth_js = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = {runtime: {}};
        """
        driver.execute_script(stealth_js)
        
        self.wait = WebDriverWait(driver, self.timeout)
        return driver

    def random_delay(self, min_seconds: float = 1, max_seconds: float = 3):
        """Enhanced random delay with variable ranges"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)
        logger.debug(f"Delayed for {delay:.2f} seconds")

    def smart_wait_and_find(self, driver, selector: str, timeout: int = None) -> Optional[any]:
        """Smart element finder with multiple strategies"""
        if timeout is None:
            timeout = self.timeout

        strategies = [
            (By.CSS_SELECTOR, selector)
        ]

        for by, value in strategies:
            if value is None:
                continue
            try:
                element = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
                return element
            except TimeoutException:
                continue
        return None

    def fill_search_form(self, driver, job_title: str, location: str) -> bool:
        """Enhanced form filling with better error handling"""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                logger.info(f"Loading SimplyHired homepage (attempt {attempt + 1})")
                driver.get("https://www.simplyhired.co.in/")
                
                # Wait for page to load completely
                self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                self.random_delay(2, 4)
                
                # Find and fill job title
                job_input = self.smart_wait_and_find(driver, 'input[data-testid="findJobsKeywordInput"]')
                if not job_input:
                    raise Exception("Could not find job title input")
                
                # Clear and fill job title
                job_input.clear()
                self.random_delay(0.5, 1)
                job_input.send_keys(job_title)
                logger.info(f"Filled job title: {job_title}")
                
                # Find and fill location
                loc_input = self.smart_wait_and_find(driver, 'input[data-testid="findJobsLocationInput"]')
                if not loc_input:
                    raise Exception("Could not find location input")
                
                # Enhanced location clearing
                loc_input.click()
                self.random_delay(0.2, 0.5)
                
                # Try multiple clearing methods
                clearing_methods = [
                    lambda: loc_input.send_keys(Keys.CONTROL, 'a'),  # Windows/Linux
                    lambda: loc_input.send_keys(Keys.COMMAND, 'a'),  # Mac
                    lambda: ActionChains(driver).key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL).perform()
                ]
                
                for method in clearing_methods:
                    try:
                        method()
                        loc_input.send_keys(Keys.DELETE)
                        break
                    except Exception:
                        continue
                
                # Additional clearing
                loc_input.clear()
                for _ in range(10):
                    loc_input.send_keys(Keys.BACKSPACE)
                
                self.random_delay(0.2, 0.5)
                loc_input.send_keys(location)
                logger.info(f"Filled location: {location}")
                
                # Find and click search button
                search_btn = self.smart_wait_and_find(driver, 'button[data-testid="findJobsSearchSubmit"]')
                if not search_btn:
                    raise Exception("Could not find search button")
                
                # Scroll to button and click
                driver.execute_script("arguments[0].scrollIntoView(true);", search_btn)
                self.random_delay(0.5, 1)
                
                try:
                    search_btn.click()
                except ElementClickInterceptedException:
                    driver.execute_script("arguments[0].click();", search_btn)
                
                logger.info("Search form submitted")
                self.random_delay(3, 5)
                
                # Verify search results loaded
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[data-testid="searchSerpJob"], .no-results')))
                return True
                
            except Exception as e:
                logger.warning(f"Form fill attempt {attempt + 1} failed: {e}")
                if attempt < max_attempts - 1:
                    self.random_delay(2, 4)
                    continue
                else:
                    raise Exception(f"Failed to fill search form after {max_attempts} attempts: {e}")
        
        return False

    def extract_job_cards(self, driver) -> List[Dict]:
        """Enhanced job card extraction with better error handling"""
        jobs = []
        max_wait_time = 15
        
        try:
            # Wait for job cards or no results message
            self.wait.until(
                EC.any_of(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div[data-testid="searchSerpJob"]')),
                    EC.presence_of_element_located((By.CSS_SELECTOR, '.no-results, [data-testid="noResultsMessage"]'))
                )
            )
            
            # Check for no results
            no_results_elements = driver.find_elements(By.CSS_SELECTOR, '.no-results, [data-testid="noResultsMessage"]')
            if no_results_elements:
                logger.warning("No job results found on this page")
                return jobs
            
            # Get all job cards
            cards = driver.find_elements(By.CSS_SELECTOR, 'div[data-testid="searchSerpJob"]')
            logger.info(f"Found {len(cards)} job cards on current page")
            
            for i, card in enumerate(cards):
                try:
                    # Scroll card into view
                    driver.execute_script("arguments[0].scrollIntoView(true);", card)
                    self.random_delay(0.2, 0.5)
                    
                    job_data = self._extract_single_job_card(card)
                    if job_data and job_data.get('job_id'):
                        jobs.append(job_data)
                        logger.debug(f"Extracted job {i+1}: {job_data.get('title', 'N/A')}")
                    
                except StaleElementReferenceException:
                    logger.warning(f"Stale element reference for card {i+1}, skipping")
                    continue
                except Exception as e:
                    logger.warning(f"Failed to extract job card {i+1}: {e}")
                    continue
            
            logger.info(f"Successfully extracted {len(jobs)} jobs from current page")
            
        except TimeoutException:
            logger.error("Timeout waiting for job cards to load")
        except Exception as e:
            logger.error(f"Error extracting job cards: {e}")
        
        return jobs

    def _extract_single_job_card(self, card) -> Optional[Dict]:
        """Extract data from a single job card"""
        try:
            job_data = {}
            
            # Job ID and URL
            job_data['job_id'] = card.get_attribute('data-jobkey')
            
            # Title and URL
            title_elem = card.find_element(By.CSS_SELECTOR, 'h2[data-testid="searchSerpJobTitle"] a')
            job_data['title'] = title_elem.text.strip()
            relative_url = title_elem.get_attribute('href')
            job_data['job_url'] = urljoin("https://www.simplyhired.co.in", relative_url)
            
            # Company info
            info_elements = card.find_elements(By.CSS_SELECTOR, 'p.chakra-text span')
            for span in info_elements:
                data_testid = span.get_attribute('data-testid')
                text = span.text.strip()
                
                if data_testid == 'companyName' and text:
                    job_data['company_name'] = text
                elif data_testid == 'searchSerpJobLocation' and text:
                    job_data['location'] = text
                elif data_testid == 'searchSerpJobCompanyRating' and text:
                    job_data['company_rating'] = text
            
            # Job description snippet
            try:
                snippet_elem = card.find_element(By.CSS_SELECTOR, 'p[data-testid="searchSerpJobSnippet"]')
                job_data['job_description_snippet'] = snippet_elem.text.strip()
            except NoSuchElementException:
                job_data['job_description_snippet'] = None
            
            # Posted date
            try:
                date_elem = card.find_element(By.CSS_SELECTOR, 'p[data-testid="searchSerpJobDateStamp"]')
                job_data['posted_date'] = date_elem.text.strip()
            except NoSuchElementException:
                job_data['posted_date'] = None
            
            # Salary (if available)
            try:
                salary_elem = card.find_element(By.CSS_SELECTOR, '[data-testid="searchSerpJobSalary"]')
                job_data['salary_range'] = salary_elem.text.strip()
            except NoSuchElementException:
                job_data['salary_range'] = None
            
            return job_data
            
        except Exception as e:
            logger.warning(f"Error extracting single job card: {e}")
            return None

    def go_to_next_page(self, driver) -> bool:
        """Enhanced pagination with better detection"""
        try:
            # Look for next button
            next_selectors = [
                'a[data-testid="pageNumberBlockNext"]',
                'a[aria-label="Next page"]',
                '.pagination-next a',
                '[data-testid*="next"] a'
            ]
            
            next_btn = None
            for selector in next_selectors:
                try:
                    next_btn = driver.find_element(By.CSS_SELECTOR, selector)
                    if next_btn.is_displayed() and next_btn.is_enabled():
                        break
                except NoSuchElementException:
                    continue
            
            if not next_btn:
                logger.info("No next page button found")
                return False
            
            # Check if button is clickable
            href = next_btn.get_attribute('href')
            if not href or 'javascript:' in href:
                logger.info("Next page button is not functional")
                return False
            
            # Scroll to button and click
            driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
            self.random_delay(1, 2)
            
            try:
                next_btn.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", next_btn)
            
            logger.info("Navigated to next page")
            self.random_delay(3, 5)
            
            # Wait for new page to load
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[data-testid="searchSerpJob"]')))
            return True
            
        except Exception as e:
            logger.warning(f"Failed to go to next page: {e}")
            return False

    def extract_job_details(self, driver, job_url: str) -> Dict:
        """Enhanced job details extraction with retry logic"""
        details = {}
        max_attempts = 2
        
        for attempt in range(max_attempts):
            try:
                logger.debug(f"Extracting details from: {job_url} (attempt {attempt + 1})")
                driver.get(job_url)
                self.random_delay(2, 4)
                
                # Wait for main job content
                main_selectors = [
                    'aside[aria-label]',
                    '.job-detail',
                    '[data-testid*="viewJob"]',
                    'main'
                ]
                
                main_container = None
                for selector in main_selectors:
                    try:
                        main_container = self.wait.until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        break
                    except TimeoutException:
                        continue
                
                if not main_container:
                    raise Exception("Could not find main job container")
                
                # Extract all available details
                details.update(self._extract_job_detail_fields(driver, main_container))
                break
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {job_url}: {e}")
                if attempt < max_attempts - 1:
                    self.random_delay(2, 3)
                    continue
                else:
                    logger.error(f"Failed to extract details from {job_url} after {max_attempts} attempts")
        
        return details

    def _extract_job_detail_fields(self, driver, container) -> Dict:
        """Extract detailed job information fields"""
        details = {}
        
        # Field mapping: (field_name, selectors_list)
        field_mappings = {
            'title': [
                'h1[data-testid="viewJobTitle"]',
                '.job-title h1',
                'h1'
            ],
            'company_name': [
                '[data-testid="viewJobCompanyName"] [data-testid="detailText"]',
                '[data-testid="viewJobCompanyName"]',
                '.company-name'
            ],
            'company_logo': [
                'img[data-testid="companyVJLogo"]',
                '.company-logo img',
                'img[alt*="logo"]'
            ],
            'company_rating': [
                '[data-testid="viewJobCompanyRating"] span[aria-hidden="true"]',
                '.company-rating',
                '[data-testid*="rating"]'
            ],
            'location': [
                '[data-testid="viewJobCompanyLocation"] [data-testid="detailText"]',
                '[data-testid="viewJobCompanyLocation"]',
                '.job-location'
            ],
            'posted_date': [
                '[data-testid="viewJobBodyJobPostingTimestamp"] [data-testid="detailText"]',
                '[data-testid*="timestamp"]',
                '.posted-date'
            ],
            'salary_range': [
                '[data-testid="viewJobSalary"]',
                '.salary-range',
                '[data-testid*="salary"]'
            ],
            'job_type': [
                '[data-testid="viewJobType"]',
                '.job-type',
                '[data-testid*="type"]'
            ],
            'experience_level': [
                '[data-testid="viewJobExperienceLevel"]',
                '.experience-level',
                '[data-testid*="experience"]'
            ],
            'full_job_description': [
                '[data-testid="viewJobBodyJobFullDescriptionContent"]',
                '.job-description-content',
                '.job-description'
            ],
            'apply_url': [
                'a[data-testid="viewJobHeaderFooterApplyButton"]',
                '.apply-button a',
                'a[href*="apply"]'
            ]
        }
        
        # Extract single-value fields
        for field, selectors in field_mappings.items():
            for selector in selectors:
                try:
                    if field == 'company_logo':
                        element = container.find_element(By.CSS_SELECTOR, selector)
                        details[field] = element.get_attribute('src')
                    elif field == 'apply_url':
                        element = container.find_element(By.CSS_SELECTOR, selector)
                        details[field] = element.get_attribute('href')
                    else:
                        element = container.find_element(By.CSS_SELECTOR, selector)
                        details[field] = element.text.strip()
                    break
                except NoSuchElementException:
                    continue
            
            if field not in details:
                details[field] = None
        
        # Extract list fields
        list_field_mappings = {
            'benefits': [
                '[data-testid="viewJobBenefitItem"]',
                '.benefit-item',
                '.benefits li'
            ],
            'qualifications': [
                '[data-testid="viewJobQualificationItem"]',
                '.qualification-item',
                '.qualifications li'
            ]
        }
        
        for field, selectors in list_field_mappings.items():
            details[field] = []
            for selector in selectors:
                try:
                    elements = container.find_elements(By.CSS_SELECTOR, selector)
                    details[field] = [elem.text.strip() for elem in elements if elem.text.strip()]
                    if details[field]:
                        break
                except NoSuchElementException:
                    continue
        
        return details

    def scrape_jobs(self, job_title: str, location: str, num_jobs: int = 5, 
                   save_csv: bool = False, detailed_extraction: bool = True) -> Dict:
        """Main scraping method with enhanced features"""
        start_time = time.time()
        driver = None
        jobs = []
        pages_scraped = 0
        
        try:
            logger.info(f"Starting scrape: '{job_title}' in '{location}' (target: {num_jobs} jobs)")
            
            # Setup driver
            driver = self.setup_driver()
            
            # Fill search form
            if not self.fill_search_form(driver, job_title, location):
                raise Exception("Failed to submit search form")
            
            # Scrape jobs across pages
            while len(jobs) < num_jobs:
                logger.info(f"Scraping page {pages_scraped + 1}...")
                
                # Extract job cards from current page
                page_jobs = self.extract_job_cards(driver)
                
                if not page_jobs:
                    logger.warning("No jobs found on current page")
                    break
                
                # Process each job
                for job in page_jobs:
                    if len(jobs) >= num_jobs:
                        break
                    
                    try:
                        if detailed_extraction:
                            # Get detailed information
                            job_details = self.extract_job_details(driver, job['job_url'])
                            # Merge basic and detailed info
                            combined_job = {**job, **job_details}
                        else:
                            combined_job = job
                        
                        # Create JobData object for validation
                        job_obj = JobData(**{k: v for k, v in combined_job.items() 
                                           if k in JobData.__dataclass_fields__})
                        jobs.append(asdict(job_obj))
                        
                        logger.info(f"Scraped job {len(jobs)}/{num_jobs}: {combined_job.get('title', 'N/A')}")
                        
                    except Exception as e:
                        logger.warning(f"Failed to process job: {e}")
                        continue
                
                pages_scraped += 1
                
                # Check if we have enough jobs or can't continue
                if len(jobs) >= num_jobs:
                    break
                
                # Try to go to next page
                if not self.go_to_next_page(driver):
                    logger.info("No more pages available or pagination failed")
                    break
            
            # Final results
            final_jobs = jobs[:num_jobs]
            duration = time.time() - start_time
            
            result = {
                'success': True,
                'scraped_jobs': final_jobs,
                'scraped_count': len(final_jobs),
                'pages_scraped': pages_scraped,
                'duration_seconds': round(duration, 2),
                'search_query': {
                    'job_title': job_title,
                    'location': location,
                    'requested_count': num_jobs
                },
                'timestamp': datetime.now().isoformat()
            }
            
            logger.info(f"Scraping completed: {len(final_jobs)} jobs in {duration:.2f}s across {pages_scraped} pages")
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Scraping failed after {duration:.2f}s: {e}")
            return {
                'success': False,
                'scraped_jobs': jobs,
                'scraped_count': len(jobs),
                'pages_scraped': pages_scraped,
                'duration_seconds': round(duration, 2),
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
        
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception as e:
                    logger.warning(f"Error closing driver: {e}")

# Request model for POST endpoint
class SimplyHiredRequest(BaseModel):
    job_title: str
    location: str
    num_jobs: int = 5
    headless: bool = True
    detailed_extraction: bool = True

# Global scraper instance
scraper = SimplyHiredScraper()

# Background tasks storage
background_tasks_status = {}

@app.get("/")
async def root():
    return {
        "message": "SimplyHired Job Scraper Pro API",
        "version": "2.0.0",
        "endpoints": {
            "/scrape_simplyhired": "Main scraping endpoint",
            "/scrape_async": "Asynchronous scraping endpoint",
            "/task_status/{task_id}": "Check async task status",
            "/health": "Health check",
            "/download/{filename}": "Download CSV files"
        }
    }

@app.get("/scrape_simplyhired")
async def scrape_simplyhired_api(
    job_title: str = Query(..., description="Job title to search for"),
    location: str = Query(..., description="Location to search in"),
    num_jobs: int = Query(5, ge=1, le=100, description="Number of jobs to scrape (1-100)"),
    headless: bool = Query(True, description="Run browser in headless mode"),
    detailed_extraction: bool = Query(True, description="Extract detailed job information")
):
    """Synchronous job scraping endpoint (GET)"""
    try:
        scraper_instance = SimplyHiredScraper(headless=headless)
        result = scraper_instance.scrape_jobs(
            job_title=job_title,
            location=location,
            num_jobs=num_jobs,
            detailed_extraction=detailed_extraction
        )
        if not result.get('success', True) or 'error' in result:
            raise HTTPException(status_code=400, detail=result.get('error', 'Unknown error'))
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scrape_simplyhired")
async def scrape_simplyhired_post_api(request: SimplyHiredRequest):
    """Synchronous job scraping endpoint (POST)"""
    try:
        scraper_instance = SimplyHiredScraper(headless=request.headless)
        result = scraper_instance.scrape_jobs(
            job_title=request.job_title,
            location=request.location,
            num_jobs=request.num_jobs,
            detailed_extraction=request.detailed_extraction
        )
        if not result.get('success', True) or 'error' in result:
            raise HTTPException(status_code=400, detail=result.get('error', 'Unknown error'))
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.options("/scrape_simplyhired")
async def options_scrape_simplyhired():
    return {"message": "OK"}

@app.options("/scrape_simplyhired")
async def options_scrape_simplyhired_post():
    return {"message": "OK"}