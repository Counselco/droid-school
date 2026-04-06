#!/usr/bin/env python3
"""
DroidSchool Re-Examination Scheduler
Runs daily via cron. Checks roster for due re-tests.
Notifies operators when their droids need re-certification.

Cron: 0 9 * * * python3 /home/droidschool/dag/retest_scheduler.py

"""
import sqlite3
import json
import requests
import os
from datetime import datetime, timedelta

DB = '/home/droidschool/roster.db'
DAG = 'https://dag.tibotics.com'
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

RETEST_SCHEDULE = {
    'day_7':  {'days': 7,   'scenarios': 2, 'label': 'Day 7 Check-In',          'paid': False},
    'day_30': {'days': 30,  'scenarios': 3, 'label': 'Monthly Check',            'paid': False},
    'day_90': {'days': 90,  'scenarios': 6, 'label': 'Quarterly Certification',  'paid': True},
    'day_180':{'days': 180, 'scenarios': 6, 'label': 'Semi-Annual Renewal',      'paid': True},
}

def get_due_retests():
    """Find all droids with re-tests due today."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Get all enrolled droids
    cur.execute("""
        SELECT d.userid, d.bot, d.operator_email, d.enrolled_at,
               d.bootcamp_passed_at, d.last_retest_at, d.next_retest_type
        FROM droids d
        WHERE d.bootcamp_passed_at IS NOT NULL
    """)
    droids = cur.fetchall()
    conn.close()
    
    due = []
    today = datetime.now()
    
    for droid in droids:
        if not droid['bootcamp_passed_at']:
            continue
            
        bootcamp_date = datetime.fromisoformat(droid['bootcamp_passed_at'])
        last_retest = datetime.fromisoformat(droid['last_retest_at']) if droid['last_retest_at'] else bootcamp_date
        next_type = droid['next_retest_type'] or 'day_7'
        
        schedule = RETEST_SCHEDULE.get(next_type)
        if not schedule:
            continue
            
        days_since = (today - last_retest).days
        
        if days_since >= schedule['days']:
            due.append({
                'userid': droid['userid'],
                'bot': droid['bot'],
                'operator_email': droid['operator_email'],
                'retest_type': next_type,
                'schedule': schedule,
                'days_overdue': days_since - schedule['days'],
                'bootcamp_date': bootcamp_date.isoformat()
            })
    
    return due

def notify_operator(droid_info):
    """Write a notification to the DAG for the operator."""
    notification = {
        'author': 'tibotics_verified',
        'skill_name': f"RETEST-DUE-{droid_info['bot']}-{datetime.now().strftime('%Y%m%d')}",
        'tier': 'system_event',
        'content': json.dumps({
            'type': 'retest_due',
            'droid': droid_info['bot'],
            'operator_email': droid_info.get('operator_email', ''),
            'retest_type': droid_info['retest_type'],
            'label': droid_info['schedule']['label'],
            'scenarios': droid_info['schedule']['scenarios'],
            'paid': droid_info['schedule']['paid'],
            'days_overdue': droid_info['days_overdue'],
            'instructions': (
                f"Tell your droid: 'Go to tibotics.com and complete your "
                f"{droid_info['schedule']['label']} re-test.' "
                f"Or run: python3 bootcamp.py --droid {droid_info['bot']} "
                f"--retest {droid_info['retest_type']}"
            ),
            'timestamp': datetime.now().isoformat()
        })
    }
    
    try:
        resp = requests.post(f"{DAG}/write",
            json=notification,
            headers={'Content-Type': 'application/json'},
            timeout=10)
        return resp.json().get('status') == 'ok'
    except Exception as e:
        print(f"  Warning: Could not write notification to DAG: {e}")
        return False

def update_droid_retest_status(userid, retest_type):
    """Update the droid's next scheduled re-test."""
    schedule_order = ['day_7', 'day_30', 'day_90', 'day_180']
    current_idx = schedule_order.index(retest_type)
    next_type = 'day_180' if current_idx >= len(schedule_order) - 1 else schedule_order[current_idx + 1]
    
    conn = sqlite3.connect(DB)
    conn.execute("""
        UPDATE droids 
        SET last_retest_at = ?, next_retest_type = ?
        WHERE userid = ?
    """, (datetime.now().isoformat(), next_type, userid))
    conn.commit()
    conn.close()

def add_missing_columns():
    """Ensure droids table has retest columns."""
    conn = sqlite3.connect(DB)
    columns_to_add = [
        ('bootcamp_passed_at', 'DATETIME'),
        ('last_retest_at', 'DATETIME'),
        ('next_retest_type', 'TEXT DEFAULT "day_7"'),
        ('operator_email', 'TEXT'),
        ('compliance_status', 'TEXT DEFAULT "pending_bootcamp"'),
        ('serial_number', 'TEXT'),
    ]
    for col, coltype in columns_to_add:
        try:
            conn.execute(f"ALTER TABLE droids ADD COLUMN {col} {coltype}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    conn.close()

def main():
    print(f"\nDroidSchool Re-Examination Scheduler")
    print(f"Running: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 40)
    
    # Ensure schema is current
    add_missing_columns()
    
    # Find due re-tests
    try:
        due = get_due_retests()
    except Exception as e:
        print(f"Could not query roster: {e}")
        print("Schema may need updating — run with --migrate flag")
        return
    
    if not due:
        print("No re-tests due today.")
        return
    
    print(f"\n{len(due)} re-test(s) due:\n")
    
    for droid in due:
        print(f"  {droid['bot']}")
        print(f"    Type: {droid['schedule']['label']}")
        print(f"    Scenarios: {droid['schedule']['scenarios']} of 6")
        print(f"    Paid tier: {'Yes' if droid['schedule']['paid'] else 'No — free'}")
        print(f"    Days overdue: {droid['days_overdue']}")
        
        # Notify operator via DAG
        if notify_operator(droid):
            print(f"    Notification: Written to DAG")
        
        # Update scheduler
        update_droid_retest_status(droid['userid'], droid['retest_type'])
        print(f"    Next scheduled: {RETEST_SCHEDULE.get(droid['retest_type'],{}).get('label','ongoing')}")
        print()
    
    print(f"Scheduler complete. {len(due)} notification(s) sent.")

if __name__ == '__main__':
    import sys
    if '--migrate' in sys.argv:
        add_missing_columns()
        print("Schema migration complete.")
    else:
        main()

