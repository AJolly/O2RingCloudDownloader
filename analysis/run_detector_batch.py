"""Run hr_spike_detector on all CSV files in the directory and save results."""
import sys
import os
import glob
import traceback
import numpy as np
from collections import Counter

# Add the script dir to path
sys.path.insert(0, os.path.dirname(__file__))

from hr_spike_detector import (
    load_data, preprocess, compute_baseline, detect_spikes, 
    classify_spike, compute_summary, PRESETS, Preset
)

CSV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))

# Selected nights: best to worst (kept for labels)
KNOWN_LABELS = {
    '20260218021927_219am_6h_37m.csv': '2/17-2/18 BEST - ASVAuto, EERs, esoph guardian, simethicone, GI 1.54',
    '20260207040040_400am_7h_52m.csv': '2/6-2/7  GOOD - First ASV night, GI 1.25',
    '20260206044534_445am_7h_12m.csv': '2/5-2/6  MID  - ASVAuto, GI 1.58',
    
    
}

results = []
outlines = []

# Find all CSV files
csv_files = glob.glob(os.path.join(CSV_DIR, "*.csv"))
# Sort reverse to show newest first
csv_files.sort(reverse=True)


def generate_session_chart(fpath, hr_smooth, baseline, valid, events, summary, chart_dir):
    import plotly.graph_objects as go
    import pandas as pd
    from datetime import datetime
    import json
    
    os.makedirs(chart_dir, exist_ok=True)
    
    # Read the timestamp column
    try:
        df = pd.read_csv(fpath)
        if 'Time' in df.columns:
            t_axis = pd.to_datetime(df['Time'])
            start_time_str = t_axis.iloc[0].strftime("%Y-%m-%d %H:%M:%S")
            start_ts = t_axis.iloc[0].timestamp()
        else:
            t_axis = np.arange(len(hr_smooth)) / 3600.0
            start_time_str = ""
            start_ts = 0
            
        # Extract CSV content for in-browser export
        csv_content = df.to_csv(index=False).replace('\\n', '\\\\n').replace('\\"', '\\\\"')
        csv_js_string = f'`{csv_content}`'
    except Exception as e:
        print(f"Error loading timestamps for chart: {e}")
        t_axis = np.arange(len(hr_smooth)) / 3600.0
        start_time_str = ""
        start_ts = 0
        csv_js_string = '""'
        
    fig = go.Figure()
    
    # HR trace
    hr_plot = hr_smooth.copy()
    hr_plot[~valid] = np.nan
    fig.add_trace(go.Scatter(x=t_axis, y=hr_plot, mode='lines', name='HR', line=dict(color='gray', width=1), opacity=0.8))
    
    # Baseline
    fig.add_trace(go.Scatter(x=t_axis, y=baseline, mode='lines', name='Baseline (P25)', line=dict(color='blue', width=2), opacity=0.8))
    
    # Event highlights
    type_colors = {
        'A': 'orange',
        'B': 'purple',
        'C': 'yellow',
        'D': 'green',
        'U': 'gray'
    }
    
    for event in events:
        stype = str(event.spike_type).split('.')[-1]
        tcode = 'U'
        if '_' in stype:
            tcode = stype.split('_')[0]
            
        color = type_colors.get(tcode, 'gray')
        
        t0 = t_axis.iloc[event.onset_idx] if hasattr(t_axis, 'iloc') else t_axis[event.onset_idx]
        t1 = t_axis.iloc[event.end_idx] if hasattr(t_axis, 'iloc') else t_axis[event.end_idx]
        t_peak = t_axis.iloc[event.peak_idx] if hasattr(t_axis, 'iloc') else t_axis[event.peak_idx]
        
        # Add vrect
        fig.add_vrect(x0=t0, x1=t1, fillcolor=color, opacity=0.15, line_width=0)
        
        # Add peak marker
        fig.add_trace(go.Scatter(
            x=[t_peak], y=[event.peak_hr],
            mode='markers',
            marker=dict(color=color, size=7, symbol='triangle-down'),
            name=f'Type {stype}',
            showlegend=False,
            hoverinfo='text',
            hovertext=f"Score: {event.severity_score:.1f}<br>ΔHR: {event.delta_hr:.1f}<br>Dur: {event.total_duration}s"
        ))

    valid_indices = np.where(valid)[0]
    if len(valid_indices) > 0:
        first_valid_idx = valid_indices[0]
        last_valid_idx = valid_indices[-1]
        x_min = t_axis.iloc[first_valid_idx] if hasattr(t_axis, 'iloc') else t_axis[first_valid_idx]
        x_max = t_axis.iloc[last_valid_idx] if hasattr(t_axis, 'iloc') else t_axis[last_valid_idx]
    else:
        x_min = t_axis.iloc[0] if hasattr(t_axis, 'iloc') else t_axis[0]
        x_max = t_axis.iloc[-1] if hasattr(t_axis, 'iloc') else t_axis[-1]
        
    min_hr = np.nanmin(hr_plot) if not np.all(np.isnan(hr_plot)) else 40
    y_min = max(30, min_hr - 2)

    fig.update_layout(
        title=f"HR Analysis - {os.path.basename(fpath)} (Score: {summary.severity_score:.1f})",
        xaxis_title="Time",
        xaxis=dict(range=[x_min, x_max]),
        yaxis_title="Heart Rate (bpm)",
        yaxis=dict(range=[y_min, 140]),
        dragmode="select",
        hovermode="x unified",
        margin=dict(l=40, r=40, t=40, b=40)
    )

    chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
    fname = os.path.basename(fpath)

    full_html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>HR Spikes - {fname}</title>
        <style>
            body {{ font-family: sans-serif; display: flex; margin: 0; height: 100vh; }}
            #chart-container {{ flex-grow: 1; padding: 10px; display: flex; flex-direction: column; overflow: hidden; }}
            .plotly-graph-div {{ flex-grow: 1; }}
            #sidebar {{ width: 320px; padding: 15px; background: #f8f9fa; border-left: 1px solid #ddd; display: flex; flex-direction: column; overflow-y: auto; }}
            h3 {{ margin-top: 0; }}
            .trim-item {{ background: #fff; border: 1px solid #ccc; padding: 8px; margin-bottom: 8px; border-radius: 4px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            .trim-info {{ font-size: 13px; }}
            .del-btn {{ color: #dc3545; cursor: pointer; font-weight: bold; border: none; background: none; font-size: 16px; padding: 0 5px; }}
            .del-btn:hover {{ color: #a71d2a; }}
            .btn {{ margin-top: 10px; padding: 10px; cursor: pointer; font-weight: bold; border: none; border-radius: 4px; color: white; width: 100%; }}
            .btn-save {{ background: #28a745; }}
            .btn-save:hover {{ background: #218838; }}
            .btn-load {{ background: #17a2b8; }}
            .btn-load:hover {{ background: #138496; }}
            .btn-export {{ background: #007bff; margin-top: auto; }}
            .btn-export:hover {{ background: #0069d9; }}
            .header-bar {{ display: flex; justify-content: space-between; align-items: center; padding: 0 10px; flex-shrink: 0; }}
        </style>
    </head>
    <body>
        <div id="chart-container">
            <div class="header-bar">
                <h2>{fname}</h2>
            </div>
            {chart_html}
        </div>
        <div id="sidebar">
            <h3>Excluded Regions</h3>
            <p style="font-size: 12px; color: #666;">Use the <b>Box Select</b> tool in the chart to mark regions to exclude (awake/artifact).</p>
            <div id="trim-list"></div>
            
            <hr style="width: 100%; border: 0; border-top: 1px solid #ddd; margin: 15px 0;">
            
            <button class="btn btn-save" onclick="saveTrims()">💾 Save Trims Locally</button>
            <button class="btn btn-load" onclick="loadTrims()">🔄 Reload Trims</button>
            
            <div style="flex-grow: 1;"></div>
            
            <button class="btn btn-export" onclick="exportCsv()">📥 Export Trimmed CSV</button>
            <div id="export-msg" style="font-size: 11px; color: green; margin-top: 5px; text-align: center; display: none;">Saved to Downloads!</div>
        </div>
        
        <script>
            const FNAME = "{fname}";
            const START_TS = {start_ts};
            const HAS_DATES = {str(start_ts > 0).lower()};
            let trims = [];
            
            // Wait for Plotly to render
            let plotDivInterval = setInterval(() => {{
                const plotDiv = document.querySelector('.plotly-graph-div');
                if (plotDiv && plotDiv.layout) {{
                    clearInterval(plotDivInterval);
                    initPlotly(plotDiv);
                }}
            }}, 500);
            
            function initPlotly(plotDiv) {{
                plotDiv.on('plotly_selected', (eventData) => {{
                    if (!eventData || !eventData.range) return;
                    
                    let startStr = eventData.range.x[0];
                    let endStr = eventData.range.x[1];
                    let startSec, endSec;
                    
                    if (HAS_DATES) {{
                        let tsStart = new Date(startStr).getTime() / 1000;
                        let tsEnd = new Date(endStr).getTime() / 1000;
                        startSec = Math.max(0, tsStart - START_TS);
                        endSec = tsEnd - START_TS;
                    }} else {{
                        // x axis is in hours
                        startSec = Math.max(0, parseFloat(startStr) * 3600);
                        endSec = parseFloat(endStr) * 3600;
                    }}
                    
                    if (endSec > startSec) {{
                        let label = prompt("Label for this excluded region (optional):", "awake");
                        if (label !== null) {{
                            trims.push({{ startSec: startSec, endSec: endSec, label: label, startStr: startStr, endStr: endStr }});
                            renderTrims();
                            drawTrimOverlays();
                        }}
                    }}
                    
                    // Clear plotly selection box
                    Plotly.restyle(plotDiv, {{selectedpoints: [null]}});
                }});
                
                // Load existing on start
                loadTrims();
            }}
            
            function renderTrims() {{
                const list = document.getElementById('trim-list');
                list.innerHTML = '';
                trims.forEach((t, i) => {{
                    const dur = Math.round(t.endSec - t.startSec);
                    list.innerHTML += `
                        <div class="trim-item">
                            <div class="trim-info">
                                <strong>${{t.label}}</strong><br>
                                ${{Math.round(t.startSec)}}s &rarr; ${{Math.round(t.endSec)}}s (${{dur}}s)
                            </div>
                            <button class="del-btn" onclick="deleteTrim(${{i}})" title="Remove Region">✕</button>
                        </div>
                    `;
                }});
            }}
            
            window.deleteTrim = function(index) {{
                trims.splice(index, 1);
                renderTrims();
                drawTrimOverlays();
                saveTrims(); // auto-save on delete
            }}
            
            window.saveTrims = function() {{
                let data = JSON.parse(localStorage.getItem('hrTrimRegions') || '{{}}');
                data[FNAME] = {{ regions: trims }};
                localStorage.setItem('hrTrimRegions', JSON.stringify(data));
            }}
            
            window.loadTrims = function() {{
                let data = JSON.parse(localStorage.getItem('hrTrimRegions') || '{{}}');
                if (data[FNAME] && data[FNAME].regions) {{
                    trims = data[FNAME].regions;
                    renderTrims();
                    drawTrimOverlays();
                }}
            }}
            
            function drawTrimOverlays() {{
                const plotDiv = document.querySelector('.plotly-graph-div');
                if (!plotDiv) return;
                
                // Get existing layout shapes
                let shapes = plotDiv.layout.shapes ? [...plotDiv.layout.shapes] : [];
                // Filter out previous trim overlays
                shapes = shapes.filter(s => s.fillcolor !== 'rgba(255, 0, 0, 0.3)');
                
                // Add new trim shapes
                trims.forEach(t => {{
                    let x0, x1;
                    if (HAS_DATES) {{
                        x0 = new Date((START_TS + t.startSec) * 1000).toISOString().replace('T', ' ').replace('Z', '');
                        x1 = new Date((START_TS + t.endSec) * 1000).toISOString().replace('T', ' ').replace('Z', '');
                    }} else {{
                        x0 = t.startSec / 3600.0;
                        x1 = t.endSec / 3600.0;
                    }}
                    
                    shapes.push({{
                        type: 'rect',
                        xref: 'x',
                        yref: 'paper',
                        x0: x0,
                        x1: x1,
                        y0: 0,
                        y1: 1,
                        fillcolor: 'rgba(255, 0, 0, 0.3)',
                        line: {{width: 0}},
                        layer: 'below'
                    }});
                }});
                
                Plotly.relayout(plotDiv, {{shapes: shapes}});
            }}
            
            window.exportCsv = function() {{
                if (trims.length === 0) {{
                    alert("No trims to export (list is empty).");
                    return;
                }}
                
                // Note: The embedded CSV data approach
                const csvStr = {csv_js_string};
                if (!csvStr) {{
                    alert("CSV data not embedded. Cannot export.");
                    return;
                }}
                
                // Split lines
                let lines = csvStr.split("\\n");
                let header = lines[0];
                let outLines = [header];
                
                // Calculate comment header
                let parts = [];
                trims.forEach(t => {{ parts.push(`${{Math.round(t.startSec)}}-${{Math.round(t.endSec)}}s`); }});
                outLines.push(`# Trimmed: excluded ${{parts.join(', ')}}`);
                
                // Parse rows based on second offset. Assuming 1 row = 1 second.
                let tIdx = header.split(',').indexOf('Time');
                let startMs = START_TS * 1000;
                
                for (let i = 1; i < lines.length; i++) {{
                    let line = lines[i].trim();
                    if (!line || line.startsWith('#')) continue;
                    
                    let secOffset = 0;
                    if (tIdx >= 0 && HAS_DATES) {{
                        let cols = line.split(',');
                        let dt = new Date(cols[tIdx]).getTime();
                        secOffset = (dt - startMs) / 1000;
                    }} else {{
                        // Fallback: assume 1 line = 1 sec
                        secOffset = i - 1;
                    }}
                    
                    // Check if inside any trim
                    let isTrimmed = false;
                    for (let t of trims) {{
                        if (secOffset >= t.startSec && secOffset <= t.endSec) {{
                            isTrimmed = true;
                            break;
                        }}
                    }}
                    
                    if (!isTrimmed) {{
                        outLines.push(line);
                    }}
                }}
                
                let outCsv = outLines.join("\\n");
                let blob = new Blob([outCsv], {{ type: 'text/csv' }});
                let url = URL.createObjectURL(blob);
                let a = document.createElement('a');
                a.href = url;
                a.download = FNAME.replace('.csv', '_trimmed.csv');
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                
                let msg = document.getElementById('export-msg');
                msg.style.display = 'block';
                setTimeout(() => {{ msg.style.display = 'none'; }}, 3000);
            }}
        </script>
    </body>
    </html>
    """
    
    out_path = os.path.join(chart_dir, fname.replace('.csv', '_chart.html'))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(full_html)
        print(f"Chart generated: {out_path}", flush=True)


def analyze_night(fpath, label, generate_chart=False, chart_dir=None):
    """Run detection pipeline on a single file and return text report + result dict."""
    out_lines = []
    
    out_lines.append(f"\n{'='*80}")
    out_lines.append(f"FILE: {os.path.basename(fpath)}")
    out_lines.append(f"NIGHT: {label}")
    out_lines.append(f"{'='*80}")

    try:
        # Load using the detector's own loader
        hr_raw = load_data(fpath, source='auto')
        n = len(hr_raw)
        hrs = n / 3600
        out_lines.append(f"Samples: {n} ({hrs:.1f} hours)")
        out_lines.append(f"Raw HR: min={int(np.nanmin(hr_raw))} max={int(np.nanmax(hr_raw))} mean={np.nanmean(hr_raw):.1f}")
        
        # Run pipeline with SENSITIVE preset
        hr_smooth, valid = preprocess(hr_raw)
        baseline = compute_baseline(hr_smooth, valid)
        
        params = PRESETS[Preset.SENSITIVE].copy()
        events = detect_spikes(hr_smooth, baseline, valid, params)
        
        summary = compute_summary(events, valid, n)
        
        if generate_chart and chart_dir:
            try:
                generate_session_chart(fpath, hr_smooth, baseline, valid, events, summary, chart_dir)
            except Exception as e:
                out_lines.append(f"Chart generation error: {e}")
                traceback.print_exc()
        
        out_lines.append(f"\n--- SENSITIVE PRESET ---")
        out_lines.append(f"Events detected: {len(events)}")
        out_lines.append(f"Spike Index: {summary.spike_index}/hr")
        out_lines.append(f"Total Autonomic Burden: {summary.total_autonomic_burden:.1f} bpm*s/hr")
        out_lines.append(f"Severity Score: {summary.severity_score}/100 ({summary.severity_label})")
        
        deltas = []
        peaks = []
        durations = []
        
        if events:
            deltas = [e.delta_hr for e in events]
            peaks = [e.peak_hr for e in events]
            durations = [e.total_duration for e in events]
            overshoots = [e.overshoot for e in events]
            
            out_lines.append(f"\nEvent Stats:")
            out_lines.append(f"  Delta HR:  mean={np.mean(deltas):.1f}  P50={np.median(deltas):.1f}  P90={np.percentile(deltas,90):.1f}  max={max(deltas):.1f}")
            out_lines.append(f"  Peak HR:   mean={np.mean(peaks):.1f}  P50={np.median(peaks):.1f}  P90={np.percentile(peaks,90):.1f}  max={max(peaks):.1f}")
            out_lines.append(f"  Duration:  mean={np.mean(durations):.1f}s  P50={np.median(durations):.1f}s  max={max(durations):.1f}s")
            out_lines.append(f"  Overshoot: mean={np.mean(overshoots):.1f}  P50={np.median(overshoots):.1f}  max={max(overshoots):.1f}")
            
            # Count by threshold
            pc6 = sum(1 for d in deltas if d >= 6)
            pc10 = sum(1 for d in deltas if d >= 10)
            pc15 = sum(1 for d in deltas if d >= 15)
            pc20 = sum(1 for d in deltas if d >= 20)
            out_lines.append(f"\n  By threshold:")
            out_lines.append(f"    >=6 bpm:  {pc6} events ({pc6/hrs:.1f}/hr)")
            out_lines.append(f"    >=10 bpm: {pc10} events ({pc10/hrs:.1f}/hr)")
            out_lines.append(f"    >=15 bpm: {pc15} events ({pc15/hrs:.1f}/hr)")
            out_lines.append(f"    >=20 bpm: {pc20} events ({pc20/hrs:.1f}/hr)")
            
            # Type distribution
            type_counts = Counter(str(e.spike_type) for e in events)
            out_lines.append(f"\n  Type distribution:")
            for t, c in sorted(type_counts.items()):
                out_lines.append(f"    {t}: {c} ({c/len(events)*100:.0f}%)")
            
            # Temporal: 1st half vs 2nd half
            mid_idx = n // 2
            first_half = [e for e in events if e.peak_idx < mid_idx]
            second_half = [e for e in events if e.peak_idx >= mid_idx]
            h1_rate = len(first_half) / (mid_idx/3600)
            h2_rate = len(second_half) / ((n - mid_idx)/3600)
            out_lines.append(f"\n  Temporal:")
            out_lines.append(f"    1st half: {len(first_half)} events ({h1_rate:.1f}/hr)")
            out_lines.append(f"    2nd half: {len(second_half)} events ({h2_rate:.1f}/hr)")
            ratio = h2_rate/h1_rate if h1_rate > 0 else 0
            out_lines.append(f"    Ratio (2nd/1st): {ratio:.2f}" if h1_rate > 0 else "    Ratio: N/A")
        
        # Result dict
        res_dict = {
            'file': os.path.basename(fpath),
            'label': label,
            'hours': round(hrs, 1),
            'hours_exact': hrs,
            'events': len(events),
            'si': summary.spike_index,
            'tab': round(summary.total_autonomic_burden, 1),
            'score': summary.severity_score,
            'mean_delta': round(np.mean(deltas), 1) if events else 0,
            'p90_delta': round(np.percentile(deltas, 90), 1) if events else 0,
            'mean_peak': round(np.mean(peaks), 1) if events else 0,
            'pc10_per_hr': round(sum(1 for d in deltas if d >= 10)/hrs, 1) if events else 0,
            'pc15_per_hr': round(sum(1 for d in deltas if d >= 15)/hrs, 1) if events else 0,
            'pct_a': round(summary.pct_type_a, 1),
            'pct_b': round(summary.pct_type_b, 1),
            'pct_c': round(summary.pct_type_c, 1),
        }
        
        # Calculate major presets
        for pname, pkey in [("A", Preset.MAJOR_A), ("B", Preset.MAJOR_B), ("C", Preset.MAJOR_C)]:
            p_params = PRESETS[pkey].copy()
            p_events = detect_spikes(hr_smooth, baseline, valid, p_params)
            res_dict[f'events_{pname}'] = len(p_events)
            res_dict[f'events_{pname}_ph'] = round(len(p_events) / hrs, 1) if hrs > 0 else 0
        
        return "\n".join(out_lines), res_dict

    except Exception as e:
        out_lines.append(f"ERROR: {e}")
        out_lines.append(traceback.format_exc())
        return "\n".join(out_lines), None

def main():
    results = []
    outlines = []

    # Find all CSV files
    csv_files = glob.glob(os.path.join(CSV_DIR, "*.csv"))
    # Sort reverse to show newest first
    csv_files.sort(reverse=True)

    print(f"Found {len(csv_files)} files to process")

    for fpath in csv_files:
        fname = os.path.basename(fpath)
        label = KNOWN_LABELS.get(fname, fname)
        
        if not os.path.exists(fpath):
            outlines.append(f"\n{'='*80}\nMISSING: {fname} - {label}\n")
            continue
        
        print(f"Processing {fname}...")
        text_out, res_dict = analyze_night(fpath, label)
        outlines.append(text_out)
        if res_dict:
            results.append(res_dict)

    # Summary comparison table
    outlines.append(f"\n\n{'='*80}")
    outlines.append("COMPARISON TABLE")
    outlines.append(f"{'='*80}")
    outlines.append(f"{'Night':<35} {'Hrs':>4} {'Evts':>5} {'SI/h':>5} {'TAB':>6} {'Score':>5} {'ΔHR':>5} {'P90Δ':>5} {'PC10':>5} {'PC15':>5}")
    outlines.append('-'*95)
    for r in results:
        outlines.append(f"{r['label']:<35} {r['hours']:>4} {r['events']:>5} {r['si']:>5} {r['tab']:>6} {r['score']:>5} {r['mean_delta']:>5} {r['p90_delta']:>5} {r['pc10_per_hr']:>5} {r['pc15_per_hr']:>5}")

    # Write output to the common data dir
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
    outpath = os.path.join(data_dir, 'detector_results.txt')
    try:
        with open(outpath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(outlines))
        print(f"Results written to {outpath}")
    except Exception as e:
        print(f"Error writing to {outpath}: {e}")
        # Fallback
        fallback = os.path.join(data_dir, 'detector_results_new.txt')
        with open(fallback, 'w', encoding='utf-8') as f:
            f.write('\n'.join(outlines))
        print(f"Usage fallback. Results written to {fallback}")

if __name__ == "__main__":
    main()
