#old code
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
import json
import time
from selenium.webdriver.common.keys import Keys
import sys
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn
import os
try:
    import groq
except ImportError:
    groq = None

app = FastAPI()

@app.get("/scrape_jobs")
def scrape_jobs_api(job_title: str = Query(...), location: str = Query(...), num_jobs: int = Query(5, ge=1, le=50)):
    result = scrape_with_selenium(job_title, location, num_jobs)
    return JSONResponse(content=result)

def setup_driver():
    """Setup Chrome driver with options to avoid detection"""
    options = Options()
    # Add user agent
    options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36')
    # Disable automation indicators
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    # Other useful options
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    # Enable performance logging
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    service = Service('/Users/saadmomin/Desktop/glass/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)
    # Execute script to remove webdriver property
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def intercept_api_calls(driver):
    """Intercept and capture API calls"""
    # Enable logging
    driver.execute_cdp_cmd('Network.enable', {})
    # Navigate to the target page
    driver.get("https://www.glassdoor.co.in/Job/index.htm")
    # Wait for page to load and trigger API calls
    time.sleep(5)
    # Get network logs
    logs = driver.get_log('performance')
    api_responses = []
    for log in logs:
        message = json.loads(log['message'])
        if message['message']['method'] == 'Network.responseReceived':
            url = message['message']['params']['response']['url']
            # Check if it's the API call we're interested in
            if '/graph' in url:
                request_id = message['message']['params']['requestId']
                try:
                    # Get response body
                    response = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
                    api_responses.append({
                        'url': url,
                        'response': json.loads(response['body'])
                    })
                except:
                    continue
    return api_responses


def scrape_with_selenium(job_title, location, num_jobs=5):
    """Main scraping function using Selenium"""
    driver = setup_driver()
    try:
        driver.get("https://www.glassdoor.co.in/Job/index.htm")
        wait = WebDriverWait(driver, 20)
        # Fill in job name and location before scraping
        job_title_input = wait.until(
            EC.presence_of_element_located((By.ID, "searchBar-jobTitle"))
        )
        job_title_input.clear()
        job_title_input.send_keys(job_title)
        location_input = wait.until(
            EC.presence_of_element_located((By.ID, "searchBar-location"))
        )
        location_input.clear()
        location_input.send_keys(location)
        job_title_input.send_keys(Keys.RETURN)
        location_input.send_keys(Keys.RETURN)
        time.sleep(10)
        driver.save_screenshot("after_search.png")
        jobs = []
        seen_links = set()
        while len(jobs) < num_jobs:
            job_link_elems = driver.find_elements(By.CSS_SELECTOR, "a.JobCard_jobTitle__GLyJ1[data-test='job-title']")
            job_links = []
            for elem in job_link_elems:
                href = elem.get_attribute("href")
                if href and href not in seen_links:
                    job_links.append(href)
                    seen_links.add(href)
            job_links = job_links[:num_jobs - len(jobs)]
            for job_url in job_links:
                driver.get(job_url)
                time.sleep(3)
                # Click 'Show more' button if present to expand full job description
                try:
                    show_more_btn = driver.find_element(By.CSS_SELECTOR, "button[data-test='show-more-cta']")
                    if show_more_btn.is_displayed():
                        show_more_btn.click()
                        time.sleep(1)  # Wait for content to expand
                except Exception as e:
                    pass  # Button not present, continue
                # Extract fields from the detail page
               
                soup = None
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(driver.page_source, 'html.parser')
                except Exception as e:
                    print(f"Error parsing page source: {e}")
                def safe_text(selector, by=By.CSS_SELECTOR, attr=None):
                    try:
                        if by == By.CSS_SELECTOR:
                            elem = driver.find_element(By.CSS_SELECTOR, selector)
                        elif by == By.ID:
                            elem = driver.find_element(By.ID, selector)
                        elif by == By.XPATH:
                            elem = driver.find_element(By.XPATH, selector)
                        if attr:
                            return elem.get_attribute(attr)
                        return elem.text
                    except:
                        return None
                # Title
                title = safe_text("h1[id^='jd-job-title-']")
                # Company
                company = safe_text("h4.EmployerProfile_employerNameHeading__bXBYr")
                if not company and soup:
                    company_heading = soup.select_one("div.EmployerProfile_employerNameHeading__bXBYr h4")
                    if company_heading:
                        company = company_heading.get_text(strip=True)
                # Location
                location_val = safe_text("div[data-test='location']")
                if not location_val and soup:
                    loc_div = soup.select_one("div[data-test='location']")
                    if loc_div:
                        location_val = loc_div.get_text(strip=True)
                # Salary
                salary = safe_text("div[data-test='detailSalary']")
                if not salary and soup:
                    salary_div = soup.select_one("div[data-test='detailSalary']")
                    if salary_div:
                        salary = salary_div.get_text(strip=True)
                # Easy Apply
                easy_apply = False
                try:
                    # Try by data-test
                    easy_apply_btn = driver.find_element(By.CSS_SELECTOR, "button[data-test='easyApply']")
                    if easy_apply_btn and easy_apply_btn.is_displayed():
                        easy_apply = True
                except:
                    # Fallback: check for button with text 'Easy Apply'
                    try:
                        easy_apply_btns = driver.find_elements(By.TAG_NAME, "button")
                        for btn in easy_apply_btns:
                            if btn.is_displayed() and 'easy apply' in btn.text.strip().lower():
                                easy_apply = True
                                break
                    except:
                        easy_apply = False
                # Company Logo
                company_logo = None
                if soup:
                    logo_img = soup.select_one(".EmployerProfile_profileContainer__63w3R img")
                    if logo_img:
                        company_logo = logo_img.get("src")
                # Job Description (as HTML and text)
                job_desc_html = None
                job_desc_text = None
                job_type = None
                pay = None
                work_location = None
                benefits = None
                schedule = None
                education = None
                most_relevant_skills = None
                other_relevant_skills = None
                extra_sections = {}
                if soup:
                    desc_div = soup.find("div", class_="JobDetails_jobDescription__uW_fK")
                    if desc_div:
                        job_desc_html = str(desc_div)
                        for br in desc_div.find_all("br"):
                            br.replace_with("\n")
                        job_desc_text = desc_div.get_text(separator='\n', strip=True)

                        # --- Enhanced section extraction ---
                        section_map = {
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
                        # Track last found section label
                        last_label = None
                        children_list = list(desc_div.children)
                        from bs4 import NavigableString
                        for idx, child in enumerate(children_list):
                            # If it's a <p>, <h2>, <h3>, or <b> section header
                            if hasattr(child, 'name') and child.name in ['p', 'h2', 'h3']:
                                # If <p> contains <b>, treat as label
                                b = child.find('b') if child.name == 'p' else child.find('b') or child
                                if b:
                                    label = b.get_text(strip=True).lower().rstrip(':')
                                    last_label = label
                                    # If next sibling is a <ul>, treat as list for this section
                                    next_sib = None
                                    if idx + 1 < len(children_list):
                                        next_sib = children_list[idx + 1]
                                    if hasattr(next_sib, 'name') and next_sib.name == 'ul':
                                        items = [li.get_text(strip=True) for li in next_sib.find_all('li')]
                                        key = section_map.get(label, label)
                                        if key == 'benefits':
                                            benefits = items
                                        elif key == 'schedule':
                                            schedule = items
                                        elif key == 'requirements':
                                            extra_sections['requirements'] = items
                                        elif key == 'responsibilities':
                                            extra_sections['responsibilities'] = items
                                        elif key == 'perks':
                                            extra_sections['perks'] = items
                                        else:
                                            extra_sections[key] = items
                                        last_label = None
                                        continue
                                    # If next sibling is a <p> with only text, treat as value for this section
                                    if idx + 1 < len(children_list):
                                        next_sib = children_list[idx + 1]
                                        if hasattr(next_sib, 'name') and next_sib.name == 'p':
                                            # Check if <p> has only text (no <b>, <ul>, etc.)
                                            if not next_sib.find('b') and not next_sib.find('ul'):
                                                value = next_sib.get_text(strip=True)
                                                key = section_map.get(label, label)
                                                if key == 'jobType':
                                                    job_type = value
                                                elif key == 'pay':
                                                    pay = value
                                                elif key == 'workLocation':
                                                    work_location = value
                                                elif key == 'benefits':
                                                    benefits = value
                                                elif key == 'schedule':
                                                    schedule = value
                                                elif key == 'education':
                                                    education = value
                                                elif key == 'mostRelevantSkills':
                                                    most_relevant_skills = value
                                                elif key == 'otherRelevantSkills':
                                                    other_relevant_skills = value
                                                elif key == 'contractLength':
                                                    extra_sections['contractLength'] = value
                                                elif key == 'expectedStartDate':
                                                    extra_sections['expectedStartDate'] = value
                                                else:
                                                    extra_sections[key] = value
                                            last_label = None
                                            continue
                                    # Try to get value from same tag (for single-value fields)
                                    content = child.get_text(separator=' ', strip=True).replace(b.get_text(strip=True), '').strip(': ').strip()
                                    if content:
                                        key = section_map.get(label, label)
                                        if key == 'jobType':
                                            job_type = content
                                        elif key == 'pay':
                                            pay = content
                                        elif key == 'workLocation':
                                            work_location = content
                                        elif key == 'benefits':
                                            benefits = content
                                        elif key == 'schedule':
                                            schedule = content
                                        elif key == 'education':
                                            education = content
                                        elif key == 'mostRelevantSkills':
                                            most_relevant_skills = content
                                        elif key == 'otherRelevantSkills':
                                            other_relevant_skills = content
                                        elif key == 'contractLength':
                                            extra_sections['contractLength'] = content
                                        elif key == 'expectedStartDate':
                                            extra_sections['expectedStartDate'] = content
                                        else:
                                            extra_sections[key] = content
                                    continue
                                # If <p> contains a colon, treat as label: value
                                txt = child.get_text(separator=' ', strip=True)
                                if ':' in txt:
                                    label, content = txt.split(':', 1)
                                    label = label.strip().lower()
                                    content = content.strip()
                                    key = section_map.get(label, label)
                                    if key == 'jobType':
                                        job_type = content
                                    elif key == 'pay':
                                        pay = content
                                    elif key == 'workLocation':
                                        work_location = content
                                    elif key == 'benefits':
                                        benefits = content
                                    elif key == 'schedule':
                                        schedule = content
                                    elif key == 'education':
                                        education = content
                                    elif key == 'mostRelevantSkills':
                                        most_relevant_skills = content
                                    elif key == 'otherRelevantSkills':
                                        other_relevant_skills = content
                                    elif key == 'contractLength':
                                        extra_sections['contractLength'] = content
                                    elif key == 'expectedStartDate':
                                        extra_sections['expectedStartDate'] = content
                                    else:
                                        extra_sections[key] = content
                                    last_label = None
                                    continue
                                # If <p> is a label for a following <ul>
                                last_label = txt.strip().lower().rstrip(':')
                            # If it's a <ul> and last_label is set, treat as list for previous section
                            if hasattr(child, 'name') and child.name == 'ul' and last_label:
                                items = [li.get_text(strip=True) for li in child.find_all('li')]
                                key = section_map.get(last_label, last_label)
                                if key == 'benefits':
                                    benefits = items
                                elif key == 'schedule':
                                    schedule = items
                                elif key == 'requirements':
                                    extra_sections['requirements'] = items
                                elif key == 'responsibilities':
                                    extra_sections['responsibilities'] = items
                                elif key == 'perks':
                                    extra_sections['perks'] = items
                                else:
                                    extra_sections[key] = items
                                last_label = None
                            # If it's a <ul> and no last_label, assign to generic section
                            if hasattr(child, 'name') and child.name == 'ul' and not last_label:
                                items = [li.get_text(strip=True) for li in child.find_all('li')]
                                extra_sections.setdefault('other', []).extend(items)
                            # If it's a NavigableString and last_label is set, treat as value for previous section
                            if isinstance(child, NavigableString) and last_label:
                                content = str(child).strip()
                                if content:
                                    key = section_map.get(last_label, last_label)
                                    if key == 'jobType' and not job_type:
                                        job_type = content
                                    elif key == 'pay' and not pay:
                                        pay = content
                                    elif key == 'workLocation' and not work_location:
                                        work_location = content
                                    elif key == 'benefits' and not benefits:
                                        benefits = content
                                    elif key == 'schedule' and not schedule:
                                        schedule = content
                                    elif key == 'education' and not education:
                                        education = content
                                    elif key == 'mostRelevantSkills' and not most_relevant_skills:
                                        most_relevant_skills = content
                                    elif key == 'otherRelevantSkills' and not other_relevant_skills:
                                        other_relevant_skills = content
                                    elif key == 'contractLength':
                                        extra_sections['contractLength'] = content
                                    elif key == 'expectedStartDate':
                                        extra_sections['expectedStartDate'] = content
                                    else:
                                        extra_sections[key] = content
                                    last_label = None
                        # Fallback: parse for label: value pairs in <p> tags (legacy logic)
                        import re
                        for p in desc_div.find_all("p"):
                            txt = p.get_text(separator=' ', strip=True)
                            # Try to match 'Label: Value' or 'Label - Value'
                            m = re.match(r"([\w\s'']+)[:\-]\s*(.+)", txt)
                            if m:
                                label = m.group(1).strip().lower()
                                content = m.group(2).strip()
                                key = section_map.get(label, label)
                                if key == 'jobType' and not job_type:
                                    job_type = content
                                elif key == 'pay' and not pay:
                                    pay = content
                                elif key == 'workLocation' and not work_location:
                                    work_location = content
                                elif key == 'benefits' and not benefits:
                                    benefits = content
                                elif key == 'schedule' and not schedule:
                                    schedule = content
                                elif key == 'education' and not education:
                                    education = content
                                elif key == 'mostRelevantSkills' and not most_relevant_skills:
                                    most_relevant_skills = content
                                elif key == 'otherRelevantSkills' and not other_relevant_skills:
                                    other_relevant_skills = content
                                elif key == 'contractLength':
                                    extra_sections['contractLength'] = content
                                elif key == 'expectedStartDate':
                                    extra_sections['expectedStartDate'] = content
                                else:
                                    extra_sections[key] = content
                            # Also handle 'Pay: From ₹5,000.00 per month' and similar
                            if txt.lower().startswith('pay:') and not pay:
                                pay = txt[4:].strip()
                            if txt.lower().startswith('job type:') and not job_type:
                                job_type = txt[9:].strip()
                            if txt.lower().startswith('contract length:'):
                                extra_sections['contractLength'] = txt[16:].strip()
                            if txt.lower().startswith('work location:') and not work_location:
                                work_location = txt[14:].strip()
                            if txt.lower().startswith('expected start date:'):
                                extra_sections['expectedStartDate'] = txt[20:].strip()
                # Collect jobId from URL if possible
                import re
                job_id = None
                m = re.search(r'jl=(\d+)', job_url)
                if not m:
                    m = re.search(r'job-title-(\d+)', driver.page_source)
                if not m:
                    m = re.search(r'jlid=(\d+)', driver.page_source)
                if m:
                    job_id = m.group(1)
                # Fallback extraction using regex on job_desc_text if fields are still missing
                import re
                def extract_field_by_regex(text, field):
                    patterns = {
                        'jobType': r'\b(full[- ]?time|part[- ]?time|contract|internship|temporary)\b',
                        'pay': r'(?:Salary|Pay)[:\-]?\s*([₹$€£]?\s?[\d,\.]+(?:\s*(?:per|/)?\s*\w+)?)',
                        'workLocation': r'Work location[:\-]?\s*([A-Za-z, \-/]+)',
                        'benefits': r'Benefits[:\-]?\s*(.+)',
                        'schedule': r'Schedule[:\-]?\s*(.+)',
                        'education': r'Education[:\-]?\s*(.+)',
                        'mostRelevantSkills': r'Most Relevant Skills[:\-]?\s*(.+)',
                        'otherRelevantSkills': r'Other Relevant Skills[:\-]?\s*(.+)',
                    }
                    pattern = patterns.get(field)
                    if not pattern or not text:
                        return None
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        return match.group(1).strip()
                    return None

                # Only run regex fallback if job_desc_text is available
                if job_desc_text:
                    if not job_type:
                        job_type = extract_field_by_regex(job_desc_text, 'jobType')
                    if not pay:
                        pay = extract_field_by_regex(job_desc_text, 'pay')
                    if not work_location:
                        work_location = extract_field_by_regex(job_desc_text, 'workLocation')
                    if not benefits:
                        benefits = extract_field_by_regex(job_desc_text, 'benefits')
                    if not schedule:
                        schedule = extract_field_by_regex(job_desc_text, 'schedule')
                    if not education:
                        education = extract_field_by_regex(job_desc_text, 'education')
                    if not most_relevant_skills:
                        most_relevant_skills = extract_field_by_regex(job_desc_text, 'mostRelevantSkills')
                    if not other_relevant_skills:
                        other_relevant_skills = extract_field_by_regex(job_desc_text, 'otherRelevantSkills')

                # LLM fallback using Groq Llama if any field is still missing
                if job_desc_text and (
                    not job_type or not pay or not work_location or not benefits or not schedule or not education or not most_relevant_skills or not other_relevant_skills
                ):
                    llama_fields = extract_fields_with_llama(job_desc_text)
                    job_type = job_type or llama_fields.get("jobType")
                    pay = pay or llama_fields.get("pay")
                    work_location = work_location or llama_fields.get("workLocation")
                    benefits = benefits or llama_fields.get("benefits")
                    schedule = schedule or llama_fields.get("schedule")
                    education = education or llama_fields.get("education")
                    most_relevant_skills = most_relevant_skills or llama_fields.get("mostRelevantSkills")
                    other_relevant_skills = other_relevant_skills or llama_fields.get("otherRelevantSkills")

                # Add job dict (after all fallback logic)
                jobs.append({
                    'title': title,
                    'companyName': company,
                    'location': location_val,
                    'salary': salary,
                    'jobType': job_type,
                    'pay': pay,
                    'workLocation': work_location,
                    'benefits': benefits,
                    'schedule': schedule,
                    'education': education,
                    'mostRelevantSkills': most_relevant_skills,
                    'otherRelevantSkills': other_relevant_skills,
                    'easyApply': easy_apply,
                    'companyLogo': company_logo,
                    'jobDescription': job_desc_text,
                    'extraSections': extra_sections,
                    'jobId': job_id,
                    'jdURL': job_url
                })
            if len(jobs) >= num_jobs:
                break
            # Try to click 'Show more jobs' button
            try:
                show_more_btn = driver.find_element(By.CSS_SELECTOR, "button[data-test='load-more']")
                if show_more_btn.is_displayed():
                    show_more_btn.click()
                    time.sleep(3)
                else:
                    break
            except Exception:
                break
        return {'scraped_jobs': jobs}
    finally:
        driver.quit()


def extract_fields_with_llama(job_desc_text):
    prompt = f"""
    Extract the following fields from the job description below. 
    If a field is not present, return \"Not specified\" for strings or an empty list for lists.
    Fields:
    - jobType (string)
    - pay (string)
    - workLocation (string)
    - benefits (string)
    - schedule (string)
    - education (string)
    - mostRelevantSkills (list of specific skills/technologies, e.g. [\"Python\", \"SQL\", \"Tableau\"])
    - otherRelevantSkills (list of other skills/technologies, e.g. [\"Project Management\", \"Data Visualization\"])
    If no skills are found, return an empty list (not a string like \"see above\").

    Job Description:
    {job_desc_text}

    Return the result as a JSON object with all fields present, like:
    {{
      \"jobType\": \"...\",
      \"pay\": \"...\",
      \"workLocation\": \"...\",
      \"benefits\": \"...\",
      \"schedule\": \"...\",
      \"education\": \"...\",
      \"mostRelevantSkills\": [\"...\", \"...\"],
      \"otherRelevantSkills\": [\"...\", \"...\"]
    }}
    """
    if groq is None:
        raise ImportError("groq Python package is not installed. Please install it with 'pip install groq'.")
    client = groq.Groq(api_key=os.environ.get("GROQ_API_KEY"))
    try:
        response = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
        import json
        import re
        content = response.choices[0].message.content
        try:
            fields = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    fields = json.loads(match.group(0))
                except Exception as e2:
                    print("LLM output not valid JSON after extraction:", match.group(0))
                    print("Secondary JSON error:", e2)
                    fields = {}
            else:
                print("LLM output not valid JSON:", content)
                fields = {}
        # Post-process to ensure all fields are present and correct type
        def normalize_llm_fields(fields):
            keys_str = ["jobType", "pay", "workLocation", "benefits", "schedule", "education"]
            keys_list = ["mostRelevantSkills", "otherRelevantSkills"]
            for k in keys_str:
                if k not in fields or not fields[k] or fields[k] is None:
                    fields[k] = "Not specified"
            for k in keys_list:
                # If missing or not a list, set to []
                if k not in fields or not isinstance(fields[k], list):
                    fields[k] = []
                # If it's a string (e.g. 'see above'), set to []
                elif isinstance(fields[k], str) or (isinstance(fields[k], list) and len(fields[k]) == 1 and isinstance(fields[k][0], str) and fields[k][0].lower().startswith("see above")):
                    fields[k] = []
            return fields
        return normalize_llm_fields(fields)
    except Exception as e:
        print("LLM extraction error:", e)
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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        # Run FastAPI server
        uvicorn.run("run_glassdoor_scraper:app", host="0.0.0.0", port=8000, reload=True)
    else:
        # Accept job name and location as command-line arguments
        job_title = sys.argv[1] if len(sys.argv) > 1 else "Software Engineer"
        location = sys.argv[2] if len(sys.argv) > 2 else "San Francisco, CA"
        num_jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        result = scrape_with_selenium(job_title, location, num_jobs)
        print(json.dumps(result, indent=2)) 