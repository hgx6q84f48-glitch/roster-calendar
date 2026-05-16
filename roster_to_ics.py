import os
import re
import requests
import urllib3
import xml.etree.ElementTree as ET

from datetime import datetime, timezone

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

# TEMPORARY:
# paste token from Safari network request
TOKEN = "VFxFl5JveayIr/E7EEs8yh4ASIdH/WzOHc3MuPFYiKc="

if not USERNAME or not PASSWORD:
    raise Exception("❌ Missing CREW_USER / CREW_PASS")


LOGIN_URL = "https://saacrewconnect.cocre8.africa/html/home.html"
ROSTER_URL = "https://saacrewconnect.cocre8.africa/php/roster.php"
CREW_API_URL = "https://saacrewconnect.cocre8.africa/crewApi"


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
        return None


def fmt_full(dt):

    return dt.strftime("%d %b %Y %H:%M")


def fmt_utc(dt):

    utc = dt.astimezone(timezone.utc)

    return utc.strftime("%H:%MZ")


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
# BUILD REQUESTS SESSION
# =====================================================

def build_session(context):

    session = requests.Session()

    cookies = context.cookies()

    for c in cookies:

        session.cookies.set(
            c["name"],
            c["value"]
        )

    return session


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
# DIRECT CREW API
# =====================================================

def get_crew_for_flight(
    session,
    flight_no,
    flight_date,
    carrier,
    number,
    from_airport
):

    print(f"🔎 Crew lookup {flight_no}")

    try:

        xml_payload = f"""
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
    <soapenv:Header/>
    <soapenv:Body>
        <FlightCrewListRequest Version="1.0">
            <Token>{TOKEN}</Token>
            <Data>
                <Flight>
                    <Date>{flight_date}</Date>
                    <CarrierCode>{carrier}</CarrierCode>
                    <Number>{number}</Number>
                    <OperationalSuffix></OperationalSuffix>
                    <FromAirport>{from_airport}</FromAirport>
                    <Status>S</Status>
                </Flight>
            </Data>
        </FlightCrewListRequest>
    </soapenv:Body>
</soapenv:Envelope>
"""

        headers = {

            "Accept":
                "application/xml, text/xml, */*; q=0.01",

            "Content-Type":
                "application/x-www-form-urlencoded; charset=UTF-8",

            "Origin":
                "https://saacrewconnect.cocre8.africa",

            "Referer":
                "https://saacrewconnect.cocre8.africa/php/roster.php",

            "X-Requested-With":
                "XMLHttpRequest",
        }

        response = session.post(
            CREW_API_URL,
            data=xml_payload,
            headers=headers,
            timeout=30,
            verify=False
        )

        print(
            f"📡 crewApi status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            return []
        
        print(response.text[:2000])
        
        crew = []

        root = ET.fromstring(response.text)

        for crew_member in root.iter():

            tag = crew_member.tag.split('}')[-1]

            if tag != "Crew":
                continue

            first_name = ""
            surname = ""
            rank_code = ""
            position_code = ""

            for x in crew_member:

                xt = x.tag.split('}')[-1]

                if xt == "FirstName":
                    first_name = x.text or ""

                elif xt == "Surname":
                    surname = x.text or ""

                elif xt == "RankCode":
                    rank_code = x.text or ""

                elif xt == "PositionCode":
                    position_code = x.text or ""

            if not first_name and not surname:
                continue

            crew.append(
                f"{rank_code} "
                f"{first_name} {surname} "
                f"({position_code})"
            )

        crew = list(dict.fromkeys(crew))

        print("👨‍✈️ CREW FOUND:", crew)

        return crew

    except Exception as e:

        print("⚠️ Crew lookup failed:", e)

        return []


# =====================================================
# PARSE ROSTER XML
# =====================================================

def parse(xml_data, session):

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

        # =================================================
        # REPORT
        # =================================================

        if report:

            report_dt = datetime.strptime(
                report,
                "%Y-%m-%d %H:%M"
            )

            description_lines.append("Report:")

            description_lines.append(
                f"{fmt_full(report_dt)} "
                f"({fmt_utc(report_dt)})"
            )

            description_lines.append("")

        # =================================================
        # PAIRING
        # =================================================

        pairing = None

        for elem in activity:

            if "Pairing" in elem.tag:
                pairing = elem
                break

        if pairing is not None:

            description_lines.append("Flights:")

            all_crew = []

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

                description_lines.append(line)

                # =================================================
                # DIRECT CREW LOOKUP
                # =================================================

                try:

                    crew = get_crew_for_flight(
                        session=session,
                        flight_no=flight_no,
                        flight_date=start_dt.strftime("%Y-%m-%d"),
                        carrier=carrier,
                        number=number,
                        from_airport=dep
                    )

                    if crew:
                        all_crew.extend(crew)

                except Exception as e:

                    print(
                        "⚠️ Crew lookup failed:",
                        e
                    )

            # =================================================
            # CREW SECTION
            # =================================================

            all_crew = list(dict.fromkeys(all_crew))

            if all_crew:

                description_lines.append("")
                description_lines.append("Crew:")

                for c in all_crew:
                    description_lines.append(c)

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

        if t == "OPEN DAY":
            summary = "🟡 OPEN"

        elif "OFF" in t:
            summary = "🟢 DAY OFF"

        elif "LEAVE" in t:
            summary = "🎉 LEAVE"

        else:

            duty_routes = []

            if description:

                for line in description.split("\n"):

                    if "→" in line:

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

                            if not duty_routes:
                                duty_routes.append(dep)

                            duty_routes.append(arr)

                        except:
                            pass

            if len(duty_routes) >= 2:

                route_text = "-".join(duty_routes)

                summary = f"✈️ {route_text}"

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

        session = build_session(context)

        open_roster(page)

        xml_data = fetch_roster_xml(page)

        activities = parse(
            xml_data,
            session
        )

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
