import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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

        page.goto(
            "https://saacrewconnect.cocre8.africa/html/home.html"
        )

        page.wait_for_selector(
            'input[type="password"]',
            timeout=15000
        )

        page.fill('input[type="text"]', USERNAME)
        page.fill('input[type="password"]', PASSWORD)

        try:
            page.press('input[type="password"]', 'Enter')
        except:
            pass

        page.wait_for_timeout(2000)

        try:
            page.locator(
                "button:visible"
            ).first.click(timeout=3000)
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


# ===== FETCH ROSTER =====
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


# ===== FETCH CREW =====
def get_flight_crew(
    token,
    date,
    carrier,
    number,
    from_airport
):

    import urllib3
    urllib3.disable_warnings()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

    soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Header/>
      <soapenv:Body>
        <FlightCrewListRequest Version="1.0">
          <Token>{token}</Token>
          <Data>
            <Flight>
              <Date>{date}</Date>
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

    response = requests.post(
        API_URL,
        data=soap_body,
        headers=headers,
        verify=False
    )

    if response.status_code != 200:
        return []

    try:

        root = ET.fromstring(response.text)

        crew = []

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

        print("⚠️ CREW PARSE ERROR:", e)

        return []


# ===== HELPERS =====
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


# ===== PARSE =====
def parse(xml_data, token):

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

        # ===== REPORT =====
        if report:

            report_dt = datetime.strptime(
                report,
                "%Y-%m-%d %H:%M"
            )

            description_lines.append("Report:")
            description_lines.append(
                f"{fmt_full(report_dt)} ({fmt_utc(report_dt)})"
            )

            description_lines.append("")

        # ===== FLIGHTS =====
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
                flight_date = ""

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

                    elif ft == "Date":

                        raw_date = f.text or ""

                        try:
                            flight_date = raw_date.split("T")[0]
                        except:
                            flight_date = raw_date

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

                # ===== CREW LOOKUP =====
                try:

                    crew = get_flight_crew(
                        token,
                        flight_date,
                        carrier,
                        number,
                        dep
                    )

                    if crew:
                        all_crew.extend(crew)

                except Exception as e:

                    print("⚠️ Crew lookup failed:", e)

            # ===== CREW SECTION =====
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


# ===== BUILD ICS =====
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

                            dep = route_section.split("→")[0].strip()

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


# ===== SAVE =====
def save(cal):

    with open("roster.ics", "wb") as f:
        f.write(cal.to_ical())

    print("📅 roster.ics saved")


# ===== MAIN =====
if __name__ == "__main__":

    token = get_token()

    xml_data = fetch_roster(token)

    activities = parse(xml_data, token)

    cal = build_ics(activities)

    save(cal)
