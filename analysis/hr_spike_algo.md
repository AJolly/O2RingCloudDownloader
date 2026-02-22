# HR Spike Detection & Scoring Algorithm
## For 1-Second Pulse Rate Data (O2Ring / PPG)

### Design Goals
- Detect sharp upward HR spikes representing autonomic arousals during sleep
- Each rise-peak-fall = ONE event (no double-counting)
- Insensitive to slow HR trends (stage changes, circadian drift, non-dipping)
- Robust to PPG artifacts (motion, poor perfusion, PVCs)
- Produce cross-night-comparable metrics
- Configurable thresholds for sensitivity tuning
- Computationally simple enough for OSCAR (C++/Qt)

---

## STAGE 1: PREPROCESSING

### 1.1 Physiological Bounds Filter
```
For each sample HR[t]:
  if HR[t] < 30 or HR[t] > 200:
    mark as INVALID
  if HR[t] == 0:
    mark as INVALID (O2Ring signal dropout)
```

### 1.2 Rate-of-Change Filter
PPG-derived pulse rate at 1-second resolution can produce artificial jumps
from PVCs, signal loss recovery, and motion artifact. True sinus HR cannot
change more than ~15-20 bpm in a single second even during maximal
sympathetic activation. PVC artifacts on PPG can produce apparent jumps
of 30+ bpm in one sample.

```
For each sample:
  delta = |HR[t] - HR[t-1]|
  if delta > 25:  // configurable, 25 bpm/sec is generous
    mark HR[t] as SUSPECT
    
  // 3+ consecutive SUSPECT samples = ARTIFACT segment
  if 3+ consecutive SUSPECT:
    mark entire segment + 5s padding as ARTIFACT
```

NOTE: Do NOT reject single SUSPECT samples outright — a real arousal
onset can produce 5-8 bpm/sec changes over several seconds. The key
distinction: real spikes show sustained moderate rate-of-change (3-8 bpm/sec
for 3-5 seconds). Artifacts show single-sample extreme jumps (>25 bpm
in 1 second).

### 1.3 Interpolation of Invalid/Artifact Samples
```
For gaps ≤ 5 seconds:
  Linear interpolation from last valid to next valid
For gaps > 5 seconds:
  Mark as UNUSABLE — exclude from all analysis
  Do not interpolate (would create false flat segments)
```

### 1.4 Light Smoothing
Apply 3-second moving MEDIAN (not mean) to the valid/interpolated signal.

Why median: A 3-sample median eliminates single-sample noise without
smearing real spike onsets. Mean would blunt the spike. Wider smoothing
(5s, 7s) would delay spike detection and reduce measured magnitude.

```
HR_smooth[t] = median(HR[t-1], HR[t], HR[t+1])
```

This is now the working signal for all subsequent stages.

### 1.5 Quality Metric
```
quality = (total_valid_seconds - total_artifact_seconds) / total_recording_seconds
if quality < 0.80:
  FLAG recording as low quality
if quality < 0.60:
  REJECT recording — unreliable for scoring
```

---

## STAGE 2: ADAPTIVE BASELINE

### The Core Problem
The baseline must track the "floor" of HR — the resting rate between events.
It must:
- Follow genuine sustained HR changes (NREM→REM, circadian)
- NOT be pulled up by frequent spikes (at 25/hr, spikes occupy ~20% of signal)
- NOT be pulled down by brief bradycardic overshoots after spikes
- Work whether HR dips overnight or not

### 2.1 Primary Method: Moving 25th Percentile

```
Window: ±150 seconds (300 second / 5 minute total window)
For each second t:
  baseline[t] = percentile_25(HR_smooth[t-150 : t+150])
  
  // Exclude ARTIFACT and UNUSABLE samples from percentile calculation
  // Require minimum 120 valid samples (40%) in window to compute
  // Otherwise: interpolate baseline from nearest valid baseline values
```

Why 25th percentile: If spikes occupy 20% of the signal and post-spike
bradycardia occupies another 10%, the 25th percentile sits right at the
top of the "quiet floor" distribution. It's robust to anything affecting
<75% of the window.

Why 5-minute window: Balances responsiveness (tracks stage changes over
5-10 minutes) against stability (not perturbed by individual 30-second events).
A 3-minute window would be more responsive but more volatile. A 10-minute
window would be smoother but could lag behind REM onset.

### 2.2 Efficient Computation (for OSCAR C++ implementation)

Exact running percentile over a sliding window is O(n) per step using a
sorted data structure. For OSCAR, two practical approaches:

**Option A: Histogram method (recommended)**
```
// Maintain a histogram of HR values in current window
int hist[256] = {0};  // bins for HR 0-255, 1 bpm resolution
// Circular buffer tracks which values to add/remove as window slides

// To find 25th percentile:
int target = window_valid_count * 0.25;
int cumulative = 0;
for (int hr = 0; hr < 256; hr++) {
    cumulative += hist[hr];
    if (cumulative >= target) return hr;
}
```
O(1) amortized per step (add one sample, remove one sample, scan 256 bins).

**Option B: Dual-heap or order-statistic tree**
More complex, O(log n) per step, but exact. Probably overkill for this.

### 2.3 Alternative Method: Asymmetric Exponential Smoother (simpler, less robust)

If the percentile approach is too complex:
```
alpha_down = 0.05   // follows HR decreases: τ ≈ 20 seconds
alpha_up   = 0.001  // follows HR increases: τ ≈ 1000 seconds (~17 min)

if HR_smooth[t] <= baseline[t-1]:
    baseline[t] = baseline[t-1] + alpha_down * (HR_smooth[t] - baseline[t-1])
else:
    baseline[t] = baseline[t-1] + alpha_up * (HR_smooth[t] - baseline[t-1])
```

This is trivially simple but has edge cases: after prolonged awakenings, the
baseline creeps up slowly and takes minutes to come back down. The percentile
method handles this better. Use this only as a fallback.

---

## STAGE 3: SPIKE DETECTION — STATE MACHINE

### 3.0 Key Design Principle: One Event = One State Machine Cycle
The state machine processes the entire lifecycle of a spike as a single unit.
Once a spike onset is detected, it tracks through to recovery before
re-arming. This ELIMINATES double-counting by design.

### 3.1 Computed Signals
```
delta[t]    = HR_smooth[t] - baseline[t]        // deviation from baseline
rise_rate[t] = HR_smooth[t] - HR_smooth[t-3]    // 3-second rate of change
```

### 3.2 Configurable Thresholds
```
ONSET_ABS    = 6    // absolute bpm above baseline (Lachapelle: 6, Mayer: 10)
ONSET_REL    = 0.10 // OR relative increase (10% above baseline)
ONSET_RATE   = 1.0  // minimum rise rate: 1 bpm/sec over 3 seconds
ONSET_SUSTAIN = 3   // must exceed threshold for 3 consecutive seconds
RECOVERY_MARGIN = 3 // spike ends when within 3 bpm of baseline
MAX_DURATION = 180  // cap at 3 minutes (longer = sustained awakening, scored separately)
REFRACTORY   = 10   // seconds before re-arming after spike end
MIN_DELTA    = 4    // minimum peak delta to accept event (rejects tiny crossings)
```

NOTE ON THRESHOLDS: The onset threshold has both absolute and relative
components. Use whichever is LOWER (more sensitive):
```
threshold[t] = min(ONSET_ABS, baseline[t] * ONSET_REL)
```
This ensures sensitivity at both low baselines (where 6 bpm = 12% is big)
and high baselines (where 6 bpm = 7.5% is modest).

### 3.3 State Machine

```
States: QUIET → RISING → TRACKING → RECOVERY → REFRACTORY → QUIET

═══════════════════════════════════════════════════════════════

STATE: QUIET (watching for spike onset)
  
  Every second:
    effective_threshold = min(ONSET_ABS, baseline[t] * ONSET_REL)
    
    if delta[t] > effective_threshold 
       AND rise_rate[t] > ONSET_RATE:
      sustain_count++
    else:
      sustain_count = 0
    
    if sustain_count >= ONSET_SUSTAIN:
      onset_time = t - ONSET_SUSTAIN + 1  // backdate to first crossing
      onset_baseline = baseline[onset_time]
      peak_hr = HR_smooth[t]
      peak_time = t
      → transition to RISING

═══════════════════════════════════════════════════════════════

STATE: RISING (spike is climbing, tracking the peak)

  Every second:
    if HR_smooth[t] > peak_hr:
      peak_hr = HR_smooth[t]
      peak_time = t
    
    // Spike has peaked when HR drops 2+ bpm from peak for 2+ seconds
    if HR_smooth[t] < peak_hr - 2 for 2 consecutive seconds:
      → transition to TRACKING
    
    // Safety: if still rising after 30 seconds, force transition
    if (t - onset_time) > 30:
      → transition to TRACKING

═══════════════════════════════════════════════════════════════

STATE: TRACKING (spike peaked, tracking through fall and recovery)

  Every second:
    // Update peak if we get a second higher peak (double-peaked spikes exist)
    if HR_smooth[t] > peak_hr:
      peak_hr = HR_smooth[t]
      peak_time = t
    
    // Check for recovery
    if delta[t] <= RECOVERY_MARGIN:
      recovery_time = t
      → transition to RECOVERY
    
    // Check for max duration (sustained awakening)
    if (t - onset_time) > MAX_DURATION:
      recovery_time = t
      → transition to RECOVERY (flag as PROLONGED)

═══════════════════════════════════════════════════════════════

STATE: RECOVERY (spike has returned near baseline)

  // Finalize the event
  delta_hr = peak_hr - onset_baseline
  
  // Final validation
  if delta_hr >= MIN_DELTA:
    EMIT EVENT:
      onset_time      = onset_time
      peak_time       = peak_time  
      end_time        = recovery_time
      baseline_hr     = onset_baseline
      peak_hr         = peak_hr
      delta_hr        = delta_hr
      rise_time       = peak_time - onset_time        (seconds)
      fall_time       = recovery_time - peak_time      (seconds)
      total_duration  = recovery_time - onset_time     (seconds)
      auc             = sum(delta[onset_time:recovery_time])  (bpm·sec)
      rise_slope      = delta_hr / rise_time            (bpm/sec)
      symmetry        = rise_time / fall_time           (ratio)
      is_prolonged    = (total_duration > MAX_DURATION)
  
  refractory_start = recovery_time
  → transition to REFRACTORY

═══════════════════════════════════════════════════════════════

STATE: REFRACTORY (dead period to prevent re-triggering)

  if (t - refractory_start) >= REFRACTORY:
    sustain_count = 0
    → transition to QUIET

═══════════════════════════════════════════════════════════════
```

### 3.4 Handling Edge Cases

**Post-spike bradycardia**: After a tachy-bradycardia pattern, HR dips BELOW
baseline. This is physiological (vagal rebound). The REFRACTORY period
prevents this dip from affecting the next detection cycle. Additionally,
the baseline (25th percentile) naturally absorbs occasional dips.

**Staircase spikes**: Sometimes HR steps up, partially recovers, then spikes
again before returning to baseline. The RECOVERY_MARGIN of 3 bpm means
the first step must come substantially back down before the state machine
re-arms. If it doesn't come down enough, the entire staircase is tracked
as one prolonged event. This is correct behavior — it's one arousal episode.

**Plateau spikes**: HR rises and stays elevated for 30+ seconds (full
awakening). The state machine stays in TRACKING until either recovery
or MAX_DURATION. Flagged as is_prolonged. These get scored but can be
analyzed separately from brief autonomic arousals.

**Artifact during a spike**: If samples become INVALID/ARTIFACT during
RISING or TRACKING states, freeze the state machine (don't advance timer)
until valid data resumes. If artifact gap exceeds 15 seconds, ABORT the
event — too much missing data to characterize.

---

## STAGE 4: EVENT CLASSIFICATION (optional, for diagnostic use)

Each emitted event can be classified by morphology:

```
Type A — Tachy-bradycardia (classic autonomic arousal)
  Criteria: 
    rise_time 3-15 sec
    AND fall_time > rise_time
    AND HR drops below baseline within 30 sec of peak
         (post-peak minimum < onset_baseline - 2 bpm)
    AND total_duration 10-60 sec
  
Type B — Sustained tachycardia (full awakening)
  Criteria:
    is_prolonged == true
    OR (total_duration > 60 sec AND no bradycardic overshoot)
    
Type C — Brief spike (<10 seconds total)
  Criteria:
    total_duration < 10 sec
    (May be: PVC artifact that passed preprocessing, K-complex,
     or very brief subcortical microarousal)
     
Type D — Gradual rise
  Criteria:
    rise_slope < 0.5 bpm/sec
    AND rise_time > 20 sec
    (Possible: REM onset, slow positional shift, temperature)
```

---

## STAGE 5: NIGHT SUMMARY METRICS

### 5.1 Primary Metrics

```
Spike Index (SI)
  = total qualifying events / total valid recording hours
  Units: events/hour
  Severity: <5 normal, 5-15 mild, 15-30 moderate, >30 severe
  
Total Autonomic Burden (TAB)  
  = sum of all event AUCs / total valid recording hours
  Units: bpm·sec/hour
  This captures both frequency AND magnitude in one number.
  A few huge spikes and many small spikes are distinguished.
  Analogous to hypoxic burden concept.
  
Mean Delta HR
  = mean of delta_hr across all events
  Units: bpm
  Represents average spike severity.
  
Median Spike Duration  
  = median of total_duration across all events
  Units: seconds
```

### 5.2 Secondary Metrics

```
90th Percentile Delta HR (P90_delta)
  Captures severity of worst spikes, less affected by many small ones
  
Spike Rate Variability
  CV of inter-spike intervals
  Low CV (<0.5): periodic/regular → suggests PLMs or periodic breathing
  High CV (>1.5): random → suggests spontaneous or multi-cause
  
Temporal Distribution
  first_half_SI / second_half_SI
  >1.5: first-half dominant → unusual, may suggest positional/NREM issue
  0.7-1.3: roughly even → multi-cause or arousal threshold problem
  <0.7: second-half dominant → REM-related or medication wearing off
  
Morphology Distribution
  % Type A, B, C, D (if classification is implemented)
```

### 5.3 Cross-Night Normalization

The Spike Index is already normalized by time (events/hour).

For magnitude-based metrics (TAB, Mean Delta HR), normalize each spike's 
delta_hr as a percentage of that night's baseline:

```
delta_hr_pct[i] = event[i].delta_hr / event[i].baseline_hr * 100

TAB_normalized = sum(auc_pct) / hours
  where auc_pct uses delta as percentage rather than absolute bpm
```

This handles different resting HR between nights (e.g., one night baseline
55 bpm, another night 65 bpm — a 10 bpm spike means different things).

### 5.4 Recording and Reporting for OSCAR

For OSCAR integration, each event should be stored as an annotation 
with timestamp, enabling overlay on flow/pressure/SpO2 channels:

```
Per event (written to session data):
  onset_timestamp   (epoch seconds or session-relative)
  peak_timestamp
  end_timestamp
  baseline_hr
  peak_hr  
  delta_hr
  delta_hr_pct
  auc
  duration
  type (A/B/C/D)
  
Per session summary:
  spike_index
  total_autonomic_burden
  mean_delta_hr
  p90_delta_hr
  quality_pct
  total_valid_hours
  morphology_distribution (if classification enabled)
```

---

## STAGE 6: SENSITIVITY PRESETS

For user-configurable detection sensitivity:

```
PRESET: "Sensitive" (catch everything, for UARS/diagnostic use)
  ONSET_ABS    = 5
  ONSET_REL    = 0.08
  ONSET_RATE   = 0.8
  ONSET_SUSTAIN = 2
  MIN_DELTA    = 3
  
PRESET: "Standard" (balanced, default)
  ONSET_ABS    = 6
  ONSET_REL    = 0.10
  ONSET_RATE   = 1.0
  ONSET_SUSTAIN = 3
  MIN_DELTA    = 4

PRESET: "Specific" (fewer false positives, for scoring/comparison)
  ONSET_ABS    = 10
  ONSET_REL    = 0.15
  ONSET_RATE   = 1.5
  ONSET_SUSTAIN = 3
  MIN_DELTA    = 6
  
PRESET: "Clinical" (matches Mayer 2019 ≥10 bpm, ICC 0.89 vs PSG)
  ONSET_ABS    = 10
  ONSET_REL    = 0.20
  ONSET_RATE   = 1.0
  ONSET_SUSTAIN = 3
  MIN_DELTA    = 8
```

---

## APPENDIX A: Why Each Design Choice

| Choice | Rationale |
|--------|-----------|
| 3-sec median smooth | Kills single-sample noise, preserves spike onset timing |
| Moving 25th percentile baseline | Resistant to spike contamination (up to 75% of window) and bradycardic dips. Tracks genuine level changes over 5-minute timescale |
| ±150 sec window | Balances responsiveness with stability. Wide enough for 2+ complete spike cycles to not contaminate baseline |
| State machine detection | Inherently prevents double-counting. Models the physical lifecycle of a spike |
| Dual threshold (abs + relative) | Absolute catches spikes at all baselines; relative ensures proportional sensitivity when baseline is high |
| Rise rate criterion | Eliminates gradual drifts from being scored as spikes |
| Sustain criterion (3 sec) | Eliminates single-beat artifacts (PVCs) that passed preprocessing |
| Refractory period | Prevents re-triggering on post-spike oscillations |
| AUC-based burden metric | Captures both frequency and severity like hypoxic burden |

## APPENDIX B: Known Limitations of 1-sec PPG Data

1. **Cannot distinguish PVCs from real spikes**: A PVC produces a single-beat 
   HR artifact. At 1-second averaging, this manifests as a brief 1-2 second 
   "spike" of ~10-20 bpm. The MIN_DELTA and ONSET_SUSTAIN criteria filter 
   most of these, but some will leak through as Type C events. ECG data 
   (P10) is needed to definitively identify PVCs.

2. **PPG latency**: Pulse arrival time at the finger is ~200ms after the 
   R-wave. Additionally, the O2Ring's internal algorithm adds processing 
   delay. Expect spike timing to lag true cardiac events by 1-3 seconds.
   This doesn't affect spike counting or magnitude — only matters for
   precise temporal correlation with respiratory events.

3. **Poor perfusion**: User has Raynaud's-like symptoms. Cold fingers = 
   weak PPG signal = more artifacts. Monitor the quality metric. Consider 
   warming the O2Ring hand or using the earlobe clip version.

4. **O2Ring internal smoothing**: The device likely applies some internal 
   smoothing before reporting 1-second values. This means true spike 
   magnitudes may be slightly attenuated compared to ECG-derived HR.
   Consistent across nights, so cross-night comparison remains valid.

## APPENDIX C: Validation Strategy

To validate this algorithm against a reference:

1. Run one night with simultaneous P10 (RR intervals) and O2Ring
2. Apply the same algorithm to both data streams
3. Compare: event count, timing of events, magnitude agreement
4. P10 RR intervals give ground truth (true R-R timing, PVC identification)
5. Calibrate O2Ring thresholds if systematic offset exists

If EEG is ever available:
1. Run one night with EEG + O2Ring
2. Have EEG manually scored for arousals (≥3 sec alpha/beta shift)
3. Compare algorithm-detected events against EEG arousals
4. Calculate sensitivity, specificity, PPV for each preset
5. This creates a personal ground truth calibration
