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
# DISMISS ANY BLOCKING MODAL
# =====================================================

def dismiss_modal(page, modal_id):
    try:
        modal = page.locator(f"#{modal_id}")
        if modal.is_visible(timeout=2000):
            close = modal.locator(
                "button[data-dismiss='modal'], "
                "button.close, "
                ".modal-footer button"
            ).first
            close.click()
            page.wait_for_timeout(1500)
    except:
        pass

# =====================================================
# FETCH CREW FOR A PAIRING
# Clicks the pairing row, then clicks the first flight
# leg inside the detail modal to get the crew list.
# Returns a list of strings like "Captain: Ian Jackson"
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
        # Click the pairing row to open the flight detail modal
        row.click(timeout=10000)
        page.wait_for_timeout(2000)

        # Wait for the activity modal
        modal = page.locator("#activityModal")
        modal.wait_for(state="visible", timeout=5000)

        # Find flight leg rows inside the modal (rows with a plane icon)
        # They contain flight numbers like SA280
        flight_rows = modal.locator("tr, [class*='flight'], [class*='leg']")

        # Try clicking the first flight row inside the modal
        # that looks like a real flight (has a flight number)
        clicked_flight = False
        for j in range(min(flight_rows.count(), 10)):
            try:
                row_text = flight_rows.nth(j).inner_text()
                # Flight rows contain airport codes like ZSSXD or flight numbers
                if re.search(r'(SA|FA)\d{2,4}|ZSS|PER|JNB|CPT|DUR', row_text):
                    captured.clear()
                    flight_rows.nth(j).click(timeout=5000)
                    page.wait_for_timeout(3000)
                    clicked_flight = True
                    break
            except:
                continue

        if not clicked_flight:
            # Fallback: just click the first row in the modal
            try:
                captured.clear()
                flight_rows.first.click(timeout=5000)
                page.wait_for_timeout(3000)
            except:
                pass

        # Parse crew from the captured response
        if captured:
            crew_lines = parse_crew_xml(captured[0])
        else:
            # Try parsing crew directly from the crew modal HTML
            crew_modal = page.locator(".modal.show").last
            crew_lines = parse_crew_html(crew_modal)

    except Exception as e:
        print(f"   ⚠️ Could not fetch crew: {e}")

    finally:
        page.remove_listener("response", on_response)
        # Close any open modals
        for modal_id in ["activityModal", "crewModal"]:
            dismiss_modal(page, modal_id)
        # Also try closing any visible modal
        try:
            page.locator(".modal.show button[data-dismiss='modal']").first.click()
            page.wait_for_timeout(1000)
        except:
            pass

    return crew_lines

# =====================================================
# PARSE CREW FROM XML RESPONSE
# =====================================================

def parse_crew_xml(xml_text):
    crew_lines = []
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            tag = elem.tag.split('}')[-1]
            if "CrewMember" in tag or "Crew" in tag:
                role = None
                name = None
                subrole = None
                for child in elem:
                    ct = child.tag.split('}')[-1].lower()
                    if "role" in ct and "sub" not in ct:
                        role = child.text
                    elif "name" in ct or "firstname" in ct or "lastname" in ct:
                        name = child.text
                    elif "sub" in ct or "function" in ct or "position" in ct:
                        subrole = child.text
                if role and name:
                    line = f"{role}: {name}"
                    if subrole:
                        line += f" ({subrole})"
                    crew_lines.append(line)
    except:
        pass
    return crew_lines

# =====================================================
# PARSE CREW FROM HTML (fallback)
# =====================================================

def parse_crew_html(modal):
    crew_lines = []
    try:
        # The crew modal has rows with role, name, subrole
        rows = modal.locator("tr, .crew-row")
        for i in range(rows.count()):
            try:
                text = rows.nth(i).inner_text().strip()
                if not text:
                    continue
                # Skip header rows
                if any(h in text.lower() for h in ["role", "name", "crew list"]):
                    continue
                # Clean up whitespace between columns
                parts = re.split(r'\s{2,}|\t', text)
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) >= 2:
                    role = parts[0]
                    name = parts[1]
                    subrole = parts[2] if len(parts) > 2 else None
                    line = f"{role}: {name}"
                    if subrole and subrole.lower() != "due":
                        line += f" ({subrole})"
                    crew_lines.append(line)
            except:
                continue
    except:
        pass
    return crew_lines

# =====================================================
# FETCH ALL CREW DATA
# Navigates month by month and collects crew for
# every pairing row found across all months.
# =====================================================

def fetch_all_crew(page):
    print("\n🧑‍✈️ Fetching crew data for all pairings...")

    # Dismiss the notify modal first
    try:
        notify = page.locator("#notifyModal")
        if notify.is_visible(timeout=3000):
            notify.locator("button").first.click()
            page.wait_for_timeout(2000)
            print("   ✅ Dismissed notify modal")
    except:
        pass

    crew_by_pairing = {}  # key: pairing label, value: list of crew strings

    # We'll scan the current month plus the next 2 months
    for month_offset in range(3):

        if month_offset > 0:
            print(f"\n   ➡️  Navigating to next month...")
            try:
                next_btn = page.locator("button.fc-next-button, a.fc-next-button, button:has-text('>'), [aria-label='next']")
                if next_btn.count() == 0:
                    # Try the blue > button visible in your screenshots
                    next_btn = page.locator("button").filter(has_text=re.compile(r'^>$'))
                if next_btn.count() == 0:
                    # Last resort: find buttons with right-arrow styling
                    next_btn = page.locator(".fc-next-button, [class*='next']").first
                next_btn.first.click()
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"   ⚠️ Could not navigate to next month: {e}")
                break

        # Get current month label
        try:
            month_label = page.locator("h2, .fc-toolbar-title, [class*='title']").first.inner_text()
            print(f"\n   📅 Scanning: {month_label}")
        except:
            print(f"\n   📅 Scanning month {month_offset + 1}")

        # Find all pairing rows — they have "Pairing" in their text
        rows = page.locator("table tbody tr, .fc-event, [class*='event']")
        count = rows.count()
        print(f"   Found {count} rows")

        pairing_indices = []
        for i in range(count):
            try:
                text = rows.nth(i).inner_text().strip()
                if "Pairing" in text or "pairing" in text:
                    pairing_indices.append((i, text))
            except:
                continue

        print(f"   ✈️  Found {len(pairing_indices)} pairing rows")

        for i, label in pairing_indices:
            label_clean = label.strip()[:50]
            if label_clean in crew_by_pairing:
                print(f"   ⏭  Already have crew for: {label_clean}")
                continue

            print(f"   🖱  Clicking pairing: {label_clean}")
            try:
                row = rows.nth(i)
                crew = fetch_crew_for_pairing(page, row)
                if crew:
                    crew_by_pairing[label_clean] = crew
                    print(f"   ✅ Got {len(crew)} crew members")
                    for c in crew:
                        print(f"      {c}")
                else:
                    print(f"   ⚠️  No crew found for this pairing")
                page.wait_for_timeout(1000)
            except Exception as e:
                print(f"   ⚠️  Error: {e}")
                continue

    print(f"\n✅ Crew fetch complete — {len(crew_by_pairing)} pairings with crew data")
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

        title = get("TypeDescription") or "Duty"
        start = get("LCLStart")
        end   = get("LCLEnd")
        report = get("LCLExpectedSignOn")
        pairing_id = get("PairingId") or get("PairingCode") or get("Code") or ""

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

        # Add crew if we have it for this pairing
        crew = []

        # Try matching by pairing id from XML
        if pairing_id:
            for key, val in crew_by_pairing.items():
                if pairing_id in key:
                    crew = val
                    break

        # Fallback: match by date
        if not crew:
            date_str = start_dt.strftime("%Y-%m-%d")
            for key, val in crew_by_pairing.items():
                if date_str in key:
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
