# HR Spike Report — Session Labels & Data Trimming

This document describes two features in `generate_html_report.py` that control
how sessions are labelled in the dashboard and how bad/awake data is excluded
from analysis.

---

## 1. Smart Session Labels

### How labels are sourced

Labels are resolved in this order (first match wins):

| Priority | Source | Example result |
|----------|--------|----------------|
| 1 | `KNOWN_LABELS` dict in `run_detector_batch.py` | `"2/17-2/18 BEST - ASVAuto, EERs"` |
| 2 | Notes embedded in the CSV filename | `"Miami first night g"` |
| 3 | Auto-generated time + duration from filename | `"605am 8h 36m"` |

### Filename note extraction

The O2Ring downloader encodes session metadata into the CSV filename like this:

```
{14-digit-timestamp}_{optional notes}_{start time}_{duration}.csv
20260122015409_Miami first night g_154am_10h_1m.csv
                ^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^
                user notes (→ label) time+duration (→ Date col only)
```

**Rule:** if notes text is present in the filename, only the notes are shown as
the label. The `start time` and `duration` are already visible in the **Date/Time**
and **Length** columns, so they are *not* repeated in the Notes column.

| Filename suffix (after timestamp) | Notes column shows |
|-----------------------------------|--------------------|
| `605am_8h_36m` | `605am 8h 36m` *(no notes — fallback to time+dur)* |
| `Miami first night g_154am_10h_1m` | `Miami first night g` |
| `diy cpap_338am_8h_9m` | `diy cpap` |
| `228 427 546 610_236am_6h_16m` | `228 427 546 610` |
| `slice everything after 8 18 am_642am_3h_12m` | `slice everything after 8 18 am` |

### Editing labels in the dashboard

Labels are editable directly in the report — click any Notes cell and type.
Edited labels are saved to `localStorage` and restored on next load. They take
priority over everything above.

---

## 2. Comment-Based Data Trimming ("slice after …")

### The problem

Sometimes a session contains data you want to exclude — you woke up, got out
of bed, but the recorder kept running. Analyzing that wake time inflates your
spike metrics and skews the nightly summary.

The **exclude-regions tool** in the individual chart sidebar handles *arbitrary
regions*, but requires manual box-selection per session each time the report is
regenerated. For simple "everything after X time" cases you can instead put a
directive right in the session filename or `KNOWN_LABELS` entry.

### Syntax

Anywhere in the notes text, write one of:

```
slice everything after H:MM am
slice everything after H:MM pm
slice after H:MM am
after H:MM am
after HMMam          ← no colon/space variant
```

Examples that all work:

| Notes text | Cutoff applied |
|------------|----------------|
| `slice everything after 8:18 am` | 08:18 |
| `slice after 3:45am` | 03:45 |
| `after 8 18 am` | 08:18 |
| `after 818am` | 08:18 |
| `after 230pm` | 14:30 |

Case-insensitive. The directive can appear anywhere in the notes string and
is stripped from the displayed label if the notes contain only the directive.

### How trimming works

When the report generator sees a trim directive for a session it:

1. Re-reads the CSV's `Time` column using pandas.
2. Walks forward through the timestamps to find the first sample **at or
   after** the cutoff time.
3. Truncates both the HR array and the Motion array at that index.
4. Runs the full detection pipeline (baseline, spike detection, all four
   engine presets, major-spike algorithms) on the shortened data.
5. All derived metrics (SI/hr, TAB, Score, hours, …) are computed from the
   trimmed data only.

The trim is applied **before caching**, so the cached result reflects the
trimmed session. If you change the cutoff time, delete the session from
`data/detector_cache.json` (or delete the whole cache file) to force a
re-analysis.

### Midnight-spanning sessions

Sessions that start in the evening (≥ 18:00) and end in the morning are handled
correctly: a trim directive of `after 8:18 am` will not trigger at 8:18 PM
during the pre-midnight portion. The scan only starts after the clock has
passed midnight.

### Trim directive + chart

The trim only affects the **analysis metrics** (dashboard numbers). The
individual per-session chart (opened by clicking the date) still renders the
full raw data. Use the chart's **Excluded Regions** sidebar for visual
confirmation of what you trimmed.

---

## 3. In-Filename Wake Times (informal notation)

A common practice is to log times of brief awakenings in the notes:

```
20260430023614_228 427 546 610_236am_6h_16m.csv
                ^^^^^^^^^^^^^^^^
                woke at 2:28, 4:27, 5:46, 6:10
```

These are currently just stored as freeform notes and displayed in the Notes
column. They are **not** automatically parsed as trim directives (they lack
the `after …` keyword). Use the `after` syntax if you want to truncate data.

---

## 4. Workflow Example

You had a session that started at 6:42 AM but you got out of bed at 8:18 AM
and the ring kept recording until 9:54 AM. The filename looks like:

```
20260503064200_642am_3h_12m.csv
```

You rename (or relabel in the downloader) the notes part to:

```
20260503064200_slice everything after 8 18 am_642am_3h_12m.csv
```

On next report generation:

- **Notes column**: `slice everything after 8 18 am`
- **Date column**: `5/2-5/3 6:42am`  
- **Length column**: reflects trimmed duration (~1h 36m instead of 3h 12m)
- All spike metrics calculated on the 6:42–8:18 sleep window only

Alternatively, add it to `KNOWN_LABELS` in `run_detector_batch.py`:

```python
KNOWN_LABELS = {
    '20260503064200_642am_3h_12m.csv':
        'Good sleep, slice everything after 8:18 am',
}
```
