import sys
import time
import json
import logging
import random
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
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
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
from urllib.parse import urljoin
from selenium.webdriver.common.keys import Keys

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SimplyHired Job Scraper", version="1.0.0")

@dataclass
class JobData:
    job_id: Optional[str]
    title: Optional[str]
    company_name: Optional[str]
    location: Optional[str]
    company_rating: Optional[str]
    job_description_snippet: Optional[str]
    posted_date: Optional[str]
    job_url: Optional[str]

class SimplyHiredScraper:
    def __init__(self, headless: bool = False, timeout: int = 20):
        self.headless = headless
        self.timeout = timeout
        self.driver = None
        self.wait = None

    def setup_driver(self) -> webdriver.Chrome:
        options = Options()
        if self.headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-images')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-plugins')
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        options.add_argument(f'--user-agent={random.choice(user_agents)}')
        service = Service('./chromedriver')
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.wait = WebDriverWait(driver, self.timeout)
        return driver

    def random_delay(self, min_seconds: float = 1, max_seconds: float = 2):
        time.sleep(random.uniform(min_seconds, max_seconds))

    def fill_search_form(self, driver, job_title: str, location: str):
        driver.get("https://www.simplyhired.co.in/")
        self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-testid="findJobsKeywordInput"]')))
        job_input = driver.find_element(By.CSS_SELECTOR, 'input[data-testid="findJobsKeywordInput"]')
        job_input.clear()
        job_input.send_keys(job_title)
        loc_input = driver.find_element(By.CSS_SELECTOR, 'input[data-testid="findJobsLocationInput"]')
        loc_input.click()
        # Try Mac-style select all and delete
        loc_input.send_keys(Keys.COMMAND, 'a')
        loc_input.send_keys(Keys.DELETE)
        time.sleep(0.2)
        # Extra deletes in case
        for _ in range(5):
            loc_input.send_keys(Keys.DELETE)
        loc_input.clear()
        loc_input.send_keys(location)
        search_btn = driver.find_element(By.CSS_SELECTOR, 'button[data-testid="findJobsSearchSubmit"]')
        search_btn.click()
        self.random_delay(2, 3)

    def extract_job_cards(self, driver) -> List[Dict]:
        self.wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div[data-testid="searchSerpJob"]')))
        cards = driver.find_elements(By.CSS_SELECTOR, 'div[data-testid="searchSerpJob"]')
        jobs = []
        for card in cards:
            try:
                job_id = card.get_attribute('data-jobkey')
                title_elem = card.find_element(By.CSS_SELECTOR, 'h2[data-testid="searchSerpJobTitle"] a')
                title = title_elem.text.strip()
                job_url = urljoin("https://www.simplyhired.co.in", title_elem.get_attribute('href'))
                company_name = None
                location = None
                company_rating = None
                info_spans = card.find_elements(By.CSS_SELECTOR, 'p.chakra-text span')
                for span in info_spans:
                    data_testid = span.get_attribute('data-testid')
                    if data_testid == 'companyName':
                        company_name = span.text.strip()
                    elif data_testid == 'searchSerpJobLocation':
                        location = span.text.strip()
                    elif data_testid == 'searchSerpJobCompanyRating':
                        company_rating = span.text.strip()
                job_description_snippet = None
                try:
                    job_description_snippet = card.find_element(By.CSS_SELECTOR, 'p[data-testid="searchSerpJobSnippet"]').text.strip()
                except Exception:
                    pass
                posted_date = None
                try:
                    posted_date = card.find_element(By.CSS_SELECTOR, 'p[data-testid="searchSerpJobDateStamp"]').text.strip()
                except Exception:
                    pass
                jobs.append({
                    'job_id': job_id,
                    'title': title,
                    'company_name': company_name,
                    'location': location,
                    'company_rating': company_rating,
                    'job_description_snippet': job_description_snippet,
                    'posted_date': posted_date,
                    'job_url': job_url
                })
            except Exception as e:
                logger.warning(f"Failed to extract job card: {e}")
        return jobs

    def go_to_next_page(self, driver) -> bool:
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, 'a[data-testid="pageNumberBlockNext"]')
            if next_btn.is_displayed():
                next_btn.click()
                self.random_delay(2, 3)
                return True
        except Exception:
            pass
        return False

    def extract_job_details(self, driver, job_url: str) -> dict:
        driver.get(job_url)
        self.random_delay(1, 2)
        details = {}
        try:
            # Wait for main job aside to load
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'aside[aria-label]')))
            aside = driver.find_element(By.CSS_SELECTOR, 'aside[aria-label]')
            # Job Title
            try:
                details['title'] = aside.find_element(By.CSS_SELECTOR, 'h1[data-testid="viewJobTitle"]').text.strip()
            except Exception:
                details['title'] = None
            # Company Name
            try:
                details['company_name'] = aside.find_element(By.CSS_SELECTOR, '[data-testid="viewJobCompanyName"] [data-testid="detailText"]').text.strip()
            except Exception:
                details['company_name'] = None
            # Company Logo
            try:
                details['company_logo'] = aside.find_element(By.CSS_SELECTOR, 'img[data-testid="companyVJLogo"]').get_attribute('src')
            except Exception:
                details['company_logo'] = None
            # Company Rating
            try:
                details['company_rating'] = aside.find_element(By.CSS_SELECTOR, '[data-testid="viewJobCompanyRating"] span[aria-hidden="true"]').text.strip()
            except Exception:
                details['company_rating'] = None
            # Location
            try:
                details['location'] = aside.find_element(By.CSS_SELECTOR, '[data-testid="viewJobCompanyLocation"] [data-testid="detailText"]').text.strip()
            except Exception:
                details['location'] = None
            # Posted Date
            try:
                details['posted_date'] = aside.find_element(By.CSS_SELECTOR, '[data-testid="viewJobBodyJobPostingTimestamp"] [data-testid="detailText"]').text.strip()
            except Exception:
                details['posted_date'] = None
            # Benefits
            try:
                benefits = aside.find_elements(By.CSS_SELECTOR, '[data-testid="viewJobBenefitItem"]')
                details['benefits'] = [b.text.strip() for b in benefits if b.text.strip()]
            except Exception:
                details['benefits'] = []
            # Qualifications
            try:
                quals = aside.find_elements(By.CSS_SELECTOR, '[data-testid="viewJobQualificationItem"]')
                details['qualifications'] = [q.text.strip() for q in quals if q.text.strip()]
            except Exception:
                details['qualifications'] = []
            # Full Job Description
            try:
                desc_elem = aside.find_element(By.CSS_SELECTOR, '[data-testid="viewJobBodyJobFullDescriptionContent"]')
                details['full_job_description'] = desc_elem.text.strip()
            except Exception:
                details['full_job_description'] = None
            # Apply URL
            try:
                apply_btn = aside.find_element(By.CSS_SELECTOR, 'a[data-testid="viewJobHeaderFooterApplyButton"]')
                details['apply_url'] = apply_btn.get_attribute('href')
            except Exception:
                details['apply_url'] = None
        except Exception as e:
            logger.warning(f"Failed to extract job details from {job_url}: {e}")
        return details

    def scrape_jobs(self, job_title: str, location: str, num_jobs: int = 5) -> Dict:
        driver = self.setup_driver()
        jobs = []
        try:
            self.fill_search_form(driver, job_title, location)
            while len(jobs) < num_jobs:
                cards = self.extract_job_cards(driver)
                for job in cards:
                    if len(jobs) >= num_jobs:
                        break
                    # Visit job page and extract all details
                    job_details = self.extract_job_details(driver, job['job_url'])
                    job_combined = {**job, **job_details}
                    jobs.append(job_combined)
                if len(jobs) >= num_jobs:
                    break
                if not self.go_to_next_page(driver):
                    break
            return {
                'scraped_jobs': jobs[:num_jobs],
                'scraped_count': len(jobs[:num_jobs])
            }
        except Exception as e:
            logger.error(f"Error during scraping: {e}")
            return {'scraped_jobs': [], 'error': str(e)}
        finally:
            driver.quit()

scraper = SimplyHiredScraper()

@app.get("/scrape_simplyhired")
async def scrape_simplyhired_api(
    job_title: str = Query(..., description="Job title to search for"),
    location: str = Query(..., description="Location to search in"),
    num_jobs: int = Query(5, ge=1, le=50, description="Number of jobs to scrape"),
    headless: bool = Query(False, description="Run browser in headless mode (default: False)")
):
    try:
        scraper = SimplyHiredScraper(headless=headless)
        result = scraper.scrape_jobs(job_title, location, num_jobs)
        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['error'])
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        uvicorn.run("simplyhired_scraper:app", host="0.0.0.0", port=8003, reload=True)
    else:
        job_title = sys.argv[1] if len(sys.argv) > 1 else "Software Engineer"
        location = sys.argv[2] if len(sys.argv) > 2 else "India"
        num_jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        headless = False
        if len(sys.argv) > 4:
            headless = sys.argv[4].lower() == 'true'
        scraper = SimplyHiredScraper(headless=headless)
        result = scraper.scrape_jobs(job_title, location, num_jobs)
        print(json.dumps(result, indent=2)) 