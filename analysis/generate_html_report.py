import sys
import traceback
import webbrowser
import re
import html

print("Script starting...", flush=True)

try:
    import os
    import glob
    import json
    import numpy as np
    from run_detector_batch import analyze_night, KNOWN_LABELS, CSV_DIR
    
    print(f"Imported run_detector_batch. CSV_DIR: {CSV_DIR}", flush=True)
except Exception as e:
    print(f"Failed to import dependencies: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)


def parse_label_parts(raw_label):
    """Split a filename-derived label into (notes, time_info) parts.
    The filename suffix pattern is: {optional_notes}_{HMMam/pm}_{Xh}_{Ym}
    Returns (notes_str, time_duration_str).
    If there are no notes, notes_str will be empty and time_duration_str holds the full label.
    """
    # Match trailing time + duration pattern: e.g. '642am 3h 12m' or '1244pm 10h 1m'
    m = re.match(r'^(.*?)(\d{1,4}[ap]m\s+\d+h\s+\d+m)\s*$', raw_label)
    if m:
        notes_part = m.group(1).strip().rstrip('_').strip()
        time_part = m.group(2).strip()
        return (notes_part, time_part)
    return ('', raw_label)


def parse_trim_directive(label_text):
    """Parse a label/notes string for 'slice after' time directives.
    Looks for patterns like:
      'slice everything after 8:18 am'
      'slice after 3:45am'
      'after 8 18 am'
    Returns a datetime.time object if found, otherwise None.
    """
    import datetime as dt_mod
    # Pattern 1: 'after H:MM am/pm' or 'after HH:MM am/pm'
    m = re.search(r'(?:slice\s+(?:everything\s+)?)?after\s+(\d{1,2})[:\s](\d{2})\s*(am|pm)', label_text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = m.group(3).lower()
        if ampm == 'pm' and hour != 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        return dt_mod.time(hour, minute)
    # Pattern 2: 'after HMMam/pm' or 'after HHMMam/pm' (no colon/space)
    m = re.search(r'(?:slice\s+(?:everything\s+)?)?after\s+(\d{1,2})(\d{2})\s*(am|pm)', label_text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = m.group(3).lower()
        if ampm == 'pm' and hour != 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        return dt_mod.time(hour, minute)
    return None

def generate_report():
    print("Collecting data...", flush=True)
    results = []
    
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
    cache_file = os.path.join(data_dir, 'detector_cache.json')
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            print(f"Loaded cache with {len(cache)} entries.", flush=True)
        except Exception as e:
            print(f"Failed to load cache: {e}", flush=True)
    
    search_path = os.path.join(CSV_DIR, "*.csv")
    csv_files = glob.glob(search_path)
    csv_files.sort(reverse=True)
    
    ignored_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ignored_sessions.txt")
    ignored_sessions = set()
    if os.path.exists(ignored_file):
        with open(ignored_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.split()
                if parts:
                    ignored_sessions.add(parts[0])

    print(f"Found {len(csv_files)} files.", flush=True)
    
    chart_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'charts'))
    
    cache_updated = False
    
    for fpath in csv_files:
        fname = os.path.basename(fpath)
        
        # Skip output/report files
        if "detector_results" in fname:
            continue
            
        # Skip ignored sessions
        if any(fname.startswith(ign) for ign in ignored_sessions):
            print(f"Skipping ignored session: {fname}", flush=True)
            continue
            
        label = KNOWN_LABELS.get(fname, fname)
        trim_after_time = None
        if label == fname:
            # Parse 20260217032620_326am_10h_23m.csv
            m = re.match(r'^(\d{4})(\d{2})(\d{2})\d{6}_(.*)\.csv', fname)
            if m:
                raw_label = m.group(4).replace('_', ' ')
                notes_part, time_part = parse_label_parts(raw_label)
                # Only show time+duration if there are no user notes
                if notes_part:
                    label = notes_part
                    # Check notes for trim directives
                    trim_after_time = parse_trim_directive(notes_part)
                else:
                    label = time_part
        else:
            # KNOWN_LABELS entry - also check for trim directives
            trim_after_time = parse_trim_directive(label)

        if trim_after_time:
            print(f"Trim directive found for {fname}: exclude data after {trim_after_time}", flush=True)

        if not os.path.exists(fpath): continue
        try:
            mtime = os.path.getmtime(fpath)
            chart_exists = os.path.exists(os.path.join(chart_dir, fname.replace('.csv', '_chart.html')))
            
            if fname in cache and cache[fname].get('mtime') == mtime and chart_exists:
                res = cache[fname]['res']
                res['label'] = label # dynamic label
                results.append(res)
                print(f"Loaded from cache: {fname}", flush=True)
            else:
                _, res = analyze_night(fpath, label, generate_chart=True, chart_dir=chart_dir, trim_after_time=trim_after_time)
                if res:
                    res['filename'] = fname
                    results.append(res)
                    cache[fname] = {'mtime': mtime, 'res': res}
                    cache_updated = True
                    print(f"Parsed {fname} -> Score: {res.get('score', 0)} | SI/hr: {res.get('si', 0)} | TAB: {res.get('tab', 0)} | Events: {res.get('events', 0)} | Hrs: {res.get('hours', 0)}", flush=True)
                else:
                    print(f"No results for {fname}", flush=True)
        except Exception as e:
            print(f"ERROR analyzing {fname}: {e}", flush=True)
            
    if cache_updated:
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            print(f"Failed to save cache: {e}", flush=True)
            
    if not results:
        print("No results found.", flush=True)
        return

    report_parts = []
    report_parts.append("""<html>
    <head>
        <meta charset="utf-8">
        <title>HR Spike Detector Results</title>
        <style>
            body { font-family: sans-serif; margin: 10px; background-color: #fff; color: #000; }
            table { border-collapse: collapse; width: 100%; font-size: 13px; }
            th, td { border: 1px solid #ddd; padding: 4px 6px; text-align: center; }
            th { background-color: #f2f2f2; position: sticky; top: 0; cursor: pointer; }
            tr:nth-child(even) { background-color: #f9f9f9; }
            tr:hover { background-color: #f1f1f1; }
            .left-align { text-align: left; }
            .mono { font-family: monospace; }
            .disabled-row { opacity: 0.3; }
            .selected-row { background-color: #dbeafe !important; box-shadow: inset 0 0 0 2px #3b82f6; }
            .editable-label { cursor: text; border-bottom: 1px dashed #ccc; min-width: 100px; display: inline-block; padding: 2px; }
            .editable-label:focus { outline: 1px solid #00f; background-color: #fff; }
        </style>
        <script src="https://www.kryogenix.org/code/browser/sorttable/sorttable.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script>
            function openChart(url, el) {
                let tr = el.closest('tr');
                let nextTr = tr.nextElementSibling;
                
                if (nextTr && nextTr.classList.contains('inline-chart-row')) {
                    nextTr.remove();
                    return;
                }
                
                let newTr = document.createElement('tr');
                newTr.classList.add('inline-chart-row');
                
                let td = document.createElement('td');
                td.colSpan = tr.children.length;
                td.style.padding = '0';
                
                let iframe = document.createElement('iframe');
                iframe.src = url;
                iframe.style.width = '100%';
                iframe.style.height = '410px';
                iframe.style.border = 'none';
                iframe.style.display = 'block';
                
                td.appendChild(iframe);
                newTr.appendChild(td);
                tr.parentNode.insertBefore(newTr, tr.nextSibling);
            }
            
            const metricsFields = ['tmb', 'score', 'score_standard', 'score_specific', 'score_clinical', 'si', 'prri_index', 'tab', 'tab_standard', 'tab_specific', 'tab_clinical', 'tab10', 'tab_major_a', 'p90', 'pc10', 'pc15', 'events_A_ph', 'events_B_ph', 'events_C_ph'];
            
            // Engine-switchable columns: these show data from whichever preset is selected
            const engineSwitchableCells = ['si', 'p90', 'pc10', 'pc15', 'tab10'];
            const engineNames = {sensitive: 'Sens', standard: 'Std', specific: 'Spc', clinical: 'Cln'};
            let currentEngine = 'standard';
            
            function updateEngine() {
                currentEngine = document.getElementById('engineSelector').value;
                let label = engineNames[currentEngine];
                // Update column headers
                engineSwitchableCells.forEach(cellId => {
                    let th = document.querySelector(`th[data-engine-col="${cellId}"]`);
                    if (th) th.textContent = th.dataset.baseLabel + ' (' + label + ')';
                });
                // Update all data cells
                document.querySelectorAll('tbody tr').forEach(row => {
                    engineSwitchableCells.forEach(cellId => {
                        let cell = row.querySelector(`.cell-${cellId}`);
                        if (cell) {
                            let val = cell.dataset['value' + currentEngine.charAt(0).toUpperCase() + currentEngine.slice(1)];
                            if (val !== undefined) {
                                cell.dataset.value = val;
                                cell.textContent = val;
                            }
                        }
                    });
                });
                updateColors();
                applyEngineColumnVisibility();
            }
            
            let hideUnused = true;

            function toggleUnusedEngines() {
                hideUnused = !hideUnused;
                localStorage.setItem('hrSpikeHideUnusedV2', hideUnused ? 'true' : 'false');
                applyEngineColumnVisibility();
            }

            function applyEngineColumnVisibility() {
                let btn = document.getElementById('toggleEnginesBtn');
                if (btn) {
                    btn.textContent = hideUnused ? 'Show Score/TAB from all engines' : 'Hide unused Score/TAB columns';
                    if (!hideUnused) {
                        btn.style.backgroundColor = '#e0f7fa';
                        btn.style.borderColor = '#00bcd4';
                        btn.style.fontWeight = 'bold';
                    } else {
                        btn.style.backgroundColor = '#f0f0f0';
                        btn.style.borderColor = '#ccc';
                        btn.style.fontWeight = 'normal';
                    }
                }

                const suffixes = {
                    'sensitive': '',
                    'standard': '_standard',
                    'specific': '_specific',
                    'clinical': '_clinical'
                };
                const bases = ['score', 'tab'];
                
                for (let eng in suffixes) {
                    let suffix = suffixes[eng];
                    let isCurrent = (eng === currentEngine);
                    let shouldShow = (!hideUnused) || isCurrent;
                    let displayStyle = shouldShow ? '' : 'none';
                    
                    bases.forEach(base => {
                        let colClass = base + suffix;
                        document.querySelectorAll(`th.col-${colClass}`).forEach(th => th.style.display = displayStyle);
                        document.querySelectorAll(`td.cell-${colClass}`).forEach(td => td.style.display = displayStyle);
                    });
                }
            }
            
            // Linear color scale (lower = better for ALL metrics)
            //ALWAYS use colorts for maximum data visuzlization and clarity.  Think edward tufte, not modern design colors. 
            // 7 bands: vivid green -> green -> yellow-green -> yellow -> orange -> red -> purple
            const colorStops = [
                [0.00, [0, 200, 0]],    // Vivid green (best / min)
                [0.10, [0, 255, 0]],   // Green
                [0.30, [180, 255, 60]],   // Yellow-green
                [0.50, [255, 235, 0]],   // Yellow (midpoint)
                [0.70, [255, 165, 0]],   // Orange
                [0.85, [255, 0, 0]],    // Red
                [0.90, [200, 0, 200]],  // purple (worst ~10%)
                [1.00, [160, 0, 200]]    // Deep purple (max)
            ];
            
            function getColor(value, min_val, max_val) {
                if (isNaN(value)) return "#ffffff";
                if (min_val === max_val) return "#ffffff";
                let norm = (value - min_val) / (max_val - min_val);
                norm = Math.max(0.0, Math.min(1.0, norm));
                
                // Find which two color stops we're between
                let lower = colorStops[0], upper = colorStops[colorStops.length - 1];
                for (let i = 0; i < colorStops.length - 1; i++) {
                    if (norm >= colorStops[i][0] && norm <= colorStops[i + 1][0]) {
                        lower = colorStops[i];
                        upper = colorStops[i + 1];
                        break;
                    }
                }
                
                // Linear interpolate between the two stops
                let range = upper[0] - lower[0];
                let t = range === 0 ? 0 : (norm - lower[0]) / range;
                let r = Math.round(lower[1][0] + t * (upper[1][0] - lower[1][0]));
                let g = Math.round(lower[1][1] + t * (upper[1][1] - lower[1][1]));
                let b = Math.round(lower[1][2] + t * (upper[1][2] - lower[1][2]));
                
                return `rgb(${r}, ${g}, ${b})`;
            }

            function updateColors() {
                let rows = document.querySelectorAll("tbody tr");
                let mins = {};
                let maxs = {};
                
                metricsFields.forEach(m => {
                    mins[m] = Infinity;
                    maxs[m] = -Infinity;
                });
                
                // First pass: find min/max ONLY for enabled, visible rows
                rows.forEach(row => {
                    if (row.style.display === 'none') return;
                    let cb = row.querySelector('.row-checkbox');
                    if (cb && cb.checked) {
                        metricsFields.forEach(m => {
                            let cell = row.querySelector(`.cell-${m}`);
                            if (cell) {
                                let val = parseFloat(cell.dataset.value);
                                if (!isNaN(val)) {
                                    if (val < mins[m]) mins[m] = val;
                                    if (val > maxs[m]) maxs[m] = val;
                                }
                            }
                        });
                    }
                });
                
                // Second pass: apply colors to ALL rows using the min/max from enabled rows
                rows.forEach(row => {
                    if (row.style.display === 'none') return;
                    let cb = row.querySelector('.row-checkbox');
                    let isChecked = cb && cb.checked;
                    
                    if (isChecked) {
                        row.classList.remove('disabled-row');
                    } else {
                        row.classList.add('disabled-row');
                    }
                    
                    metricsFields.forEach(m => {
                        let cell = row.querySelector(`.cell-${m}`);
                        if (cell) {
                            if (!isChecked) {
                                cell.style.backgroundColor = "";
                            } else {
                                let val = parseFloat(cell.dataset.value);
                                cell.style.backgroundColor = getColor(val, mins[m], maxs[m]);
                            }
                        }
                    });
                });
                
                // Keep the Chart updated
                if (typeof renderChart === 'function') {
                    renderChart();
                }
            }


            function saveData() {
                let rows = document.querySelectorAll("tbody tr");
                let data = {};
                let merges = [];
                
                // If we are in a temporary view (savedMergeState !== null), ONLY save the original manual merges
                if (typeof savedMergeState !== 'undefined' && savedMergeState !== null) {
                    savedMergeState.forEach(m => merges.push(m));
                }

                rows.forEach(row => {
                    let filename = row.dataset.filename;
                    
                    if (typeof savedMergeState === 'undefined' || savedMergeState === null) {
                        if (row.dataset.isMerged === "true" && row.style.display !== 'none') {
                            merges.push(filename.split(' + '));
                        }
                    }

                    let cb = row.querySelector('.row-checkbox');
                    let labelNode = row.querySelector('.editable-label');
                    if (filename && cb && labelNode) {
                        let entry = { checked: cb.checked };
                        if (labelNode.dataset.edited === "true") {
                            entry.label = labelNode.innerText;
                        }
                        data[filename] = entry;
                    }
                });
                
                localStorage.setItem('hrSpikeDataV2', JSON.stringify(data));
                localStorage.setItem('hrSpikeMergesV2', JSON.stringify(merges));
            }
            
            function loadData() {
                let savedMerges = localStorage.getItem('hrSpikeMergesV2');
                if (savedMerges) {
                    try {
                        let merges = JSON.parse(savedMerges);
                        merges.forEach(mergeObj => {
                            let rowsToMerge = [];
                            let allRows = Array.from(document.querySelectorAll("tbody tr"));
                            mergeObj.forEach(fname => {
                                let row = allRows.find(r => r.dataset.filename === fname);
                                if (row && row.style.display !== 'none') {
                                    rowsToMerge.push(row);
                                }
                            });
                            if (rowsToMerge.length === mergeObj.length) {
                                doMergeForRows(rowsToMerge, false);
                            }
                        });
                    } catch(e) {}
                }

                let saved = localStorage.getItem('hrSpikeDataV2');
                if (saved) {
                    try {
                        let data = JSON.parse(saved);
                        let rows = document.querySelectorAll("tbody tr");
                        rows.forEach(row => {
                            let filename = row.dataset.filename;
                            if (filename && data[filename]) {
                                let cb = row.querySelector('.row-checkbox');
                                let labelNode = row.querySelector('.editable-label');
                                if (data[filename].checked !== undefined) {
                                    cb.checked = data[filename].checked;
                                }
                                if (data[filename].label) {
                                    labelNode.innerText = data[filename].label;
                                    labelNode.dataset.edited = "true";
                                }
                            }
                        });
                    } catch(e) {}
                }
            }

            function mergeSelected() {
                let rows = Array.from(document.querySelectorAll("tbody tr")).filter(row => {
                    return row.classList.contains('selected-row') && row.style.display !== 'none';
                });

                if (rows.length < 2) {
                    alert("Please select at least 2 rows to merge using Ctrl+Click (or Cmd+Click).");
                    return;
                }

                doMergeForRows(rows, true);
            }

            function doMergeForRows(rows, saveAfter) {
                if (rows.length < 2) return;
                
                let totalHrs = 0, totalEvents = 0, totalEvents10 = 0, totalEvents15 = 0, totalPrriCount = 0;
                let totalMajorA = 0, totalMajorB = 0, totalMajorC = 0;
                let sumTab = 0, sumTabStandard = 0, sumTabSpecific = 0, sumTabClinical = 0, sumTab10 = 0, sumTmb = 0, sumTabMajorA = 0;
                let sumScore = 0, sumScoreStandard = 0, sumScoreSpecific = 0, sumScoreClinical = 0, sumP90 = 0;
                // Per-engine derived metric accumulators
                let sumSiByEngine = {sensitive:0, standard:0, specific:0, clinical:0};
                let sumP90ByEngine = {sensitive:0, standard:0, specific:0, clinical:0};
                let sumEvtsByEngine = {sensitive:0, standard:0, specific:0, clinical:0};
                let sumPc10ByEngine = {sensitive:0, standard:0, specific:0, clinical:0};
                let sumPc15ByEngine = {sensitive:0, standard:0, specific:0, clinical:0};
                let sumTab10ByEngine = {sensitive:0, standard:0, specific:0, clinical:0};
                let filenames = [], labels = [];

                rows.forEach(row => {
                    let hrs = parseFloat(row.cells[3].dataset.sort) || 0;
                    totalHrs += hrs;
                    
                    let evts = (parseFloat(row.querySelector('.cell-si')?.dataset?.value) || 0) * hrs;
                    totalEvents += evts;
                    
                    totalEvents10 += (parseFloat(row.querySelector('.cell-pc10')?.dataset?.value) || 0) * hrs;
                    totalEvents15 += (parseFloat(row.querySelector('.cell-pc15')?.dataset?.value) || 0) * hrs;
                    
                    // Accumulate per-engine derived metrics
                    ['sensitive','standard','specific','clinical'].forEach(eng => {
                        let suffix = eng.charAt(0).toUpperCase() + eng.slice(1);
                        let siCell = row.querySelector('.cell-si');
                        let siVal = parseFloat(siCell?.dataset?.['value'+suffix]) || 0;
                        sumSiByEngine[eng] += siVal * hrs;
                        let p90Cell = row.querySelector('.cell-p90');
                        let p90Val = parseFloat(p90Cell?.dataset?.['value'+suffix]) || 0;
                        let p90Evts = siVal * hrs;
                        sumP90ByEngine[eng] += p90Val * p90Evts;
                        sumEvtsByEngine[eng] += p90Evts;
                        let pc10Cell = row.querySelector('.cell-pc10');
                        sumPc10ByEngine[eng] += (parseFloat(pc10Cell?.dataset?.['value'+suffix]) || 0) * hrs;
                        let pc15Cell = row.querySelector('.cell-pc15');
                        sumPc15ByEngine[eng] += (parseFloat(pc15Cell?.dataset?.['value'+suffix]) || 0) * hrs;
                        let tab10Cell = row.querySelector('.cell-tab10');
                        sumTab10ByEngine[eng] += (parseFloat(tab10Cell?.dataset?.['value'+suffix]) || 0) * hrs;
                    });
                    
                    totalMajorA += (parseFloat(row.querySelector('.cell-events_A_ph')?.dataset?.value) || 0) * hrs;
                    totalMajorB += (parseFloat(row.querySelector('.cell-events_B_ph')?.dataset?.value) || 0) * hrs;
                    totalMajorC += (parseFloat(row.querySelector('.cell-events_C_ph')?.dataset?.value) || 0) * hrs;
                    
                    sumTab += (parseFloat(row.querySelector('.cell-tab')?.dataset?.value) || 0) * hrs;
                    sumTabStandard += (parseFloat(row.querySelector('.cell-tab_standard')?.dataset?.value) || 0) * hrs;
                    sumTabSpecific += (parseFloat(row.querySelector('.cell-tab_specific')?.dataset?.value) || 0) * hrs;
                    sumTabClinical += (parseFloat(row.querySelector('.cell-tab_clinical')?.dataset?.value) || 0) * hrs;
                    sumTab10 += (parseFloat(row.querySelector('.cell-tab10')?.dataset?.value) || 0) * hrs;
                    sumTmb += (parseFloat(row.querySelector('.cell-tmb')?.dataset?.value) || 0) * hrs;
                    sumTabMajorA += (parseFloat(row.querySelector('.cell-tab_major_a')?.dataset?.value) || 0) * hrs;
                    sumScore += (parseFloat(row.querySelector('.cell-score')?.dataset?.value) || 0) * hrs;
                    sumScoreStandard += (parseFloat(row.querySelector('.cell-score_standard')?.dataset?.value) || 0) * hrs;
                    sumScoreSpecific += (parseFloat(row.querySelector('.cell-score_specific')?.dataset?.value) || 0) * hrs;
                    sumScoreClinical += (parseFloat(row.querySelector('.cell-score_clinical')?.dataset?.value) || 0) * hrs;
                    
                    sumP90 += (parseFloat(row.querySelector('.cell-p90')?.dataset?.value) || 0) * evts;
                    totalPrriCount += (parseFloat(row.querySelector('.cell-prri_index')?.dataset?.value) || 0) * hrs;
                    
                    filenames.push(row.cells[row.cells.length - 1]?.innerText?.trim() || '');
                    let labelEl = row.querySelector('.editable-label');
                    labels.push(labelEl ? labelEl.innerText : '');

                    // Unselect and hide
                    row.classList.remove('selected-row');
                    row.style.display = 'none';
                });

                if (totalHrs === 0) return;

                let newTab = sumTab / totalHrs;
                let newTabStandard = sumTabStandard / totalHrs;
                let newTabSpecific = sumTabSpecific / totalHrs;
                let newTabClinical = sumTabClinical / totalHrs;
                let newTab10 = sumTab10 / totalHrs;
                let newTmb = sumTmb / totalHrs;
                let newTabMajorA = sumTabMajorA / totalHrs;
                let newScore = sumScore / totalHrs;
                let newScoreStandard = sumScoreStandard / totalHrs;
                let newScoreSpecific = sumScoreSpecific / totalHrs;
                let newScoreClinical = sumScoreClinical / totalHrs;
                let newP90 = totalEvents > 0 ? sumP90 / totalEvents : 0;
                
                let newSi = totalEvents / totalHrs;
                let newPrri = totalPrriCount / totalHrs;
                let newPc10ph = totalEvents10 / totalHrs;
                let newPc15ph = totalEvents15 / totalHrs;
                
                let newMajorAph = totalMajorA / totalHrs;
                let newMajorBph = totalMajorB / totalHrs;
                let newMajorCph = totalMajorC / totalHrs;
                
                // Per-engine derived values
                let engSi = {}, engP90 = {}, engPc10 = {}, engPc15 = {}, engTab10 = {};
                ['sensitive','standard','specific','clinical'].forEach(eng => {
                    engSi[eng] = (sumSiByEngine[eng] / totalHrs).toFixed(1);
                    engP90[eng] = sumEvtsByEngine[eng] > 0 ? (sumP90ByEngine[eng] / sumEvtsByEngine[eng]).toFixed(1) : '0.0';
                    engPc10[eng] = (sumPc10ByEngine[eng] / totalHrs).toFixed(1);
                    engPc15[eng] = (sumPc15ByEngine[eng] / totalHrs).toFixed(1);
                    engTab10[eng] = (sumTab10ByEngine[eng] / totalHrs).toFixed(1);
                });

                let h = Math.floor(totalHrs);
                let m = Math.round((totalHrs - h) * 60);
                if (m === 60) { h++; m=0; }
                let hrStr = `${h}h ${m.toString().padStart(2, '0')}m`;

                let earliestRow = rows[rows.length - 1];
                let mergedDate = earliestRow ? earliestRow.cells[1].innerHTML : "Merged Date";
                
                filenames.reverse();
                labels.reverse();

                let tr = document.createElement('tr');
                tr.dataset.filename = filenames.join(' + ');
                tr.dataset.isMerged = "true";

                tr.innerHTML = `
                    <td><input type="checkbox" class="row-checkbox" checked></td>
                    <td class="left-align" style="white-space: nowrap;">${mergedDate}</td>
                    <td class="left-align"><span class="editable-label" contenteditable="true">Merged: ${labels.join(' + ')}</span></td>
                    <td data-sort="${totalHrs}">${hrStr}</td>
                    <td class="cell-tmb" data-value="${newTmb.toFixed(1)}">${newTmb.toFixed(1)}</td>
                    <td class="cell-score" data-value="${newScore.toFixed(1)}">${newScore.toFixed(1)}</td>
                    <td class="cell-score_standard" data-value="${newScoreStandard.toFixed(1)}">${newScoreStandard.toFixed(1)}</td>
                    <td class="cell-score_specific" data-value="${newScoreSpecific.toFixed(1)}">${newScoreSpecific.toFixed(1)}</td>
                    <td class="cell-score_clinical" data-value="${newScoreClinical.toFixed(1)}">${newScoreClinical.toFixed(1)}</td>
                    <td class="cell-tab" data-value="${newTab.toFixed(1)}">${newTab.toFixed(1)}</td>
                    <td class="cell-tab_standard" data-value="${newTabStandard.toFixed(1)}">${newTabStandard.toFixed(1)}</td>
                    <td class="cell-tab_specific" data-value="${newTabSpecific.toFixed(1)}">${newTabSpecific.toFixed(1)}</td>
                    <td class="cell-tab_clinical" data-value="${newTabClinical.toFixed(1)}">${newTabClinical.toFixed(1)}</td>
                    <td class="cell-tab10" data-value="${engTab10[currentEngine]}" data-value-sensitive="${engTab10.sensitive}" data-value-standard="${engTab10.standard}" data-value-specific="${engTab10.specific}" data-value-clinical="${engTab10.clinical}">${engTab10[currentEngine]}</td>
                    <td class="cell-tab_major_a" data-value="${newTabMajorA.toFixed(1)}">${newTabMajorA.toFixed(1)}</td>
                    <td class="cell-p90" data-value="${engP90[currentEngine]}" data-value-sensitive="${engP90.sensitive}" data-value-standard="${engP90.standard}" data-value-specific="${engP90.specific}" data-value-clinical="${engP90.clinical}">${engP90[currentEngine]}</td>
                    <td class="cell-si" data-value="${engSi[currentEngine]}" data-value-sensitive="${engSi.sensitive}" data-value-standard="${engSi.standard}" data-value-specific="${engSi.specific}" data-value-clinical="${engSi.clinical}">${engSi[currentEngine]}</td>
                    <td class="cell-prri_index" data-value="${newPrri.toFixed(1)}">${newPrri.toFixed(1)}</td>
                    <td class="cell-pc10" data-value="${engPc10[currentEngine]}" data-value-sensitive="${engPc10.sensitive}" data-value-standard="${engPc10.standard}" data-value-specific="${engPc10.specific}" data-value-clinical="${engPc10.clinical}">${engPc10[currentEngine]}</td>
                    <td class="cell-pc15" data-value="${engPc15[currentEngine]}" data-value-sensitive="${engPc15.sensitive}" data-value-standard="${engPc15.standard}" data-value-specific="${engPc15.specific}" data-value-clinical="${engPc15.clinical}">${engPc15[currentEngine]}</td>
                    <td class="cell-events_A_ph" data-value="${newMajorAph.toFixed(1)}">${newMajorAph.toFixed(1)}</td>
                    <td class="cell-events_B_ph" data-value="${newMajorBph.toFixed(1)}">${newMajorBph.toFixed(1)}</td>
                    <td class="cell-events_C_ph" data-value="${newMajorCph.toFixed(1)}">${newMajorCph.toFixed(1)}</td>
                    <td class="left-align mono" style="font-size:11px;" title="${filenames.join('\\n')}">
                        Merged (${filenames.length} sessions)
                        <button class="unmerge-btn" style="margin-left: 5px; padding: 2px 4px; font-size: 9px; cursor: pointer;">Unmerge</button>
                    </td>
                `;

                if (rows[0] && rows[0].parentNode) {
                    rows[0].parentNode.insertBefore(tr, rows[0]);
                } else {
                    let tbody = document.querySelector('tbody');
                    tbody.insertBefore(tr, tbody.firstChild);
                }

                tr.querySelector('.row-checkbox').addEventListener('change', () => { updateColors(); saveData(); });
                tr.querySelector('.editable-label').addEventListener('input', () => { tr.querySelector('.editable-label').dataset.edited = "true"; saveData(); });
                
                tr.querySelector('.unmerge-btn').addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (confirm("Are you sure you want to unmerge these sessions?")) {
                        tr.remove();
                        rows.forEach(r => {
                            r.style.display = '';
                        });
                        updateColors();
                        saveData();
                        updateMergeButtonState();
                    }
                });

                tr.addEventListener('click', (e) => {
                    if (e.target.tagName.toLowerCase() === 'button' || e.target.tagName.toLowerCase() === 'input') return;
                    if (e.ctrlKey || e.metaKey) {
                        e.stopPropagation();
                        tr.classList.toggle('selected-row');
                        updateMergeButtonState();
                    }
                });

                applyEngineColumnVisibility();

                if (saveAfter) {
                    updateColors();
                    saveData();
                    updateMergeButtonState();
                }
            }
            
            function updateMergeButtonState() {
                let selectedCount = document.querySelectorAll("tbody tr.selected-row").length;
                let btn = document.getElementById('mergeBtn');
                if (btn) {
                    btn.disabled = selectedCount < 2;
                    if (btn.disabled) {
                        btn.style.opacity = '0.5';
                        btn.style.cursor = 'not-allowed';
                    } else {
                        btn.style.opacity = '1';
                        btn.style.cursor = 'pointer';
                    }
                }
            }
            let savedMergeState = null; // null means we are in original user state

            function clearCurrentMerges(backupOriginals) {
                let mergedRows = Array.from(document.querySelectorAll('tbody tr[data-is-merged="true"]'));
                
                if (backupOriginals && savedMergeState === null) {
                    savedMergeState = [];
                    mergedRows.forEach(mergedRow => {
                        savedMergeState.push(mergedRow.dataset.filename.split(' + '));
                    });
                }
                
                mergedRows.forEach(mergedRow => {
                    let filenames = mergedRow.dataset.filename.split(' + ');
                    let allRows = Array.from(document.querySelectorAll('tbody tr'));
                    filenames.forEach(fname => {
                        let origRow = allRows.find(r => r.dataset.filename === fname && r !== mergedRow);
                        if (origRow) origRow.style.display = '';
                    });
                    mergedRow.remove();
                });
                return mergedRows.length;
            }

            function unmergeAll() {
                let count = clearCurrentMerges(true);
                if (count === 0 && savedMergeState === null) {
                    alert('No merged sessions to unmerge.');
                    return;
                }
                updateColors();
                document.getElementById('remergeBtn').style.display = '';
            }

            function mergeByDay() {
                clearCurrentMerges(true);
                
                let allRows = Array.from(document.querySelectorAll('tbody tr:not([data-is-merged="true"]):not(.inline-chart-row)'));
                let rowsByDay = {};
                allRows.forEach(row => {
                    if (row.style.display !== 'none' && row.cells.length > 1 && row.querySelector('.editable-label') && row.dataset.filename) {
                        let dateText = row.cells[1].textContent.trim();
                        let dayGroup = dateText.split(/\s+/)[0];
                        if (!rowsByDay[dayGroup]) rowsByDay[dayGroup] = [];
                        rowsByDay[dayGroup].push(row);
                    }
                });
                
                for (let day in rowsByDay) {
                    if (rowsByDay[day].length > 1) {
                        doMergeForRows(rowsByDay[day], false);
                    }
                }
                
                updateColors();
                document.getElementById('remergeBtn').style.display = '';
            }

            function remergeAll() {
                if (savedMergeState === null) {
                    alert('No saved merge state to restore.');
                    return;
                }
                
                clearCurrentMerges(false);
                
                savedMergeState.forEach(mergeFilenames => {
                    let rowsToMerge = [];
                    let allRows = Array.from(document.querySelectorAll('tbody tr'));
                    mergeFilenames.forEach(fname => {
                        let row = allRows.find(r => r.dataset.filename === fname && r.style.display !== 'none');
                        if (row) rowsToMerge.push(row);
                    });
                    if (rowsToMerge.length === mergeFilenames.length) {
                        doMergeForRows(rowsToMerge, false);
                    }
                });
                savedMergeState = null;
                updateColors();
                saveData();
                document.getElementById('remergeBtn').style.display = 'none';
            }

            let trendChart = null;
            const chartMetrics = [
                { id: 'score', label: 'Score (Sens)', color: '#e6194b' },
                { id: 'score_standard', label: 'Score (Std)', color: '#f032e6', hidden: true },
                { id: 'score_specific', label: 'Score (Spec)', color: '#911eb4', hidden: true },
                { id: 'score_clinical', label: 'Score (Clin)', color: '#800000', hidden: true },
                { id: 'tab', label: 'TAB (Sens)', color: '#3cb44b' },
                { id: 'tab_standard', label: 'TAB (Std)', color: '#aaffc3', hidden: true },
                { id: 'tab_specific', label: 'TAB (Spec)', color: '#46f0f0', hidden: true },
                { id: 'tab_clinical', label: 'TAB (Clin)', color: '#008080', hidden: true },
                { id: 'tab10', label: 'TAB10 (engine)', color: '#42d4f4', hidden: true },
                { id: 'tmb', label: 'TMB (Motion)', color: '#ffd8b1', hidden: false },
                { id: 'tab_major_a', label: 'TAB MajA', color: '#bfef45', hidden: true },
                { id: 'p90', label: 'P90Δ (engine)', color: '#4363d8' },
                { id: 'si', label: 'SI/hr (engine)', color: '#f58231', hidden: true },
                { id: 'prri_index', label: 'PRRI-6/hr (raw)', color: '#808000', hidden: true },
                { id: 'pc10', label: 'PC10/hr (engine)', color: '#911eb4', hidden: true },
                { id: 'pc15', label: 'PC15/hr (engine)', color: '#46f0f0', hidden: true },
                { id: 'events_A_ph', label: 'MajA/hr', color: '#f032e6', hidden: true },
                { id: 'events_B_ph', label: 'MajB/hr', color: '#bcf60c', hidden: true },
                { id: 'events_C_ph', label: 'MajC/hr', color: '#fabebe', hidden: true }
            ];

            function renderChart() {
                let dataRows = Array.from(document.querySelectorAll("tbody tr")).filter(r => {
                    let cb = r.querySelector('.row-checkbox');
                    return r.style.display !== 'none' && cb && cb.checked;
                });
                
                dataRows.sort((a,b) => {
                    let fa = a.dataset.filename ? a.dataset.filename.split(' + ')[0] : '';
                    let fb = b.dataset.filename ? b.dataset.filename.split(' + ')[0] : '';
                    return fa.localeCompare(fb);
                });

                let labels = [];
                let datasetData = chartMetrics.map(() => []);

                dataRows.forEach(r => {
                    // Try to parse Date from anchor or just text
                    let dateCell = r.cells[1];
                    let dateStr = dateCell.innerText.trim();
                    labels.push(dateStr);
                    
                    chartMetrics.forEach((m, idx) => {
                        let cell = r.querySelector(`.cell-${m.id}`);
                        if(cell) {
                            datasetData[idx].push(parseFloat(cell.dataset.value) || 0);
                        } else {
                            datasetData[idx].push(null);
                        }
                    });
                });

                if (trendChart) {
                    trendChart.data.labels = labels;
                    chartMetrics.forEach((m, idx) => {
                        trendChart.data.datasets[idx].data = datasetData[idx];
                    });
                    trendChart.update();
                } else {
                    let canvas = document.getElementById('trendChart');
                    if (!canvas) return; // Might not be inserted yet
                    let ctx = canvas.getContext('2d');
                    let datasets = chartMetrics.map((m, idx) => ({
                        label: m.label,
                        data: datasetData[idx],
                        borderColor: m.color,
                        backgroundColor: m.color,
                        fill: false,
                        hidden: m.hidden,
                        tension: 0.1,
                        yAxisID: `y-${m.id}`
                    }));
                    
                    let scales = {
                        x: { display: true }
                    };
                    
                    // Create an individual hidden axis for each metric so they all scale nicely
                    chartMetrics.forEach((m, idx) => {
                        scales[`y-${m.id}`] = {
                            display: false, // Hide all these individual axes so it doesn't clutter
                            beginAtZero: false
                        };
                    });
                    
                    trendChart = new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: datasets
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            interaction: {
                                mode: 'index',
                                intersect: false,
                            },
                            scales: scales,
                            plugins: {
                                legend: {
                                    position: 'top',
                                    labels: { boxWidth: 12 }
                                }
                            }
                        }
                    });
                }
            }

            document.addEventListener("DOMContentLoaded", () => {
                let savedHide = localStorage.getItem('hrSpikeHideUnusedV2');
                if (savedHide !== null) {
                    hideUnused = (savedHide === 'true');
                } else {
                    hideUnused = true;
                }
                
                loadData();
                updateColors();
                updateMergeButtonState();
                applyEngineColumnVisibility();
                
                // Attach event listeners to rows for Ctrl+Click selection
                document.querySelectorAll('tbody tr').forEach(row => {
                    row.addEventListener('click', (e) => {
                        // Ignore clicks on checkboxes and editable labels
                        if (e.target.tagName.toLowerCase() === 'input' || e.target.classList.contains('editable-label')) {
                            return;
                        }
                        if (e.ctrlKey || e.metaKey) {
                            // Prevent text selection when ctrl clicking
                            e.preventDefault();
                            row.classList.toggle('selected-row');
                            updateMergeButtonState();
                        }
                    });
                });
                
                // Attach event listeners to checkboxes with shift+click range selection
                let lastCheckedIndex = null;
                let allCheckboxes = Array.from(document.querySelectorAll('.row-checkbox'));
                allCheckboxes.forEach((cb, idx) => {
                    cb.addEventListener('click', (e) => {
                        if (e.shiftKey && lastCheckedIndex !== null) {
                            let start = Math.min(lastCheckedIndex, idx);
                            let end = Math.max(lastCheckedIndex, idx);
                            let state = cb.checked;
                            for (let i = start; i <= end; i++) {
                                let row = allCheckboxes[i].closest('tr');
                                if (row && row.style.display !== 'none') {
                                    allCheckboxes[i].checked = state;
                                }
                            }
                        }
                        lastCheckedIndex = idx;
                        updateColors();
                        saveData();
                    });
                });
                
                // Attach event listeners to labels
                document.querySelectorAll('.editable-label').forEach(lbl => {
                    lbl.addEventListener('input', () => {
                        lbl.dataset.edited = "true";
                        saveData();
                    });
                });
            });
        </script>
    </head>
    <body>
        <h1>HR Spike Detection Results</h1>
        
        <div id="chartContainer" style="width: 100%; height: 350px; margin-bottom: 20px; border: 1px solid #ddd; background: #fff; padding: 10px; box-sizing: border-box; border-radius: 4px;">
            <canvas id="trendChart"></canvas>
        </div>
        
        <div style="margin: 8px 0; padding: 6px 12px; background: #f8f9fa; border: 1px solid #ddd; border-radius: 4px; display: flex; align-items: center; gap: 6px; font-size: 12px;">
            <strong>Color Scale:</strong>
            <span style="background: rgb(46,190,89); color: #000; padding: 2px 8px; border-radius: 3px;">Best</span>
            <span style="background: rgb(102,204,80); color: #000; padding: 2px 8px; border-radius: 3px;">Good</span>
            <span style="background: rgb(170,212,60); color: #000; padding: 2px 8px; border-radius: 3px;">Above Avg</span>
            <span style="background: rgb(255,235,59); color: #000; padding: 2px 8px; border-radius: 3px;">Median</span>
            <span style="background: rgb(255,152,0); color: #000; padding: 2px 8px; border-radius: 3px;">Below Avg</span>
            <span style="background: rgb(255,87,34); color: #000; padding: 2px 8px; border-radius: 3px;">Bad</span>
            <span style="background: rgb(224,119,244); color: #000; padding: 2px 8px; border-radius: 3px;">Worst</span>
            <span style="color: #666; margin-left: 6px;">(linear: min → max, lower = better)</span>
        </div>
        <p>
            Generated report. Colors show linear scale from min to max across your dataset (lower values = better). Uncheck rows to exclude from color scaling. Edit labels directly.<br><br>
            <span style="font-size: 11px; margin-left: 10px;"><b>Shift+Click</b> checkboxes to enable/disable a range of rows. <b>Ctrl+Click</b> (or Cmd+Click) rows to select for merging.</span><br><br>
            <strong>Event Threshold:</strong> A spike is counted if HR rises &ge;6 bpm (or +8% from baseline), is sustained for &ge;2s with a rise rate of &ge;0.8 bpm/sec.<br>
            <strong>Scientific Basis:</strong> This threshold matches the <strong>PRRI-6</strong> (pulse rate rises &gt;6 bpm) metric validated as a screening marker for sleep fragmentation. 
            Source: <a href="https://pubmed.ncbi.nlm.nih.gov/14607348/" target="_blank">Adachi et al., "Clinical significance of pulse rate rise during sleep..." (Sleep Medicine, 2003)</a>. 
            DOI: <a href="https://doi.org/10.1016/j.sleep.2003.06.003" target="_blank">10.1016/j.sleep.2003.06.003</a>.<br>
            <strong>Metrics Breakdown & Scoring Documentaton:</strong>
            <ul>
                <li><strong>Score (0-100):</strong> A weighted composite severity score out of 100. It combines Frequency (0-30 pts, based on Spike Index * 0.6), Magnitude Burden (0-30 pts, based on TAB normalized), Spike Intensity (0-20 pts, based on 90th percentile peak jump), and Pattern factors (0-20 pts, penalizing extreme regularity or lack of vagal recovery).</li>
                <li><strong>Spike (PC) Total index/hr:</strong> Filtered total events divided by total valid sleep hours. Uses advanced state machine (requires min peak delta &gt;=6, min rise rate 0.8bpm/sec, tracking baseline at P25). Indicates how often the nervous system is reacting to distinct stressors.</li>
                <li><strong>PRRI-6/hr (raw):</strong> The simplistic Pulse Rate Rise Index matching Adachi algorithm strictly: purely calculates how many times HR rises by 6 bpm from a local trough to peak, ignoring state tracking or slow drift rejections. Shows higher numbers generally than the Spike Index.</li>
                <li><strong>TAB:</strong> Total Autonomic Burden. The sum of the area-under-the-curve for all spikes, heavily reflecting spike duration and intensity.</li>
                <li><strong>Mean ΔHR:</strong> The average heart rate jump (in bpm) across all spikes.</li>
                <li><strong>Intensity (P90Δ):</strong> The 90th percentile peak jump. Shows the intensity of the worst 10% of your spikes.</li>
            </ul>
            <strong>Experimental Major Spike Algorithms:</strong> These try to detect only the most profound "major" spikes using different parameters.<br>
            <ul>
                <li><strong>Major A:</strong> Requires &ge;15 bpm jump, minimum 120s refractor period (ignores subsequent spikes for 2 mins).</li>
                <li><strong>Major B:</strong> Requires &ge;20 bpm jump, minimum 60s refractor period (higher threshold, shorter lockout).</li>
                <li><strong>Major C:</strong> Requires &ge;18 bpm jump, minimum 60s refractor period.</li>
            </ul>
        </p>
        
        <details style="margin-bottom: 10px; background: #f0f4f8; padding: 10px; border-radius: 5px; border: 1px solid #d0d7de;">
            <summary style="cursor: pointer; font-weight: bold;">Analysis Engine Presets (click to expand)</summary>
            <table style="margin-top: 8px; font-size: 12px; border-collapse: collapse; width: 100%;">
                <tr style="background: #e2e8f0;"><th style="padding: 4px 8px; text-align: left;">Preset</th><th style="padding: 4px 8px;">Onset (abs)</th><th style="padding: 4px 8px;">Onset (rel)</th><th style="padding: 4px 8px;">Rise Rate</th><th style="padding: 4px 8px;">Sustain</th><th style="padding: 4px 8px;">Min Delta</th><th style="padding: 4px 8px;">Recovery</th><th style="padding: 4px 8px;">Refractory</th><th style="padding: 4px 8px; text-align: left;">Description</th></tr>
                <tr><td style="padding: 4px 8px; font-weight: bold;">Sensitive</td><td style="padding: 4px 8px; text-align: center;">6 bpm</td><td style="padding: 4px 8px; text-align: center;">8%</td><td style="padding: 4px 8px; text-align: center;">0.8 bpm/s</td><td style="padding: 4px 8px; text-align: center;">2s</td><td style="padding: 4px 8px; text-align: center;">6 bpm</td><td style="padding: 4px 8px; text-align: center;">3 bpm</td><td style="padding: 4px 8px; text-align: center;">8s</td><td style="padding: 4px 8px;">Catches the most events. Lowest thresholds, fastest re-arm. Best for detecting subtle arousals but includes more noise.</td></tr>
                <tr style="background: #f8fafc;"><td style="padding: 4px 8px; font-weight: bold;">Standard</td><td style="padding: 4px 8px; text-align: center;">6 bpm</td><td style="padding: 4px 8px; text-align: center;">10%</td><td style="padding: 4px 8px; text-align: center;">1.0 bpm/s</td><td style="padding: 4px 8px; text-align: center;">3s</td><td style="padding: 4px 8px; text-align: center;">4 bpm</td><td style="padding: 4px 8px; text-align: center;">3 bpm</td><td style="padding: 4px 8px; text-align: center;">10s</td><td style="padding: 4px 8px;">Balanced trade-off. Slightly tighter rise-rate and sustain requirements filter out slow drifts while keeping true arousals.</td></tr>
                <tr><td style="padding: 4px 8px; font-weight: bold;">Specific</td><td style="padding: 4px 8px; text-align: center;">10 bpm</td><td style="padding: 4px 8px; text-align: center;">15%</td><td style="padding: 4px 8px; text-align: center;">1.5 bpm/s</td><td style="padding: 4px 8px; text-align: center;">3s</td><td style="padding: 4px 8px; text-align: center;">6 bpm</td><td style="padding: 4px 8px; text-align: center;">4 bpm</td><td style="padding: 4px 8px; text-align: center;">12s</td><td style="padding: 4px 8px;">High specificity. Requires large, sharp HR jumps. Eliminates most noise but may miss moderate arousals.</td></tr>
                <tr style="background: #f8fafc;"><td style="padding: 4px 8px; font-weight: bold;">Clinical</td><td style="padding: 4px 8px; text-align: center;">10 bpm</td><td style="padding: 4px 8px; text-align: center;">20%</td><td style="padding: 4px 8px; text-align: center;">1.0 bpm/s</td><td style="padding: 4px 8px; text-align: center;">3s</td><td style="padding: 4px 8px; text-align: center;">8 bpm</td><td style="padding: 4px 8px; text-align: center;">4 bpm</td><td style="padding: 4px 8px; text-align: center;">15s</td><td style="padding: 4px 8px;">Most conservative. Requires 20% relative rise and 8 bpm min delta. Only flags dramatic autonomic events. Longest refractory period (15s).</td></tr>
            </table>
        </details>
        
        <div style="margin-bottom: 8px;">
            <label for="engineSelector" style="font-weight: bold;">Derived Metrics Engine:</label>
            <select id="engineSelector" onchange="updateEngine()" style="padding: 4px 8px; font-size: 13px; border-radius: 4px; border: 1px solid #999; margin-right: 15px;">
                <option value="sensitive">Sensitive</option>
                <option value="standard" selected>Standard</option>
                <option value="specific">Specific</option>
                <option value="clinical">Clinical</option>
            </select>
            <span style="font-size: 11px; color: #666;">Controls which engine&rsquo;s data is shown in the SI/hr, P90&Delta;, PC10/hr, PC15/hr, and TAB10 columns.</span>
            <button id="toggleEnginesBtn" onclick="toggleUnusedEngines()" style="margin-left: 15px; padding: 6px 12px; font-size: 13px; cursor: pointer; border-radius: 4px; border: 1px solid #ccc; background-color: #f0f0f0;">Show Score/TAB from all engines</button>
         <p> <button id="mergeBtn" onclick="mergeSelected()" disabled style="padding: 6px 12px; font-weight: bold; cursor: not-allowed; opacity: 0.5; background-color: #2196F3; color: white; border: none; border-radius: 4px;">Merge Selected Rows (UI Only)</button>
            <button id="unmergeAllBtn" onclick="unmergeAll()" style="padding: 6px 12px; font-weight: bold; cursor: pointer; background-color: #ff9800; color: white; border: none; border-radius: 4px; margin-left: 8px;">Unmerge All Sessions</button>
            <button id="mergeByDayBtn" onclick="mergeByDay()" style="padding: 6px 12px; font-weight: bold; cursor: pointer; background-color: #9c27b0; color: white; border: none; border-radius: 4px; margin-left: 8px;">Auto-Merge by Day</button>
            <button id="remergeBtn" onclick="remergeAll()" style="padding: 6px 12px; font-weight: bold; cursor: pointer; background-color: #4caf50; color: white; border: none; border-radius: 4px; margin-left: 8px; display: none;">Restore Original Merges</button>
          </p>
        </div>
        <table class="sortable">
            <thead>
                <tr>
                    <th class="sorttable_nosort">Inc</th>
                    <th class="left-align" style="white-space: nowrap;">Date / Time</th>
                    <th class="left-align">Notes</th>
                    <th>Length</th>
                    <th title="Total Motion Burden">TMB</th>
                    <th title="Sensitive Engine" class="col-score">Score (Sens)</th>
                    <th title="Standard Engine" class="col-score_standard">Score (Std)</th>
                    <th title="Specific Engine" class="col-score_specific">Score (Spc)</th>
                    <th title="Clinical Engine" class="col-score_clinical">Score (Cln)</th>
                    <th title="Sensitive Engine" class="col-tab">TAB (Sens)</th>
                    <th title="Standard Engine" class="col-tab_standard">TAB (Std)</th>
                    <th title="Specific Engine" class="col-tab_specific">TAB (Spc)</th>
                    <th title="Clinical Engine" class="col-tab_clinical">TAB (Cln)</th>
                    <th title="Total Autonomic Burden for spikes &ge;10 bpm" data-engine-col="tab10" data-base-label="TAB10">TAB10 (Std)</th>
                    <th title="Total Autonomic Burden for Major A spikes">TAB MajA</th>
                    <th data-engine-col="p90" data-base-label="P90Δ">P90Δ (Std)</th>
                    <th data-engine-col="si" data-base-label="SI/hr">SI/hr (Std)</th>
                    <th>PRRI-6/hr (raw)</th>
                    <th data-engine-col="pc10" data-base-label="PC10/hr">PC10/hr (Std)</th>
                    <th data-engine-col="pc15" data-base-label="PC15/hr">PC15/hr (Std)</th>
                    <th title="Major Spike Detector: 15bpm min delta, 120s refractor">MajA/hr</th>
                    <th title="Major Spike Detector: 20bpm min delta, 60s refractor">MajB/hr</th>
                    <th title="Major Spike Detector: 18bpm min delta, 60s refractor">MajC/hr</th>
                    <th class="left-align">Filename</th>
                </tr>
            </thead>
            <tbody>""")

    import datetime

    for idx, r in enumerate(results):
        def cell(metric_key, val, display=None):
            if display is None: display = val
            return f'<td class="cell-{metric_key}" data-value="{val}">{display}</td>'
        
        # Calculate formatted hours
        hrs_exact = r.get('hours_exact', r['hours'])
        h = int(hrs_exact)
        m = int(round((hrs_exact - h) * 60))
        if m == 60:
            h += 1
            m = 0
        hr_str = f"{h}h {m:02d}m"
        
        # Extract date string from filename using datetime
        fname = r['filename']
        date_str = ""
        m_date = re.match(r'^(\d{14})_', fname)
        if m_date:
            try:
                dt = datetime.datetime.strptime(m_date.group(1), "%Y%m%d%H%M%S")
                prev_dt = dt - datetime.timedelta(days=1)
                time_str = dt.strftime("%I:%M%p").lstrip("0").lower()
                date_str = f"{prev_dt.month}/{prev_dt.day}-{dt.month}/{dt.day} {time_str}"
            except:
                pass

        if not date_str:
            # fallback
            m_date = re.match(r'^(\d{4})(\d{2})(\d{2})\d{6}_(.*)\.csv', fname)
            if m_date:
                date_str = f"{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}"
                time_part = m_date.group(4).split('_')[0] if '_' in m_date.group(4) else m_date.group(4)
                date_str += f" ({time_part})"

        r['date'] = date_str

        report_parts.append(f'<tr data-filename="{html.escape(fname, quote=True)}">')
        report_parts.append(f'<td><input type="checkbox" class="row-checkbox" checked></td>')
        chart_fname = fname.replace('.csv', '_chart.html')
        
        display_date = f"<b>{date_str}</b>"
        if idx == 0:
            display_date += " <span style='font-size: 14px; font-weight: bold; color: #d97706;'>(Click me!)</span>"
            
        chart_fname_js = chart_fname.replace("'", "\\'")
        report_parts.append(f'<td class="left-align" style="white-space: nowrap;"><a href="javascript:void(0);" onclick="openChart(\'charts/{chart_fname_js}\', this);" style="text-decoration:none; color:#0366d6;">{display_date}</a></td>')
        report_parts.append(f'<td class="left-align"><span class="editable-label" contenteditable="true">{html.escape(r["label"])}</span></td>')
        report_parts.append(f'<td data-sort="{hrs_exact}">{hr_str}</td>')
        report_parts.append(cell('tmb', r.get('tmb', 0)))
        report_parts.append(cell('score', r['score']))
        report_parts.append(cell('score_standard', r.get('score_standard', 0)))
        report_parts.append(cell('score_specific', r.get('score_specific', 0)))
        report_parts.append(cell('score_clinical', r.get('score_clinical', 0)))
        report_parts.append(cell('tab', r['tab']))
        report_parts.append(cell('tab_standard', r.get('tab_standard', 0)))
        report_parts.append(cell('tab_specific', r.get('tab_specific', 0)))
        report_parts.append(cell('tab_clinical', r.get('tab_clinical', 0)))
        # Engine-switchable cells: store all 4 preset values as data attributes
        si_s = r['si']; si_std = r.get('si_standard', 0); si_spc = r.get('si_specific', 0); si_cln = r.get('si_clinical', 0)
        p90_s = r.get('p90_delta', 0); p90_std = r.get('p90_delta_standard', 0); p90_spc = r.get('p90_delta_specific', 0); p90_cln = r.get('p90_delta_clinical', 0)
        pc10_s = r['pc10_per_hr']; pc10_std = r.get('pc10_per_hr_standard', 0); pc10_spc = r.get('pc10_per_hr_specific', 0); pc10_cln = r.get('pc10_per_hr_clinical', 0)
        pc15_s = r['pc15_per_hr']; pc15_std = r.get('pc15_per_hr_standard', 0); pc15_spc = r.get('pc15_per_hr_specific', 0); pc15_cln = r.get('pc15_per_hr_clinical', 0)
        tab10_s = r.get('tab10', 0); tab10_std = r.get('tab10_standard', 0); tab10_spc = r.get('tab10_specific', 0); tab10_cln = r.get('tab10_clinical', 0)
        
        def engine_cell(metric_key, val_sens, val_std, val_spc, val_cln, default='standard'):
            vals = {'sensitive': val_sens, 'standard': val_std, 'specific': val_spc, 'clinical': val_cln}
            active = vals[default]
            return (f'<td class="cell-{metric_key}" data-value="{active}"'
                    f' data-value-sensitive="{val_sens}" data-value-standard="{val_std}"'
                    f' data-value-specific="{val_spc}" data-value-clinical="{val_cln}">{active}</td>')
        
        report_parts.append(engine_cell('tab10', tab10_s, tab10_std, tab10_spc, tab10_cln))
        report_parts.append(cell('tab_major_a', r.get('tab_major_a', 0)))
        report_parts.append(engine_cell('p90', p90_s, p90_std, p90_spc, p90_cln))
        report_parts.append(engine_cell('si', si_s, si_std, si_spc, si_cln))
        report_parts.append(cell('prri_index', r.get('prri_index', 0)))
        report_parts.append(engine_cell('pc10', pc10_s, pc10_std, pc10_spc, pc10_cln))
        report_parts.append(engine_cell('pc15', pc15_s, pc15_std, pc15_spc, pc15_cln))
        report_parts.append(cell('events_A_ph', r.get('events_A_ph', 0)))
        report_parts.append(cell('events_B_ph', r.get('events_B_ph', 0)))
        report_parts.append(cell('events_C_ph', r.get('events_C_ph', 0)))
        report_parts.append(f'<td class="left-align mono" style="font-size:11px;">{fname}</td>')
        report_parts.append("</tr>")

    report_parts.append("""</tbody></table>
        
        <br><br>
        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #ddd; margin-bottom: 30px;">
            <h2>Changelog (Stats & Algorithms)</h2>
            <ul style="line-height: 1.6;">
                <li><strong>Latest Update:</strong> Added generic PRRI-6 tracking alongside our custom state-machine Spike Index to allow comparison against raw literature methodologies. Expanded scoring and metrics documentation section.</li>
                <li><strong>Previous:</strong> Added Experimental Major Spike algorithms (A, B, C) with varying refractory periods and magnitude thresholds to isolate profound awakenings.</li>
                <li><strong>Previous:</strong> Shifted Spike Index baseline from a static pre-sleep value to an adaptive 5-minute moving 25th-percentile (P25) HR baseline, dramatically improving robustness against normal sleep stage transitions.</li>
            </ul>
        </div>
    </body></html>""")

    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
    out_file = os.path.join(data_dir, 'detector_results.html')
    try:
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(report_parts))
        print(f"HTML Report generated: {out_file}", flush=True)
        
        # Save CSV copy
        import csv
        csv_file = os.path.join(data_dir, 'detector_results.csv')
        
        # Prepare filtered results for CSV
        csv_results = []
        keys_to_remove = {'events', 'prri_count', 'events_A', 'events_B', 'events_C', 'hours', 'filename'}
        
        for r in results:
            row = r.copy()
            for k in keys_to_remove:
                row.pop(k, None)
                
            # Rename major events for clarity
            row['major_a_ph'] = row.pop('events_A_ph', 0)
            row['major_b_ph'] = row.pop('events_B_ph', 0)
            row['major_c_ph'] = row.pop('events_C_ph', 0)
            
            csv_results.append(row)

        keys = ['date']
        for r in csv_results:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
                    
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(csv_results)
        print(f"CSV Report generated: {csv_file}", flush=True)

        # Auto open in web browser
        webbrowser.open('file://' + os.path.abspath(out_file))
    except Exception as e:
        print(f"Failed to write HTML report: {e}", flush=True)
        traceback.print_exc()

if __name__ == "__main__":
    try:
        generate_report()
    except Exception as e:
        print(f"Script crashed: {e}", flush=True)
        traceback.print_exc()
