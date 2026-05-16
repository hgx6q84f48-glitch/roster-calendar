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
# FETCH CREW FOR A PAIRING
# =====================================================

def fetch_crew_for_pairing(page, row):
    crew_lines = []
    captured = []

    def on_response(response):
        if "crewApi" not in response.url:
            return
        try:
            text = response.text()
            if "RosterResponse" in text:
                return
            captured.append(text)
        except:
            pass

    page.on("response", on_response)

    try:
        # Click the pairing calendar block
        row.click(timeout=10000)
        page.wait_for_timeout(2000)

        # Wait for pairingModal (not activityModal!)
        modal = page.locator("#pairingModal")
        modal.wait_for(state="visible", timeout=5000)
        print("   ✅ pairingModal opened")

        # Find flight leg rows inside — they have class pairing-leg-flight-row
        flight_rows = modal.locator(".pairing-leg-flight-row")
        count = flight_rows.count()
        print(f"   Found {count} flight leg rows")

        for j in range(count):
            try:
                captured.clear()
                print(f"   🖱  Clicking flight leg {j+1}...")
                flight_rows.nth(j).click(timeout=5000)
                page.wait_for_timeout(3000)

                if captured:
                    print(f"   📡 Got crew API response!")
                    crew = parse_crew_response(captured[0])
                    crew_lines.extend(crew)
                    # Print raw response for debugging
                    print(f"   📄 Raw response (500 chars): {captured[0][:500]}")
                    break  # just need crew once per pairing
            except Exception as e:
                print(f"   ⚠️ Could not click leg {j+1}: {e}")
                continue

        # Close pairingModal
        try:
            modal.locator("button[data-dismiss='modal'], .btn").first.click()
            page.wait_for_timeout(1500)
            print("   🚪 pairingModal closed")
        except:
            pass

        # Close crewListModal if it opened
        try:
            crew_modal = page.locator("#crewListModal")
            if crew_modal.is_visible(timeout=2000):
                crew_modal.locator("button").first.click()
                page.wait_for_timeout(1000)
                print("   🚪 crewListModal closed")
        except:
            pass

    except Exception as e:
        print(f"   ⚠️ Could not fetch crew: {e}")
        # Try closing any open modal
        try:
            page.locator(".modal.show .btn").first.click()
            page.wait_for_timeout(1000)
        except:
            pass

    finally:
        page.remove_listener("response", on_response)

    return crew_lines

# =====================================================
# PARSE CREW RESPONSE
# Handles both XML and HTML responses
# =====================================================

def parse_crew_response(text):
    crew_lines = []

    # Try XML first
    try:
        root = ET.fromstring(text)
        for elem in root.iter():
            tag = elem.tag.split('}')[-1].lower()
            if "crew" in tag and elem.text:
                # Print all tags so we can see structure
                print(f"   🏷  XML tag: {elem.tag} = {elem.text[:50] if elem.text else ''}")
        # Try to find crew members
        for elem in root.iter():
            tag = elem.tag.split('}')[-1]
            children = list(elem)
            if len(children) >= 2:
                role = None
                name = None
                subrole = None
                for child in children:
                    ct = child.tag.split('}')[-1].lower()
                    val = (child.text or "").strip()
                    if not val:
                        continue
                    if "role" in ct and "sub" not in ct:
                        role = val
                    elif "name" in ct or "first" in ct or "last" in ct:
                        name = val
                    elif "function" in ct or "position" in ct or "sub" in ct:
                        subrole = val
                if role and name:
                    line = f"{role}: {name}"
                    if subrole and subrole.lower() not in ["due", ""]:
                        line += f" ({subrole})"
                    crew_lines.append(line)
        if crew_lines:
            return crew_lines
    except:
        pass

    # Try HTML parsing
    try:
        # Look for table rows with role/name pattern
        rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            text,
            re.DOTALL
        )
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            cells = [c for c in cells if c and c.lower() != "due"]
            if len(cells) >= 2:
                role = cells[0]
                name = cells[1]
                subrole = cells[2] if len(cells) > 2 else None
                skip = ["role", "name", "crew", "position", "function"]
                if any(s in role.lower() for s in skip):
                    continue
                line = f"{role}: {name}"
                if subrole and subrole.lower() not in ["due", ""]:
                    line += f" ({subrole})"
                crew_lines.append(line)
    except:
        pass

    return crew_lines

# =====================================================
# FETCH ALL CREW
# =====================================================

def fetch_all_crew(page):
    print("\n🧑‍✈️ Fetching crew data for all pairings...")

    # Dismiss notify modal
    try:
        notify = page.locator("#notifyModal")
        if notify.is_visible(timeout=3000):
            notify.locator("button").first.click()
            page.wait_for_timeout(2000)
            print("   ✅ Dismissed notify modal")
    except:
        pass

    crew_by_pairing = {}

    # Scan current month + next 2 months
    for month_offset in range(3):

        if month_offset > 0:
            print(f"\n   ➡️  Navigating to next month...")
            try:
                # From your screenshot the next button is a plain > button
                next_btn = page.locator("button.fc-next-button").first
                if not next_btn.is_visible(timeout=1000):
                    next_btn = page.locator("button:has-text('›'), button:has-text('>')").first
                if not next_btn.is_visible(timeout=1000):
                    next_btn = page.locator("a[ng-click*='next'], button[ng-click*='next']").first
                next_btn.click()
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"   ⚠️ Could not navigate: {e}")
                break

        # Get month label
        try:
            month_label = page.locator("h2, .fc-toolbar-title, [class*='title']").first.inner_text()
            print(f"\n   📅 Scanning: {month_label}")
        except:
            print(f"\n   📅 Scanning month {month_offset + 1}")

        # Find all elements containing "Pairing"
        pairing_elems = page.locator("text=Pairing")
        count = pairing_elems.count()
        print(f"   ✈️  Found {count} pairing elements")

        for i in range(count):
            try:
                elem = pairing_elems.nth(i)
                label = elem.inner_text().strip()[:60]

                if label in crew_by_pairing:
                    print(f"   ⏭  Already have: {label}")
                    continue

                print(f"\n   🖱  Clicking: {label}")
                crew = fetch_crew_for_pairing(page, elem)

                if crew:
                    crew_by_pairing[label] = crew
                    print(f"   ✅ Got {len(crew)} crew members:")
                    for c in crew:
                        print(f"      {c}")
                else:
                    crew_by_pairing[label] = []
                    print(f"   ⚠️  No crew found")

                page.wait_for_timeout(1000)

            except Exception as e:
                print(f"   ⚠️  Error on pairing {i+1}: {e}")
                continue

    print(f"\n✅ Done — {len(crew_by_pairing)} pairings processed")
    return crew_by_pairing

# =====================================================
# PARSE XML
# =====================================================

def parse(xml_data, crew_by_pairing):
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

        title  = get("TypeDescription") or "Duty"
        start  = get("LCLStart")
        end    = get("LCLEnd")
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

        # Match crew to this activity
        crew = []
        date_str = start_dt.strftime("%Y-%m-%d")
        for key, val in crew_by_pairing.items():
            if date_str in key or (val and "Pairing" in title):
                crew = val
                break

        if crew:
            description_lines.append("Crew")
            description_lines.extend(crew)
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
        crew_by_pairing = fetch_all_crew(page)
        activities = parse(xml_data, crew_by_pairing)
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
