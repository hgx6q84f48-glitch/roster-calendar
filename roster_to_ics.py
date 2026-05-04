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

        token = page.evaluate(
            "JSON.parse(localStorage.getItem('jStorage')).crew_token"
        )

        browser.close()

        print("✅ Token acquired")
        return token


# ===== FETCH =====
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
        verify=False
    )

    if response.status_code != 200:
        raise Exception("❌ API failed")

    return response.text


# ===== HELPERS =====
def fmt_time(dt_str):
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M").strftime("%H:%M")
    except:
        return None


def fmt_time_short(t):
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M").strftime("%H:%M")
    except:
        return t


# ===== PARSE =====
def parse(xml_data):
    root = ET.fromstring(xml_data)
    activities = []

    for activity in root.iter():
        # ✅ FIX: namespace-safe tag check
        tag = activity.tag.split('}')[-1]
        if 'Activity' not in tag:
            continue

        # ✅ FIX: recursive search
        def get(tag):
            for elem in activity.iter():
                if tag in elem.tag:
                    return elem.text
            return None

        title = get('TypeDescription') or "Duty"
        start = get('LCLStart')
        end = get('LCLEnd')
        report = get('LCLExpectedSignOn')

        if not start or not end:
            continue

        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M")
        except:
            continue

        # ===== COURSE + MODULES =====
        course_elem = None
        for elem in activity:
            if 'Course' in elem.tag:
                course_elem = elem
                break

        description_lines = []

        if course_elem is not None:
            # COURSE INFO
            course_name = None
            base = None

            for c in course_elem:
                if 'Description' in c.tag:
                    course_name = c.text
                if 'Base' in c.tag:
                    base = c.text

            if report:
                description_lines.append(f"Report: {fmt_time(report)}")

            if course_name:
                description_lines.append(f"Course: {course_name}")

            if base:
                description_lines.append(f"Location: {base}")

            description_lines.append("")

            # MODULES (UNCHANGED)
            modules = []
            for m in course_elem.iter():
                if 'Module' not in m.tag:
                    continue

                m_desc = None
                m_start = None
                m_end = None
                m_type = None

                for x in m:
                    if 'Description' in x.tag:
                        m_desc = x.text
                    if 'LCLStart' in x.tag:
                        m_start = x.text
                    if 'LCLEnd' in x.tag:
                        m_end = x.text
                    if 'Type' in x.tag:
                        for t in x:
                            if 'Description' in t.tag:
                                m_type = t.text

                if not m_start or not m_end:
                    continue

                # TYPE CLEANUP (UNCHANGED)
                label = "EVENT"
                if m_type:
                    mt = m_type.lower()
                    if "brief" in mt:
                        label = "BRIEF"
                    elif "sim" in mt:
                        label = "SIM"
                    elif "debrief" in mt:
                        label = "DEBRIEF"

                modules.append((
                    m_start,
                    f"{fmt_time_short(m_start)}–{fmt_time_short(m_end)}  {label} — {m_desc}"
                ))

            # SORT MODULES
            modules.sort(key=lambda x: x[0])

            for _, line in modules:
                description_lines.append(line)

        description = "\n".join(description_lines) if description_lines else None

        activities.append((title, start_dt, end_dt, description))

    print(f"🔍 Found {len(activities)} activities")

    if not activities:
        raise Exception("❌ No activities")

    return activities


# ===== ICS =====
def build_ics(activities):
    cal = Calendar()

    for title, start, end, description in activities:
        event = Event()

        t = title.upper()

        if t == "OPEN DAY":
            summary = "🟡 OPEN"
        elif "OFF" in t:
            summary = "🟢 DAY OFF"
        elif "LEAVE" in t:
            summary = "🎉 LEAVE"
        elif description:
            summary = "📘 TRAINING"
        else:
            summary = "🔴 DUTY"

        event.add('summary', summary)
        event.add('dtstart', start)
        event.add('dtend', end)

        if description:
            event.add('description', description)

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
