# INAES Trial Datasets

These trial files are derived from real INAES-compatible file structures and have been anonymized for public software testing.
They are intended only for trying the INAES desktop workflows and must not be interpreted as publishable scientific data.

## Files

- `INAES_trial_analyzed_curves.csv`  
  Use with **Data Upload -> Upload analyzed file**.  
  This file contains all analyzed-curve rows for three anonymized samples: `S1`, `S2`, and `S3`.

- `INAES_trial_metadata.csv`  
  Optional metadata file for testing **Boxplots** and **Correlations** after loading `INAES_trial_analyzed_curves.csv`.  
  Sample IDs and location fields have been anonymized to `S1`/`S2`/`S3` and `Site1`/`Site2`/`Site3`.

- `INAES_trial_raw_micropinguin.csv`  
  Use with **Data Upload -> Analyze RAW file**.  
  This is a Micro-PINGUIN-style RAW export with semicolon-separated columns:
  `Well Name`, `Partition`, `Content`, `Freeze Temp`, and `Frozen Fraction`.  
  Content names have been anonymized to `RAW_DEMO_SAMPLE` and `MilliQ_RAW_DEMO`.

## Suggested RAW Mapping

When loading `INAES_trial_raw_micropinguin.csv`, use automatic mapping or select:

- Sample: `Content`
- Freezing.temperature: `Freeze Temp`
- FF: `Frozen Fraction`

Suggested RAW analysis parameters:

- Droplet volume: `30 uL`
- N0: `384`
- Auto-dilution from sample name: enabled
- Auto-control detection from sample name: enabled