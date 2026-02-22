import requests
import json
import time
import hashlib
import os
import re
import argparse
import configparser
import sys
import subprocess
from datetime import datetime
from pathlib import Path

try:
    import viatom_session_utils as vsu
except ImportError:
    vsu = None

# Configuration
SECRET = "a64255ab64344fb99612badde43d5365"
BASE_URL = "https://ai.viatomtech.com"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

class ViatomClient:
    def __init__(self, secret, token=None, user_id=None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json"
        })
        if token:
            self.session.headers.update({"Authorization": f"Viatom {token}"})
        self.user_id = user_id
        self.secret = secret

    def login(self, email, password):
        url = f"{BASE_URL}/login/new"
        payload = {"email": email, "password": password}
        res = self.post(url, payload)
        if res and res.get('code') in (0, 200):
            data = res.get('data', {})
            token = data.get('token')
            user_id = data.get('userId')
            if token and user_id:
                self.user_id = str(user_id)
                self.session.headers.update({"Authorization": token})
                print("Login successful.")
                return True
        print(f"Login failed: {res.get('msg') if res else 'Unknown error'}")
        return False

    def sign_request(self, payload_dict, timestamp):
        """
        Reverse-engineered from Smali (SignInterceptor + SignInterceptorKt).
        1. Parse body as map
        2. Set "timeStamp" = ts, "salt" = secret
        3. Force all values to strings (due to GSON untyped map parsing)
        4. Serialize to JSON natively (TreeMap sorts keys alphabetically)
        5. Replace '\\' with ''
        6. MD5 hex string, uppercase.
        """
        # Copy to avoid modifying the caller's payload
        m = payload_dict.copy()
        m["timeStamp"] = str(timestamp)
        m["salt"] = self.secret
        
        # Force all values to strings
        for k, v in m.items():
            if not isinstance(v, str):
                m[k] = str(v)
                
        # Serialize sorted without spaces
        json_str = json.dumps(m, separators=(',', ':'), sort_keys=True)
        
        # Apply escape stripping (from smali)
        json_str = json_str.replace("\\", "")
        
        # Hash
        signature = hashlib.md5(json_str.encode('utf-8')).hexdigest().upper()
        return signature

    def post(self, url, payload):
        ts = str(int(time.time() * 1000))
        signature = self.sign_request(payload, ts)
        
        headers = {
            "timeStamp": ts,
            "sign": signature
        }
        
        body_str = json.dumps(payload, separators=(',', ':'))
        
        resp = self.session.post(url, data=body_str, headers=headers)
        if resp.status_code != 200:
            print(f"[{resp.status_code}] Network Request Error")
            return None
            
        data = resp.json()
        if data.get('code') not in (0, 200):
             print(f"API Error [{data.get('code')}]: {data.get('msg')}")
             
        return data

    def get_oxygen_list(self, page=1, size=50):
        url = f"{BASE_URL}/v1/oxygen/list"
        payload = {
            "current": page,
            "size": size,
            "userId": int(self.user_id)
        }
        return self.post(url, payload)

    def delete_session(self, session_id):
        url = f"{BASE_URL}/v1/oxygen/delete"
        # The delete endpoint expects the ID in the query string, but we still sign the payload
        payload = {"id": int(session_id)}
        
        ts = str(int(time.time() * 1000))
        signature = self.sign_request(payload, ts)
        
        headers = {
            "timeStamp": ts,
            "sign": signature
        }
        
        # Append to URL directly
        resp = self.session.post(url + f"?id={session_id}", headers=headers, json=payload)
        if resp.status_code != 200:
            print(f"[{resp.status_code}] Network Request Error")
            return None
            
        data = resp.json()
        if data.get('code') not in (0, 200):
             print(f"API Error [{data.get('code')}]: {data.get('msg')}")
             
        return data

    def update_remark(self, session_id, remark):
        url = f"{BASE_URL}/v1/oxygen/update/remark"
        payload = {"id": int(session_id), "remark": remark}
        return self.post(url, payload)


def sanitize_filename(name):
    """Remove characters that are invalid in Windows filenames, including newlines."""
    # Replace newlines with spaces
    name = name.replace('\n', ' ').replace('\r', '')
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def get_pc_app_credentials():
    if os.name != 'nt':
        return None, None
        
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        local_app_data = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        
    config_path = os.path.join(local_app_data, 'O2 Insight Pro', 'config.ini')
    if not os.path.exists(config_path):
        return None, None
        
    email = None
    password = None
    try:
        with open(config_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line.startswith('usr='):
                    email = line.split('=', 1)[1]
                elif line.startswith('pwd='):
                    password = line.split('=', 1)[1]
    except Exception as e:
        print(f"Error reading PC app config: {e}")
                
    return email, password

def load_ignored_sessions():
    log_file = os.path.join(SCRIPT_DIR, "ignored_sessions.txt")
    if not os.path.exists(log_file):
        try:
            with open(log_file, 'w') as f:
                pass
        except Exception as e:
            print(f"Warning: Could not create {log_file}: {e}")
        return set()
    with open(log_file, 'r') as f:
        return set(line.strip() for line in f if line.strip())

def log_ignored_session(session_id):
    log_file = os.path.join(SCRIPT_DIR, "ignored_sessions.txt")
    with open(log_file, 'a') as f:
        f.write(f"{session_id}\n")

def find_session_by_timestamp(client, timestamp_str):
    page = 1
    size = 50
    while True:
        res = client.get_oxygen_list(page=page, size=size)
        if not res or 'data' not in res:
            break
        records = res.get('data', {}).get('records', [])
        if not records:
            break
        for r in records:
            ts = r.get('measureTime')
            dt_str = None
            if isinstance(ts, (int, float)):
                dt_str = datetime.fromtimestamp(ts / 1000.0).strftime("%Y%m%d%H%M%S")
            elif isinstance(ts, str):
                try:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                    dt_str = dt.strftime("%Y%m%d%H%M%S")
                except:
                    pass
            if dt_str == timestamp_str:
                return r.get('id')
        if len(records) < size:
            break
        page += 1
    return None

def main():
    parser = argparse.ArgumentParser(description="O2Ring Auto Downloader and Manager")
    parser.add_argument("--delete", metavar="ID_OR_TIMESTAMP", type=str, help="Delete a session by API ID or timestamp (YYYYMMDDHHMMSS)")
    parser.add_argument("--remark", nargs=2, metavar=("ID", "REMARK"), help="Update remark for a session")
    parser.add_argument("--config", help="Path to config file", default="o2_config.ini")
    parser.add_argument("--output-dir", help="Directory to save downloads", default=None)
    parser.add_argument("--csv", action="store_true", default=None, help="Generate CSV files after download")
    parser.add_argument("--no-csv", action="store_false", dest="csv", help="Do not generate CSV files")
    parser.add_argument("--analyze", action="store_true", default=None, help="Run HR Spike analysis after download")
    parser.add_argument("--no-analyze", action="store_false", dest="analyze", help="Do not run HR Spike analysis")
    args = parser.parse_args()

    # Load local config
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(SCRIPT_DIR, config_path)
    config_parser = configparser.ConfigParser()
    
    email = None
    password = None
    output_dir = "data"
    should_generate_csv = False
    should_run_analysis = True
    min_duration_mins = 60
    launch_after = ""
    
    # Try to load defaults from sample if this is a brand new run
    sample_config_path = os.path.join(SCRIPT_DIR, 'o2_config.sample.ini')
    if not os.path.exists(config_path) and os.path.exists(sample_config_path):
        try:
            config_parser.read(sample_config_path, encoding='utf-8')
            if 'Settings' in config_parser:
                # Remove dummy credentials so we don't try to log in with them
                if config_parser['Settings'].get('email') == 'your_email@example.com':
                    config_parser['Settings'].pop('email', None)
                if config_parser['Settings'].get('password') == 'your_password':
                    config_parser['Settings'].pop('password', None)
        except Exception as e:
            print(f"Error reading sample config: {e}")
    
    if os.path.exists(config_path):
        try:
            config_parser.read(config_path, encoding='utf-8')
        except Exception as e:
            print(f"Error reading config {config_path}: {e}")

    if 'Settings' in config_parser:
        email = config_parser['Settings'].get('email')
        password = config_parser['Settings'].get('password')
        output_dir = config_parser['Settings'].get('output_dir', 'data')
        should_generate_csv = config_parser['Settings'].getboolean('generate_csv', fallback=False)
        should_run_analysis = config_parser['Settings'].getboolean('run_analysis_report', fallback=True)
        min_duration_mins = config_parser['Settings'].getint('skip_short_sessions_under_mins', fallback=60)
        launch_after = config_parser['Settings'].get('launch_after', fallback='')
    else:
        config_parser.add_section('Settings')

    # Priority: CLI > Config
    if args.output_dir:
        output_dir = args.output_dir
    if args.csv is not None:
        should_generate_csv = args.csv
    if args.analyze is not None:
        should_run_analysis = args.analyze
        
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(SCRIPT_DIR, output_dir)

    client = ViatomClient(secret=SECRET)
    login_success = False

    if email and password:
        login_success = client.login(email, password)
        
    if not login_success:
        print("Local config credentials missing or invalid. Attempting to extract from PC App config...")
        pc_email, pc_password = get_pc_app_credentials()
        if pc_email and pc_password:
            if client.login(pc_email, pc_password):
                login_success = True
                config_parser.set('Settings', 'email', pc_email)
                config_parser.set('Settings', 'password', pc_password)
                config_parser.set('Settings', 'output_dir', output_dir)
                config_parser.set('Settings', 'generate_csv', str(should_generate_csv).lower())
                config_parser.set('Settings', 'run_analysis_report', str(should_run_analysis).lower())
                config_parser.set('Settings', 'skip_short_sessions_under_mins', str(min_duration_mins))
                config_parser.set('Settings', 'launch_after', launch_after)
                try:
                    with open(config_path, 'w', encoding='utf-8') as f:
                        config_parser.write(f)
                    print(f"Saved extracted credentials to {config_path}")
                except Exception as e:
                    print(f"Error saving config: {e}")
            else:
                print("Failed to login with PC App credentials.")
        else:
            print("Could not find PC App credentials.")
            
    if not login_success:
        print(f"Please provide valid credentials by running the script once on a PC with the app installed, or manually populate {config_path}")
        return

    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
        except Exception as e:
            print(f"Failed to create output directory {output_dir}: {e}")
            return

    ignored_ids = load_ignored_sessions()
    merged_fragments = vsu.load_merged_fragments(output_dir) if vsu else set()

    if args.delete:
        target = args.delete
        session_id = target
        is_timestamp = False
        
        if len(target) >= 14 or not target.isdigit():
            is_timestamp = True
            print(f"Looking up API ID for timestamp {target}...")
            session_id = find_session_by_timestamp(client, target)
            if not session_id:
                print(f"Could not find session with timestamp {target} on the server.")
                return
                
        print(f"Deleting session {session_id}...")
        res = client.delete_session(session_id)
        if res and res.get('code') in (0, 200):
            print("Session deleted successfully (and logged locally).")
            # If they provided a timestamp, log that so it's readable in the txt file
            log_ignored_session(target if is_timestamp else session_id)
        else:
            print(f"Failed to delete session: {res}")
        return

    if args.remark:
        session_id = int(args.remark[0])
        remark_text = args.remark[1]
        print(f"Updating remark for session {session_id} to '{remark_text}'...")
        res = client.update_remark(session_id, remark_text)
        if res and res.get('code') in (0, 200):
            print("Remark updated successfully.")
        return

    print("Fetching oxygen session list...")
    
    all_raw_records = []
    page = 1
    size = 50
    
    while True:
        res = client.get_oxygen_list(page=page, size=size)
        
        if not res or 'data' not in res:
            if page == 1:
                print("Failed to get session list or empty data.")
                return
            else:
                break
                
        # In Viatom's response, `data.records` usually holds the list
        page_records = res.get('data', {}).get('records', [])
        if not page_records:
            break
            
        print(f"Found {len(page_records)} sessions on page {page}.")
        all_raw_records.extend(page_records)
        
        if len(page_records) < size:
            break
            
        page += 1

    print(f"Found a total of {len(all_raw_records)} sessions.")
    
    # Deduplicate records by measureTime to prevent renaming loops (API can return duplicates)
    records = []
    seen = {}
    for r in all_raw_records:
        t = r.get('measureTime')
        if t not in seen:
            seen[t] = len(records)
            records.append(r)
        else:
            old_idx = seen[t]
            old_r = records[old_idx]
            # Replace if new one has a remark or flag but old doesn't
            if (r.get('remark') and not old_r.get('remark')) or (r.get('isStar') and not old_r.get('isStar')):
                records[old_idx] = r

    for idx, record in enumerate(records, 1):
        file_url = record.get('originalFileUrl')
        record_id = record.get('id')
        mac_sn = record.get('deviceName') or record.get('deviceSn') or record.get('deviceMacAddress') or "UNKNOWN"
        start_time_ms = record.get('measureTime') # e.g. "2026-02-18 13:53:26"
        remark = record.get('remark') or ""
        duration_sec = record.get('measureDuration') or 0
        # The API might use isStar or something else, but if it's there we capture it
        is_star = record.get('isStar') or record.get('star') # boolean or int like 1/0
            
        if not file_url:
            print(f"[{idx}] Skipping ID {record_id} - No fileUrl present.")
            continue
            
        # Parse timestamp into a human readable string for filename
        dt_str = "UnknownTime"
        dt = None
        if start_time_ms:
            try:
                # If it's a timestamp
                if isinstance(start_time_ms, (int, float)):
                    dt = datetime.fromtimestamp(start_time_ms / 1000.0)
                    dt_str = dt.strftime("%Y%m%d%H%M%S") #important: no _ in between the date and time because Oscar wont recognize it otherwise.
                # If it's already a formatted string from the API (e.g. "2026-02-18 13:53:26")
                elif isinstance(start_time_ms, str):
                    dt = datetime.strptime(start_time_ms, "%Y-%m-%d %H:%M:%S")
                    dt_str = dt.strftime("%Y%m%d%H%M%S")
            except Exception as e:
                print(f"   -> Could not parse time {start_time_ms}: {e}")
                
        if str(record_id) in ignored_ids or dt_str in ignored_ids:
            ignore_match = str(record_id) if str(record_id) in ignored_ids else dt_str
            print(f"[{idx}/{len(records)}] WARNING: Session {ignore_match} is in ignored_sessions.txt. Skipping download.")
            continue
            
        # Construct Filename: "{start_time}[_FLAGGED][_REMARK].bin"
        flag_str = "_FLAGGED" if is_star else ""
        remark_str = f"_{sanitize_filename(remark)}" if remark else ""
        
        # Original filename suffix (usually .bin or .csv)
        ext = ".bin"
        if file_url.endswith('.csv'):
            ext = ".csv"
        elif file_url.endswith('.dat'):
            ext = ".dat"
        else:
             parts = file_url.split('.')
             if len(parts) > 1:
                 ext = '.' + parts[-1].split('?')[0] # remove query params if any
        
        filename = f"{dt_str}{flag_str}{remark_str}{ext}"
        local_path = os.path.join(output_dir, filename)
        
        if filename in merged_fragments:
            print(f"[{idx}/{len(records)}] Skipping {filename} - already merged into a unified file.")
            continue
        
        # Helper to update timestamp
        def update_file_time(path):
            try:
                if dt:
                    ts = dt.timestamp()
                    os.utime(path, (ts, ts))
            except Exception:
                pass

        # Check if exact file already exists
        if os.path.exists(local_path) or os.path.exists(local_path + ".merged"):
            print(f"[{idx}/{len(records)}] Skipping {filename} - already exists locally.")
            update_file_time(local_path)
            continue
            
        # Check if file with same timestamp exists (indicating a potential local label or remote remark/flag change)
        existing_files = [f for f in os.listdir(output_dir) if f.startswith(dt_str) and f.endswith(ext)]
        if existing_files:
            existing_file = existing_files[0]
            existing_label = existing_file[len(dt_str):-len(ext)]
            new_label = f"{flag_str}{remark_str}"
            
            # Conflict resolution: combine existing label with new comment part (if not already present)
            combined_label = existing_label
            if new_label and new_label not in existing_label:
                combined_label += new_label
                
            final_filename = f"{dt_str}{combined_label}{ext}"
            final_local_path = os.path.join(output_dir, final_filename)
            
            if existing_file == final_filename:
                # The local file already includes all labels
                continue
                
            print(f"[{idx}/{len(records)}] Label conflict/update. Renaming '{existing_file}' to '{final_filename}'...")
            try:
                os.replace(os.path.join(output_dir, existing_file), final_local_path)
                update_file_time(final_local_path)
            except Exception as e:
                print(f"[{idx}/{len(records)}] Failed to rename: {e}. Downloading instead...")
                filename = final_filename
                local_path = final_local_path
            else:
                continue
            
        print(f"[{idx}/{len(records)}] Downloading {filename} ...")
        try:
            r = requests.get(file_url, stream=True)
            if r.status_code == 200:
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        
                # Update file modification and access time to the session start time
                try:
                    if dt:
                        ts = dt.timestamp()
                        os.utime(local_path, (ts, ts))
                except Exception as utime_e:
                    print(f"   -> Could not update file timestamp: {utime_e}")
                    
                print(f"   -> Saved successfully.")
            else:
                print(f"   -> HTTP {r.status_code} Error downloading file.")
        except Exception as e:
            print(f"   -> Exception during download: {e}")

    # After all downloads, attempt to merge split DAT files if utils are available
    if vsu:
        vsu.merge_dat_files_in_directory(output_dir)

    # Generate CSVs if requested
    if should_generate_csv:
        print("\nTriggering CSV generation...")
        try:
            # Use the same python executable to run viatom_to_csv.py
            cmd = [sys.executable, os.path.join(SCRIPT_DIR, "viatom_to_csv.py"), output_dir]
            # Run and show output
            result = subprocess.run(cmd, capture_output=False)
            if result.returncode == 0:
                print("CSV generation completed successfully.")
            else:
                print(f"CSV generation failed with return code {result.returncode}")
        except Exception as e:
            print(f"Error running CSV generation: {e}")

    # Generate HR spike HTML report if requested
    if should_run_analysis:
        print("\nTriggering HR Spike Analysis report...")
        try:
            cmd = [sys.executable, os.path.join(SCRIPT_DIR, "analysis", "generate_html_report.py")]
            result = subprocess.run(cmd, capture_output=False)
            if result.returncode == 0:
                print("HR Spike Analysis completed successfully.")
            else:
                print(f"HR Spike Analysis failed with return code {result.returncode}")
        except Exception as e:
            print(f"Error running HR Spike Analysis: {e}")

    # Launch followup program if configured
    if launch_after:
        print(f"\nLaunching followup program: {launch_after}...")
        try:
            # We use Popen without wait() so the downloader can exit while the new app runs
            subprocess.Popen(launch_after, shell=True)
        except Exception as e:
            print(f"Error launching followup program: {e}")

if __name__ == "__main__":
    main()
