import os
import requests
import urllib3
from datetime import datetime, timedelta
from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright
import xml.etree.ElementTree as ET

urllib3.disable_warnings()

USERNAME = os.environ.get("CREW_USER")
PASSWORD = os.environ.get("CREW_PASS")

LOGIN_URL = "https://crewconnect.saa.co.za/"
API_URL = "https://crewconnect.saa.co.za/crewApi"

def get_token():
    print("🔐 Logging in...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(LOGIN_URL)

        page.fill('input[type="text"]', USERNAME)
        page.fill('input[type="password"]', PASSWORD)

        page.keyboard.press("Enter")

        page.wait_for_function(
            "() => localStorage.getItem('crew_token') !== null"
        )

        token = page.evaluate("() => localStorage.getItem('crew_token')")

        browser.close()
        print("✅ Token acquired")
        return token

def get_roster(token):
    today = datetime.utcnow()
    start = today.strftime("%Y-%m-%d")
    end = (today + timedelta(days=30)).strftime("%Y-%m-%d")

    body = f"""
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:crew="http://crewconnect/">
       <soapenv:Header/>
       <soapenv:Body>
          <crew:getRoster>
             <token>{token}</token>
             <fromDate>{start}</fromDate>
             <toDate>{end}</toDate>
          </crew:getRoster>
       </soapenv:Body>
    </soapenv:Envelope>
    """

    headers = {
        "Content-Type": "text/xml"
    }

    response = requests.post(API_URL, data=body, headers=headers, verify=False)
    return response.text

def parse_roster(xml_data):
    root = ET.fromstring(xml_data)
    activities = []

    for act in root.iter("activity"):
        start = act.findtext("start")
        end = act.findtext("end")
        code = act.findtext("code")

        activities.append({
            "start": start,
            "end": end,
            "code": code
        })

    print(f"🔍 Found {len(activities)} activities")
    return activities

def create_ics(activities):
    cal = Calendar()

    for act in activities:
        event = Event()
        event.add("summary", act["code"])
        event.add("dtstart", datetime.fromisoformat(act["start"]))
        event.add("dtend", datetime.fromisoformat(act["end"]))
        cal.add_component(event)

    return cal

def save_file(cal):
    temp_file = "roster.ics.tmp"
    final_file = "roster.ics"

    with open(temp_file, "wb") as f:
        f.write(cal.to_ical())

    os.replace(temp_file, final_file)
    print("📅 ICS file updated")

if __name__ == "__main__":
    token = get_token()
    xml_data = get_roster(token)
    activities = parse_roster(xml_data)
    cal = create_ics(activities)
    save_file(cal)