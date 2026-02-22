import os
import glob
import re
import datetime
import shutil
import pandas as pd

from run_detector_batch import CSV_DIR

def merge_sessions():
    print(f"Scanning for split sessions in {CSV_DIR}...")
    
    csv_files = glob.glob(os.path.join(CSV_DIR, "*.csv"))
    groups = {}
    
    for f in csv_files:
        fname = os.path.basename(f)
        
        # Skip already merged files (optional, but good practice)
        if "_merged" in fname:
            continue
            
        m = re.match(r'^(\d{14})_', fname)
        if m:
            try:
                dt = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
                # Sleep Day starts at 4 PM, so shift back 16 hours
                sleep_date = (dt - datetime.timedelta(hours=16)).date()
                date_str = sleep_date.strftime("%Y-%m-%d")
                
                if date_str not in groups:
                    groups[date_str] = []
                groups[date_str].append((dt, f))
            except Exception as e:
                print(f"Could not parse date from {fname}: {e}")
                
    for k, v in groups.items():
        print(f"Date {k} has {len(v)} files")

    merged_count = 0
    
    for date_str, files in groups.items():
        if len(files) > 1:
            print(f"\nFound {len(files)} files for Sleep Day {date_str}:")
            # Sort chronologically
            files.sort(key=lambda x: x[0])
            for _, f in files:
                print(f"  - {os.path.basename(f)}")
                
            try:
                dfs = []
                for dt, f in files:
                    df = pd.read_csv(f, skipinitialspace=True)
                    # Use exact column names
                    time_col = [c for c in df.columns if 'time' in c.lower()][0]
                    df[time_col] = pd.to_datetime(df[time_col])
                    df.set_index(time_col, inplace=True)
                    dfs.append(df)
                    
                master_df = pd.concat(dfs)
                master_df = master_df[~master_df.index.duplicated(keep='first')]
                master_df.sort_index(inplace=True)
                
                # Resample to 1-second intervals from absolute min to max
                full_idx = pd.date_range(start=master_df.index.min(), end=master_df.index.max(), freq='S')
                master_df = master_df.reindex(full_idx)
                
                # Invalid rows and missing data should become NaN here
                # Our detector's preprocess function converts NaN to "Invalid" automatically
                
                master_df.reset_index(inplace=True)
                master_df.rename(columns={'index': 'Time'}, inplace=True)
                master_df['Time'] = master_df['Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
                
                # Determine total exact duration
                duration_secs = master_df.shape[0]
                hrs = duration_secs // 3600
                mins = (duration_secs % 3600) // 60
                
                start_dt = files[0][0]
                time_am_pm = start_dt.strftime("%I%M%p").lstrip("0").lower()
                
                new_basename = f"{start_dt.strftime('%Y%m%d%H%M%S')}_{time_am_pm}_{hrs}h_{mins}m_merged.csv"
                new_fpath = os.path.join(CSV_DIR, new_basename)
                
                print(f" -> Saving combined valid/nan timeline to: {new_basename}")
                master_df.to_csv(new_fpath, index=False)
                
                archive_dir = os.path.join(CSV_DIR, "archive")
                os.makedirs(archive_dir, exist_ok=True)
                
                for _, f in files:
                    dest = os.path.join(archive_dir, os.path.basename(f))
                    shutil.move(f, dest)
                    print(f" -> Archived native segment: {os.path.basename(f)}")
                    
                merged_count += 1
                
            except Exception as e:
                print(f"Failed to merge files for {date_str}: {e}")
                import traceback
                traceback.print_exc()

    if merged_count == 0:
        print("\nNo split sessions found to merge.")
    else:
        print(f"\nSuccessfully created {merged_count} merged sessions.")

if __name__ == "__main__":
    merge_sessions()
