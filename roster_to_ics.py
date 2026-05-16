import os
import re
import requests
import urllib3
import xml.etree.ElementTree as ET

from datetime import datetime
from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)

# =====================================================
# CONFIG
# =====================================================

USERNAME = os.environ.get("CREW_USER")
PASSWORD = os.environ.get("CREW_PASS")

if not USERNAME or not PASSWORD:
    raise Exception("❌ Missing CREW_USER / CREW_PASS")

LOGIN_URL = "https://saacrewconnect.cocre8.africa/html/home.html"
ROSTER_URL = "https://saacrewconnect.cocre8.africa/php/roster.php"

# =====================================================
# HELPERS
# =====================================================

def fmt_time(dt_str):

    try:
        return datetime.strptime(
            dt_str,
            "%Y-%m-%d %H:%M"
        ).strftime("%H:%M")

    except:
        return ""


def fmt_full(dt):

    return dt.strftime("%d %b %Y %H:%M")


def clean(s):

    if not s:
        return ""

    return re.sub(r"\s+", " ", s).strip()


# =====================================================
# LOGIN
# =====================================================

def login():

    print("🔐 Logging in...")

    playwright = sync_playwright().start()

    browser = playwright.chromium.launch(
        headless=True
    )

    context = browser.new_context()

    page = context.new_page()

    page.goto(LOGIN_URL)

    page.wait_for_selector(
        'input[type="password"]',
        timeout=15000
    )

    page.fill(
        'input[type="text"]',
        USERNAME
    )

    page.fill(
        'input[type="password"]',
        PASSWORD
    )

    try:
        page.press(
            'input[type="password"]',
            'Enter'
        )
    except:
        pass

    page.wait_for_timeout(3000)

    try:
        page.locator(
            "button:visible"
        ).first.click(timeout=3000)
    except:
        pass

    page.wait_for_load_state("networkidle")

    print("✅ Logged in")

    return playwright, browser, context, page


# =====================================================
# OPEN ROSTER
# =====================================================

def open_roster(page):

    print("🛰 Opening roster...")

    page.goto(ROSTER_URL)

    page.wait_for_load_state("networkidle")

    page.wait_for_timeout(5000)


# =====================================================
# FETCH ROSTER XML
# =====================================================

def fetch_roster_xml(page):

    responses = []

    page.on(
        "response",
        lambda response: responses.append(response)
    )

    page.reload()

    page.wait_for_load_state("networkidle")

    page.wait_for_timeout(5000)

    for response in responses:

        try:

            if "crewApi" not in response.url:
                continue

            text = response.text()

            if "RosterResponse" in text:

                print("✅ Roster XML found")

                return text

        except:
            pass

    raise Exception(
        "❌ Could not find roster response"
    )


# =====================================================
# PARSE ROSTER XML
# =====================================================

def parse(xml_data):

    root = ET.fromstring(xml_data)

    activities = []

    for activity in root.iter():

        tag = activity.tag.split('}')[-1]

        if "Activity" not in tag:
            continue

        def get(tag_name):

            for elem in activity.iter():

                if tag_name in elem.tag:
                    return elem.text

            return None

        title = get("TypeDescription") or "Duty"

        start = get("LCLStart")
        end = get("LCLEnd")
        report = get("LCLExpectedSignOn")

        if not start or not end:
            continue

        try:

            start_dt = datetime.strptime(
                start,
                "%Y-%m-%d %H:%M"
            )

            end_dt = datetime.strptime(
                end,
                "%Y-%m-%d %H:%M"
            )

        except:
            continue

        description_lines = []

        # =============================================
        # REPORT
        # =============================================

        if report:

            report_dt = datetime.strptime(
                report,
                "%Y-%m-%d %H:%M"
            )

            description_lines.append(
                f"Report: {fmt_full(report_dt)}"
            )

            description_lines.append("")

        # =============================================
        # PAIRING
        # =============================================

        pairing = None

        for elem in activity:

            if "Pairing" in elem.tag:
                pairing = elem
                break

        if pairing is not None:

            flight_lines = []

            for leg in pairing.iter():

                tag_name = leg.tag.split('}')[-1]

                if tag_name != "Leg":
                    continue

                leg_type = None

                for child in leg:

                    child_tag = child.tag.split('}')[-1]

                    if child_tag == "Type":
                        leg_type = child.text

                if leg_type != "Flight":
                    continue

                flight_elem = None

                for child in leg:

                    child_tag = child.tag.split('}')[-1]

                    if child_tag == "Flight":
                        flight_elem = child

                if flight_elem is None:
                    continue

                carrier = ""
                number = ""
                dep = ""
                arr = ""
                dep_time = ""
                arr_time = ""
                duration = ""

                for f in flight_elem.iter():

                    ft = f.tag.split('}')[-1]

                    if ft == "CarrierCode":
                        carrier = f.text or ""

                    elif ft == "Number":
                        number = f.text or ""

                    elif ft == "FromAirport":
                        dep = f.text or ""

                    elif ft == "ToAirport":
                        arr = f.text or ""

                    elif ft == "LCLLTD":
                        dep_time = f.text or ""

                    elif ft == "LCLLTA":
                        arr_time = f.text or ""

                for child in leg:

                    child_tag = child.tag.split('}')[-1]

                    if child_tag == "Duration":
                        duration = child.text or ""

                dep_fmt = fmt_time(dep_time)
                arr_fmt = fmt_time(arr_time)

                flight_no = f"{carrier}{number}"

                line = (
                    f"{flight_no}  "
                    f"{dep} → {arr}  "
                    f"{dep_fmt}-{arr_fmt}"
                )

                if duration:
                    line += f"  ({duration})"

                flight_lines.append(line)

            if flight_lines:

                description_lines.append("Flights")
                description_lines.append("────────")

                for line in flight_lines:
                    description_lines.append(line)

        description = (
            "\n".join(description_lines)
            if description_lines else None
        )

        activities.append((
            title,
            start_dt,
            end_dt,
            description
        ))

    print(f"🔍 Found {len(activities)} activities")

    return activities


# =====================================================
# BUILD ICS
# =====================================================

def build_ics(activities):

    cal = Calendar()

    for title, start, end, description in activities:

        event = Event()

        t = title.upper()

        # =============================================
        # SPECIAL DAYS
        # =============================================

        if t == "OPEN DAY":
            summary = "🟡 OPEN"

        elif "OFF" in t:
            summary = "🟢 DAY OFF"

        elif "LEAVE" in t:
            summary = "🎉 LEAVE"

        else:

            routes = []

            if description:

                for line in description.split("\n"):

                    if "→" not in line:
                        continue

                    try:

                        route_section = line.split("  ")[1]

                        dep = (
                            route_section
                            .split("→")[0]
                            .strip()
                        )

                        arr = (
                            route_section
                            .split("→")[1]
                            .strip()
                            .split(" ")[0]
                        )

                        if not routes:
                            routes.append(dep)

                        routes.append(arr)

                    except:
                        pass

            if len(routes) >= 2:

                summary = (
                    f"✈️ {'-'.join(routes)}"
                )

            else:
                summary = "✈️ DUTY"

        event.add("summary", summary)
        event.add("dtstart", start)
        event.add("dtend", end)

        if description:
            event.add("description", description)

        cal.add_component(event)

    return cal


# =====================================================
# SAVE
# =====================================================

def save(cal):

    with open("roster.ics", "wb") as f:
        f.write(cal.to_ical())

    print("📅 roster.ics saved")


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    playwright = None
    browser = None

    try:

        playwright, browser, context, page = login()

        open_roster(page)

        xml_data = fetch_roster_xml(page)

        activities = parse(xml_data)

        cal = build_ics(activities)

        save(cal)

    finally:

        try:
            browser.close()
        except:
            pass

        try:
            playwright.stop()
        except:
            pass
