import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from icalendar import Calendar, Event

from playwright.sync_api import sync_playwright

# ===== CONFIG =====
USERNAME = os.environ.get("CREW_USER")
PASSWORD = os.environ.get("CREW_PASS")

if not USERNAME or not PASSWORD:
    raise Exception("❌ Missing credentials")

API_URL = "https://saacrewconnect.cocre8.africa/crewApi"


# ===== GET TOKEN =====
def get_token():
    print("🔐 Logging in...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://saacrewconnect.cocre8.africa/html/home.html")

        page.wait_for_selector('input[type="password"]', timeout=15000)

        page.fill('input[type="text"]', USERNAME)
        page.fill('input[type="password"]', PASSWORD)

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

        print("✅ Token acquired")
        return token


# ===== FETCH =====
def fetch_roster(token):
    print("📡 Fetching roster...")

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

   def fetch_roster(token):
    print("📡 Fetching roster...")

    import urllib3
    urllib3.disable_warnings()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

    soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Header/>
      <soapenv:Body>
        <RosterRequest Version="1.0">
          <Token>{token}</Token>
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

    response = requests.post(
        API_URL,
        data=soap_body,
        headers=headers,
        verify=False   # 🔥 THIS IS THE FIX
    )

    if response.status_code != 200:
        raise Exception("❌ API failed")

    return response.text

# ===== PARSE =====
def parse(xml_data):
    root = ET.fromstring(xml_data)
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

        activities.append((title, start_dt, end_dt))

    print(f"🔍 Found {len(activities)} activities")

    if not activities:
        raise Exception("❌ No activities")

    return activities


# ===== ICS =====
def build_ics(activities):
    cal = Calendar()

    for title, start, end in activities:
        event = Event()

        t = title.upper()
        if "OFF" in t:
            summary = "🟢 DAY OFF"
        elif "LEAVE" in t:
            summary = "🎉 LEAVE"
        elif "TRAINING" in t:
            summary = "📘 TRAINING"
        else:
            summary = "🔴✈️ DUTY"

        event.add('summary', summary)
        event.add('dtstart', start)
        event.add('dtend', end)

        cal.add_component(event)

    return cal


# ===== SAVE =====
def save(cal):
    with open("roster.ics", "wb") as f:
        f.write(cal.to_ical())

    print("📅 File saved")


# ===== MAIN =====
if __name__ == "__main__":
    token = get_token()
    xml_data = fetch_roster(token)
    activities = parse(xml_data)
    cal = build_ics(activities)
    save(cal)