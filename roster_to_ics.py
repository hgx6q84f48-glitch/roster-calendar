import os
import re
import xml.etree.ElementTree as ET

from datetime import datetime, timezone

from icalendar import Calendar, Event

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


# =====================================================
# CONFIG
# =====================================================

USERNAME = os.environ.get("CREW_USER")
PASSWORD = os.environ.get("CREW_PASS")

if not USERNAME or not PASSWORD:
    raise Exception("❌ Missing CREW_USER / CREW_PASS")


LOGIN_URL = "https://saacrewconnect.cocre8.africa/html/home.html"
ROSTER_URL = "https://saacrewconnect.cocre8.africa/php/roster.php"
ROSTER_EVENT_SELECTOR = ".fc-event, .event, .roster-event"


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


def xml_tag(elem):

    return elem.tag.split('}')[-1]


def normalize_flight_no(flight_no):

    return re.sub(
        r"[^A-Z0-9]",
        "",
        clean(flight_no).upper()
    )


def crew_api_post_response(response):

    return (
        "crewApi" in response.url
        and response.request.method == "POST"
    )


def response_text(response):

    try:
        return response.text()
    except:
        return ""


def parse_crew_xml(xml_text):

    crew = []

    root = ET.fromstring(xml_text)

    for crew_member in root.iter():
