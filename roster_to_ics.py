import os
import re
import urllib3
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =====================================================
# CONFIG
# =====================================================

USERNAME = os.environ.get("CREW_USER")
PASSWORD = os.environ.get("CREW_PASS")

if not USERNAME or not PASSWORD:
    raise Exception("❌ Missing CREW_USER / CREW_PASS")

LOGIN_URL  = "https://saacrewconnect.cocre8.africa/html/home.html"
ROSTER_URL = "https://saacrewconnect.cocre8.africa/php/roster.php"

SA_TZ = ZoneInfo("Africa/Johannesburg")

# =====================================================
# HELPERS
# =====================================================

def fmt_zulu(dt):
    local_dt = dt.replace(tzinfo=SA_TZ)
    utc_dt = local_dt.astimezone(timezone.utc)
    return utc_dt.strftime("%H:%MZ")

def fmt_block(duration):
    if not duration:
        return ""
    duration = duration.strip()
    if ":" in duration:
        h, m = duration.split(":")
        return f"{h.zfill(2)}h{m.zfill(2)}"
    return duration

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
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    page.goto(LOGIN_URL)
    page.wait_for_selector('input[type="password"]', timeout=15000)
    page.fill('input[type="text"]', USERNAME)
    page.fill('input[type="password"]', PASSWORD)
    try:
        page.press('input[type="password"]', 'Enter')
    except:
        pass
    page.wait_for_timeout(3000)
    try:
        page.locator("button:visible").first.click(timeout=3000)
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
    page.on("response", lambda r: responses.append(r))
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
    raise Exception("❌ Could not find roster response")

# =====================================================
# DISCOVER CREW API  ← temporary, remove after one run
# =====================================================

def discover_crew_api(page):
    print("\n" + "="*60)
    print("🔍 CREW API DISCOVERY — clicking roster rows...")
    print("="*60)

    # Dismiss any popup modal that might be blocking clicks
    print("🔔 Checking for blocking modal...")
    try:
        modal_close = page.locator(
            "#notifyModal button, "
            "#notifyModal [data-dismiss='modal'], "
            "#notifyModal .close, "
            "#notifyModal .btn"
        ).first
        if modal_close.is_visible(timeout=3000):
            print("   ✅ Modal found — dismissing it...")
            modal_close.click()
            page.wait_for_timeout(2000)
        else:
            print("   ℹ️ No modal visible")
    except:
        print("   ℹ️ No modal to dismiss")

    captured = []

    def on_response(response):
        if "crewApi" not in response.url:
            return
        try:
            text = response.text()
            if "RosterResponse" in text:
                return
            captured.append((response.url, text))
        except:
            pass

    page.on("response", on_response)

    row_selectors = [
        "tr.activity-row",
        "tr[class*='activity']",
        "tr[class*='duty']",
        "tr[class*='flight']",
        "tr[class*='pairing']",
        "table tbody tr",
    ]

    rows = None
    for selector in row_selectors:
        found = page.locator(selector)
        if found.count() > 0:
            rows = found
            print(f"✅ Found {found.count()} rows with selector: {selector}")
            break

    if rows is None:
        print("❌ No clickable rows found. Printing page HTML for inspection:")
        print(page.content()[:3000])
        return

    max_clicks = min(3, rows.count())
    for i in range(max_clicks):
        captured.clear()
        print(f"\n🖱  Clicking row {i+1}...")
        try:
            rows.nth(i).click()
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"   ⚠️ Could not click row {i+1}: {e}")
            continue

        if captured:
            url, text = captured[0]
            print(f"   📡 URL: {url}")
            print(f"   📄 Response (first 2000 chars):")
            print(text[:2000])
            print("   ...")
        else:
            print("   ⚠️ No crew API response captured for this row")

    print("\n" + "="*60)
    print("🔍 DISCOVERY COMPLETE")
    print("="*60 + "\n")

# =====================================================
# PARSE XML
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
        end   = get("LCLEnd")
        report = get("LCLExpectedSignOn")

        if not start or not end:
            continue

        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
            end_dt   = datetime.strptime(end,   "%Y-%m-%d %H:%M")
        except:
            continue

        description_lines = []

        if report:
            report_dt = datetime.strptime(report, "%Y-%m-%d %H:%M")
            description_lines.append("Report")
            description_lines.append(
                f"{report_dt.strftime('%d %b %H:%M')}L ({fmt_zulu(report_dt)})"
            )
            description_lines.append("")

        pairing = None
        for elem in activity:
            if "Pairing" in elem.tag:
                pairing = elem
                break

        if pairing is not None:
            for leg in pairing.iter():
                tag_name = leg.tag.split('}')[-1]
                if tag_name != "Leg":
                    continue

                leg_type = None
                for child in leg:
                    if child.tag.split('}')[-1] == "Type":
                        leg_type = child.text

                if leg_type != "Flight":
                    continue

                flight_elem = None
                for child in leg:
                    if child.tag.split('}')[-1] == "Flight":
                        flig
