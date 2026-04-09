import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from icalendar import Calendar, Event
import os
import subprocess
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from playwright.sync_api import sync_playwright

# ===== CONFIG =====
USERNAME = "SBW412E"
PASSWORD = "9532"

API_URL = "https://saacrewconnect.cocre8.africa/crewApi"

WORKING_DIR = "/Users/KIT/Library/Mobile Documents/com~apple~CloudDocs/Aviation/SAA/Rosters/Automation"
ICS_FILE = os.path.join(WORKING_DIR, "roster.ics")


# ===== GET TOKEN =====
def get_token():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://saacrewconnect.cocre8.africa/html/home.html")
        page.wait_for_selector('input[type="password"]', timeout=15000)

        page.fill('input[type="text"]', USERNAME)
        page.fill('input[type="password"]', PASSWORD)

        # Multi-strategy login
        try:
            page.press('input[type="password"]', 'Enter')
        except:
            pass

        page.wait_for_timeout(2000)

        try:
            page.locator("button:visible").first.click(timeout=3000)
        except:
            pass

        try:
            page.evaluate("""
                () => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const loginBtn = buttons.find(btn =>
                        btn.innerText.toLowerCase().includes('login') ||
                        btn.innerText.toLowerCase().includes('sign')
                    );
                    if (loginBtn) loginBtn.click();
                }
            """)
        except:
            pass

        page.wait_for_load_state("networkidle")

        page.wait_for_function("""
            () => {
                const data = localStorage.getItem('jStorage');
                if (!data) return false;
                try {
                    return JSON.parse(data).crew_token !== undefined;
                } catch {
                    return false;
                }
            }
        """, timeout=20000)

        token = page.evaluate("""
            JSON.parse(localStorage.getItem('jStorage')).crew_token
        """)

        browser.close()
        return token


print("🔐 Logging in...")
TOKEN = get_token()
print("✅ Token acquired")


# ===== BUILD SOAP =====
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
}

SOAP_BODY = f"""<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Header/>
  <soapenv:Body>
    <RosterRequest Version="1.0">
      <Token>{TOKEN}</Token>
      <Data>
        <startDate>2025-10-10</startDate>
        <endDate>2026-10-05</endDate>
        <emplNbr>{USERNAME}</emplNbr>
        <rstrHist>0</rstrHist>
      </Data>
    </RosterRequest>
  </soapenv:Body>
</soapenv:Envelope>
"""


# ===== FETCH =====
response = requests.post(API_URL, data=SOAP_BODY, headers=HEADERS, verify=False)

if response.status_code != 200:
    raise Exception("❌ API failed")

root = ET.fromstring(response.text)


# ===== PARSE =====
activities = []

for activity in root.iter():
    if 'Activity' not in activity.tag:
        continue

    def get(tag):
        for elem in activity:
            if tag in elem.tag:
                return elem.text
        return None

    title = get('TypeDescription') or "Duty"
    start = get('LCLStart')
    end = get('LCLEnd')

    if not start or not end:
        continue

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M")
    except:
        continue

    activities.append({
        "title": title,
        "start": start_dt,
        "end": end_dt
    })

print(f"🔍 Found {len(activities)} activities")

if len(activities) == 0:
    raise Exception("❌ No activities found — token/API issue")


# ===== SORT =====
activities.sort(key=lambda x: x["start"])


# ===== BUILD CALENDAR =====
cal = Calendar()

for act in activities:
    title = act["title"].upper()

    if "OFF" in title:
        summary = "🟢 DAY OFF"
    elif "OPEN" in title:
        summary = "🟡 OPEN"
    elif "LEAVE" in title:
        summary = "🎉 LEAVE"
    elif "TRAINING" in title or "APT" in title:
        summary = "📘 TRAINING"
    else:
        summary = "🔴✈️ DUTY"

    event = Event()
    event.add('summary', summary)
    event.add('dtstart', act["start"])
    event.add('dtend', act["end"])

    cal.add_component(event)


# ===== WRITE FILE =====
temp_file = ICS_FILE + ".tmp"

with open(temp_file, 'wb') as f:
    f.write(cal.to_ical())

os.replace(temp_file, ICS_FILE)

print("✅ Calendar updated")


# ===== GIT PUSH (ONLY IF CHANGED) =====
os.chdir(WORKING_DIR)

diff = subprocess.run(["git", "diff", "--quiet", "roster.ics"])

if diff.returncode != 0:
    subprocess.run(["git", "add", "roster.ics"])
    subprocess.run(["git", "commit", "-m", "auto update"])
    subprocess.run(["git", "push"])
    print("🚀 Changes pushed")
else:
    print("✅ No changes — skipping commit")
