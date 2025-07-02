import asyncio
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from itertools import cycle
from typing import Dict, List, Optional, Union, Any
from urllib.parse import urljoin, urlparse

import uvicorn
from bs4 import BeautifulSoup, NavigableString
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, start_http_server, REGISTRY
from pydantic import BaseModel, field_validator
from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    ElementClickInterceptedException, StaleElementReferenceException
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from fastapi.middleware.cors import CORSMiddleware
try:
    import groq
except ImportError:
    groq = None

# Metrics
JOBS_SCRAPED = Counter('jobs_scraped_total', 'Total jobs scraped')
SCRAPE_DURATION = Histogram('scrape_duration_seconds', 'Time spent scraping')
ERRORS_TOTAL = Counter('scrape_errors_total', 'Total scraping errors', ['error_type'])
EXTRACTION_SUCCESS = Counter('extraction_success_total', 'Successful field extractions', ['field'])

# Configuration Management
@dataclass
class ScraperConfig:
    """Configuration class for the scraper"""
    chrome_driver_path: str = '/Users/saadmomin/Desktop/glass/chromedriver'
    max_workers: int = 3
    default_timeout: int = 20
    page_load_timeout: int = 30
    max_retries: int = 3
    retry_delay: int = 2
    metrics_port: int = 8001
    
    # Selectors with fallbacks
    selectors: Dict[str, List[str]] = field(default_factory=lambda: {
        'title': [
            "h1[id^='jd-job-title-']",
            "h1[data-test='job-title']",
            ".JobDetails_jobTitle__Nw_N2",
            "h1.css-1qaijid",
            "h1"
        ],
        'company': [
            "h4.EmployerProfile_employerNameHeading__bXBYr",
            "[data-test='employer-name']",
            ".EmployerProfile_profileContainer__d6vLt h4",
            ".EmployerProfile_employerNameHeading__bXBYr h4",
            "h4"
        ],
        'location': [
            "div[data-test='location']",
            ".JobDetails_location__mSg5h",
            "[data-test='job-location']"
        ],
        'salary': [
            "div[data-test='detailSalary']",
            ".JobDetails_salary__6VyJK",
            "[data-test='salary']"
        ],
        'easy_apply': [
            "button[data-test='easyApply']",
            "button[data-test='apply-button']",
            "button.css-1n6j6mr"
        ],
        'company_logo': [
            ".EmployerProfile_profileContainer__63w3R img",
            ".EmployerProfile_logo__3xqON img",
            "img[alt*='logo']"
        ],
        'job_description': [
            "div.JobDetails_jobDescription__uW_fK",
            ".JobDetails_jobDescription__6VeBn",
            "[data-test='jobDescriptionContent']"
        ],
        'show_more': [
            "button[data-test='show-more-cta']",
            "button[data-test='show-more']",
            "button.css-1gpqj0y"
        ],
        'load_more': [
            "button[data-test='load-more']",
            "button[data-test='pagination-footer-next']",
            "button.css-1gpqj0y"
        ],
        'job_links': [
            "a.JobCard_jobTitle__GLyJ1[data-test='job-title']",
            "a[data-test='job-title']",
            ".JobCard_jobTitle__rw2J1 a"
        ]
    })
    
    user_agents: List[str] = field(default_factory=lambda: [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
    ])

# Pydantic Models for API
class JobPosting(BaseModel):
    title: str
    company_name: Optional[str] = None
    location: Optional[str] = None
    salary: Optional[str] = None
    job_type: Optional[str] = None
    pay: Optional[str] = None
    work_location: Optional[str] = None
    benefits: Optional[Union[str, List[str]]] = None
    schedule: Optional[Union[str, List[str]]] = None
    education: Optional[str] = None
    most_relevant_skills: Optional[List[str]] = field(default_factory=list)
    other_relevant_skills: Optional[List[str]] = field(default_factory=list)
    easy_apply: bool = False
    company_logo: Optional[str] = None
    job_description: Optional[str] = None
    extra_sections: Dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None
    jd_url: Optional[str] = None

    @field_validator('title')
    @classmethod
    def title_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Job title cannot be empty')
        return v.strip()

    @field_validator('salary', 'pay')
    @classmethod
    def normalize_currency(cls, v):
        if v:
            return re.sub(r'[^\d,.-₹$€£\s]', '', v).strip()
        return v

    @field_validator('most_relevant_skills', 'other_relevant_skills', mode='before')
    @classmethod
    def ensure_list(cls, v):
        if isinstance(v, str):
            return [skill.strip() for skill in v.split(',') if skill.strip()]
        return v or []

class ScrapeRequest(BaseModel):
    job_title: str
    location: str
    num_jobs: int = Query(5, ge=1, le=50)

class ScrapeResponse(BaseModel):
    scraped_jobs: List[JobPosting]
    metadata: Dict[str, Any]

# Enhanced Logging
class ScraperLogger:
    def __init__(self, name: str = "GlassdoorScraper"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def info(self, msg: str, **kwargs):
        self.logger.info(msg, extra=kwargs)
    
    def error(self, msg: str, **kwargs):
        self.logger.error(msg, extra=kwargs)
        ERRORS_TOTAL.labels(error_type='general').inc()
    
    def warning(self, msg: str, **kwargs):
        self.logger.warning(msg, extra=kwargs)
    
    def debug(self, msg: str, **kwargs):
        self.logger.debug(msg, extra=kwargs)

# Smart Retry Handler
class SmartRetryHandler:
    def __init__(self, logger: ScraperLogger):
        self.logger = logger
        self.retry_strategies = {
            'timeout': self._handle_timeout,
            'stale_element': self._handle_stale_element,
            'no_such_element': self._handle_no_such_element,
            'click_intercepted': self._handle_click_intercepted,
            'general': self._handle_general_error
        }
    
    def determine_retry_strategy(self, exception: Exception) -> str:
        if isinstance(exception, TimeoutException):
            return 'timeout'
        elif isinstance(exception, StaleElementReferenceException):
            return 'stale_element'
        elif isinstance(exception, NoSuchElementException):
            return 'no_such_element'
        elif isinstance(exception, ElementClickInterceptedException):
            return 'click_intercepted'
        return 'general'
    
    def _handle_timeout(self, delay: int) -> int:
        self.logger.warning(f"Timeout occurred, retrying with {delay * 2}s delay")
        return delay * 2
    
    def _handle_stale_element(self, delay: int) -> int:
        self.logger.warning("Stale element reference, refreshing page state")
        return delay
    
    def _handle_no_such_element(self, delay: int) -> int:
        self.logger.warning("Element not found, trying fallback selectors")
        return delay
    
    def _handle_click_intercepted(self, delay: int) -> int:
        self.logger.warning("Click intercepted, scrolling to element")
        return delay
    
    def _handle_general_error(self, delay: int) -> int:
        return delay * 2

def safe_execute_with_retry(func, retries: int = 3, delay: int = 2, logger: ScraperLogger = None):
    """Execute function with smart retry logic"""
    retry_handler = SmartRetryHandler(logger or ScraperLogger())
    
    for attempt in range(retries):
        try:
            return func()
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Failed after {retries} attempts: {str(e)}")
                ERRORS_TOTAL.labels(error_type=type(e).__name__).inc()
                return None
            
            strategy = retry_handler.determine_retry_strategy(e)
            delay = retry_handler.retry_strategies[strategy](delay)
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}, retrying in {delay}s")
            time.sleep(delay)
    
    return None

# Enhanced Driver Manager
class DriverManager:
    def __init__(self, config: ScraperConfig, logger: ScraperLogger):
        self.config = config
        self.logger = logger
        self.user_agent_cycle = cycle(config.user_agents)
    
    def create_driver(self) -> webdriver.Chrome:
        """Create Chrome driver with enhanced options"""
        options = Options()
        options.add_argument('--headless=new')
        
        # Anti-detection measures
        options.add_argument(f'--user-agent={next(self.user_agent_cycle)}')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        # Performance options
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-images')
        
        # Logging options
        options.add_argument('--log-level=3')
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        
        try:
            service = Service(self.config.chrome_driver_path)
            driver = webdriver.Chrome(service=service, options=options)
            
            # Remove webdriver property
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # Set timeouts
            driver.set_page_load_timeout(self.config.page_load_timeout)
            driver.implicitly_wait(5)
            
            self.logger.info("Chrome driver created successfully")
            return driver
            
        except Exception as e:
            self.logger.error(f"Failed to create driver: {str(e)}")
            raise

# Enhanced Field Extractor
class FieldExtractor:
    def __init__(self, config: ScraperConfig, logger: ScraperLogger):
        self.config = config
        self.logger = logger
        self.section_map = {
            'responsibilities': 'responsibilities',
            'key responsibilities': 'responsibilities',
            'qualifications': 'qualifications',
            "what we're looking for": 'requirements',
            'what we are looking for': 'requirements',
            'requirements': 'requirements',
            "what you'll gain": 'perks',
            'what you will gain': 'perks',
            'benefits': 'benefits',
            'schedule': 'schedule',
            'job type': 'jobType',
            'type': 'jobType',
            'contract length': 'contractLength',
            'pay': 'pay',
            'stipend': 'pay',
            'work location': 'workLocation',
            'location': 'workLocation',
            'expected start date': 'expectedStartDate',
            'education': 'education',
            'most relevant skills': 'mostRelevantSkills',
            'other relevant skills': 'otherRelevantSkills',
            'time type': 'timeType',
            'job family group': 'jobFamilyGroup',
            'job family': 'jobFamily',
            'what experience is mandatory': 'mandatoryExperience',
            'what experience is beneficial (but optional)': 'beneficialExperience',
            'what we offer': 'perks',
            'application questions': 'applicationQuestions',
            'application deadline': 'applicationDeadline',
        }
    
    def safe_extract_text(self, driver: webdriver.Chrome, selectors: List[str], attribute: str = None) -> Optional[str]:
        """Extract text using fallback selectors"""
        for selector in selectors:
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
                if attribute:
                    result = element.get_attribute(attribute)
                else:
                    result = element.text.strip()
                
                if result:
                    EXTRACTION_SUCCESS.labels(field=selector.split('.')[0]).inc()
                    return result
                    
            except NoSuchElementException:
                continue
            except Exception as e:
                self.logger.debug(f"Error with selector {selector}: {str(e)}")
                continue
        
        return None
    
    def extract_job_description_sections(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Enhanced job description parsing"""
        sections = {}
        desc_div = soup.find("div", class_="JobDetails_jobDescription__uW_fK")
        
        if not desc_div:
            # Try fallback selectors
            for selector in self.config.selectors['job_description']:
                desc_div = soup.select_one(selector.replace('div.', '').replace('div', ''))
                if desc_div:
                    break
        
        if not desc_div:
            return sections
        
        # Process HTML structure
        current_section = None
        current_content = []
        
        for element in desc_div.descendants:
            if hasattr(element, 'name'):
                if element.name in ['h1', 'h2', 'h3', 'h4', 'b', 'strong']:
                    # Save previous section
                    if current_section and current_content:
                        sections[current_section] = self._process_section_content(current_content)
                    
                    # Start new section
                    section_text = element.get_text(strip=True).lower().rstrip(':')
                    current_section = self.section_map.get(section_text, section_text)
                    current_content = []
                    
                elif element.name in ['p', 'li', 'div'] and current_section:
                    text = element.get_text(strip=True)
                    if text:
                        current_content.append(text)
        
        # Save last section
        if current_section and current_content:
            sections[current_section] = self._process_section_content(current_content)
        
        return sections
    
    def _process_section_content(self, content: List[str]) -> Union[str, List[str]]:
        """Process section content based on type"""
        if len(content) == 1:
            return content[0]
        elif len(content) > 1:
            # Check if it looks like a list
            if all(len(item) < 200 for item in content):
                return content
            else:
                return ' '.join(content)
        return ""
    
    def extract_with_regex_fallback(self, text: str, field: str) -> Optional[str]:
        """Regex-based extraction as fallback"""
        if not text:
            return None
            
        patterns = {
            'jobType': r'\b(full[- ]?time|part[- ]?time|contract|internship|temporary)\b',
            'pay': r'(?:Salary|Pay)[:\-]?\s*([₹$€£]?\s?[\d,\.]+(?:\s*(?:per|/)?\s*\w+)?)',
            'workLocation': r'Work location[:\-]?\s*([A-Za-z, \-/]+)',
            'benefits': r'Benefits[:\-]?\s*(.+)',
            'schedule': r'Schedule[:\-]?\s*(.+)',
            'education': r'Education[:\-]?\s*(.+)',
        }
        
        pattern = patterns.get(field)
        if pattern:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None

# LLM Field Extractor
class LLMFieldExtractor:
    def __init__(self, logger: ScraperLogger):
        self.logger = logger
        self.client = None
        
        if groq:
            api_key = os.environ.get("GROQ_API_KEY")
            if api_key:
                self.client = groq.Groq(api_key=api_key)
            else:
                self.logger.warning("GROQ_API_KEY not found in environment")
    
    def extract_fields(self, job_desc_text: str) -> Dict[str, Any]:
        """Extract fields using LLM as fallback"""
        if not self.client or not job_desc_text:
            return self._get_default_fields()
        
        prompt = self._build_extraction_prompt(job_desc_text)
        
        try:
            response = self.client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=512,
            )
            
            content = response.choices[0].message.content
            fields = self._parse_llm_response(content)
            return self._normalize_llm_fields(fields)
            
        except Exception as e:
            self.logger.error(f"LLM extraction failed: {str(e)}")
            return self._get_default_fields()
    
    def _build_extraction_prompt(self, job_desc_text: str) -> str:
        return f"""
        Extract the following fields from the job description below. 
        If a field is not present, return "Not specified" for strings or an empty list for lists.
        
        Fields to extract:
        - jobType (string): Full-time, Part-time, Contract, Internship, etc.
        - pay (string): Salary or payment information
        - workLocation (string): Remote, On-site, Hybrid, specific location
        - benefits (string): Benefits offered
        - schedule (string): Work schedule information
        - education (string): Education requirements
        - mostRelevantSkills (list): Key technical skills/technologies required
        - otherRelevantSkills (list): Additional skills mentioned
        
        Job Description:
        {job_desc_text[:2000]}  # Limit to avoid token limits
        
        Return as valid JSON only:
        {{
          "jobType": "...",
          "pay": "...",
          "workLocation": "...",
          "benefits": "...",
          "schedule": "...",
          "education": "...",
          "mostRelevantSkills": ["skill1", "skill2"],
          "otherRelevantSkills": ["skill3", "skill4"]
        }}
        """
    
    def _parse_llm_response(self, content: str) -> Dict[str, Any]:
        """Parse LLM response with fallback"""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            
            self.logger.warning("Failed to parse LLM response as JSON")
            return {}
    
    def _normalize_llm_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize LLM extracted fields"""
        normalized = self._get_default_fields()
        
        for key in normalized.keys():
            if key in fields and fields[key]:
                if key in ['mostRelevantSkills', 'otherRelevantSkills']:
                    if isinstance(fields[key], list):
                        normalized[key] = [skill.strip() for skill in fields[key] if skill.strip()]
                    elif isinstance(fields[key], str) and fields[key] != "Not specified":
                        normalized[key] = [skill.strip() for skill in fields[key].split(',') if skill.strip()]
                else:
                    if fields[key] != "Not specified":
                        normalized[key] = fields[key]
        
        return normalized
    
    def _get_default_fields(self) -> Dict[str, Any]:
        return {
            "jobType": "Not specified",
            "pay": "Not specified", 
            "workLocation": "Not specified",
            "benefits": "Not specified",
            "schedule": "Not specified",
            "education": "Not specified",
            "mostRelevantSkills": [],
            "otherRelevantSkills": []
        }

# Main Scraper Class
class GlassdoorScraper:
    def __init__(self, config: ScraperConfig = None):
        self.config = config or ScraperConfig()
        self.logger = ScraperLogger()
        self.driver_manager = DriverManager(self.config, self.logger)
        self.field_extractor = FieldExtractor(self.config, self.logger) 
        self.llm_extractor = LLMFieldExtractor(self.logger)
        self.executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
        
        # Start metrics server
        try:
            start_http_server(self.config.metrics_port)
            self.logger.info(f"Metrics server started on port {self.config.metrics_port}")
        except Exception as e:
            self.logger.warning(f"Failed to start metrics server: {str(e)}")
    
    @SCRAPE_DURATION.time()
    def scrape_jobs(self, job_title: str, location: str, num_jobs: int = 5) -> Dict[str, Any]:
        """Main scraping method with monitoring"""
        start_time = time.time()
        metadata = {
            'start_time': datetime.utcnow().isoformat(),
            'job_title': job_title,
            'location': location,
            'requested_jobs': num_jobs,
            'scraped_jobs': 0,
            'errors': []
        }
        
        try:
            driver = self.driver_manager.create_driver()
            
            try:
                # Navigate and search
                self._perform_search(driver, job_title, location)
                
                # Collect job URLs
                job_urls = self._collect_job_urls(driver, num_jobs)
                self.logger.info(f"Found {len(job_urls)} job URLs")
                
                # Extract job details
                jobs = self._extract_jobs_concurrent(job_urls)
                
                # Save to database
                JOBS_SCRAPED.inc(len(jobs))
                
                metadata.update({
                    'scraped_jobs': len(jobs),
                    'execution_time': time.time() - start_time,
                    'end_time': datetime.utcnow().isoformat()
                })
                
                self.logger.info(f"Successfully scraped {len(jobs)} jobs in {metadata['execution_time']:.2f}s")
                
                return {
                    'scraped_jobs': [job.dict() for job in jobs],
                    'metadata': metadata
                }
                
            finally:
                driver.quit()
                
        except Exception as e:
            self.logger.error(f"Scraping failed: {str(e)}")
            metadata['errors'].append(str(e))
            ERRORS_TOTAL.labels(error_type=type(e).__name__).inc()
            raise
    
    def _perform_search(self, driver: webdriver.Chrome, job_title: str, location: str):
        """Perform job search"""
        self.logger.info(f"Searching for '{job_title}' in '{location}'")
        
        def search_action():
            driver.get("https://www.glassdoor.co.in/Job/index.htm")
            wait = WebDriverWait(driver, self.config.default_timeout)
            # Fill job title
            job_title_input = wait.until(
                EC.presence_of_element_located((By.ID, "searchBar-jobTitle"))
            )
            job_title_input.clear()
            job_title_input.send_keys(job_title)
            # Fill location
            location_input = wait.until(
                EC.presence_of_element_located((By.ID, "searchBar-location"))
            )
            location_input.clear()
            location_input.send_keys(location)
            # Submit search (send RETURN to both fields, as in old code)
            job_title_input.send_keys(Keys.RETURN)
            location_input.send_keys(Keys.RETURN)
            time.sleep(10)  # Wait for results to load (match old code)
            return True
        
        result = safe_execute_with_retry(
            search_action, 
            retries=self.config.max_retries,
            delay=self.config.retry_delay,
            logger=self.logger
        )
        if not result:
            raise RuntimeError("Search action failed after retries.")
        return True

    def _collect_job_urls(self, driver: webdriver.Chrome, num_jobs: int) -> List[str]:
        """Collect job posting URLs from the search results page."""
        job_urls = set()
        selectors = self.config.selectors['job_links']
        while len(job_urls) < num_jobs:
            found = False
            for selector in selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        href = elem.get_attribute('href')
                        if href and href not in job_urls:
                            job_urls.add(href)
                            if len(job_urls) >= num_jobs:
                                break
                    found = True
                except Exception:
                    continue
                if len(job_urls) >= num_jobs:
                    break
            if not found or len(job_urls) >= num_jobs:
                break
            # Try to click 'Show more jobs' or pagination if available
            load_more_selectors = self.config.selectors.get('load_more', [])
            clicked = False
            for btn_selector in load_more_selectors:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, btn_selector)
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(2)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break
        return list(job_urls)[:num_jobs]

    def _extract_jobs_concurrent(self, job_urls: List[str]) -> List[JobPosting]:
        """Extract job details concurrently for each job URL."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(self._extract_jobs_async(job_urls))
        return [job for job in results if job]

    async def _extract_jobs_async(self, job_urls: List[str]) -> List[Optional[JobPosting]]:
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(self.executor, self._extract_single_job, url) for url in job_urls]
        return await asyncio.gather(*tasks)

    def _extract_single_job(self, job_url: str) -> Optional[JobPosting]:
        """Extract all relevant fields from a single job posting page."""
        driver = None
        try:
            driver = self.driver_manager.create_driver()
            driver.get(job_url)
            time.sleep(2)
            # Expand job description if needed
            for selector in self.config.selectors.get('show_more', []):
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, selector)
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(1)
                        break
                except Exception:
                    continue
            # Parse page
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            # Extract fields
            title = self.field_extractor.safe_extract_text(driver, self.config.selectors['title'])
            company = self.field_extractor.safe_extract_text(driver, self.config.selectors['company'])
            location = self.field_extractor.safe_extract_text(driver, self.config.selectors['location'])
            salary = self.field_extractor.safe_extract_text(driver, self.config.selectors['salary'])
            easy_apply = bool(self.field_extractor.safe_extract_text(driver, self.config.selectors['easy_apply']))
            company_logo = self.field_extractor.safe_extract_text(driver, self.config.selectors['company_logo'], attribute='src')
            job_desc_html = self.field_extractor.safe_extract_text(driver, self.config.selectors['job_description'])
            job_desc_text = None
            if job_desc_html:
                try:
                    desc_soup = BeautifulSoup(driver.page_source, 'html.parser')
                    desc_div = desc_soup.select_one(self.config.selectors['job_description'][0])
                    if desc_div:
                        job_desc_text = desc_div.get_text(separator='\n', strip=True)
                except Exception:
                    pass
            # Extract extra sections
            extra_sections = self.field_extractor.extract_job_description_sections(soup)
            # Fallbacks for structured fields
            jobType = extra_sections.get('jobType')
            pay = extra_sections.get('pay')
            workLocation = extra_sections.get('workLocation')
            benefits = extra_sections.get('benefits')
            schedule = extra_sections.get('schedule')
            education = extra_sections.get('education')
            mostRelevantSkills = extra_sections.get('mostRelevantSkills')
            otherRelevantSkills = extra_sections.get('otherRelevantSkills')
            # Regex fallback
            if job_desc_text:
                for field in ['jobType', 'pay', 'workLocation', 'benefits', 'schedule', 'education']:
                    if not locals().get(field):
                        val = self.field_extractor.extract_with_regex_fallback(job_desc_text, field)
                        if val:
                            locals()[field] = val
            # LLM fallback
            if job_desc_text and (not jobType or not pay or not workLocation or not benefits or not schedule or not education or not mostRelevantSkills or not otherRelevantSkills):
                llm_fields = self.llm_extractor.extract_fields(job_desc_text)
                jobType = jobType or llm_fields.get('jobType')
                pay = pay or llm_fields.get('pay')
                workLocation = workLocation or llm_fields.get('workLocation')
                benefits = benefits or llm_fields.get('benefits')
                schedule = schedule or llm_fields.get('schedule')
                education = education or llm_fields.get('education')
                mostRelevantSkills = mostRelevantSkills or llm_fields.get('mostRelevantSkills')
                otherRelevantSkills = otherRelevantSkills or llm_fields.get('otherRelevantSkills')
            # Job ID from URL
            job_id = None
            import re
            m = re.search(r'jl=(\d+)', job_url)
            if m:
                job_id = m.group(1)
            # Build JobPosting
            job_posting = JobPosting(
                title=title or '',
                company_name=company,
                location=location,
                salary=salary,
                job_type=jobType,
                pay=pay,
                work_location=workLocation,
                benefits=benefits,
                schedule=schedule,
                education=education,
                most_relevant_skills=mostRelevantSkills,
                other_relevant_skills=otherRelevantSkills,
                easy_apply=easy_apply,
                company_logo=company_logo,
                job_description=job_desc_text,
                extra_sections=extra_sections,
                job_id=job_id,
                jd_url=job_url
            )
            return job_posting
        except Exception as e:
            self.logger.error(f"Failed to extract job from {job_url}: {str(e)}")
            return None
        finally:
            if driver:
                driver.quit()

# --- FastAPI endpoint and CLI entry point ---

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

scraper_instance = GlassdoorScraper()

# Request model for POST endpoint
class GlassdoorRequest(BaseModel):
    job_title: str
    location: str
    num_jobs: int = 5

@app.get("/scrape_jobs")
def scrape_jobs_api(job_title: str = Query(...), location: str = Query(...), num_jobs: int = Query(5, ge=1, le=50)):
    """API endpoint to scrape Glassdoor jobs (GET)"""
    result = scraper_instance.scrape_jobs(job_title, location, num_jobs)
    return JSONResponse(content=result)

@app.post("/scrape_jobs")
def scrape_jobs_post_api(request: GlassdoorRequest):
    """API endpoint to scrape Glassdoor jobs (POST)"""
    result = scraper_instance.scrape_jobs(request.job_title, request.location, request.num_jobs)
    return JSONResponse(content=result)

@app.options("/scrape_jobs")
async def options_scrape_jobs():
    return {"message": "OK"}

@app.options("/scrape_jobs")
async def options_scrape_jobs_post():
    return {"message": "OK"}

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        uvicorn.run("new_glassdoor:app", host="0.0.0.0", port=8000, reload=True)
    else:
        job_title = sys.argv[1] if len(sys.argv) > 1 else "Software Engineer"
        location = sys.argv[2] if len(sys.argv) > 2 else "San Francisco, CA"
        num_jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        result = scraper_instance.scrape_jobs(job_title, location, num_jobs)
        print(json.dumps(result, indent=2))