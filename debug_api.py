import configparser
import json
import os
import sys
from o2_downloader import ViatomClient, SECRET

def main():
    config = configparser.ConfigParser()
    if not os.path.exists('o2_config.ini'):
        print("o2_config.ini not found.")
        return
        
    config.read('o2_config.ini')
    email = config['Settings']['email']
    password = config['Settings']['password']
    
    client = ViatomClient(SECRET)
    if not client.login(email, password):
        print("Login failed")
        return
        
    print("Fetching page 1...")
    res = client.get_oxygen_list(page=1, size=50)
    records = res.get('data', {}).get('records', [])
    
    print("Fetching page 2...")
    res2 = client.get_oxygen_list(page=2, size=50)
    records.extend(res2.get('data', {}).get('records', []))
    
    target = '2026-01-24 11:45:38'
    if len(sys.argv) > 1:
        target = sys.argv[1]
    
    print(f"\n--- Records matching {target} ---")
    matches = [r for r in records if r.get('measureTime') == target]
    
    for idx, r in enumerate(matches):
        print(f"\nRecord #{idx+1}:")
        print(json.dumps(r, indent=2))
        
    print(f"\nTotal matches: {len(matches)}")
    print("\nTip: You can pass a specific 'YYYY-MM-DD HH:MM:SS' time as an argument to this script to search for other duplicates.")

if __name__ == '__main__':
    main()
