# SAP BW Transformation Source Code Tables

Reference for the database tables that store transformation routine source code in SAP BW.

## Core Table: RSAABAP (ABAP Routine Source Code)

Holds the actual lines of ABAP source code for all transformation routines.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `CODEID` (key) | GUID identifying the routine | CHAR 25 |
| `OBJVERS` (key) | Object version (A = active, M = modified) | CHAR 1 |
| `LINE_NO` (key) | Line number in the routine | NUMC 6 |
| `LINE` | One line of ABAP source code | CHAR 72 |

Each routine is identified by a `CODEID`, and the source is stored one line per row.

## RSTRAN (Transformation Header)

Contains the transformation definition and references to routine CODEIDs:

| Column | Purpose |
|--------|---------|
| `STARTROUTINE` | CODEID of the start routine |
| `ENDROUTINE` | CODEID of the end routine |
| `EXPERT` | CODEID of the expert routine |
| `GLBCODE` | CODEID for global declarations (shared types/variables) |
| `TRANPROG` | Generated program name |

## RSTRANSTEPROUT (Field-Level Routine References)

Stores the CODEID for field-level (rule step) routines. Key fields include the transformation ID, rule ID, step ID, and the `CODEID` linking back to `RSAABAP`.

## RSTRANRULE (Transformation Rules)

Defines individual transformation rules (one per target field). The `RULETYPE` field indicates whether the rule uses a routine, direct assignment, formula, constant, etc. If `RULETYPE` indicates a routine, the corresponding CODEID is found in `RSTRANSTEPROUT`.

## Supporting Tables

| Table | Purpose |
|-------|---------|
| `RSAROUT` | Check table for routine IDs (validates CODEID) |
| `RSAABAPINV` | Inverse dependency - tracks which objects reference which CODEID |
| `RSTRANRULESTEP` | Rule step details (RULEID, STEPID) |
| `RSTRANINFO` | Header/descriptive information for transformations |

## Relationship Diagram

```
RSTRAN (header)
  ├── STARTROUTINE → RSAABAP (CODEID)
  ├── ENDROUTINE   → RSAABAP (CODEID)
  ├── EXPERT       → RSAABAP (CODEID)
  └── GLBCODE      → RSAABAP (CODEID)

RSTRANRULE (one row per target field mapping)
  └── RSTRANSTEPROUT (field routine details)
        └── CODEID → RSAABAP (CODEID)
```

## Usage

To extract all transformation source code:
1. Query `RSTRAN` for start/end/expert/global CODEIDs
2. Query `RSTRANSTEPROUT` for field routine CODEIDs
3. Read code lines from `RSAABAP` filtered by CODEID, ordered by `LINE_NO`
