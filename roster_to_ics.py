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

def fmt_local(dt):
    return dt.strftime("%d %b %H:%M") + "L"

def fmt_local_time(dt):
    return dt.strftime("%H:%M") + "L"

def fmt_utc(dt):
    return dt.strftime("%H:%M") + "Z"

def fmt_block(duration):
    if not duration:
        return ""
    duration = duration.strip()
    if ":" in duration:
        parts = duration.split(":")
        h, m = parts[0], parts[1]
        return f"{h.zfill(2)}h{m.zfill(2)}"
    return duration

def clean(s):
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()

def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
    except:
        return None

# =====================================================
# FORCE CLOSE ALL MODALS
# =====================================================

def force_close_modals(page):
    try:
        page.evaluate("""
            document.querySelectorAll('.modal-backdrop').forEach(e => e.remove());
            document.querySelectorAll('.modal.show').forEach(e => {
                e.classList.remove('show');
                e.style.display = 'none';
            });
            document.body.classList.remove('modal-open');
        """)
        page.wait_for_timeout(800)
    except:
        pass

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
# PARSE CREW FROM XML
# =====================================================

def parse_crew_response(text):
    flight_crew = []
    cabin_crew  = []
    try:
        root = ET.fromstring(text)
        for crew_elem in root.iter("Crew"):
            first     = ""
            surname   = ""
            rank      = ""
            rank_code = ""
            position  = ""
            for child in crew_elem:
                tag = child.tag.split('}')[-1]
                val = (child.text or "").strip()
                if tag == "FirstName":  first     = val
                elif tag == "Surname":  surname   = val
                elif tag == "Rank":     rank      = val
                elif tag == "RankCode": rank_code = val
                elif tag == "Position": position  = val

            if not (first and surname and rank):
                continue

            name = f"{first} {surname}"
            pos = position.strip()
            pos = re.sub(r'CABIN CREW AB-INITIO', 'AB-INITIO', pos, flags=re.IGNORECASE)
            pos = re.sub(r'CABIN CREW MEMBER', '', pos, flags=re.IGNORECASE).strip()
            pos = re.sub(r'PURSER', '', pos, flags=re.IGNORECASE).strip()

            if rank_code in ("CAPT", "FO"):
                line = f"{rank}: {name}"
                if pos and pos.upper() != rank.upper():
                    line += f" ({pos})"
                flight_crew.append(line)
            else:
                role = "Purser" if rank_code == "PUR" else "Cabin Crew"
                line = f"{role}: {name}"
                if pos:
                    line += f" ({pos})"
                cabin_crew.append(line)

    except Exception as e:
        print(f"   ⚠️ Crew parse error: {e}")

    return flight_crew, cabin_crew

# =====================================================
# FETCH CREW FOR ONE PAIRING
# =====================================================

def fetch_crew_for_pairing(page, row):
    flight_crew = []
    cabin_crew  = []
    captured    = []

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
        row.click(timeout=10000)
        page.wait_for_timeout(2000)

        modal = page.locator("#pairingModal")
        modal.wait_for(state="visible", timeout=5000)
        print("   ✅ pairingModal opened")

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
                    flight_crew, cabin_crew = parse_crew_response(captured[0])
                    if flight_crew or cabin_crew:
                        print(f"   ✅ {len(flight_crew)} flight crew, {len(cabin_crew)} cabin crew")
                    break
            except Exception as e:
                print(f"   ⚠️ Could not click leg {j+1}: {e}")
                continue

    except Exception as e:
        print(f"   ⚠️ Could not fetch crew: {e}")

    finally:
        page.remove_listener("response", on_response)
        force_close_modals(page)

    return flight_crew, cabin_crew

# =====================================================
# FETCH ALL CREW
# =====================================================

def fetch_all_crew(page):
    print("\n🧑‍✈️ Fetching crew data for all pairings...")

    try:
        notify = page.locator("#notifyModal")
        if notify.is_visible(timeout=3000):
            notify.locator("button").first.click()
            page.wait_for_timeout(2000)
            print("   ✅ Dismissed notify modal")
    except:
        pass

    crew_by_pairing = {}

    for month_offset in range(3):

        if month_offset > 0:
            print(f"\n   ➡️  Navigating to next month...")
            force_close_modals(page)
            try:
                next_btn = page.locator("button.fc-next-button").first
                next_btn.click(timeout=10000)
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"   ⚠️ Could not navigate: {e}")
                break

        try:
            month_label = page.locator("h2, .fc-toolbar-title").first.inner_text()
            print(f"\n   📅 Scanning: {month_label}")
        except:
            print(f"\n   📅 Scanning month {month_offset + 1}")

        pairing_elems = page.locator("text=Pairing")
        count = pairing_elems.count()
        print(f"   ✈️  Found {count} pairing elements")

        seen_codes = set()

        for i in range(count):
            try:
                elem = pairing_elems.nth(i)
                label = elem.inner_text().strip()

                match = re.search(r'Pairing:\s*(\S+)', label)
                if not match:
                    continue
                pairing_code = match.group(1)

                if pairing_code in crew_by_pairing or pairing_code in seen_codes:
                    print(f"   ⏭  Already have: {pairing_code}")
                    continue

                seen_codes.add(pairing_code)
                print(f"\n   🖱  Clicking: {pairing_code}")
                flight_crew, cabin_crew = fetch_crew_for_pairing(page, elem)

                crew_by_pairing[pairing_code] = (flight_crew, cabin_crew)

                if flight_crew or cabin_crew:
                    print(f"   ✅ Flight crew: {len(flight_crew)}, Cabin crew: {len(cabin_crew)}")
                else:
                    print(f"   ⚠️  No crew found")

                page.wait_for_timeout(500)

            except Exception as e:
                print(f"   ⚠️  Error on pairing {i+1}: {e}")
                force_close_modals(page)
                continue

    print(f"\n✅ Done — {len(crew_by_pairing)} pairings with crew data")
    return crew_by_pairing

# =====================================================
# PARSE ROSTER XML
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

        title      = clean(get("TypeDescription") or "Duty")
        start      = get("LCLStart")
        end        = get("LCLEnd")
        report_lcl = get("LCLExpectedSignOn")
        report_utc = get("UTCExpectedSignOn")

        if not start or not end:
            continue

        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
            end_dt   = datetime.strptime(end,   "%Y-%m-%d %H:%M")
        except:
            continue

        description_lines = []
        t = title.upper()
        subtitle = ""

        # =====================================================
        # TRAINING
        # =====================================================
        if "TRAINING" in t:
            course_code      = ""
            course_desc      = ""
            course_type      = ""
            course_lcl_start = ""
            course_lcl_end   = ""
            course_utc_start = ""
            course_utc_end   = ""
            modules          = []

            for elem in activity.iter():
                et = elem.tag.split('}')[-1]
                if et == "Course":
                    for child in elem:
                        ct  = child.tag.split('}')[-1]
                        val = (child.text or "").strip()
                        if ct == "Code":           course_code      = val
                        elif ct == "Description":  course_desc      = val
                        elif ct == "LCLStart":     course_lcl_start = val
                        elif ct == "LCLEnd":       course_lcl_end   = val
                        elif ct == "UTCStart":     course_utc_start = val
                        elif ct == "UTCEnd":       course_utc_end   = val
                        elif ct == "Type":
                            for tc in child:
                                if tc.tag.split('}')[-1] == "Description":
                                    course_type = (tc.text or "").strip()
                        elif ct == "Modules":
                            for module in child:
                                if module.tag.split('}')[-1] != "Module":
                                    continue
                                mod = {}
                                for mc in module:
                                    mt = mc.tag.split('}')[-1]
                                    if mt == "Description": mod["desc"]  = (mc.text or "").strip()
                                    elif mt == "LCLStart":  mod["start"] = (mc.text or "").strip()
                                    elif mt == "LCLEnd":    mod["end"]   = (mc.text or "").strip()
                                if mod:
                                    modules.append(mod)

            subtitle = course_code

            if course_desc:
                description_lines.append(course_desc)
            if course_type:
                description_lines.append(course_type)
            description_lines.append("")

            cs   = parse_dt(course_lcl_start)
            ce   = parse_dt(course_lcl_end)
            cu_s = parse_dt(course_utc_start)
            cu_e = parse_dt(course_utc_end)

            if cs and ce:
                start_str = fmt_local_time(cs)
                if cu_s:
                    start_str += f" ({fmt_utc(cu_s)})"
                end_str = fmt_local_time(ce)
                if cu_e:
                    end_str += f" ({fmt_utc(cu_e)})"
                description_lines.append(f"{start_str} - {end_str}")
                description_lines.append("")

            if modules:
                def mod_sort_key(m):
                    try:
                        return datetime.strptime(m.get("start", "00:00"), "%H:%M")
                    except:
                        return datetime.min

                modules.sort(key=mod_sort_key)
                description_lines.append("── Modules ──")
                for mod in modules:
                    ms = mod.get("start", "")
                    me = mod.get("end", "")
                    md = mod.get("desc", "")
                    if ms and me:
                        description_lines.append(f"{ms}L - {me}L")
                    if md:
                        description_lines.append(md)
                    description_lines.append("")

            activities.append((
                title, start_dt, end_dt,
                "\n".join(description_lines).strip(),
                subtitle
            ))
            continue

        # =====================================================
        # RESERVE
        # =====================================================
        if "RESERVE" in t:
            res_code    = ""
            res_airport = ""
            res_lcl_s   = ""
            res_lcl_e   = ""
            res_utc_s   = ""
            res_utc_e   = ""

            for elem in activity.iter():
                et = elem.tag.split('}')[-1]
                if et == "Reserve":
                    for child in elem:
                        ct  = child.tag.split('}')[-1]
                        val = (child.text or "").strip()
                        if ct == "Code":      res_code    = val
                        elif ct == "Airport": res_airport = val
                        elif ct == "LCLStart": res_lcl_s  = val
                        elif ct == "LCLEnd":   res_lcl_e  = val
                        elif ct == "UTCStart": res_utc_s  = val
                        elif ct == "UTCEnd":   res_utc_e  = val

            lcl_s = parse_dt(res_lcl_s)
            lcl_e = parse_dt(res_lcl_e)
            utc_s = parse_dt(res_utc_s)
            utc_e = parse_dt(res_utc_e)

            description_lines.append("Reserve Window")
            if lcl_s and lcl_e:
                s_str = fmt_local_time(lcl_s)
                if utc_s:
                    s_str += f" ({fmt_utc(utc_s)})"
                e_str = fmt_local_time(lcl_e)
                if utc_e:
                    e_str += f" ({fmt_utc(utc_e)})"
                description_lines.append(f"{s_str} - {e_str}")
            description_lines.append("")
            if res_code:
                description_lines.append(f"Type: {res_code}")
            if res_airport:
                description_lines.append(f"Base: {res_airport}")

            activities.append((
                title, start_dt, end_dt,
                "\n".join(description_lines).strip(),
                subtitle
            ))
            continue

        # =====================================================
        # REPORT TIME (pairing + other duties)
        # =====================================================
        report_airport = ""

        if report_lcl and report_utc:
            r_lcl = parse_dt(report_lcl)
            r_utc = parse_dt(report_utc)
            if r_lcl and r_utc:
                description_lines.append("Report")
                description_lines.append(
                    f"{fmt_local(r_lcl)} ({fmt_utc(r_utc)})"
                )
                # Airport added after we find the first flight leg
                description_lines.append("__AIRPORT__")
                description_lines.append("")

        # =====================================================
        # PAIRING
        # =====================================================
        pairing_code = ""
        pairing = None
        for elem in activity:
            if "Pairing" in elem.tag:
                pairing = elem
                for child in elem:
                    if child.tag.split('}')[-1] == "Code":
                        pairing_code = (child.text or "").strip()
                break

        if pairing is not None:
            first_flight = True
            for leg_container in pairing:
                if leg_container.tag.split('}')[-1] != "Legs":
                    continue
                for leg_elem in leg_container:
                    if leg_elem.tag.split('}')[-1] != "Leg":
                        continue

                    leg_type = ""
                    for child in leg_elem:
                        if child.tag.split('}')[-1] == "Type":
                            leg_type = child.text or ""

                    # ── Flight ──
                    if leg_type == "Flight":
                        flight_elem = None
                        duration    = ""
                        for child in leg_elem:
                            ct = child.tag.split('}')[-1]
                            if ct == "Flight":     flight_elem = child
                            elif ct == "Duration": duration    = child.text or ""

                        if flight_elem is None:
                            continue

                        carrier = number = dep = arr = ""
                        lcl_dep = lcl_arr = utc_dep = utc_arr = ""
                        dep_airport_name = ""

                        for f in flight_elem.iter():
                            ft = f.tag.split('}')[-1]
                            if ft == "CarrierCode":   carrier          = f.text or ""
                            elif ft == "Number":      number           = f.text or ""
                            elif ft == "FromAirport": dep              = f.text or ""
                            elif ft == "ToAirport":   arr              = f.text or ""
                            elif ft == "LCLLTD":      lcl_dep          = f.text or ""
                            elif ft == "LCLLTA":      lcl_arr          = f.text or ""
                            elif ft == "UTCLTD":      utc_dep          = f.text or ""
                            elif ft == "UTCLTA":      utc_arr          = f.text or ""

                        # Use first flight's departure airport for report
                        if first_flight and dep:
                            report_airport = dep
                            first_flight = False

                        dep_lcl = parse_dt(lcl_dep)
                        arr_lcl = parse_dt(lcl_arr)
                        dep_utc = parse_dt(utc_dep)
                        arr_utc = parse_dt(utc_arr)

                        if not dep_lcl or not arr_lcl:
                            continue

                        flight_no = f"{carrier}{number}"
                        description_lines.append(dep_lcl.strftime("%d %b"))
                        description_lines.append(f"{flight_no}  {dep} → {arr}")
                        description_lines.append(
                            f"Dep {dep_lcl.strftime('%H:%M')}L"
                            + (f" ({fmt_utc(dep_utc)})" if dep_utc else "")
                        )
                        description_lines.append(
                            f"Arr {fmt_local(arr_lcl)}"
                            + (f" ({fmt_utc(arr_utc)})" if arr_utc else "")
                        )
                        if duration:
                            description_lines.append(f"Block {fmt_block(duration)}")
                        description_lines.append("")

                    # ── Layover ──
                    elif leg_type == "Layover":
                        utc_s = ""
                        utc_e = ""
                        for child in leg_elem:
                            ct = child.tag.split('}')[-1]
                            if ct == "UTCStart":   utc_s = child.text or ""
                            elif ct == "UTCEnd":   utc_e = child.text or ""
                            elif ct == "Layover":
                                hotel = tel = email = lcl_s = lcl_e = ""
                                for lc in child.iter():
                                    lt = lc.tag.split('}')[-1]
                                    if lt == "LocationName":    hotel = lc.text or ""
                                    elif lt == "LCLStart":     lcl_s = lc.text or ""
                                    elif lt == "LCLEnd":       lcl_e = lc.text or ""
                                    elif lt == "WorkTelephone": tel   = lc.text or ""
                                    elif lt == "Email":        email = lc.text or ""

                                arr_lcl = parse_dt(lcl_s)
                                dep_lcl = parse_dt(lcl_e)
                                arr_utc = parse_dt(utc_s)
                                dep_utc = parse_dt(utc_e)

                                description_lines.append("Layover")
                                if hotel:
                                    description_lines.append(hotel)
                                if tel:
                                    description_lines.append(f"Tel: {tel}")
                                if email:
                                    description_lines.append(f"Email: {email}")
                                if arr_lcl:
                                    description_lines.append(
                                        f"Arr {fmt_local(arr_lcl)}"
                                        + (f" ({fmt_utc(arr_utc)})" if arr_utc else "")
                                    )
                                if dep_lcl:
                                    description_lines.append(
                                        f"Dep {fmt_local(dep_lcl)}"
                                        + (f" ({fmt_utc(dep_utc)})" if dep_utc else "")
                                    )
                                description_lines.append("")

        # ── Crew ──
        if pairing_code and pairing_code in crew_by_pairing:
            flight_crew, cabin_crew = crew_by_pairing[pairing_code]
            if flight_crew:
                description_lines.append("── Flight Crew ──")
                description_lines.extend(flight_crew)
                description_lines.append("")
            if cabin_crew:
                description_lines.append("── Cabin Crew ──")
                description_lines.extend(cabin_crew)
                description_lines.append("")

        # Replace airport placeholder
        # We need to look up the full airport name from the XML
        # For now use the IATA code — we'll use what we have
        description_lines = [
            report_airport if line == "__AIRPORT__" else line
            for line in description_lines
        ]

        # Remove blank airport placeholder if nothing found
        description_lines = [
            line for line in description_lines
            if line != "__AIRPORT__"
        ]

        description = (
            "\n".join(description_lines).strip()
            if description_lines else None
        )

        activities.append((title, start_dt, end_dt, description, subtitle))

    print(f"🔍 Found {len(activities)} activities")
    return activities

# =====================================================
# BUILD ICS
# =====================================================

def build_ics(activities):
    cal = Calendar()

    for title, start, end, description, subtitle in activities:
        event = Event()
        t = title.upper()

        if "OFF" in t:
            summary = "🟢 DAY OFF"
        elif "OPEN" in t:
            summary = "🟡 OPEN"
        elif "LEAVE" in t:
            summary = "🎉 LEAVE"
        elif "RESERVE" in t:
            summary = "🟠 RESERVE"
        elif "GROUND" in t:
            summary = f"🕹️ {subtitle}" if subtitle else f"🕹️ {title}"
        elif "TRAINING" in t:
            summary = f"📘 {subtitle}" if subtitle else "📘 TRAINING"
        elif "PAIRING" in t:
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
        else:
            summary = f"📋 {title}"

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
