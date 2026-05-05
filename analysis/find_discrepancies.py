"""
find_discrepancies.py

This script compares the different scoring engines (Sensitive, Standard, Specific, Clinical)
by calculating percentile ranks for each session and identifying the nights where the algorithms
have the largest disagreements.
It outputs the top nights with the largest percentile shift in Severity Score and Total Autonomic Burden (TAB),
highlighting edge cases where different scoring criteria lead to vastly different conclusions.
"""
import pandas as pd

# Load data
df = pd.read_csv('../data/detector_results.csv')

# Filter out very short nights
df = df[df['hours_exact'] > 4.0].copy()

# Calculate percentile ranks for all scoring algos
df['rank_sensitive'] = df['score'].rank(pct=True)
df['rank_standard'] = df['score_standard'].rank(pct=True)
df['rank_specific'] = df['score_specific'].rank(pct=True)
df['rank_clinical'] = df['score_clinical'].rank(pct=True)

# Find the max and min percentile rank for each night across all 4 algos
rank_cols = ['rank_sensitive', 'rank_standard', 'rank_specific', 'rank_clinical']
df['max_rank'] = df[rank_cols].max(axis=1)
df['min_rank'] = df[rank_cols].min(axis=1)
df['max_shift_abs'] = df['max_rank'] - df['min_rank']

# Let's find which algorithm gave the min and which gave the max
df['algo_min'] = df[rank_cols].idxmin(axis=1).str.replace('rank_', '')
df['algo_max'] = df[rank_cols].idxmax(axis=1).str.replace('rank_', '')

df_sorted = df.sort_values(by='max_shift_abs', ascending=False)

print("=== Top 5 Nights with BIGGEST DISAGREEMENT Across ALL Algorithms ===\n")

for i, row in df_sorted.head(5).iterrows():
    print(f"Date/File: {row['file']}")
    
    sens_pct = int(row['rank_sensitive'] * 100)
    std_pct = int(row['rank_standard'] * 100)
    spec_pct = int(row['rank_specific'] * 100)
    clin_pct = int(row['rank_clinical'] * 100)
    
    min_algo = row['algo_min'].upper()
    max_algo = row['algo_max'].upper()
    shift = int(row['max_shift_abs'] * 100)
    
    print(f"  Ranks -> Sensitive: {sens_pct}th | Standard: {std_pct}th | Specific: {spec_pct}th | Clinical: {clin_pct}th")
    print(f"  -> Disagreement of {shift} percentiles! ({min_algo} thought it was {int(row['min_rank']*100)}th pct, but {max_algo} thought it was {int(row['max_rank']*100)}th pct)")
    print(f"  Notes: {row['label']}")
    print("-" * 60)

# We can also do the same for TAB
df['tab_rank_sensitive'] = df['tab'].rank(pct=True)
df['tab_rank_standard'] = df['tab_standard'].rank(pct=True)
df['tab_rank_specific'] = df['tab_specific'].rank(pct=True)
df['tab_rank_clinical'] = df['tab_clinical'].rank(pct=True)

tab_rank_cols = ['tab_rank_sensitive', 'tab_rank_standard', 'tab_rank_specific', 'tab_rank_clinical']
df['tab_max_shift_abs'] = df[tab_rank_cols].max(axis=1) - df[tab_rank_cols].min(axis=1)

df_tab_sorted = df.sort_values(by='tab_max_shift_abs', ascending=False)

print("\n=== Top 3 Nights with Biggest DISAGREEMENT in TAB (Burden) ===\n")
for i, row in df_tab_sorted.head(3).iterrows():
    sens_pct = int(row['tab_rank_sensitive'] * 100)
    std_pct = int(row['tab_rank_standard'] * 100)
    spec_pct = int(row['tab_rank_specific'] * 100)
    clin_pct = int(row['tab_rank_clinical'] * 100)
    
    shift = int(row['tab_max_shift_abs'] * 100)
    print(f"Date/File: {row['file']}")
    print(f"  TAB Ranks -> Sensitive: {sens_pct}th | Standard: {std_pct}th | Specific: {spec_pct}th | Clinical: {clin_pct}th")
    print(f"  -> Disagreement of {shift} percentiles!")
    print(f"  Notes: {row['label']}")
    print("-" * 60)
