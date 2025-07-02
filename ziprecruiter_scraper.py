import sys
import time
import json
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn
import undetected_chromedriver as uc
from bs4 import BeautifulSoup

app = FastAPI()

def setup_driver():
    options = uc.ChromeOptions()
    # Comment out or remove headless mode for visible browser
    # options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = uc.Chrome(options=options)
    return driver

def scrape_ziprecruiter(job_title, location, num_jobs=5):
    driver = setup_driver()
    jobs = []
    try:
        # Build initial search URL
        search_url = f"https://www.ziprecruiter.in/jobs/search?l={location.replace(' ', '+')}&q={job_title.replace(' ', '+')}"
        driver.get(search_url)
        wait = WebDriverWait(driver, 20)
        scraped_count = 0
        while len(jobs) < num_jobs:
            # Wait for job cards to load
            try:
                job_cards = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "li.job-listing")))
            except Exception:
                break
            # Collect job links on this page
            job_link_elems = driver.find_elements(By.CSS_SELECTOR, "a.jobList-title.job-link")
            job_links = []
            for elem in job_link_elems:
                href = elem.get_attribute("href")
                if href and href.startswith("/"):
                    href = "https://www.ziprecruiter.in" + href
                job_links.append(href)
            # Limit to remaining jobs needed
            job_links = job_links[:num_jobs - len(jobs)]
            for job_url in job_links:
                driver.get(job_url)
                try:
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div.job-body"))
                    )
                except Exception:
                    continue
                time.sleep(2)
                print(f"Current window handles AFTER navigation: {driver.window_handles}")
                try:
                    screenshot_path = f"job_detail_debug_{job_links.index(job_url)}.png"
                    driver.save_screenshot(screenshot_path)
                    print(f"Saved screenshot to {screenshot_path}")
                except Exception as e:
                    print(f"Error taking screenshot: {e}")
                try:
                    print("\n===== JOB DETAIL PAGE HTML START =====\n")
                    print(driver.page_source)
                    print("\n===== JOB DETAIL PAGE HTML END =====\n")
                except Exception as e:
                    print(f"Error printing page source: {e}")

                # --- Extract rich content fields using regex-based section extraction ---
                def extract_sections_from_text(text):
                    section_patterns = [
                        (r"Key Responsibilities?:", "jobDescription"),
                        (r"Role Overview :", "jobDescription"),
                        (r"Role Summary:", "jobDescription"),
                        (r"Responsibilities:", "jobDescription"),
                        (r"Key Requirements?:", "qualifications"),
                        (r"Required Skills", "qualifications"),
                        (r"Preferences?:", "additionalInformation"),
                        (r"Preferred Skills", "additionalInformation"),
                    ]
                    headers_regex = "|".join(f"({pat})" for pat, _ in section_patterns)
                    matches = list(re.finditer(headers_regex, text))
                    sections = {}
                    for i, match in enumerate(matches):
                        start = match.end()
                        end = matches[i+1].start() if i+1 < len(matches) else len(text)
                        header = match.group(0)
                        for pat, field in section_patterns:
                            if re.match(pat, header):
                                sections[field] = text[start:end].strip()
                                break
                    return (
                        None,  # companyDescription (not present in these examples)
                        sections.get("jobDescription"),
                        sections.get("qualifications"),
                        sections.get("additionalInformation"),
                    )
                # Extract text from div.job-body
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                job_body = soup.find('div', class_='job-body')
                if job_body:
                    text = job_body.get_text(separator=' ', strip=True)
                    companyDescription, jobDescription, qualifications, additionalInformation = extract_sections_from_text(text)
                else:
                    companyDescription = jobDescription = qualifications = additionalInformation = None
                job = {
                    'title': job_url.split('/')[-1],
                    'jobId': None,
                    'companyName': None,
                    'location': None,
                    'datePosted': None,
                    'employmentType': None,
                    'reference': None,
                    'companyDescription': companyDescription,
                    'jobDescription': jobDescription,
                    'qualifications': qualifications,
                    'additionalInformation': additionalInformation,
                    'jdURL': job_url
                }
                # Extract details from detailed page
                try:
                    detail_panel = driver.find_element(By.CSS_SELECTOR, "div.panel-body")
                    # Title
                    try:
                        detail_title = detail_panel.find_element(By.CSS_SELECTOR, "h1.u-textH2").text
                    except:
                        detail_title = job_url.split('/')[-1]
                    # Company
                    try:
                        detail_company = detail_panel.find_element(By.CSS_SELECTOR, ".text-primary.text-large strong").text
                    except:
                        detail_company = None
                    # Location
                    try:
                        detail_location = detail_panel.find_element(By.CSS_SELECTOR, ".fa-map-marker-alt + span").text
                    except:
                        detail_location = None
                    # Date posted
                    try:
                        date_posted = detail_panel.find_element(By.CSS_SELECTOR, ".text-muted span").text
                    except:
                        date_posted = None
                    # Employment type
                    try:
                        employment_type = detail_panel.find_element(By.CSS_SELECTOR, ".fa-hourglass + span").text
                    except:
                        employment_type = None
                    # Reference number
                    try:
                        reference = detail_panel.find_element(By.CSS_SELECTOR, ".job-posting-reference").text
                    except:
                        reference = None
                    # jobId from URL
                    job_url = job_url
                    match = re.search(r'/jobs/(\d+)-', job_url)
                    if match:
                        job['jobId'] = match.group(1)
                except Exception as e:
                    print(f"Error extracting details: {e}")
                    detail_title = job_url.split('/')[-1]
                    detail_company = None
                    detail_location = None
                    date_posted = None
                    employment_type = None
                    reference = None
                    companyDescription = None
                    jobDescription = None
                    qualifications = None
                    additionalInformation = None
                    job_url = None
                    job['jobId'] = None
                job['title'] = detail_title
                job['companyName'] = detail_company
                job['location'] = detail_location
                job['datePosted'] = date_posted
                job['employmentType'] = employment_type
                job['reference'] = reference
                jobs.append(job)
                # Go back to the search results page
                driver.back()
                WebDriverWait(driver, 20).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "li.job-listing"))
                )
                time.sleep(2)
            if len(jobs) >= num_jobs:
                break
            # Try to go to next page
            try:
                next_li = driver.find_element(By.CSS_SELECTOR, 'ul.pagination li a i.fa-chevron-right')
                next_link = next_li.find_element(By.XPATH, '..')
                next_url = next_link.get_attribute('href')
                if not next_url.startswith('http'):
                    next_url = 'https://www.ziprecruiter.in' + next_url
                driver.get(next_url)
                time.sleep(2)
            except Exception:
                break
        return {'scraped_jobs': jobs}
    finally:
        driver.quit()

@app.get("/scrape_ziprecruiter")
def scrape_ziprecruiter_api(job_title: str = Query(...), location: str = Query(...), num_jobs: int = Query(5, ge=1, le=50)):
    result = scrape_ziprecruiter(job_title, location, num_jobs)
    return JSONResponse(content=result)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        uvicorn.run("ziprecruiter_scraper:app", host="0.0.0.0", port=8001, reload=True)
    else:
        job_title = sys.argv[1] if len(sys.argv) > 1 else "Data Analyst"
        location = sys.argv[2] if len(sys.argv) > 2 else "India"
        num_jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        result = scrape_ziprecruiter(job_title, location, num_jobs)
        print(json.dumps(result, indent=2)) 