#!/usr/bin/env python3
"""
HR Spike Detection & Scoring for Sleep Data
Works with 1-second pulse rate (O2Ring) or RR-interval derived HR (Polar H10)

Usage:
    python hr_spike_detector.py data.csv --source o2ring --preset standard
    python hr_spike_detector.py data.csv --source polar --preset sensitive
    
CSV format expected:
    O2Ring: timestamp, spo2, pulse_rate  (or just a column with HR values)
    Polar:  timestamp, rr_interval_ms    (will convert to HR)
    
Outputs:
    - Night summary with Spike Index, Total Autonomic Burden, severity score
    - Per-event CSV with timestamps, magnitudes, morphology
    - Plot showing HR trace, baseline, detected events
"""

import numpy as np
import argparse
import csv
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple
from enum import Enum
import sys

# ============================================================
# CONFIGURATION
# ============================================================

class Preset(Enum):
    SENSITIVE = "sensitive"
    STANDARD = "standard"
    SPECIFIC = "specific"
    CLINICAL = "clinical"
    MAJOR_A = "major_a"
    MAJOR_B = "major_b"
    MAJOR_C = "major_c"

PRESETS = {
    Preset.SENSITIVE: {
        'onset_abs': 6,       # bpm above baseline (Adachi et al. 2003: PRRI-6)
        'onset_rel': 0.08,    # 8% above baseline
        'onset_rate': 0.8,    # bpm/sec minimum rise rate
        'onset_sustain': 2,   # seconds sustained above threshold
        'min_delta': 6,       # minimum peak magnitude to accept
        'recovery_margin': 3, # bpm from baseline = recovered
        'max_duration': 180,  # max event duration (seconds)
        'refractory': 8,      # seconds before re-arming
    },
    Preset.STANDARD: {
        'onset_abs': 6,
        'onset_rel': 0.10,
        'onset_rate': 1.0,
        'onset_sustain': 3,
        'min_delta': 4,
        'recovery_margin': 3,
        'max_duration': 180,
        'refractory': 10,
    },
    Preset.SPECIFIC: {
        'onset_abs': 10,
        'onset_rel': 0.15,
        'onset_rate': 1.5,
        'onset_sustain': 3,
        'min_delta': 6,
        'recovery_margin': 4,
        'max_duration': 180,
        'refractory': 12,
    },
    Preset.CLINICAL: {
        'onset_abs': 10,
        'onset_rel': 0.20,
        'onset_rate': 1.0,
        'onset_sustain': 3,
        'min_delta': 8,
        'recovery_margin': 4,
        'max_duration': 180,
        'refractory': 15,
    },
    Preset.MAJOR_A: {
        'onset_abs': 12,
        'onset_rel': 0.15,
        'onset_rate': 0.5,
        'onset_sustain': 3,
        'min_delta': 15,
        'recovery_margin': 5,
        'max_duration': 180,
        'refractory': 120,
    },
    Preset.MAJOR_B: {
        'onset_abs': 15,
        'onset_rel': 0.20,
        'onset_rate': 0.5,
        'onset_sustain': 3,
        'min_delta': 20,
        'recovery_margin': 5,
        'max_duration': 180,
        'refractory': 60,
    },
    Preset.MAJOR_C: {
        'onset_abs': 15,
        'onset_rel': 0.20,
        'onset_rate': 0.5,
        'onset_sustain': 3,
        'min_delta': 18,
        'recovery_margin': 5,
        'max_duration': 180,
        'refractory': 60,
    },
}

# Baseline parameters (not preset-dependent)
BASELINE_WINDOW = 150      # ±150 seconds (5 min total)
BASELINE_PERCENTILE = 25   # 25th percentile
BASELINE_MIN_VALID = 0.40  # minimum 40% valid samples in window
SMOOTH_WINDOW = 3          # 3-second median filter

# Artifact detection
MAX_PHYSIOLOGICAL_RATE = 25    # max bpm change per second
ARTIFACT_CONSECUTIVE = 3       # consecutive suspect samples = artifact
ARTIFACT_PADDING = 5           # seconds padding around artifacts
MAX_INTERP_GAP = 5             # max gap to interpolate (seconds)
HR_MIN = 30
HR_MAX = 200


# ============================================================
# DATA STRUCTURES
# ============================================================

class SpikeType(Enum):
    A_TACHYBRADY = "A"      # Classic arousal: rise then overshoot below baseline
    B_SUSTAINED = "B"       # Full awakening: prolonged elevation
    C_BRIEF = "C"           # Brief spike <10 seconds
    D_GRADUAL = "D"         # Slow drift, rise_slope < 0.5 bpm/sec
    UNCLASSIFIED = "U"


@dataclass
class SpikeEvent:
    onset_idx: int              # index into HR array
    peak_idx: int
    end_idx: int
    baseline_hr: float          # bpm at onset
    peak_hr: float              # bpm at peak
    delta_hr: float             # peak - baseline
    delta_hr_pct: float         # delta as % of baseline
    rise_time: float            # seconds onset to peak
    fall_time: float            # seconds peak to recovery
    total_duration: float       # seconds onset to recovery
    auc: float                  # area under curve (bpm·sec)
    auc_pct: float              # AUC using percentage deviation
    rise_slope: float           # bpm/sec during rise
    symmetry: float             # rise_time / fall_time
    nadir_hr: float             # minimum HR in 30s after peak
    overshoot: float            # how far below baseline after peak (bpm)
    spike_type: SpikeType = SpikeType.UNCLASSIFIED
    is_prolonged: bool = False
    
    @property
    def severity_score(self) -> float:
        """Per-event severity: 0-10 scale combining magnitude and duration."""
        # Magnitude component (0-5): based on delta_hr_pct
        # 5% = 0, 10% = 1, 20% = 2.5, 40%+ = 5
        mag = min(5.0, self.delta_hr_pct / 8.0)
        
        # Duration component (0-3): longer = worse
        # <10s = 0.5, 10-30s = 1, 30-60s = 2, >60s = 3
        if self.total_duration < 10:
            dur = 0.5
        elif self.total_duration < 30:
            dur = 1.0
        elif self.total_duration < 60:
            dur = 2.0
        else:
            dur = 3.0
        
        # Recovery component (0-2): bradycardic overshoot = healthy response
        # No overshoot = 2 (worse), overshoot >3 bpm = 0 (better)
        if self.overshoot > 3:
            rec = 0.0
        elif self.overshoot > 1:
            rec = 1.0
        else:
            rec = 2.0
            
        return mag + dur + rec


@dataclass 
class NightSummary:
    recording_hours: float
    valid_hours: float
    quality_pct: float
    
    # Counts
    total_spikes: int
    spike_index: float              # events/hour
    
    # Magnitude
    mean_delta_hr: float            # mean bpm rise
    median_delta_hr: float
    p90_delta_hr: float             # 90th percentile
    mean_delta_hr_pct: float        # mean % rise
    
    # Burden
    total_autonomic_burden: float   # bpm·sec/hour
    tab_normalized: float           # %·sec/hour (baseline-normalized)
    
    # Duration
    median_duration: float          # median spike duration
    mean_duration: float
    
    # Temporal distribution
    first_half_si: float            # spike index, first half of night
    second_half_si: float           # spike index, second half
    temporal_ratio: float           # second_half / first_half
    
    # Periodicity
    spike_interval_cv: float        # CV of inter-spike intervals
    
    # Morphology
    pct_type_a: float
    pct_type_b: float
    pct_type_c: float
    pct_type_d: float
    
    # Composite
    severity_score: float           # 0-100 composite score
    severity_label: str             # normal/mild/moderate/severe


# ============================================================
# STAGE 1: PREPROCESSING
# ============================================================

def preprocess(hr_raw: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Clean raw 1-second HR data.
    Returns (hr_smooth, valid_mask) where valid_mask[i] = True if usable.
    """
    n = len(hr_raw)
    hr = hr_raw.astype(float).copy()
    valid = np.ones(n, dtype=bool)
    
    # 1.1 Physiological bounds
    invalid = (hr < HR_MIN) | (hr > HR_MAX) | (hr == 0) | np.isnan(hr)
    valid[invalid] = False
    
    # 1.2 Rate-of-change filter
    suspect = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if valid[i] and valid[i-1]:
            if abs(hr[i] - hr[i-1]) > MAX_PHYSIOLOGICAL_RATE:
                suspect[i] = True
    
    # Mark runs of 3+ consecutive suspect as artifact (with padding)
    artifact = np.zeros(n, dtype=bool)
    run_start = -1
    run_len = 0
    for i in range(n):
        if suspect[i]:
            if run_len == 0:
                run_start = i
            run_len += 1
        else:
            if run_len >= ARTIFACT_CONSECUTIVE:
                start = max(0, run_start - ARTIFACT_PADDING)
                end = min(n, i + ARTIFACT_PADDING)
                artifact[start:end] = True
            run_len = 0
    # Handle trailing run
    if run_len >= ARTIFACT_CONSECUTIVE:
        start = max(0, run_start - ARTIFACT_PADDING)
        artifact[start:n] = True
    
    valid[artifact] = False
    
    # 1.3 Interpolation of short gaps
    hr_interp = hr.copy()
    gap_start = -1
    for i in range(n):
        if not valid[i]:
            if gap_start == -1:
                gap_start = i
        else:
            if gap_start >= 0:
                gap_len = i - gap_start
                if gap_len <= MAX_INTERP_GAP and gap_start > 0:
                    # Linear interpolation
                    start_val = hr_interp[gap_start - 1]
                    end_val = hr_interp[i]
                    for j in range(gap_start, i):
                        frac = (j - gap_start + 1) / (gap_len + 1)
                        hr_interp[j] = start_val + frac * (end_val - start_val)
                        valid[j] = True  # now usable after interpolation
                gap_start = -1
    
    # 1.4 Median smoothing (3-second window)
    hr_smooth = hr_interp.copy()
    for i in range(1, n - 1):
        if valid[i-1] and valid[i] and valid[i+1]:
            hr_smooth[i] = np.median([hr_interp[i-1], hr_interp[i], hr_interp[i+1]])
    
    return hr_smooth, valid


# ============================================================
# STAGE 2: ADAPTIVE BASELINE
# ============================================================

def compute_baseline(hr_smooth: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """
    Moving 25th percentile baseline over ±BASELINE_WINDOW seconds.
    Uses histogram method for efficiency.
    """
    n = len(hr_smooth)
    baseline = np.full(n, np.nan)
    
    # Histogram-based sliding window percentile
    hist = np.zeros(256, dtype=int)
    window_count = 0
    
    # Initialize window for first position
    w = BASELINE_WINDOW
    for j in range(0, min(w + 1, n)):
        if valid[j]:
            bin_idx = int(np.clip(hr_smooth[j], 0, 255))
            hist[bin_idx] += 1
            window_count += 1
    
    for i in range(n):
        # Add new right edge
        right = i + w
        if right < n and right > 0:
            if valid[right]:
                bin_idx = int(np.clip(hr_smooth[right], 0, 255))
                hist[bin_idx] += 1
                window_count += 1
        
        # Remove old left edge
        left = i - w - 1
        if left >= 0:
            if valid[left]:
                bin_idx = int(np.clip(hr_smooth[left], 0, 255))
                hist[bin_idx] -= 1
                window_count -= 1
        
        # Compute percentile if enough valid samples
        min_needed = int((2 * w + 1) * BASELINE_MIN_VALID)
        if window_count >= min_needed:
            target = int(window_count * BASELINE_PERCENTILE / 100.0)
            cumulative = 0
            for hr_val in range(256):
                cumulative += hist[hr_val]
                if cumulative >= target:
                    baseline[i] = float(hr_val)
                    break
    
    # Interpolate any NaN gaps in baseline
    valid_bl = ~np.isnan(baseline)
    if np.any(valid_bl) and not np.all(valid_bl):
        indices = np.arange(n)
        baseline[~valid_bl] = np.interp(
            indices[~valid_bl], 
            indices[valid_bl], 
            baseline[valid_bl]
        )
    
    return baseline


# ============================================================
# STAGE 3: SPIKE DETECTION — STATE MACHINE
# ============================================================

class DetectorState(Enum):
    QUIET = 0
    RISING = 1
    TRACKING = 2
    RECOVERY = 3
    REFRACTORY = 4


def detect_spikes(hr_smooth: np.ndarray, baseline: np.ndarray, 
                  valid: np.ndarray, params: dict) -> List[SpikeEvent]:
    """
    State machine spike detector.
    """
    n = len(hr_smooth)
    events = []
    
    # Computed signals
    delta = hr_smooth - baseline
    
    # 3-second rate of change
    rise_rate = np.zeros(n)
    for i in range(3, n):
        if valid[i] and valid[i-3]:
            rise_rate[i] = (hr_smooth[i] - hr_smooth[i-3]) / 3.0
    
    # State machine variables
    state = DetectorState.QUIET
    sustain_count = 0
    onset_idx = 0
    onset_baseline = 0.0
    peak_hr = 0.0
    peak_idx = 0
    peak_cooldown = 0
    refractory_start = 0
    
    for i in range(3, n):
        if not valid[i]:
            # If in the middle of tracking a spike and gap is long, abort
            if state in (DetectorState.RISING, DetectorState.TRACKING):
                # Look ahead for how long the gap is
                gap = 0
                for j in range(i, min(i + 20, n)):
                    if valid[j]:
                        break
                    gap += 1
                if gap > 15:
                    state = DetectorState.QUIET
                    sustain_count = 0
            continue
        
        if state == DetectorState.QUIET:
            threshold = min(params['onset_abs'], 
                          baseline[i] * params['onset_rel'])
            
            if delta[i] > threshold and rise_rate[i] > params['onset_rate']:
                sustain_count += 1
            else:
                sustain_count = 0
            
            if sustain_count >= params['onset_sustain']:
                onset_idx = i - params['onset_sustain'] + 1
                onset_baseline = baseline[onset_idx]
                peak_hr = hr_smooth[i]
                peak_idx = i
                state = DetectorState.RISING
                sustain_count = 0
        
        elif state == DetectorState.RISING:
            if hr_smooth[i] > peak_hr:
                peak_hr = hr_smooth[i]
                peak_idx = i
                peak_cooldown = 0
            else:
                peak_cooldown += 1
            
            # Peaked: HR dropped 2+ bpm from peak for 2+ seconds
            if peak_cooldown >= 2 and hr_smooth[i] < peak_hr - 2:
                state = DetectorState.TRACKING
            
            # Safety timeout
            if (i - onset_idx) > 30:
                state = DetectorState.TRACKING
        
        elif state == DetectorState.TRACKING:
            # Update peak if double-peaked
            if hr_smooth[i] > peak_hr:
                peak_hr = hr_smooth[i]
                peak_idx = i
            
            # Recovery check
            if delta[i] <= params['recovery_margin']:
                recovery_idx = i
                state = DetectorState.RECOVERY
            
            # Max duration
            if (i - onset_idx) > params['max_duration']:
                recovery_idx = i
                state = DetectorState.RECOVERY
        
        elif state == DetectorState.RECOVERY:
            # This state is instantaneous — process and move on
            recovery_idx = i  # use current i
            
            # Calculate event metrics
            d_hr = peak_hr - onset_baseline
            
            if d_hr >= params['min_delta'] and onset_baseline > 0:
                rise_t = max(1, peak_idx - onset_idx)
                fall_t = max(1, recovery_idx - peak_idx)
                total_t = max(1, recovery_idx - onset_idx)
                
                # AUC: sum of positive deviations from baseline
                segment = delta[onset_idx:recovery_idx + 1]
                auc = float(np.nansum(np.maximum(segment, 0)))
                
                # AUC normalized by baseline
                if onset_baseline > 0:
                    pct_segment = (hr_smooth[onset_idx:recovery_idx + 1] - onset_baseline) / onset_baseline * 100
                    auc_pct = float(np.nansum(np.maximum(pct_segment, 0)))
                else:
                    auc_pct = 0.0
                
                # Post-peak nadir (look 30s after peak)
                nadir_end = min(n, peak_idx + 30)
                if nadir_end > peak_idx:
                    nadir_segment = hr_smooth[peak_idx:nadir_end]
                    nadir_valid = valid[peak_idx:nadir_end]
                    valid_vals = nadir_segment[nadir_valid]
                    if len(valid_vals) > 0:
                        nadir_hr = float(np.min(valid_vals))
                    else:
                        nadir_hr = onset_baseline
                else:
                    nadir_hr = onset_baseline
                
                overshoot = max(0.0, onset_baseline - nadir_hr)
                
                event = SpikeEvent(
                    onset_idx=onset_idx,
                    peak_idx=peak_idx,
                    end_idx=recovery_idx,
                    baseline_hr=round(onset_baseline, 1),
                    peak_hr=round(peak_hr, 1),
                    delta_hr=round(d_hr, 1),
                    delta_hr_pct=round(d_hr / onset_baseline * 100, 1) if onset_baseline > 0 else 0,
                    rise_time=rise_t,
                    fall_time=fall_t,
                    total_duration=total_t,
                    auc=round(auc, 1),
                    auc_pct=round(auc_pct, 1),
                    rise_slope=round(d_hr / rise_t, 2),
                    symmetry=round(rise_t / fall_t, 2) if fall_t > 0 else 99.0,
                    nadir_hr=round(nadir_hr, 1),
                    overshoot=round(overshoot, 1),
                    is_prolonged=(total_t > params['max_duration'])
                )
                
                # Classify morphology
                event.spike_type = classify_spike(event)
                events.append(event)
            
            refractory_start = i
            state = DetectorState.REFRACTORY
        
        # Handle REFRACTORY in same iteration (it's just a timer)
        if state == DetectorState.REFRACTORY:
            if (i - refractory_start) >= params['refractory']:
                state = DetectorState.QUIET
                sustain_count = 0
    
    return events


# ============================================================
# STAGE 4: CLASSIFICATION
# ============================================================

def classify_spike(event: SpikeEvent) -> SpikeType:
    """Classify spike morphology."""
    
    # Brief spike
    if event.total_duration < 10:
        return SpikeType.C_BRIEF
    
    # Gradual rise
    if event.rise_slope < 0.5 and event.rise_time > 20:
        return SpikeType.D_GRADUAL
    
    # Sustained tachycardia (full awakening)
    if event.is_prolonged or (event.total_duration > 60 and event.overshoot < 1):
        return SpikeType.B_SUSTAINED
    
    # Tachy-bradycardia (classic arousal with vagal rebound)
    if event.overshoot > 2 and event.fall_time > event.rise_time:
        return SpikeType.A_TACHYBRADY
    
    # Default: if it has a decent rise and falls back, call it Type A
    if event.rise_slope > 0.5 and event.total_duration < 60:
        return SpikeType.A_TACHYBRADY
    
    return SpikeType.UNCLASSIFIED


# ============================================================
# STAGE 5: NIGHT SUMMARY
# ============================================================

def compute_summary(events: List[SpikeEvent], valid: np.ndarray,
                    total_seconds: int) -> NightSummary:
    """Compute all night-level metrics."""
    
    valid_seconds = int(np.sum(valid))
    recording_hours = total_seconds / 3600.0
    valid_hours = valid_seconds / 3600.0
    quality = valid_seconds / total_seconds if total_seconds > 0 else 0
    
    n_events = len(events)
    
    if n_events == 0:
        return NightSummary(
            recording_hours=round(recording_hours, 2),
            valid_hours=round(valid_hours, 2),
            quality_pct=round(quality * 100, 1),
            total_spikes=0, spike_index=0,
            mean_delta_hr=0, median_delta_hr=0, p90_delta_hr=0, mean_delta_hr_pct=0,
            total_autonomic_burden=0, tab_normalized=0,
            median_duration=0, mean_duration=0,
            first_half_si=0, second_half_si=0, temporal_ratio=0,
            spike_interval_cv=0,
            pct_type_a=0, pct_type_b=0, pct_type_c=0, pct_type_d=0,
            severity_score=0, severity_label="normal"
        )
    
    deltas = np.array([e.delta_hr for e in events])
    deltas_pct = np.array([e.delta_hr_pct for e in events])
    durations = np.array([e.total_duration for e in events])
    aucs = np.array([e.auc for e in events])
    aucs_pct = np.array([e.auc_pct for e in events])
    
    # Spike index
    si = n_events / valid_hours if valid_hours > 0 else 0
    
    # Magnitude
    mean_d = float(np.mean(deltas))
    median_d = float(np.median(deltas))
    p90_d = float(np.percentile(deltas, 90))
    mean_d_pct = float(np.mean(deltas_pct))
    
    # Burden
    tab = float(np.sum(aucs)) / valid_hours if valid_hours > 0 else 0
    tab_norm = float(np.sum(aucs_pct)) / valid_hours if valid_hours > 0 else 0
    
    # Duration
    med_dur = float(np.median(durations))
    mean_dur = float(np.mean(durations))
    
    # Temporal distribution
    midpoint = total_seconds // 2
    first_half = [e for e in events if e.peak_idx < midpoint]
    second_half = [e for e in events if e.peak_idx >= midpoint]
    
    first_valid = np.sum(valid[:midpoint]) / 3600.0
    second_valid = np.sum(valid[midpoint:]) / 3600.0
    
    first_si = len(first_half) / first_valid if first_valid > 0 else 0
    second_si = len(second_half) / second_valid if second_valid > 0 else 0
    temporal_ratio = second_si / first_si if first_si > 0 else 0
    
    # Periodicity (CV of inter-spike intervals)
    if n_events > 2:
        intervals = np.array([events[i+1].onset_idx - events[i].onset_idx 
                             for i in range(n_events - 1)])
        interval_cv = float(np.std(intervals) / np.mean(intervals)) if np.mean(intervals) > 0 else 0
    else:
        interval_cv = 0
    
    # Morphology distribution
    types = [e.spike_type for e in events]
    pct_a = types.count(SpikeType.A_TACHYBRADY) / n_events * 100
    pct_b = types.count(SpikeType.B_SUSTAINED) / n_events * 100
    pct_c = types.count(SpikeType.C_BRIEF) / n_events * 100
    pct_d = types.count(SpikeType.D_GRADUAL) / n_events * 100
    
    # ================================================================
    # COMPOSITE SEVERITY SCORE (0-100)
    # ================================================================
    # Component 1: Frequency (0-30 points)
    # 0/hr = 0, 5/hr = 5, 15/hr = 15, 30/hr = 25, 50+/hr = 30
    freq_score = min(30, si * 0.6)
    
    # Component 2: Magnitude burden (0-30 points)
    # Based on TAB (total autonomic burden)
    # <100 bpm·sec/hr = mild, 100-500 = moderate, 500-1500 = severe, >1500 = very severe
    burden_score = min(30, tab / 50.0)
    
    # Component 3: Spike intensity (0-20 points)
    # Based on P90 delta (how bad the worst spikes are)
    # <8 bpm = mild, 8-15 = moderate, 15-25 = severe, >25 = very severe
    intensity_score = min(20, p90_d * 0.8)
    
    # Component 4: Pattern factors (0-20 points)
    # Periodicity penalty: regular spacing suggests untreated pathology
    periodicity_pts = 5 if interval_cv < 0.5 else 0
    # Awakening penalty: high % Type B = frequent full awakenings
    awakening_pts = min(10, pct_b / 5.0)
    # No recovery penalty: events without bradycardic rebound
    no_recovery_events = sum(1 for e in events if e.overshoot < 1)
    no_recovery_pct = no_recovery_events / n_events * 100 if n_events > 0 else 0
    recovery_pts = min(5, no_recovery_pct / 20.0)
    pattern_score = periodicity_pts + awakening_pts + recovery_pts
    
    total_score = freq_score + burden_score + intensity_score + pattern_score
    total_score = min(100, max(0, total_score))
    
    # Severity label
    if total_score < 15:
        label = "normal"
    elif total_score < 30:
        label = "mild"
    elif total_score < 50:
        label = "moderate"
    elif total_score < 75:
        label = "severe"
    else:
        label = "very severe"
    
    return NightSummary(
        recording_hours=round(recording_hours, 2),
        valid_hours=round(valid_hours, 2),
        quality_pct=round(quality * 100, 1),
        total_spikes=n_events,
        spike_index=round(si, 1),
        mean_delta_hr=round(mean_d, 1),
        median_delta_hr=round(median_d, 1),
        p90_delta_hr=round(p90_d, 1),
        mean_delta_hr_pct=round(mean_d_pct, 1),
        total_autonomic_burden=round(tab, 0),
        tab_normalized=round(tab_norm, 0),
        median_duration=round(med_dur, 1),
        mean_duration=round(mean_dur, 1),
        first_half_si=round(first_si, 1),
        second_half_si=round(second_si, 1),
        temporal_ratio=round(temporal_ratio, 2),
        spike_interval_cv=round(interval_cv, 2),
        pct_type_a=round(pct_a, 1),
        pct_type_b=round(pct_b, 1),
        pct_type_c=round(pct_c, 1),
        pct_type_d=round(pct_d, 1),
        severity_score=round(total_score, 1),
        severity_label=label
    )


# ============================================================
# VISUALIZATION
# ============================================================

def plot_night(hr_smooth, baseline, valid, events, summary, output_path=None, start_time=None):
    """Plot full night with detected events."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches
        import matplotlib.dates as mdates
        from datetime import datetime, timedelta
    except ImportError:
        print("matplotlib not installed — skipping plot")
        return
    
    n = len(hr_smooth)
    use_dates = False
    if start_time:
        try:
            if isinstance(start_time, str):
                dt_start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            else:
                dt_start = start_time
            t_axis = [dt_start + timedelta(seconds=i) for i in range(n)]
            use_dates = True
        except:
            t_axis = np.arange(n) / 3600.0
    else:
        t_axis = np.arange(n) / 3600.0
    
    fig, axes = plt.subplots(3, 1, figsize=(20, 10), 
                              gridspec_kw={'height_ratios': [3, 1, 1]},
                              sharex=True)
    fig.suptitle(
        f"HR Spike Analysis — SI: {summary.spike_index}/hr | "
        f"TAB: {summary.total_autonomic_burden:.0f} bpm·s/hr | "
        f"Score: {summary.severity_score:.0f}/100 ({summary.severity_label})",
        fontsize=14, fontweight='bold'
    )
    
    # --- Panel 1: HR trace with baseline and events ---
    ax = axes[0]
    
    # HR trace (grey out invalid segments)
    hr_plot = hr_smooth.copy()
    hr_plot[~valid] = np.nan
    ax.plot(t_axis, hr_plot, color='#333333', linewidth=0.5, alpha=0.8, label='HR')
    ax.plot(t_axis, baseline, color='#2196F3', linewidth=1.5, alpha=0.8, label='Baseline (P25)')
    
    # Color events by type
    type_colors = {
        SpikeType.A_TACHYBRADY: '#FF5722',
        SpikeType.B_SUSTAINED: '#9C27B0',
        SpikeType.C_BRIEF: '#FFC107',
        SpikeType.D_GRADUAL: '#4CAF50',
        SpikeType.UNCLASSIFIED: '#607D8B',
    }
    
    for event in events:
        color = type_colors.get(event.spike_type, '#607D8B')
        if use_dates:
            t_start = dt_start + timedelta(seconds=event.onset_idx)
            t_end = dt_start + timedelta(seconds=event.end_idx)
            t_peak = dt_start + timedelta(seconds=event.peak_idx)
        else:
            t_start = event.onset_idx / 3600.0
            t_end = event.end_idx / 3600.0
            t_peak = event.peak_idx / 3600.0
        
        # Highlight region
        if use_dates:
            t_start = mdates.date2num(t_start)
            t_end = mdates.date2num(t_end)
        ax.axvspan(t_start, t_end, alpha=0.15, color=color)
        
        # Mark peak
        if use_dates:
            t_peak = mdates.date2num(t_peak)
        ax.plot(t_peak, event.peak_hr, 'v', color=color, markersize=4, alpha=0.7)
    
    ax.set_ylabel('Heart Rate (bpm)')
    ax.set_ylim(40, max(110, np.nanmax(hr_plot) + 5))
    ax.legend(loc='upper right', fontsize=9)
    ax.grid(True, alpha=0.3)
    
    # --- Panel 2: Delta from baseline ---
    ax2 = axes[1]
    delta = hr_smooth - baseline
    delta[~valid] = np.nan
    ax2.fill_between(t_axis, 0, np.maximum(delta, 0), color='#FF5722', alpha=0.4)
    ax2.fill_between(t_axis, 0, np.minimum(delta, 0), color='#2196F3', alpha=0.3)
    ax2.axhline(y=0, color='black', linewidth=0.5)
    ax2.set_ylabel('Δ HR (bpm)')
    ax2.set_ylim(-15, max(30, np.nanmax(delta) + 5))
    ax2.grid(True, alpha=0.3)
    
    # --- Panel 3: Event severity timeline ---
    ax3 = axes[2]
    for event in events:
        if use_dates:
            t_peak = dt_start + timedelta(seconds=event.peak_idx)
            # bar width in days for mdates
            b_width = 0.005 / 24.0
            t_peak = mdates.date2num(t_peak)
        else:
            t_peak = event.peak_idx / 3600.0
            b_width = 0.005
        color = type_colors.get(event.spike_type, '#607D8B')
        ax3.bar(t_peak, event.severity_score, width=b_width, color=color, alpha=0.7)
    
    ax3.set_ylabel('Event Score')
    if use_dates:
        ax3.set_xlabel('Time')
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax3.xaxis.set_major_locator(mdates.HourLocator())
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')
    else:
        ax3.set_xlabel('Time (hours from recording start)')
    ax3.set_ylim(0, 10)
    ax3.grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Plot saved: {output_path}")
    else:
        plt.savefig('/tmp/hr_spikes.png', dpi=150, bbox_inches='tight')
        print("Plot saved: /tmp/hr_spikes.png")
    plt.close()


# ============================================================
# DATA LOADING
# ============================================================

def load_data(filepath: str, source: str = 'auto') -> np.ndarray:
    """
    Load HR data from various formats.
    Returns 1-second HR array.
    """
    # Try to detect format
    with open(filepath, 'r') as f:
        header = f.readline().strip()
        first_line = f.readline().strip()
    
    # O2Ring CSV: typically has columns like timestamp, spo2, pulse_rate, motion
    # Polar Sensor Logger: timestamp, hr, rr_intervals
    # Simple format: just one column of HR values (one per second)
      
    
    # Try pandas if available
    try:
        import pandas as pd
        df = pd.read_csv(filepath)
        
        # Look for HR/pulse rate column
        hr_cols = [c for c in df.columns if any(k in c.lower() for k in 
                   ['pulse', 'heart', 'hr', 'bpm', 'rate'])]
        
        if hr_cols:
            hr_col = hr_cols[0]
            
            hr = df[hr_col].values.astype(float)
        elif df.shape[1] == 1:
            hr = df.iloc[:, 0].values.astype(float)
        else:
            # If it has 'rr' column, convert from RR intervals
            rr_cols = [c for c in df.columns if 'rr' in c.lower()]
            if rr_cols:
                rr = df[rr_cols[0]].values.astype(float)
                hr = 60000.0 / rr  # convert ms to bpm
            else:
                print(f"Available columns: {list(df.columns)}")
                raise ValueError("Could not identify HR column. Please specify.")
        
        return hr
        
    except ImportError:
        # Fallback: simple CSV parsing
        hr_values = []
        with open(filepath, 'r') as f:
            reader = csv.reader(f)
            header_row = next(reader)
            for row in reader:
                try:
                    # Try last numeric column as HR
                    for val in reversed(row):
                        v = float(val)
                        if 30 <= v <= 250:
                            hr_values.append(v)
                            break
                except (ValueError, IndexError):
                    hr_values.append(0)
        
        return np.array(hr_values)


# ============================================================
# MAIN — GENERATE SYNTHETIC DATA FOR DEMO
# ============================================================

def generate_demo_data(hours=8.0, seed=42):
    """
    Generate realistic synthetic sleep HR data for testing.
    Mimics a UARS patient with hyperadrenergic POTS:
    - Baseline 55-65 bpm with stage-related drift
    - 20-30 spikes/hour of varying magnitude
    - Mix of Type A, B, C, D events
    - Some artifact periods
    """
    np.random.seed(seed)
    n = int(hours * 3600)
    t = np.arange(n)
    
    # Baseline: slow sinusoidal drift (sleep stage cycling ~90 min)
    baseline = 58 + 4 * np.sin(2 * np.pi * t / (90 * 60))
    # Add slight overnight trend (HR drops slightly)
    baseline += 3 * np.exp(-t / (2 * 3600))
    
    hr = baseline.copy()
    
    # Generate spikes
    spike_rate = 25  # per hour
    n_spikes = int(spike_rate * hours)
    spike_times = np.sort(np.random.randint(300, n - 300, n_spikes))
    
    # Remove spikes too close together
    filtered = [spike_times[0]]
    for st in spike_times[1:]:
        if st - filtered[-1] > 30:
            filtered.append(st)
    spike_times = filtered
    
    for st in spike_times:
        magnitude = np.random.choice(
            [5, 8, 12, 18, 25, 35],
            p=[0.15, 0.25, 0.25, 0.20, 0.10, 0.05]
        )
        rise_time = np.random.randint(3, 12)
        fall_time = np.random.randint(8, 40)
        
        # Create spike shape
        for j in range(rise_time):
            idx = st + j
            if idx < n:
                hr[idx] += magnitude * (j / rise_time)
        for j in range(fall_time):
            idx = st + rise_time + j
            if idx < n:
                decay = magnitude * np.exp(-j / (fall_time * 0.4))
                overshoot = -2 * np.exp(-(j - fall_time * 0.6)**2 / (fall_time * 0.3)**2)
                hr[idx] += decay + overshoot
    
    # Add noise
    hr += np.random.normal(0, 0.8, n)
    
    # Add some artifact
    artifact_start = int(3.5 * 3600)
    hr[artifact_start:artifact_start + 20] = np.random.randint(30, 200, 20)
    
    return hr


def main():
    parser = argparse.ArgumentParser(description='HR Spike Detection for Sleep Data')
    parser.add_argument('input', nargs='?', help='Input CSV file (or "demo" for synthetic data)')
    parser.add_argument('--source', choices=['o2ring', 'polar', 'auto'], default='auto')
    parser.add_argument('--preset', choices=['sensitive', 'standard', 'specific', 'clinical'],
                       default='standard')
    parser.add_argument('--output-dir', default='.', help='Output directory')
    parser.add_argument('--plot', action='store_true', default=True, help='Generate plot')
    parser.add_argument('--no-plot', action='store_true', help='Skip plot')
    
    # Allow custom thresholds
    parser.add_argument('--onset-abs', type=float, help='Override onset absolute threshold')
    parser.add_argument('--onset-rel', type=float, help='Override onset relative threshold')
    parser.add_argument('--min-delta', type=float, help='Override minimum delta')
    
    args = parser.parse_args()
    
    # Load data
    if args.input is None or args.input == 'demo':
        print("=" * 60)
        print("DEMO MODE: Using synthetic sleep HR data")
        print("=" * 60)
        hr_raw = generate_demo_data()
    else:
        print(f"Loading: {args.input}")
        hr_raw = load_data(args.input, args.source)
    
    print(f"Loaded {len(hr_raw)} samples ({len(hr_raw)/3600:.1f} hours)")
    
    # Select preset
    preset_enum = Preset(args.preset)
    params = PRESETS[preset_enum].copy()
    
    # Apply any overrides
    if args.onset_abs is not None:
        params['onset_abs'] = args.onset_abs
    if args.onset_rel is not None:
        params['onset_rel'] = args.onset_rel
    if args.min_delta is not None:
        params['min_delta'] = args.min_delta
    
    print(f"Preset: {args.preset}")
    print(f"Parameters: {json.dumps(params, indent=2)}")
    
    # Run pipeline
    print("\n--- Stage 1: Preprocessing ---")
    hr_smooth, valid = preprocess(hr_raw)
    quality = np.sum(valid) / len(valid) * 100
    print(f"Quality: {quality:.1f}% valid samples")
    
    print("\n--- Stage 2: Computing baseline ---")
    baseline = compute_baseline(hr_smooth, valid)
    print(f"Baseline range: {np.nanmin(baseline):.0f} - {np.nanmax(baseline):.0f} bpm")
    
    print("\n--- Stage 3: Detecting spikes ---")
    events = detect_spikes(hr_smooth, baseline, valid, params)
    print(f"Detected {len(events)} events")
    
    print("\n--- Stage 5: Computing summary ---")
    summary = compute_summary(events, valid, len(hr_raw))
    
    # Print summary
    print("\n" + "=" * 60)
    print("NIGHT SUMMARY")
    print("=" * 60)
    print(f"Recording:    {summary.recording_hours:.1f} hrs "
          f"({summary.valid_hours:.1f} valid, {summary.quality_pct:.0f}%)")
    print(f"")
    print(f"COUNTS:")
    print(f"  Total spikes:    {summary.total_spikes}")
    print(f"  Spike Index:     {summary.spike_index}/hr")
    print(f"  1st half SI:     {summary.first_half_si}/hr")
    print(f"  2nd half SI:     {summary.second_half_si}/hr")
    print(f"  Temporal ratio:  {summary.temporal_ratio}")
    print(f"")
    print(f"MAGNITUDE:")
    print(f"  Mean ΔHR:        {summary.mean_delta_hr} bpm ({summary.mean_delta_hr_pct}%)")
    print(f"  Median ΔHR:      {summary.median_delta_hr} bpm")
    print(f"  P90 ΔHR:         {summary.p90_delta_hr} bpm")
    print(f"")
    print(f"BURDEN:")
    print(f"  Total Autonomic Burden:  {summary.total_autonomic_burden:.0f} bpm·sec/hr")
    print(f"  TAB (normalized):        {summary.tab_normalized:.0f} %·sec/hr")
    print(f"")
    print(f"DURATION:")
    print(f"  Median duration:  {summary.median_duration}s")
    print(f"  Mean duration:    {summary.mean_duration}s")
    print(f"")
    print(f"MORPHOLOGY:")
    print(f"  Type A (tachy-brady):  {summary.pct_type_a}%")
    print(f"  Type B (sustained):    {summary.pct_type_b}%")
    print(f"  Type C (brief):        {summary.pct_type_c}%")
    print(f"  Type D (gradual):      {summary.pct_type_d}%")
    print(f"")
    print(f"PERIODICITY:")
    print(f"  Inter-spike CV:  {summary.spike_interval_cv}")
    cv_interp = ("periodic — suspect PLMs/periodic breathing" if summary.spike_interval_cv < 0.5
                 else "regular" if summary.spike_interval_cv < 1.0
                 else "irregular/random — multi-cause or spontaneous")
    print(f"  Interpretation:  {cv_interp}")
    print(f"")
    print(f"{'=' * 40}")
    print(f"  SEVERITY SCORE:  {summary.severity_score}/100")
    print(f"  SEVERITY LABEL:  {summary.severity_label.upper()}")
    print(f"{'=' * 40}")
    
    # Save events CSV
    if events:
        events_path = f"{args.output_dir}/spike_events.csv"
        with open(events_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'onset_sec', 'peak_sec', 'end_sec', 'baseline_hr', 'peak_hr',
                'delta_hr', 'delta_hr_pct', 'rise_time', 'fall_time',
                'total_duration', 'auc', 'rise_slope', 'overshoot',
                'type', 'severity_score'
            ])
            for e in events:
                writer.writerow([
                    e.onset_idx, e.peak_idx, e.end_idx, e.baseline_hr, e.peak_hr,
                    e.delta_hr, e.delta_hr_pct, e.rise_time, e.fall_time,
                    e.total_duration, e.auc, e.rise_slope, e.overshoot,
                    e.spike_type.value, round(e.severity_score, 1)
                ])
        print(f"\nEvents saved: {events_path}")
    
    # Save summary JSON
    summary_path = f"{args.output_dir}/night_summary.json"
    with open(summary_path, 'w') as f:
        json.dump(asdict(summary), f, indent=2)
    print(f"Summary saved: {summary_path}")
    
    # Plot
    if args.plot and not args.no_plot:
        plot_path = f"{args.output_dir}/hr_spikes_plot.png"
        plot_night(hr_smooth, baseline, valid, events, summary, plot_path)
    
    return summary, events


if __name__ == '__main__':
    main()
