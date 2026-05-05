"""
analyze.py

This script performs statistical correlation analysis to compare the outputs of
different O2 scoring engines (Sensitive, Standard, Specific, Clinical).
It measures how similarly the engines rank nights based on metrics like
Total Autonomic Burden (TAB) and overall Severity Score using Spearman Rank correlation.
This helps to understand if the engines agree on which nights are "worst" vs "best".
"""
import pandas as pd
import scipy.stats as stats

# Load data
df = pd.read_csv('../data/detector_results.csv')

# Filter out very short nights or missing data
df = df[df['hours_exact'] > 2.0]

print("=== CORRELATION ANALYSIS ===")
print(f"Analyzing {len(df)} nights with >2 hours of data.\n")

presets = ['sensitive', 'standard', 'specific', 'clinical']

print("--- TAB Correlation (Spearman Rank) ---")
print("How similarly do they rank your worst nights vs best nights based on Burden?")
tab_cols = ['tab'] + [f'tab_{p}' for p in presets[1:]]

for i in range(len(tab_cols)):
    for j in range(i+1, len(tab_cols)):
        col1 = tab_cols[i]
        col2 = tab_cols[j]
        if col1 in df.columns and col2 in df.columns:
            corr, p = stats.spearmanr(df[col1], df[col2])
            print(f"{col1} vs {col2}: {corr:.3f}")

print("\n--- Score Correlation (Spearman Rank) ---")
print("How similarly do they assign an overall severity score (0-100)?")
score_cols = ['score'] + [f'score_{p}' for p in presets[1:]]

for i in range(len(score_cols)):
    for j in range(i+1, len(score_cols)):
        col1 = score_cols[i]
        col2 = score_cols[j]
        if col1 in df.columns and col2 in df.columns:
            corr, p = stats.spearmanr(df[col1], df[col2])
            print(f"{col1} vs {col2}: {corr:.3f}")

print("\n--- TAB Major A Correlation ---")
if 'tab_major_a' in df.columns and 'tab' in df.columns:
    corr, p = stats.spearmanr(df['tab'], df['tab_major_a'])
    print(f"Sensitive TAB vs Major A TAB: {corr:.3f}")
    if 'tab_specific' in df.columns:
        corr_spec, _ = stats.spearmanr(df['tab_specific'], df['tab_major_a'])
        print(f"Specific TAB vs Major A TAB: {corr_spec:.3f}")
