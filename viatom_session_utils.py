import struct
import os
import re
import configparser
from datetime import datetime, timedelta
from pathlib import Path

# Constants from viatom_to_csv
O2RINGS_MAX_SAMPLES = 35900
MERGE_GAP_THRESHOLD_S = 300  # 5 minutes
SKIP_EXTENSIONS = {'.csv', '.pdf', '.lnk', '.bak', '.csv~', '.txt', '.log'}

_FILENAME_TS_RE = re.compile(r'(\d{14})')
_FILENAME_TS_RE2 = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})$')
_FILENAME_PURE_DIGITS = re.compile(r'^(\d{14})$')

def get_config(config_path="o2_config.ini"):
    """Reads the shared configuration file and returns a dict of Settings."""
    config_parser = configparser.ConfigParser()
    settings = {
        'email': None,
        'password': None,
        'output_dir': 'data',
        'generate_csv': False,
        'skip_short_sessions_under_mins': 60,
        'launch_after': ''
    }
    
    if not os.path.exists(config_path) and os.path.exists('o2_config.sample.ini'):
        try:
            config_parser.read('o2_config.sample.ini', encoding='utf-8')
        except Exception:
            pass
            
    if os.path.exists(config_path):
        try:
            config_parser.read(config_path, encoding='utf-8')
        except Exception:
            pass
            
    if 'Settings' in config_parser:
        s = config_parser['Settings']
        settings['email'] = s.get('email', settings['email'])
        if settings['email'] == 'your_email@example.com':
            settings['email'] = None
            
        settings['password'] = s.get('password', settings['password'])
        if settings['password'] == 'your_password':
            settings['password'] = None
            
        settings['output_dir'] = s.get('output_dir', settings['output_dir'])
        settings['generate_csv'] = s.getboolean('generate_csv', fallback=settings['generate_csv'])
        settings['skip_short_sessions_under_mins'] = s.getint('skip_short_sessions_under_mins', fallback=settings['skip_short_sessions_under_mins'])
        settings['launch_after'] = s.get('launch_after', settings['launch_after']).strip('\'"')
        
    return settings

def parse_filename_timestamp(filename_stem: str):
    # Try finding YYYYMMDDHHmmss anywhere in the stem
    m = _FILENAME_TS_RE.search(filename_stem)
    if m:
        try: return datetime.strptime(m.group(1), '%Y%m%d%H%M%S')
        except ValueError: pass

    # Try finding YYYY-MM-DD HH:MM:SS
    m = _FILENAME_TS_RE2.search(filename_stem)
    if m:
        try: return datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
        except ValueError: pass

    m = _FILENAME_PURE_DIGITS.match(filename_stem)
    if m:
        try: return datetime.strptime(m.group(1), '%Y%m%d%H%M%S')
        except ValueError: pass

    return None

def detect_format(filepath: Path, data: bytes):
    if len(data) < 2: return None
    sig = data[0] | (data[1] << 8)
    if sig == 0x0301 and len(data) > 46: return 'o2rings'
    if sig in (0x0003, 0x0005, 0x0006) and len(data) >= 40: return 'viatom'
    parent = filepath.parent.as_posix()
    if parent.endswith('/28/host'): return 'pod2'
    return None

def parse_o2rings(filepath: Path, data: bytes):
    file_size = len(data)
    if file_size < 46: return None

    rc_offset = file_size - 36
    record_count = struct.unpack_from('<H', data, rc_offset)[0]
    
    # Handle files > 18.2 hours where record_count overflows
    actual_records = (file_size - 46) // 3
    if actual_records > 65535 and record_count == actual_records % 65536:
        record_count = actual_records
    elif actual_records < 65535 and actual_records > record_count:
        # If the file was truncated, actual is smaller, or if just safe, we can use actual
        record_count = actual_records

    ts = parse_filename_timestamp(filepath.stem)
    if ts is None: return None

    HEADER_SIZE = 10
    RECORD_SIZE = 3
    records = []
    offset = HEADER_SIZE

    for i in range(record_count):
        if offset + RECORD_SIZE > rc_offset: break
        spo2 = data[offset]
        hr   = data[offset + 1]
        motion = data[offset + 2]

        oximetry_invalid = (spo2 == 0xFF or hr == 0xFF)
        vibration = 0

        t = ts + timedelta(seconds=i)
        records.append({
            'time': t,
            'spo2': None if oximetry_invalid else spo2,
            'hr':   None if oximetry_invalid else hr,
            'motion': motion,
            'vibration': vibration,
            'oximetry_invalid': oximetry_invalid,
        })
        offset += RECORD_SIZE

    return records, 1

def parse_viatom(filepath: Path, data: bytes):
    HEADER_SIZE = 40
    RECORD_SIZE = 5
    if len(data) < HEADER_SIZE: return None

    header = data[:HEADER_SIZE]
    sig   = header[0] | (header[1] << 8)
    year  = header[2] | (header[3] << 8)
    month = header[4]
    day   = header[5]
    hour  = header[6]
    minute = header[7]
    sec   = header[8]

    if sig == 0x0006: sig = 0x0005

    if not (2015 <= year <= 2059 and 1 <= month <= 12 and 1 <= day <= 31 and
            hour <= 23 and minute <= 59 and sec <= 59):
        return None

    data_timestamp = datetime(year, month, day, hour, minute, sec)
    filename_ts = parse_filename_timestamp(filepath.stem)
    if filename_ts is not None and filename_ts != data_timestamp:
        data_timestamp = filename_ts

    duration = header[13] | (header[14] << 8)
    datasize = len(data) - HEADER_SIZE
    record_count = datasize // RECORD_SIZE
    if record_count == 0: return None

    resolution_s = duration / record_count

    raw_records = []
    offset = HEADER_SIZE
    for i in range(record_count):
        spo2 = data[offset]
        hr   = data[offset + 1]
        oximetry_invalid = data[offset + 2]
        motion = data[offset + 3]
        vibration = data[offset + 4]
        raw_records.append((spo2, hr, oximetry_invalid, motion, vibration))
        offset += RECORD_SIZE

    int_resolution_ms = int(resolution_s * 1000)
    if int_resolution_ms == 2000 and sig == 3:
        all_dup = True
        if len(raw_records) % 2 != 0:
            all_dup = False
        else:
            dedup = []
            for j in range(0, len(raw_records), 2):
                a = raw_records[j]
                b = raw_records[j + 1]
                if a != b:
                    all_dup = False
                    break
                dedup.append(a)
            if all_dup:
                raw_records = dedup

    actual_resolution_s = duration / len(raw_records)

    records = []
    for i, (spo2, hr, oxy_inv, motion, vibration) in enumerate(raw_records):
        t = data_timestamp + timedelta(seconds=i * actual_resolution_s)
        is_invalid = (oxy_inv == 0xFF)

        records.append({
            'time': t,
            'spo2': None if is_invalid else (None if spo2 == 0xFF else spo2),
            'hr':   None if is_invalid else (None if hr == 0xFF else hr),
            'motion': motion,
            'vibration': vibration,
            'oximetry_invalid': is_invalid,
        })

    return records, actual_resolution_s

def parse_pod2(filepath: Path, data: bytes):
    RECORD_SIZE = 6
    try:
        epoch_ms = int(filepath.stem)
        ts = datetime.fromtimestamp(epoch_ms / 1000.0)
    except (ValueError, OSError):
        return None

    record_count = len(data) // RECORD_SIZE
    if record_count == 0: return None

    records = []
    offset = 0
    for i in range(record_count):
        spo2    = data[offset]
        hr      = data[offset + 1]
        unk1    = data[offset + 2]
        pi      = data[offset + 3]
        unk2    = data[offset + 4]
        battery = data[offset + 5]

        t = ts + timedelta(seconds=i)
        spo2_val = None if (spo2 == 0 or spo2 == 0xFF) else spo2
        hr_val   = None if (hr == 0) else hr
        pi_val   = pi / 10.0
        battery_level = (battery & 0xC0) >> 6

        records.append({
            'time': t,
            'spo2': spo2_val,
            'hr':   hr_val,
            'pi':   pi_val,
            'battery_level': battery_level,
            'motion': None,
            'vibration': None,
            'oximetry_invalid': (spo2 == 0 and hr == 0),
        })
        offset += RECORD_SIZE

    return records, 1

def parse_file(filepath: Path):
    if filepath.suffix.lower() in SKIP_EXTENSIONS: return None
    if filepath.is_dir(): return None

    try:
        data = filepath.read_bytes()
    except Exception as e:
        print(f"  [ERROR] Cannot read {filepath.name}: {e}")
        return None

    if len(data) < 10: return None

    fmt = detect_format(filepath, data)
    if fmt is None:
        stem = filepath.stem
        if re.match(r'^\d{14}$', stem) and len(data) > 40:
            sig = data[0] | (data[1] << 8)
            if sig not in (0x0003, 0x0005, 0x0006, 0x0301): return None
        else:
            return None
        fmt = detect_format(filepath, data)
        if fmt is None: return None

    try:
        if fmt == 'o2rings': result = parse_o2rings(filepath, data)
        elif fmt == 'viatom': result = parse_viatom(filepath, data)
        elif fmt == 'pod2': result = parse_pod2(filepath, data)
        else: return None
    except Exception as e:
        print(f"  [ERROR] Failed to parse {filepath.name} as {fmt}: {e}")
        return None

    if result is None: return None
    records, resolution_s = result
    if not records: return None

    return records, resolution_s, fmt

def is_max_session(records, fmt):
    if fmt == 'o2rings':
        return len(records) >= O2RINGS_MAX_SAMPLES
    return False

def sessions_should_merge(prev_records, prev_res, prev_fmt, next_records, next_fmt):
    if prev_fmt != next_fmt: return False
    if not is_max_session(prev_records, prev_fmt): return False

    prev_end = prev_records[-1]['time'] + timedelta(seconds=prev_res)
    next_start = next_records[0]['time']
    gap = (next_start - prev_end).total_seconds()

    return gap <= MERGE_GAP_THRESHOLD_S

def group_sessions_for_merging(parsed_sessions):
    """
    Takes a list of parsed sessions (filepath, records, resolution_s, fmt)
    Returns a list of groups (lists of sessions).
    """
    if not parsed_sessions:
        return []

    # Sort by start time just in case
    parsed_sessions.sort(key=lambda x: x[1][0]['time'])

    groups = []
    current_group = [parsed_sessions[0]]

    for i in range(1, len(parsed_sessions)):
        prev_fp, prev_records, prev_res, prev_fmt = current_group[-1]
        curr_fp, curr_records, curr_res, curr_fmt = parsed_sessions[i]

        if sessions_should_merge(prev_records, prev_res, prev_fmt, curr_records, curr_fmt):
            current_group.append(parsed_sessions[i])
        else:
            groups.append(current_group)
            current_group = [parsed_sessions[i]]
    groups.append(current_group)

    return groups

def merge_records_with_interpolation(group):
    """
    Takes a single group of parsed standard sessions.
    Returns: (all_records, interpolated_count, shifted_count)
    """
    first_fp, first_records, first_res, first_fmt = group[0]
    all_records = []
    interpolated_total = 0
    shifted_total = 0

    for seg_idx, (fp, records, res, f) in enumerate(group):
        if seg_idx > 0 and all_records:
            prev_rec = all_records[-1]
            next_rec_start = records[0]['time']
            prev_end = prev_rec['time'] + timedelta(seconds=int(res))
            gap_seconds = (next_rec_start - prev_end).total_seconds()

            FORCE_CONTINUITY_THRESHOLD = 300

            if gap_seconds < 0 or gap_seconds > FORCE_CONTINUITY_THRESHOLD:
                time_shift = prev_end - next_rec_start
                for r in records:
                    r['time'] += time_shift
                shifted_total += 1
            elif gap_seconds > 0:
                gap_int = int(gap_seconds)
                s1, s2 = prev_rec['spo2'], records[0]['spo2']
                h1, h2 = prev_rec['hr'], records[0]['hr']
                total_steps = gap_int + 1

                for g in range(gap_int):
                    frac = (g + 1) / total_steps
                    if s1 is not None and s2 is not None:
                        interp_spo2 = round(s1 + (s2 - s1) * frac)
                    else:
                        interp_spo2 = s1 if s2 is None else s2

                    if h1 is not None and h2 is not None:
                        interp_hr = round(h1 + (h2 - h1) * frac)
                    else:
                        interp_hr = h1 if h2 is None else h2

                    all_records.append({
                        'time': prev_end + timedelta(seconds=g),
                        'spo2': interp_spo2,
                        'hr': interp_hr,
                        'motion': 0,
                        'vibration': 0,
                        'oximetry_invalid': False,
                    })
                interpolated_total += gap_int

        all_records.extend(records)

    return all_records, interpolated_total, shifted_total

def generate_filename(original_stem, start_time, duration_seconds, ext=".csv"):
    hour = int(start_time.strftime('%I'))
    minute = start_time.strftime('%M')
    ampm = start_time.strftime('%p').lower()
    
    human_time = f"{hour}{minute}{ampm}"
    
    total_minutes = int(duration_seconds / 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    
    duration_str = f"{hours}h_{minutes}m"
    
    return f"{original_stem}_{human_time}_{duration_str}{ext}"

def set_file_timestamps(path: Path, dt: datetime):
    timestamp = dt.timestamp()
    try:
        os.utime(path, (timestamp, timestamp))
    except Exception as e:
        pass

    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import byref
            wintime = int(timestamp * 10000000) + 116444736000000000
            
            GENERIC_WRITE = 0x40000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80

            handle = ctypes.windll.kernel32.CreateFileW(
                str(path), 256, FILE_SHARE_READ | FILE_SHARE_WRITE, 
                None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
            )

            if handle != -1:
                ctime = ctypes.c_longlong(wintime)
                ctypes.windll.kernel32.SetFileTime(handle, byref(ctime), None, None)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

def build_o2rings_bytes(records, original_header, original_trailer):
    """
    Reconstructs an O2Ring S format .dat file from the records list
    """
    data = bytearray()
    data.extend(original_header)
    
    for r in records:
        spo2 = 0xFF if r['spo2'] is None else int(r['spo2'])
        hr = 0xFF if r['hr'] is None else int(r['hr'])
        motion = int(r['motion']) if r['motion'] is not None else 0
        if r['oximetry_invalid']:
            spo2 = 0xFF
            hr = 0xFF
        data.extend(bytes([spo2, hr, motion]))
        
    trailer = bytearray(original_trailer)
    count = len(records)
    struct.pack_into('<H', trailer, 0, count % 65536)
    data.extend(trailer)
    return bytes(data)

def combine_and_save_dat_files(group, output_dir: Path):
    """
    Takes a group of parsed files to merge and outputs a physically merged .dat
    Returns the Path to the new file, or None if failed.
    """
    if len(group) <= 1:
        return None
        
    first_fp, _, first_res, first_fmt = group[0]
    if first_fmt != 'o2rings':
        print(f"  [WARN] Native merge not supported for format {first_fmt}")
        return None
        
    # Interpolate records across the group
    all_records, _, _ = merge_records_with_interpolation(group)
    
    # Needs the raw header and trailer.
    try:
        data = first_fp.read_bytes()
        original_header = data[:10]
        
        last_fp = group[-1][0]
        last_data = last_fp.read_bytes()
        original_trailer = last_data[-36:]
    except Exception as e:
        print(f"  [ERROR] Failed to read raw bytes for native merge: {e}")
        return None
        
    merged_bytes = build_o2rings_bytes(all_records, original_header, original_trailer)
    
    duration_s = len(all_records) * first_res
    
    # Just merge into a single "first" timestamp name without the human readable duration stuff for the base dat file,
    # because the base dat name needs to look like standard viatom format to not break CSV generator later,
    # OR we can append _merged to differentiate. But we want o2_downloader to track it nicely.
    # Actually wait: The `o2_downloader` downloads it with `dt_str_FLAGGED_REMARK.dat`. 
    # Let's keep the original stem but append '_merged.dat' so user knows it's the combined one.
    
    out_name = f"{first_fp.stem}_merged.dat"
    out_path = output_dir / out_name
    
    out_path.write_bytes(merged_bytes)
    set_file_timestamps(out_path, all_records[0]['time'])
    
    return out_path

def load_merged_fragments(output_dir):
    log_file = os.path.join(output_dir, "merged_fragments.log")
    if not os.path.exists(log_file):
        return set()
    with open(log_file, 'r') as f:
        return set(line.strip() for line in f if line.strip())

def log_merged_fragment(output_dir, filename):
    log_file = os.path.join(output_dir, "merged_fragments.log")
    with open(log_file, 'a') as f:
        f.write(f"{filename}\n")

def merge_dat_files_in_directory(output_dir):
    """
    Scans output_dir for split .dat files, groups them, merges them into 
    single _merged.dat files, and deletes the original fragments.
    """
    data_dir = Path(output_dir)
    dat_files = sorted([f for f in data_dir.glob("*.dat") if not f.stem.endswith('_merged')])
    
    parsed = []
    for f in dat_files:
        res = parse_file(f)
        if res:
            parsed.append((f, res[0], res[1], res[2]))
            
    groups = group_sessions_for_merging(parsed)
    merged_count = 0
    for group in groups:
        if len(group) > 1:
            print(f"Merging split sessions: {[x[0].name for x in group]} ...")
            out_path = combine_and_save_dat_files(group, data_dir)
            if out_path:
                print(f"  -> Successfully generated {out_path.name}")
                # Delete the original fragments so only the single merged file is kept
                for fp, _, _, _ in group:
                    try:
                        fp.unlink()
                        log_merged_fragment(output_dir, fp.name)
                    except Exception as e:
                        print(f"  -> Warning: could not delete {fp.name}: {e}")
                merged_count += 1
    
    if merged_count > 0:
        print(f"Merged {merged_count} groups of sub-sessions into unified .dat files.")
