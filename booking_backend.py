from flask import Flask, jsonify, request
from flask_cors import CORS
from googleapiclient.discovery import build
from google.oauth2 import service_account
import datetime
from dotenv import load_dotenv
import os

app = Flask(__name__)
CORS(app)  # Enable CORS for web app

load_dotenv()

# 🔑 Service account credentials file
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID =  os.getenv('CALENDAR_ID')

# Build the service with domain-wide delegation
def build_service():
    """Build Google Calendar service with domain-wide delegation"""
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, 
        scopes=SCOPES,
        subject='admin@pythongurukul.com'  # Service account impersonates admin
    )
    return build("calendar", "v3", credentials=creds)

# Initialize service
service = build_service()

# Define working hours blocks (7AM-1PM, 4PM-6PM, 7PM-11PM)
WORK_HOURS = [
    (datetime.time(7, 0), datetime.time(13, 0)),   # 7 AM to 1 PM
    (datetime.time(15, 0), datetime.time(18, 0)),  # 3 PM to 6 PM  
    (datetime.time(19, 0), datetime.time(23, 0)),  # 7 PM to 11 PM
]

def fmt(dt):
    return dt.strftime("%I:%M %p")

def get_free_slots(day, busy_events):
    available_slots = []
    
    # Determine the timezone (IST = UTC+5:30)
    ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    
    # Get current time in IST
    current_time_utc = datetime.datetime.now(datetime.timezone.utc)
    current_time_ist = current_time_utc.astimezone(ist_tz)
    
    # Calculate minimum bookable time (current time + 2 hours)
    min_bookable_time = current_time_ist + datetime.timedelta(hours=2)
    
    print(f"Current IST time: {current_time_ist.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Minimum bookable time: {min_bookable_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # If busy_events exist, use their timezone, otherwise use IST
    if busy_events:
        calendar_tz = busy_events[0][0].tzinfo
    else:
        calendar_tz = ist_tz
    
    # Filter busy events for this specific day
    day_busy_events = []
    for start_dt, end_dt, title in busy_events:
        if start_dt.date() == day:
            day_busy_events.append((start_dt, end_dt, title))
    
    # Generate all possible 1-hour slots within work hours
    for wh_start, wh_end in WORK_HOURS:
        work_start = datetime.datetime.combine(day, wh_start, tzinfo=calendar_tz)
        work_end = datetime.datetime.combine(day, wh_end, tzinfo=calendar_tz)

        current_slot_start = work_start
        while current_slot_start + datetime.timedelta(hours=1) <= work_end:
            current_slot_end = current_slot_start + datetime.timedelta(hours=1)
            
            # Check if this slot is at least 2 hours from current time
            is_future_enough = current_slot_start >= min_bookable_time
            
            if not is_future_enough:
                print(f"Skipping slot {current_slot_start.strftime('%H:%M')} - too close to current time")
                current_slot_start = current_slot_end
                continue
            
            # Check if this slot conflicts with any busy event
            is_free = True
            for busy_start, busy_end, busy_title in day_busy_events:
                has_overlap = current_slot_start < busy_end and current_slot_end > busy_start
                
                if has_overlap:
                    is_free = False
                    print(f"Slot {current_slot_start.strftime('%H:%M')} conflicts with: {busy_title}")
                    break
            
            if is_free:
                # Format time slot as "HH:MM-HH:MM"
                slot_time = f"{current_slot_start.strftime('%H:%M')}-{current_slot_end.strftime('%H:%M')}"
                available_slots.append(slot_time)
                print(f"Available slot: {slot_time}")
            
            current_slot_start = current_slot_end

    return available_slots

@app.route('/api/available-slots', methods=['GET'])
def get_available_slots():
    try:
        print("Fetching available slots from workspace calendar...")  # Debug log
        
        # Define time range: today + next 7 days
        # But we'll filter out slots that are too close to current time
        today = datetime.datetime.now(datetime.timezone.utc).date()
        start_date = today  # Start from today to capture slots later today
        end_date = today + datetime.timedelta(days=7)    # Next 7 days from today

        print(f"Date range: {start_date} to {end_date}")  # Debug log

        start_of_range = datetime.datetime.combine(start_date, datetime.time(0, 0), tzinfo=datetime.timezone.utc)
        end_of_range = datetime.datetime.combine(end_date, datetime.time(23, 59), tzinfo=datetime.timezone.utc)

        time_min = start_of_range.isoformat()
        time_max = end_of_range.isoformat()

        print(f"Fetching events from {time_min} to {time_max}")  # Debug log

        # Fetch events from Google Calendar
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        print(f"Found {len(events)} existing events")  # Debug log

        # Convert to datetime ranges
        busy_slots = []
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))
            start_dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
            busy_slots.append((start_dt, end_dt, event.get("summary", "(No Title)")))

        # Generate available slots for each day
        available_slots = {}
        for i in range(8):  # Include today (8 days total)
            day = start_date + datetime.timedelta(days=i)
            day_str = day.strftime('%Y-%m-%d')
            
            free_slots = get_free_slots(day, busy_slots)
            print(f"{day_str} ({day.strftime('%A')}): {len(free_slots)} free slots")  # Debug log
            
            # Only include days that have available slots
            if free_slots:
                available_slots[day_str] = {
                    'dayName': day.strftime('%A'),
                    'dayDate': day.strftime('%d %B %Y'),
                    'slots': free_slots
                }

        print(f"Returning {len(available_slots)} days of data")  # Debug log
        return jsonify(available_slots)

    except Exception as e:
        print(f"Error in get_available_slots: {str(e)}")  # Debug log
        return jsonify({'error': f'Failed to fetch available slots: {str(e)}'}), 500

@app.route('/api/book-slots', methods=['POST'])
def book_slots():
    try:
        data = request.get_json()
        print(f"Received booking data: {data}")  # Debug log
        
        if not data:
            return jsonify({'error': 'No data received'}), 400
            
        student = data.get('student', {})
        slots = data.get('slots', [])
        
        if not student or not slots:
            return jsonify({'error': 'Missing student or slots data'}), 400
        
        # IST timezone
        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        current_time_ist = datetime.datetime.now(datetime.timezone.utc).astimezone(ist_tz)
        min_bookable_time = current_time_ist + datetime.timedelta(hours=2)
        
        created_events = []
        
        for slot in slots:
            try:
                # Parse the date and time
                date_str = slot['date']
                time_range = slot['time']  # e.g., "07:00-08:00"
                
                print(f"Processing slot: {date_str} {time_range}")  # Debug log
                
                start_time_str, end_time_str = time_range.split('-')
                
                # Create datetime objects
                date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                start_time = datetime.datetime.strptime(start_time_str, '%H:%M').time()
                end_time = datetime.datetime.strptime(end_time_str, '%H:%M').time()
                
                start_datetime = datetime.datetime.combine(date_obj, start_time, tzinfo=ist_tz)
                end_datetime = datetime.datetime.combine(date_obj, end_time, tzinfo=ist_tz)
                
                # Double-check: ensure slot is at least 2 hours from now
                if start_datetime < min_bookable_time:
                    return jsonify({
                        'error': f'Slot {date_str} {time_range} is too close to current time. '
                                f'Please select a slot at least 2 hours from now.'
                    }), 400
                
                # Create the event with student as attendee and Google Meet
                event = {
                    'summary': f"{student.get('sessionType', 'Python Session')} - {student.get('name', 'Student')}",
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
                            'conferenceSolutionKey': {
                                'type': 'hangoutsMeet'
                            }
                        }
                    },
                    'reminders': {
                        'useDefault': False,
                        'overrides': [
                            {'method': 'email', 'minutes': 24 * 60},   # 1 day before
                            {'method': 'popup', 'minutes': 30},        # 30 minutes before
                        ],
                    },
                    'guestsCanModify': False,
                    'guestsCanInviteOthers': False,
                    'guestsCanSeeOtherGuests': False,
                }
                
                print(f"Creating event: {event['summary']}")  # Debug log
                
                # Insert the event and send invitations with Meet link
                created_event = service.events().insert(
                    calendarId=CALENDAR_ID, 
                    body=event,
                    conferenceDataVersion=1,  # Required for Google Meet
                    sendUpdates='all'  # This sends email invitations to attendees
                ).execute()
                created_events.append({
                    'id': created_event['id'],
                    'htmlLink': created_event.get('htmlLink', ''),
                    'meetLink': created_event.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', 'No Meet link generated'),
                    'date': date_str,
                    'time': time_range
                })
                
                print(f"Event created successfully: {created_event['id']}")  # Debug log
                
            except Exception as slot_error:
                print(f"Error processing slot {date_str} {time_range}: {str(slot_error)}")
                return jsonify({'error': f'Error processing slot {date_str} {time_range}: {str(slot_error)}'}), 500
        
        return jsonify({
            'success': True,
            'message': f'Successfully booked {len(slots)} session(s)',
            'events': created_events
        })

    except Exception as e:
        print(f"Booking error: {str(e)}")  # Debug log
        return jsonify({'error': f'Booking failed: {str(e)}'}), 500

def test_calendar_access():
    """Test function to verify calendar access and permissions"""
    try:
        print("Testing calendar access...")
        
        # Test 1: List accessible calendars
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get('items', [])
        
        print("Accessible calendars:")
        for calendar in calendars:
            print(f"- {calendar.get('summary')} ({calendar.get('id')})")
            print(f"  Access Role: {calendar.get('accessRole')}")
        
        # Test 2: Try to access the target calendar specifically
        target_calendar = service.calendars().get(calendarId=CALENDAR_ID).execute()
        print(f"Target calendar access: ✅ {target_calendar.get('summary')}")
        
        # Test 3: Try to list events (read permission)
        events_test = service.events().list(calendarId=CALENDAR_ID, maxResults=1).execute()
        print(f"Events read permission: ✅ Found {len(events_test.get('items', []))} events")
        
        return True
        
    except Exception as e:
        print(f"Calendar access test failed: {e}")
        print("This usually means domain-wide delegation is not configured properly.")
        return False

if __name__ == '__main__':
    print("Starting Gurukul Python Booking Server...")
    print(f"Calendar ID: {CALENDAR_ID}")
    print(f"Service Account File: {SERVICE_ACCOUNT_FILE}")
    print("⏰ 2-Hour minimum booking buffer enabled")
    
    # Test calendar connection with detailed diagnostics
    if test_calendar_access():
        print("✅ Google Calendar connection successful!")
        print("✅ Domain-wide delegation is working!")
    else:
        print("❌ Google Calendar connection failed!")
        print("Please check domain-wide delegation configuration.")
        print("\nTo fix this:")
        print("1. Get Client ID from your service-account.json file")
        print("2. Go to admin.google.com > Security > API Controls > Domain-wide Delegation")
        print("3. Add the Client ID with scope: https://www.googleapis.com/auth/calendar")
    
    app.run(debug=True, port=5000)