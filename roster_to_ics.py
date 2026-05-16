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
    print("🔍 CREW API DISCOVERY — phase 2")
    print("="*60)

    # Dismiss the first modal (notifyModal)
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

    # Find roster rows
    rows = page.locator("table tbody tr")
    count = rows.count()
    print(f"✅ Found {count} rows")

    # Only try the first 2 rows
    max_clicks = min(2, count)
    for i in range(max_clicks):
        print(f"\n🖱  Clicking row {i+1}...")

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

        try:
            rows.nth(i).click()
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"   ⚠️ Could not click row: {e}")
            continue

        # Check if activityModal appeared
        try:
            modal = page.locator("#activityModal")
            if modal.is_visible(timeout=3000):
                print("   ✅ activityModal opened!")

                # Print all the text/buttons inside it
                modal_html = modal.inner_html()
                print("   📄 Modal inner HTML (first 3000 chars):")
                print(modal_html[:3000])

                # Look for buttons or tabs inside the modal
                buttons = modal.locator("button, a, [role='tab'], li")
                btn_count = buttons.count()
                print(f"\n   🔘 Found {btn_count} buttons/tabs/links inside modal:")
                for b in range(min(btn_count, 20)):
                    try:
                        txt = buttons.nth(b).inner_text().strip()
                        if txt:
                            print(f"      [{b}] {txt}")
                    except:
                        pass

                # Try clicking anything that looks like "crew"
                crew_btn = modal.locator(
                    "button:has-text('Crew'), "
                    "a:has-text('Crew'), "
                    "[role='tab']:has-text('Crew'), "
                    "li:has-text('Crew')"
                )
                if crew_btn.count() > 0:
                    print("\n   ✈️ Found a Crew button/tab — clicking it...")
                    crew_btn.first.click()
                    page.wait_for_timeout(3000)

                    if captured:
                        url, text = captured[0]
                        print(f"   📡 Crew API URL: {url}")
                        print(f"   📄 Crew API Response (first 2000 chars):")
                        print(text[:2000])
                    else:
                        print("   ⚠️ No crew API response after clicking Crew button")
                else:
                    print("\n   ⚠️ No obvious 'Crew' button found in modal")

                # Close the modal before next row
                close_btn = modal.locator(
                    "button.close, "
                    "button[data-dismiss='modal'], "
                    ".modal-footer button"
                ).first
                if close_btn.is_visible(timeout=2000):
                    close_btn.click()
                    page.wait_for_timeout(1500)
                    print("   🚪 Modal closed")

        except Exception as e:
            print(f"   ⚠️ Modal interaction failed: {e}")

        page.remove_listener("response", on_response)

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
                        flight_elem = child

                if flight_elem is None:
                    continue

                carrier = number = dep = arr = ""
                dep_time = arr_time = duration = ""

                for f in flight_elem.iter():
                    ft = f.tag.split('}')[-1]
                    if ft == "CarrierCode":   carrier  = f.text or ""
                    elif ft == "Number":      number   = f.text or ""
                    elif ft == "FromAirport": dep      = f.text or ""
                    elif ft == "ToAirport":   arr      = f.text or ""
                    elif ft == "LCLLTD":      dep_time = f.text or ""
                    elif ft == "LCLLTA":      arr_time = f.text or ""

                for child in leg:
                    if child.tag.split('}')[-1] == "Duration":
                        duration = child.text or ""

                try:
                    dep_dt = datetime.strptime(dep_time, "%Y-%m-%d %H:%M")
                    arr_dt = datetime.strptime(arr_time, "%Y-%m-%d %H:%M")
                except:
                    continue

                flight_no = f"{carrier}{number}"
                description_lines.append(dep_dt.strftime("%d %b"))
                description_lines.append(f"{flight_no}  {dep} → {arr}")
                description_lines.append(
                    f"Dep {dep_dt.strftime('%H:%M')}L ({fmt_zulu(dep_dt)})"
                )
                description_lines.append(
                    f"Arr {arr_dt.strftime('%d %b %H:%M')}L ({fmt_zulu(arr_dt)})"
                )
                if duration:
                    description_lines.append(f"Block {fmt_block(duration)}")

                # Layover
                hotel_name = hotel_arr = hotel_dep = None
                for elem in leg.iter():
                    et  = elem.tag.split('}')[-1]
                    txt = elem.text
                    if not txt:
                        continue
                    txt = txt.strip()
                    if "Hotel" in et and not hotel_name:
                        hotel_name = txt
                    if "HotelArrival" in et:
                        hotel_arr = txt
                    if "HotelDeparture" in et:
                        hotel_dep = txt

                if hotel_name:
                    description_lines.append("")
                    description_lines.append("Layover")
                    description_lines.append(hotel_name)
                    try:
                        if hotel_arr:
                            ha = datetime.strptime(hotel_arr, "%Y-%m-%d %H:%M")
                            description_lines.append(
                                f"{ha.strftime('%d %b %H:%M')}L ({fmt_zulu(ha)})"
                            )
                        if hotel_dep:
                            hd = datetime.strptime(hotel_dep, "%Y-%m-%d %H:%M")
                            description_lines.append(
                                f"{hd.strftime('%d %b %H:%M')}L ({fmt_zulu(hd)})"
                            )
                    except:
                        pass

                description_lines.append("")

        description = (
            "\n".join(description_lines).strip()
            if description_lines else None
        )

        activities.append((title, start_dt, end_dt, description))

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
                        route = line.split("  ")[1]
                        dep   = route.split("→")[0].strip()
                        arr   = route.split("→")[1].strip()
                        if not routes:
                            routes.append(dep)
                        routes.append(arr)
                    except:
                        pass
            summary = f"✈️ {'-'.join(routes)}" if len(routes) >= 2 else "✈️ DUTY"

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

        # ── DISCOVERY: remove this block once we've seen the crew API ──
        discover_crew_api(page)
        # ──────────────────────────────────────────────────────────────

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
