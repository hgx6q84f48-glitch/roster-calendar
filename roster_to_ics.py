import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from icalendar import Calendar, Event

API_URL = "https://saacrewconnect.cocre8.africa/crewApi"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
}

SOAP_BODY = """<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Header/>
  <soapenv:Body>
    <RosterRequest Version="1.0">
      <Token>dz3ORIAHzWBwJqlEv0nfwdmAkLns9SQ1MObtBXhuxeA=</Token>
      <Data>
        <startDate>2025-10-10</startDate>
        <endDate>2026-10-05</endDate>
        <emplNbr>SBW412E</emplNbr>
        <rstrHist>0</rstrHist>
      </Data>
    </RosterRequest>
  </soapenv:Body>
</soapenv:Envelope>
"""

# ===== FETCH DATA =====
response = requests.post(API_URL, data=SOAP_BODY, headers=HEADERS)

if response.status_code != 200:
    print("Request failed:", response.status_code)
    print(response.text)
    exit()

root = ET.fromstring(response.text)
cal = Calendar()

# ===== TIME PARSER =====
def extract_minutes(t):
    if not t:
        return 9999
    try:
        if " " in t:
            t = t.split(" ")[-1]
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except:
        return 9999

# ===== LOOP ACTIVITIES =====
for activity in root.iter():
    if 'Activity' not in activity.tag:
        continue

    def get(tag):
        for elem in activity:
            if tag in elem.tag:
                return elem.text
        return None

    title = get('TypeDescription') or "Duty"
    title_clean = title.upper()

    lcl_start = get('LCLStart')
    lcl_end = get('LCLEnd')

    sign_on = get('LCLExpectedSignOn') or lcl_start
    sign_off = get('LCLExpectedSignOff') or lcl_end

    if not sign_on or not sign_off:
        continue

    try:
        start_dt = datetime.strptime(sign_on, "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(sign_off, "%Y-%m-%d %H:%M")
    except:
        continue

    # ===== DEFAULT =====
    summary = "🔴 DUTY"
    description = ""

    # ===== OFF =====
    if "OFF" in title_clean:
        summary = "🟢 OFF"
        description = "DAY OFF"

    # ===== LEAVE =====
    elif "LEAVE" in title_clean:
        summary = "🎉 LEAVE"
        description = "ON LEAVE"

    # ===== OPEN =====
    elif "OPEN" in title_clean:
        summary = "🟡 OPEN"
        description = f"""TYPE: Open Day

🕒 DUTY
Sign On: {sign_on}
Sign Off: {sign_off}
"""

    # ===== TRAINING =====
    course = None
    for child in activity:
        if 'Course' in child.tag:
            course = child
            break

    if course is not None:
        def get_course(tag):
            for elem in course:
                if tag in elem.tag:
                    return elem.text
            return None

        course_name = get_course('Description') or "TRAINING"
        location = get_course('Base') or ""

        modules = []

        for module in course.iter():
            if 'Module' in module.tag:
                mod_desc = None
                mod_start = None
                mod_end = None

                for m in module:
                    if 'Description' in m.tag:
                        mod_desc = m.text
                    if 'Start' in m.tag:
                        mod_start = m.text
                    if 'End' in m.tag:
                        mod_end = m.text

                if mod_desc:
                    modules.append({
                        "desc": mod_desc,
                        "start": mod_start,
                        "end": mod_end
                    })

        # ===== SORT MODULES BY TIME ONLY =====
        modules.sort(key=lambda m: extract_minutes(m["start"]))

        # ===== FORMAT =====
        modules_text = ""

        for m in modules:
            start_time = ""
            end_time = ""

            if m["start"]:
                start_time = m["start"].split(" ")[-1]

            if m["end"]:
                end_time = m["end"].split(" ")[-1]

            if start_time and end_time:
                modules_text += f"{start_time}–{end_time}  {m['desc']}\n"
            else:
                modules_text += f"{m['desc']}\n"

        aircraft = course_name.split()[0]

        summary = f"📘 {aircraft} TRAINING"

        description = f"""COURSE:
{course_name}

LOCATION:
{location}

🕒 DUTY
Sign On: {sign_on}
Sign Off: {sign_off}

MODULES:
{modules_text}
"""

    # ===== CREATE EVENT =====
    event = Event()
    event.add('summary', summary)
    event.add('dtstart', start_dt)
    event.add('dtend', end_dt)
    event.add('description', description)

    cal.add_component(event)

# ===== SAVE =====
with open('roster.ics', 'wb') as f:
    f.write(cal.to_ical())

print("✅ ICS file created: roster.ics")