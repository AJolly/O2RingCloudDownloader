import sys
import traceback
import webbrowser
import re

print("Script starting...", flush=True)

try:
    import os
    import glob
    import numpy as np
    from run_detector_batch import analyze_night, KNOWN_LABELS, CSV_DIR
    
    print(f"Imported run_detector_batch. CSV_DIR: {CSV_DIR}", flush=True)
except Exception as e:
    print(f"Failed to import dependencies: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

def generate_report():
    print("Collecting data...", flush=True)
    results = []
    
    search_path = os.path.join(CSV_DIR, "*.csv")
    csv_files = glob.glob(search_path)
    csv_files.sort(reverse=True)
    print(f"Found {len(csv_files)} files.", flush=True)
    
    for fpath in csv_files:
        fname = os.path.basename(fpath)
        label = KNOWN_LABELS.get(fname, fname)
        if label == fname:
            # Parse 20260217032620_326am_10h_23m.csv
            m = re.match(r'^(\d{4})(\d{2})(\d{2})\d{6}_(.*)\.csv', fname)
            if m:
                label = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}"
                label = label.replace('_', ' ')

        if not os.path.exists(fpath): continue
        
        try:
            chart_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'charts'))
            _, res = analyze_night(fpath, label, generate_chart=True, chart_dir=chart_dir)
            if res:
                res['filename'] = fname
                results.append(res)
                print(f"Parsed {fname} -> Score: {res.get('score', 0)} | SI/hr: {res.get('si', 0)} | TAB: {res.get('tab', 0)} | Events: {res.get('events', 0)} | Hrs: {res.get('hours', 0)}", flush=True)
            else:
                print(f"No results for {fname}", flush=True)
        except Exception as e:
            print(f"ERROR analyzing {fname}: {e}", flush=True)
            
    if not results:
        print("No results found.", flush=True)
        return

    html = []
    html.append("""<html>
    <head>
        <meta charset="utf-8">
        <title>HR Spike Detector Results</title>
        <style>
            body { font-family: sans-serif; margin: 10px; }
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
        <script>
            function openChart(url) {
                document.getElementById('chartIframe').src = url;
                document.getElementById('chartModal').style.display = 'flex';
            }
            
            const metricsFields = ['score', 'si', 'tab', 'delta', 'p90', 'pc10', 'pc15', 'events_A_ph', 'events_B_ph', 'events_C_ph'];
            
            function getColor(value, min_val, max_val, inverse) {
                if (isNaN(value)) return "#ffffff";
                if (min_val === max_val) return "#ffffff";
                let range = max_val - min_val;
                let norm = (value - min_val) / range;
                norm = Math.max(0.0, Math.min(1.0, norm));
                if (inverse) norm = 1.0 - norm;
                
                let r, g, b;
                if (norm < 0.5) {
                    r = Math.floor(norm * 2 * 255);
                    g = 255;
                    b = 0;
                } else {
                    r = 255;
                    g = Math.floor((1.0 - norm) * 2 * 255);
                    b = 0;
                }
                
                let hex = (r << 16 | g << 8 | b).toString(16).padStart(6, '0');
                return "#" + hex;
            }

            function updateColors() {
                let rows = document.querySelectorAll("tbody tr");
                let mins = {};
                let maxs = {};
                
                metricsFields.forEach(m => {
                    mins[m] = Infinity;
                    maxs[m] = -Infinity;
                });
                
                // First pass: find min/max ONLY for enabled rows
                rows.forEach(row => {
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
                            let val = parseFloat(cell.dataset.value);
                            // It will colorize based on the checked-rows scale. 
                            // If a value is outside the checked min/max, it clamps to min/max colors.
                            cell.style.backgroundColor = getColor(val, mins[m], maxs[m], false);
                        }
                    });
                });
            }
            
            function saveData() {
                let rows = document.querySelectorAll("tbody tr");
                let data = {};
                let merges = [];
                rows.forEach(row => {
                    let filename = row.dataset.filename;
                    
                    if (row.dataset.isMerged === "true") {
                        if (row.style.display !== 'none') {
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
                
                let totalHrs = 0, totalEvents = 0, totalEvents10 = 0, totalEvents15 = 0;
                let totalMajorA = 0, totalMajorB = 0, totalMajorC = 0;
                let sumTab = 0, sumScore = 0, sumDelta = 0, sumP90 = 0;
                let sumTypeA = 0, sumTypeB = 0, sumTypeC = 0;
                let filenames = [], labels = [];

                rows.forEach(row => {
                    let hrs = parseFloat(row.cells[3].dataset.sort) || 0;
                    totalHrs += hrs;
                    
                    let evts = parseFloat(row.cells[12].innerText) || 0;
                    totalEvents += evts;
                    
                    let pcArr = row.cells[13].innerText.split('/');
                    totalEvents10 += parseInt(pcArr[0] || 0);
                    totalEvents15 += parseInt(pcArr[1] || 0);
                    
                    totalMajorA += parseInt(row.cells[17].innerText) || 0;
                    totalMajorB += parseInt(row.cells[18].innerText) || 0;
                    totalMajorC += parseInt(row.cells[19].innerText) || 0;
                    
                    sumTab += (parseFloat(row.querySelector('.cell-tab').dataset.value) || 0) * hrs;
                    sumScore += (parseFloat(row.querySelector('.cell-score').dataset.value) || 0) * hrs;
                    
                    sumDelta += (parseFloat(row.querySelector('.cell-delta').dataset.value) || 0) * evts;
                    sumP90 += (parseFloat(row.querySelector('.cell-p90').dataset.value) || 0) * evts;
                    
                    let typeArr = row.cells[11].innerText.split('/');
                    sumTypeA += (parseFloat(typeArr[0]) || 0) * evts;
                    sumTypeB += (parseFloat(typeArr[1]) || 0) * evts;
                    sumTypeC += (parseFloat(typeArr[2]) || 0) * evts;

                    filenames.push(row.cells[20].innerText);
                    labels.push(row.querySelector('.editable-label').innerText);

                    // Unselect and hide
                    row.classList.remove('selected-row');
                    row.style.display = 'none';
                });

                if (totalHrs === 0) return;

                let newTab = sumTab / totalHrs;
                let newScore = sumScore / totalHrs;
                let newDelta = totalEvents > 0 ? sumDelta / totalEvents : 0;
                let newP90 = totalEvents > 0 ? sumP90 / totalEvents : 0;
                
                let newSi = totalEvents / totalHrs;
                let newPc10ph = totalEvents10 / totalHrs;
                let newPc15ph = totalEvents15 / totalHrs;
                
                let newTypeA = totalEvents > 0 ? sumTypeA / totalEvents : 0;
                let newTypeB = totalEvents > 0 ? sumTypeB / totalEvents : 0;
                let newTypeC = totalEvents > 0 ? sumTypeC / totalEvents : 0;
                
                let newMajorAph = totalMajorA / totalHrs;
                let newMajorBph = totalMajorB / totalHrs;
                let newMajorCph = totalMajorC / totalHrs;

                let h = Math.floor(totalHrs);
                let m = Math.round((totalHrs - h) * 60);
                if (m === 60) { h++; m=0; }
                let hrStr = `${h}h ${m.toString().padStart(2, '0')}m`;

                let firstRow = rows[0];
                let mergedDate = firstRow ? firstRow.cells[1].innerHTML : "Merged Date";

                let tr = document.createElement('tr');
                tr.dataset.filename = filenames.join(' + ');
                tr.dataset.isMerged = "true";

                tr.innerHTML = `
                    <td><input type="checkbox" class="row-checkbox" checked></td>
                    <td class="left-align" style="white-space: nowrap;">${mergedDate}</td>
                    <td class="left-align"><span class="editable-label" contenteditable="true">Merged: ${labels.join(' + ')}</span></td>
                    <td data-sort="${totalHrs}">${hrStr}</td>
                    <td class="cell-score" data-value="${newScore.toFixed(1)}">${newScore.toFixed(1)}</td>
                    <td class="cell-tab" data-value="${newTab.toFixed(1)}">${newTab.toFixed(1)}</td>
                    <td class="cell-delta" data-value="${newDelta.toFixed(1)}">${newDelta.toFixed(1)}</td>
                    <td class="cell-p90" data-value="${newP90.toFixed(1)}">${newP90.toFixed(1)}</td>
                    <td class="cell-si" data-value="${newSi.toFixed(1)}">${newSi.toFixed(1)}</td>
                    <td class="cell-pc10" data-value="${newPc10ph.toFixed(1)}">${newPc10ph.toFixed(1)}</td>
                    <td class="cell-pc15" data-value="${newPc15ph.toFixed(1)}">${newPc15ph.toFixed(1)}</td>
                    <td>${Math.round(newTypeA)}/${Math.round(newTypeB)}/${Math.round(newTypeC)}</td>
                    <td>${totalEvents}</td>
                    <td>${totalEvents10}/${totalEvents15}</td>
                    <td class="cell-events_A_ph" data-value="${newMajorAph.toFixed(1)}">${newMajorAph.toFixed(1)}</td>
                    <td class="cell-events_B_ph" data-value="${newMajorBph.toFixed(1)}">${newMajorBph.toFixed(1)}</td>
                    <td class="cell-events_C_ph" data-value="${newMajorCph.toFixed(1)}">${newMajorCph.toFixed(1)}</td>
                    <td>${totalMajorA}</td>
                    <td>${totalMajorB}</td>
                    <td>${totalMajorC}</td>
                    <td class="left-align mono" style="font-size:11px;" title="${filenames.join('\\n')}">
                        Merged (${filenames.length} sessions)
                        <button class="unmerge-btn" style="margin-left: 5px; padding: 2px 4px; font-size: 9px; cursor: pointer;">Unmerge</button>
                    </td>
                `;

                if (firstRow && firstRow.parentNode) {
                    firstRow.parentNode.insertBefore(tr, firstRow);
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

            document.addEventListener("DOMContentLoaded", () => {
                loadData();
                updateColors();
                updateMergeButtonState();
                
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
                
                // Attach event listeners to checkboxes
                document.querySelectorAll('.row-checkbox').forEach(cb => {
                    cb.addEventListener('change', () => {
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
        <div id="chartModal" style="display:none; position:fixed; top:5%; left:2%; width:96%; height:90%; background:white; z-index:1000; border:2px solid #ccc; box-shadow:0 0 20px rgba(0,0,0,0.5); flex-direction: column;">
            <div style="background:#f8f9fa; padding:10px;text-align:right; border-bottom:1px solid #ddd; flex-shrink: 0;">
                <button onclick="document.getElementById('chartModal').style.display='none';" style="padding:6px 15px; cursor:pointer; font-weight:bold; border-radius:4px; border:1px solid #ccc; background:#fff;">Close Chart</button>
            </div>
            <iframe id="chartIframe" style="width:100%; flex-grow: 1; border:none;"></iframe>
        </div>
        <h1>HR Spike Detection Results</h1>
        <p>
            Generated report. Columns with colors indicate severity (Green=Low, Red=High). Uncheck rows to exclude from color scaling. Edit labels directly.<br><br>
            <button id="mergeBtn" onclick="mergeSelected()" disabled style="padding: 6px 12px; font-weight: bold; cursor: not-allowed; opacity: 0.5; background-color: #2196F3; color: white; border: none; border-radius: 4px;">Merge Selected Rows (UI Only)</button>
            <span style="font-size: 11px; margin-left: 10px;"><b>Ctrl+Click</b> (or Cmd+Click on Mac) the rows you want to merge to select them. Merged states are loaded automatically.</span><br><br>
            <strong>Event Threshold:</strong> A spike is counted if HR rises &ge;6 bpm (or +8% from baseline), is sustained for &ge;2s with a rise rate of &ge;0.8 bpm/sec.<br>
            <strong>Scientific Basis:</strong> This threshold matches the <strong>PRRI-6</strong> (pulse rate rises &gt;6 bpm) metric validated as a screening marker for sleep fragmentation. 
            Source: <a href="https://pubmed.ncbi.nlm.nih.gov/14607348/" target="_blank">Adachi et al., "Clinical significance of pulse rate rise during sleep..." (Sleep Medicine, 2003)</a>. 
            DOI: <a href="https://doi.org/10.1016/j.sleep.2003.06.003" target="_blank">10.1016/j.sleep.2003.06.003</a>.<br>
            <strong>Metrics Breakdown:</strong>
            <ul>
                <li><strong>Score (0-100):</strong> A weighted composite score of Frequency (SI/h), Magnitude (TAB), Intensity (P90), and Pattern characteristics.</li>
                <li><strong>Spike (PC) Total index/hr:</strong> Total events divided by total valid sleep hours. Indicates how often the nervous system is reacting.</li>
                <li><strong>TAB:</strong> Total Autonomic Burden. The sum of the area-under-the-curve for all spikes, heavily reflecting spike duration and intensity.</li>
                <li><strong>Mean ΔHR:</strong> The average heart rate jump (in bpm) across all spikes.</li>
                <li><strong>Intensity (P90Δ):</strong> The 90th percentile peak jump. Shows the intensity of the worst 10% of your spikes.</li>
                <li><strong>Type A/B/C %:</strong> Characteristics of the spikes (A=Drop/Recovery, B=Sustained/No Recovery, C=Blunted).</li>
            </ul>
        </p>
        <table class="sortable">
            <thead>
                <tr>
                    <th class="sorttable_nosort">Inc</th>
                    <th class="left-align" style="white-space: nowrap;">Date / Time</th>
                    <th class="left-align">Night Label</th>
                    <th>Length</th>
                    <th>Score (0-100)</th>
                    <th>TAB</th>
                    <th>Mean ΔHR</th>
                    <th>Intensity (P90Δ)</th>
                    <th>Spike (PC) Total index/hr</th>
                    <th>PC10/hr</th>
                    <th>PC15/hr</th>
                    <th>Type A/B/C %</th>
                    <th>Events</th>
                    <th>Events &ge;10/15</th>
                    <th title="Major Spike Detector: 15bpm min delta, 120s refractor (Per Hour)">Major A / hr</th>
                    <th title="Major Spike Detector: 20bpm min delta, 60s refractor (Per Hour)">Major B / hr</th>
                    <th title="Major Spike Detector: 18bpm min delta, 60s refractor (Per Hour)">Major C / hr</th>
                    <th title="Major Spike Detector: 15bpm min delta, 120s refractor (Total Count)">Major A (Total)</th>
                    <th title="Major Spike Detector: 20bpm min delta, 60s refractor (Total Count)">Major B (Total)</th>
                    <th title="Major Spike Detector: 18bpm min delta, 60s refractor (Total Count)">Major C (Total)</th>
                    <th class="left-align">Filename</th>
                </tr>
            </thead>
            <tbody>""")

    import datetime

    for r in results:
        def cell(metric_key, val):
            return f'<td class="cell-{metric_key}" data-value="{val}">{val}</td>'
        
        # Calculate formatted hours
        hrs_exact = r.get('hours_exact', r['hours'])
        h = int(hrs_exact)
        m = int(round((hrs_exact - h) * 60))
        if m == 60:
            h += 1
            m = 0
        hr_str = f"{h}h {m:02d}m"
        
        # Calculate split absolute events
        events_10 = int(round(r['pc10_per_hr'] * hrs_exact))
        events_15 = int(round(r['pc15_per_hr'] * hrs_exact))
        pc_split = f"{events_10}/{events_15}"
        
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

        type_str = f"{r.get('pct_a', 0):.0f}/{r.get('pct_b', 0):.0f}/{r.get('pct_c', 0):.0f}"

        html.append(f"<tr data-filename='{fname}'>")
        html.append(f'<td><input type="checkbox" class="row-checkbox" checked></td>')
        chart_fname = fname.replace('.csv', '_chart.html')
        html.append(f'<td class="left-align" style="white-space: nowrap;"><a href="javascript:openChart(\'charts/{chart_fname}\');" style="text-decoration:none; color:#0366d6;">{date_str}</a></td>')
        html.append(f'<td class="left-align"><span class="editable-label" contenteditable="true">{r["label"]}</span></td>')
        html.append(f'<td data-sort="{hrs_exact}">{hr_str}</td>')
        html.append(cell('score', r['score']))
        html.append(cell('tab', r['tab']))
        html.append(cell('delta', r['mean_delta']))
        html.append(cell('p90', r.get('p90_delta', 0)))
        html.append(cell('si', r['si']))
        html.append(cell('pc10', r['pc10_per_hr']))
        html.append(cell('pc15', r['pc15_per_hr']))
        html.append(f'<td>{type_str}</td>')
        html.append(f'<td>{r["events"]}</td>')
        html.append(f'<td>{pc_split}</td>')
        html.append(cell('events_A_ph', r.get('events_A_ph', 0)))
        html.append(cell('events_B_ph', r.get('events_B_ph', 0)))
        html.append(cell('events_C_ph', r.get('events_C_ph', 0)))
        html.append(f'<td>{r.get("events_A", 0)}</td>')
        html.append(f'<td>{r.get("events_B", 0)}</td>')
        html.append(f'<td>{r.get("events_C", 0)}</td>')
        html.append(f'<td class="left-align mono" style="font-size:11px;">{fname}</td>')
        html.append("</tr>")

    html.append("""</tbody></table></body></html>""")

    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
    out_file = os.path.join(data_dir, 'detector_results.html')
    try:
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(html))
        print(f"HTML Report generated: {out_file}", flush=True)
        
        # Save CSV copy
        import csv
        csv_file = os.path.join(data_dir, 'detector_results.csv')
        # All keys should be relatively homogenous, find all possible keys 
        keys = []
        for r in results:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
                    
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(results)
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
