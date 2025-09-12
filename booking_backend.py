from flask import Flask, jsonify, request
from flask_cors import CORS
from googleapiclient.discovery import build
from google.oauth2 import service_account
import datetime
from dotenv import load_dotenv
import os
import json

app = Flask(__name__)
CORS(app)  # Enable CORS for web app

# Load .env only for local development
load_dotenv()

# 🔑 Google Calendar Config
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("CALENDAR_ID")

# Load credentials from environment (Render) or fallback to file (local)
if os.getenv("GOOGLE_CREDENTIALS"):
    service_account_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=SCOPES,
        subject="admin@pythongurukul.com"  # Service account impersonates admin
    )
else:
    SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service-account.json")
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject="admin@pythongurukul.com"
    )

# Build Google Calendar service
service = build("calendar", "v3", credentials=creds)

# Define working hours blocks (7AM-1PM, 3PM-6PM, 7PM-11PM)
WORK_HOURS = [
    (datetime.time(7, 0), datetime.time(13, 0)),
    (datetime.time(15, 0), datetime.time(18, 0)),
    (datetime.time(19, 0), datetime.time(23, 0)),
]

def fmt(dt):
    return dt.strftime("%I:%M %p")

def get_free_slots(day, busy_events):
    available_slots = []
    ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    current_time_utc = datetime.datetime.now(datetime.timezone.utc)
    current_time_ist = current_time_utc.astimezone(ist_tz)
    min_bookable_time = current_time_ist + datetime.timedelta(hours=2)

    if busy_events:
        calendar_tz = busy_events[0][0].tzinfo
    else:
        calendar_tz = ist_tz

    day_busy_events = [
        (start_dt, end_dt, title)
        for start_dt, end_dt, title in busy_events
        if start_dt.date() == day
    ]

    for wh_start, wh_end in WORK_HOURS:
        work_start = datetime.datetime.combine(day, wh_start, tzinfo=calendar_tz)
        work_end = datetime.datetime.combine(day, wh_end, tzinfo=calendar_tz)

        current_slot_start = work_start
        while current_slot_start + datetime.timedelta(hours=1) <= work_end:
            current_slot_end = current_slot_start + datetime.timedelta(hours=1)
            is_future_enough = current_slot_start >= min_bookable_time

            if not is_future_enough:
                current_slot_start = current_slot_end
                continue

            is_free = True
            for busy_start, busy_end, busy_title in day_busy_events:
                has_overlap = current_slot_start < busy_end and current_slot_end > busy_start
                if has_overlap:
                    is_free = False
                    break

            if is_free:
                slot_time = f"{current_slot_start.strftime('%H:%M')}-{current_slot_end.strftime('%H:%M')}"
                available_slots.append(slot_time)

            current_slot_start = current_slot_end

    return available_slots

@app.route('/api/available-slots', methods=['GET'])
def get_available_slots():
    try:
        today = datetime.datetime.now(datetime.timezone.utc).date()
        start_date = today
        end_date = today + datetime.timedelta(days=7)

        start_of_range = datetime.datetime.combine(start_date, datetime.time(0, 0), tzinfo=datetime.timezone.utc)
        end_of_range = datetime.datetime.combine(end_date, datetime.time(23, 59), tzinfo=datetime.timezone.utc)

        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start_of_range.isoformat(),
            timeMax=end_of_range.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        busy_slots = []
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))
            start_dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
            busy_slots.append((start_dt, end_dt, event.get("summary", "(No Title)")))

        available_slots = {}
        for i in range(8):
            day = start_date + datetime.timedelta(days=i)
            day_str = day.strftime('%Y-%m-%d')
            free_slots = get_free_slots(day, busy_slots)
            if free_slots:
                available_slots[day_str] = {
                    'dayName': day.strftime('%A'),
                    'dayDate': day.strftime('%d %B %Y'),
                    'slots': free_slots
                }

        return jsonify(available_slots)
    except Exception as e:
        return jsonify({'error': f'Failed to fetch available slots: {str(e)}'}), 500

@app.route('/api/book-slots', methods=['POST'])
def book_slots():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data received'}), 400

        student = data.get('student', {})
        slots = data.get('slots', [])
        if not student or not slots:
            return jsonify({'error': 'Missing student or slots data'}), 400

        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        current_time_ist = datetime.datetime.now(datetime.timezone.utc).astimezone(ist_tz)
        min_bookable_time = current_time_ist + datetime.timedelta(hours=2)

        created_events = []
        for slot in slots:
            date_str = slot['date']
            time_range = slot['time']
            start_time_str, end_time_str = time_range.split('-')

            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            start_time = datetime.datetime.strptime(start_time_str, '%H:%M').time()
            end_time = datetime.datetime.strptime(end_time_str, '%H:%M').time()

            start_datetime = datetime.datetime.combine(date_obj, start_time, tzinfo=ist_tz)
            end_datetime = datetime.datetime.combine(date_obj, end_time, tzinfo=ist_tz)

            if start_datetime < min_bookable_time:
                return jsonify({
                    'error': f'Slot {date_str} {time_range} is too close to current time. '
                             f'Please select a slot at least 2 hours from now.'
                }), 400

            event = {
                'summary': f"{student.get('name', 'Student')}",
                'description': f"""Student: {student.get('name', 'N/A')}
Email: {student.get('email', 'N/A')}
Phone: {student.get('phone', 'Not provided')}
Session Type: {student.get('sessionType', 'N/A')}

Booked via Gurukul Python Booking System

Join the session using the Google Meet link above.""",
                'start': {
                    'dateTime': start_datetime.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
                'end': {
                    'dateTime': end_datetime.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
                'attendees': [
                    {
                        'email': student.get('email'),
                        'displayName': student.get('name', 'Student'),
                        'responseStatus': 'needsAction'
                    }
                ],
                'conferenceData': {
                    'createRequest': {
                        'requestId': f"meet-{date_str}-{start_time_str.replace(':', '')}-{hash(student.get('email', '')) % 10000}",
                        'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                    }
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'email', 'minutes': 24 * 60},
                        {'method': 'popup', 'minutes': 30},
                    ],
                },
                'guestsCanModify': False,
                'guestsCanInviteOthers': False,
                'guestsCanSeeOtherGuests': False,
            }

            created_event = service.events().insert(
                calendarId=CALENDAR_ID,
                body=event,
                conferenceDataVersion=1,
                sendUpdates='all'
            ).execute()

            created_events.append({
                'id': created_event['id'],
                'htmlLink': created_event.get('htmlLink', ''),
                'meetLink': created_event.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', 'No Meet link generated'),
                'date': date_str,
                'time': time_range
            })

        return jsonify({
            'success': True,
            'message': f'Successfully booked {len(slots)} session(s)',
            'events': created_events
        })
    except Exception as e:
        return jsonify({'error': f'Booking failed: {str(e)}'}), 500

def test_calendar_access():
    try:
        calendar_list = service.calendarList().list().execute()
        target_calendar = service.calendars().get(calendarId=CALENDAR_ID).execute()
        events_test = service.events().list(calendarId=CALENDAR_ID, maxResults=1).execute()
        return True
    except Exception:
        return False

if __name__ == '__main__':
    print("Starting Gurukul Python Booking Server...")
    print(f"Calendar ID: {CALENDAR_ID}")
    print("⏰ 2-Hour minimum booking buffer enabled")

    port = int(os.environ.get("PORT", 5000))  # Render provides PORT
    app.run(host="0.0.0.0", port=port)
