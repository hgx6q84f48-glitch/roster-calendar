import os
import re
import json
import difflib
import urllib3
import xml.etree.ElementTree as ET

from collections import Counter
from datetime import datetime, date, timezone, timedelta
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

BASELINE_FILE = "roster_baseline.json"
SNAPSHOT_FILE = "roster_snapshot.json"
CHANGES_JSON  = "roster_changes.json"
CHANGES_MD    = "roster_changes.md"
ALERT_FILE    = "roster_alert.md"
ALERT_TITLE   = "roster_alert_title.txt"

# Filled in by parse(): one (departure date, block minutes) per flight leg
FLIGHT_LOG = []

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

def fmt_hm(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}"

def parse_minutes(duration):
    try:
        parts = duration.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return 0

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

def sa_suffix(local_dt, utc_dt):
    """' · 06:20 SA' when the local time differs from South African time."""
    if not local_dt or not utc_dt:
        return ""
    sa = utc_dt + timedelta(hours=2)
    if sa == local_dt:
        return ""
    if sa.date() != local_dt.date():
        return f" · {sa.strftime('%d %b %H:%M')} SA"
    return f" · {sa.strftime('%H:%M')} SA"

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
        page.wait_for_timeout(2000)
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
    page.wait_for_timeout(10000)

# =====================================================
# FETCH ROSTER XML
# =====================================================

def fetch_roster_xml(page):
    responses = []
    page.on("response", lambda r: responses.append(r))
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(10000)
    for response in responses:
        try:
            if "ws_proxy.php" not in response.url:
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

def fetch_crew_for_pairing(page, elem):
    flight_crew = []
    cabin_crew  = []
    captured    = []

    def on_response(response):
        if "ws_proxy.php" not in response.url:
            return
        try:
            text = response.text()
            if "RosterResponse" in text:
                return
            if "FlightCrewList" in text:
                captured.append(text)
        except:
            pass

    page.on("response", on_response)

    try:
        elem.click(timeout=10000)
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

                    try:
                        crew_modal = page.locator("#crewListModal")
                        if crew_modal.is_visible(timeout=2000):
                            print("   🚪 Closing crewListModal...")
                            crew_modal.locator("button").first.click()
                            page.wait_for_timeout(1500)
                    except:
                        pass

                    try:
                        close_btn = modal.locator("button").first
                        if close_btn.is_visible(timeout=2000):
                            print("   🚪 Closing pairingModal...")
                            close_btn.click()
                            page.wait_for_timeout(1500)
                    except:
                        pass

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
# Key is "PAIRINGCODE|YYYY-MM-DD" so same code on
# different dates is always fetched independently
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

        pairing_elems = page.locator(".fc-event.cma-generic-theme-pairing")
        count = pairing_elems.count()

        if count == 0:
            pairing_elems = page.locator(".fc-event[class*='pairing']")
            count = pairing_elems.count()

        print(f"   ✈️  Found {count} pairing elements")

        for i in range(count):
            try:
                elem = pairing_elems.nth(i)

                date_str = page.evaluate("""
                    (el) => {
                        let parent = el.closest('[data-date]');
                        return parent ? parent.getAttribute('data-date') : null;
                    }
                """, elem.element_handle())

                label = elem.inner_text().strip()[:60]
                match = re.search(r'([A-Z0-9]+-[A-Z0-9]+[A-Z])', label)
                pairing_code = match.group(1) if match else label[:20]

                unique_key = f"{pairing_code}|{date_str or 'unknown'}"

                if unique_key in crew_by_pairing:
                    print(f"   ⏭  Already have: {unique_key}")
                    continue

                print(f"\n   🖱  Clicking: {unique_key}")
                flight_crew, cabin_crew = fetch_crew_for_pairing(page, elem)

                crew_by_pairing[unique_key] = (flight_crew, cabin_crew)

                if flight_crew or cabin_crew:
                    print(f"   ✅ {len(flight_crew)} flight crew, {len(cabin_crew)} cabin crew")
                else:
                    print(f"   ⚠️  No crew found")

                page.wait_for_timeout(2000)

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
    FLIGHT_LOG.clear()
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

        # ── LEAVE (incl. sick / family responsibility) ──
        leave_elem = None
        for elem in activity.iter():
            if elem.tag.split('}')[-1] == "Leave":
                leave_elem = elem
                break

        if "LEAVE" in t or "SICK" in t or "FAMIL" in t or leave_elem is not None:
            l_code = l_desc = l_type = l_typecd = ""
            if leave_elem is not None:
                for child in leave_elem:
                    ct  = child.tag.split('}')[-1]
                    val = (child.text or "").strip()
                    if ct == "Code":          l_code   = val
                    elif ct == "Description": l_desc   = val
                    elif ct == "Type":        l_type   = val
                    elif ct == "TypeCd":      l_typecd = val

            print(f"🏖  Leave found: title='{title}' code='{l_code}' desc='{l_desc}' type='{l_type}' typecd='{l_typecd}'")

            subtitle = " ".join([title, l_code, l_desc, l_type, l_typecd]).upper()

            if l_desc:
                description_lines.append(l_desc)
            if l_type and l_type.upper() != l_desc.upper():
                description_lines.append(l_type)

            activities.append((
                title, start_dt, end_dt,
                "\n".join(description_lines).strip() or None,
                subtitle
            ))
            continue

        # ── TRAINING ──
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

        # ── RESERVE ──
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
                        if ct == "Code":       res_code    = val
                        elif ct == "Airport":  res_airport = val
                        elif ct == "LCLStart": res_lcl_s   = val
                        elif ct == "LCLEnd":   res_lcl_e   = val
                        elif ct == "UTCStart": res_utc_s   = val
                        elif ct == "UTCEnd":   res_utc_e   = val

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

        # ── REPORT TIME ──
        report_airport = ""

        if report_lcl and report_utc:
            r_lcl = parse_dt(report_lcl)
            r_utc = parse_dt(report_utc)
            if r_lcl and r_utc:
                description_lines.append("Report")
                description_lines.append(
                    f"{fmt_local(r_lcl)} ({fmt_utc(r_utc)})"
                )
                description_lines.append("__AIRPORT__")
                description_lines.append("")

        # ── PAIRING ──
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

                        for f in flight_elem.iter():
                            ft = f.tag.split('}')[-1]
                            if ft == "CarrierCode":   carrier  = f.text or ""
                            elif ft == "Number":      number   = f.text or ""
                            elif ft == "FromAirport": dep      = f.text or ""
                            elif ft == "ToAirport":   arr      = f.text or ""
                            elif ft == "LCLLTD":      lcl_dep  = f.text or ""
                            elif ft == "LCLLTA":      lcl_arr  = f.text or ""
                            elif ft == "UTCLTD":      utc_dep  = f.text or ""
                            elif ft == "UTCLTA":      utc_arr  = f.text or ""

                        if first_flight and dep:
                            report_airport = dep
                            first_flight = False

                        dep_lcl = parse_dt(lcl_dep)
                        arr_lcl = parse_dt(lcl_arr)
                        dep_utc = parse_dt(utc_dep)
                        arr_utc = parse_dt(utc_arr)

                        if not dep_lcl or not arr_lcl:
                            continue

                        # Record for monthly totals
                        block_min = parse_minutes(duration)
                        if block_min:
                            FLIGHT_LOG.append((dep_lcl.date(), block_min))

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
                        description_lines.append(
                            f"Track: https://www.flightradar24.com/data/flights/{carrier.lower()}{number}"
                        )
                        description_lines.append("")

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
                                        + sa_suffix(arr_lcl, arr_utc)
                                    )
                                if dep_lcl:
                                    description_lines.append(
                                        f"Dep {fmt_local(dep_lcl)}"
                                        + (f" ({fmt_utc(dep_utc)})" if dep_utc else "")
                                        + sa_suffix(dep_lcl, dep_utc)
                                    )
                                description_lines.append("")

        # ── Crew — match by pairing code + start date ──
        if pairing_code:
            date_key = start_dt.strftime("%Y-%m-%d")
            unique_key = f"{pairing_code}|{date_key}"
            crew = crew_by_pairing.get(unique_key)

            if not crew:
                for k, v in crew_by_pairing.items():
                    if k.endswith(f"|{date_key}"):
                        crew = v
                        break

            if crew:
                flight_crew, cabin_crew = crew
                if flight_crew:
                    description_lines.append("── Flight Crew ──")
                    description_lines.extend(flight_crew)
                    description_lines.append("")
                if cabin_crew:
                    description_lines.append("── Cabin Crew ──")
                    description_lines.extend(cabin_crew)
                    description_lines.append("")

        description_lines = [
            report_airport if line == "__AIRPORT__" else line
            for line in description_lines
        ]
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
# SUMMARY (calendar title) — shared by calendar + tracker
# =====================================================

def make_summary(title, description, subtitle):
    t = title.upper()
    sub = (subtitle or "").upper()

    if "SICK" in t or "SICK" in sub:
        return "🤒 SICK LEAVE"
    elif "FAMIL" in t or "FAMIL" in sub:
        return "👫🏻🏡 FAMILY RESPONSIBILITY"
    elif "OFF" in t:
        return "🟢 DAY OFF"
    elif "OPEN" in t or "AVAILABLE" in t:
        return "🟡 OPEN"
    elif "LEAVE" in t:
        return "🎉 LEAVE"
    elif "RESERVE" in t:
        return "🟠 RESERVE"
    elif "GROUND" in t:
        return "🕹️ GROUND DUTY"
    elif "TRAINING" in t:
        return f"📘 {subtitle}" if subtitle else "📘 TRAINING"
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
        return f"✈️ {'-'.join(routes)}" if len(routes) >= 2 else "✈️ DUTY"
    else:
        return f"📋 {title}"

# =====================================================
# MONTHLY TOTALS
# =====================================================

def classify(title, subtitle):
    t = title.upper()
    sub = (subtitle or "").upper()
    if "SICK" in t or "SICK" in sub or "FAMIL" in t or "FAMIL" in sub:
        return "leave"
    if "OFF" in t:
        return "off"
    if "OPEN" in t or "AVAILABLE" in t:
        return "open"
    if "LEAVE" in t:
        return "leave"
    if "RESERVE" in t:
        return "reserve"
    if "GROUND" in t:
        return "ground"
    if "TRAINING" in t:
        return "training"
    if "PAIRING" in t:
        return "pairing"
    return "other"

def days_covered(start, end):
    """Calendar days an activity occupies. An end time before noon on a
    later date (e.g. a day off ending 06:00) doesn't count that last date."""
    first = start.date()
    last = end.date()
    if last > first and end.hour < 12:
        last = last - timedelta(days=1)
    days = []
    d = first
    while d <= last:
        days.append(d)
        d = d + timedelta(days=1)
    return days

def build_totals(activities):
    months = {}

    def bucket(d):
        key = (d.year, d.month)
        if key not in months:
            months[key] = {
                "block": 0, "sectors": 0, "pairings": 0,
                "reserve": 0, "training": 0, "leave": 0, "off": 0,
            }
        return months[key]

    for d, minutes in FLIGHT_LOG:
        b = bucket(d)
        b["block"] += minutes
        b["sectors"] += 1

    for title, start, end, description, subtitle in activities:
        kind = classify(title, subtitle)
        if kind == "pairing":
            bucket(start.date())["pairings"] += 1
        elif kind in ("reserve", "training", "leave", "off"):
            for d in days_covered(start, end):
                bucket(d)[kind] += 1

    return months

# =====================================================
# BUILD ICS
# =====================================================

def build_ics(activities, now):
    cal = Calendar()

    for title, start, end, description, subtitle in activities:
        event = Event()
        summary = make_summary(title, description, subtitle)

        event.add("summary", summary)
        event.add("dtstart", start)
        event.add("dtend", end)
        if description:
            event.add("description", description)
        cal.add_component(event)

    # ── Monthly totals: all-day event on the 1st ──
    for (y, m), b in sorted(build_totals(activities).items()):
        # Skip months that only contain leave (not really published yet)
        if not (b["block"] or b["sectors"] or b["pairings"]
                or b["reserve"] or b["training"] or b["off"]):
            continue

        first = date(y, m, 1)
        label = first.strftime("%b %Y").upper()
        lines = [
            f"Block time: {fmt_hm(b['block'])}",
            f"Sectors: {b['sectors']}",
            f"Pairings: {b['pairings']}",
            f"Reserve days: {b['reserve']}",
            f"Training days: {b['training']}",
            f"Leave/sick days: {b['leave']}",
            f"Days off: {b['off']}",
        ]

        event = Event()
        event.add("uid", f"totals-{y}-{m:02d}@roster-calendar")
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("summary", f"📊 {label} TOTALS")
        event.add("dtstart", first)
        event.add("dtend", first + timedelta(days=1))
        event.add("description", "\n".join(lines))
        cal.add_component(event)

    # ── Last updated marker: all-day event on today's date ──
    stamp_short = now.strftime("%d %b %H:%M")
    stamp_long = now.strftime("%d %b %Y %H:%M")
    marker = Event()
    marker.add("uid", "sync-marker@roster-calendar")
    marker.add("dtstamp", datetime.now(timezone.utc))
    marker.add("summary", f"🔄 Synced {stamp_short}")
    marker.add("dtstart", now.date())
    marker.add("dtend", now.date() + timedelta(days=1))
    marker.add(
        "description",
        f"Last successful roster update: {stamp_long}\nDuties loaded: {len(activities)}"
    )
    cal.add_component(marker)

    return cal

# =====================================================
# SAVE
# =====================================================

def save(cal):
    with open("roster.ics", "wb") as f:
        f.write(cal.to_ical())
    print("📅 roster.ics saved")

# =====================================================
# CHANGE TRACKING
# =====================================================

CREW_MARKERS = ("── Flight Crew ──", "── Cabin Crew ──")
CATEGORY_ORDER = ["Swapped", "Times", "Details", "Crew", "Added", "Removed"]
CATEGORY_ICON = {
    "Swapped": "🔄", "Times": "⏰", "Details": "✏️",
    "Crew": "👥", "Added": "➕", "Removed": "➖",
}

# The "· 06:20 SA" additions are display-only, so the tracker ignores them
SA_SUFFIX_RE = re.compile(r"\s·\s(?:\d{2}\s[A-Za-z]{3}\s)?\d{2}:\d{2}\sSA$")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)

def split_description(desc):
    """Split a description into (body lines, crew lines or None)."""
    if not desc:
        return [], None
    positions = [desc.find(m) for m in CREW_MARKERS if desc.find(m) != -1]
    if not positions:
        body = [SA_SUFFIX_RE.sub("", l) for l in desc.split("\n") if l.strip()]
        return body, None
    cut = min(positions)
    body = [SA_SUFFIX_RE.sub("", l) for l in desc[:cut].split("\n") if l.strip()]
    crew = [l for l in desc[cut:].split("\n") if l.strip()]
    return body, crew

def build_state(activities):
    """Future duties only (from tomorrow), grouped by start date."""
    today = datetime.now(SA_TZ).date()
    state = {}
    for title, start, end, description, subtitle in activities:
        if start.date() <= today:
            continue
        body, crew = split_description(description)
        rec = {
            "summary": make_summary(title, description, subtitle),
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end": end.strftime("%Y-%m-%d %H:%M"),
            "body": body,
            "crew": crew,
        }
        state.setdefault(start.strftime("%Y-%m-%d"), []).append(rec)
    for k in state:
        state[k].sort(key=lambda r:
