# SAP BW BEx Query Definition Tables

Reference for the database tables that store BEx Query definitions and reporting components in SAP BW.

## RSZCOMPDIR (Reporting Component Directory)

Main directory table for all reusable query components (queries, calculated key figures, restricted key figures, structures, variables).

| Field | Description | Type/Length |
|-------|-------------|------------|
| `COMPUID` (key) | Component UID (unique identifier) | CHAR 25 |
| `OBJVERS` (key) | Object version (A=active, M=revised) | CHAR 1 |
| `COMPID` | Component technical name (user-facing) | CHAR 30 |
| `COMPTYPE` | Component type | CHAR 3 |
| `INFOCUBE` | InfoProvider the component belongs to | CHAR 30 |
| `OWNER` | Owner | CHAR 12 |
| `LASTCHANGEDBY` | Last changed by | CHAR 12 |
| `TIMESTMP` | Timestamp of last change | DEC 15 |
| `CREATED` | Creation timestamp | DEC 15 |
| `OBJSTAT` | Object status | CHAR 3 |

### Component Types (COMPTYPE)
| Value | Description |
|-------|-------------|
| `REP` | Query (report) |
| `CKF` | Calculated key figure |
| `RKF` | Restricted key figure |
| `STR` | Structure |
| `SEL` | Selection |
| `FOR` | Formula |
| `VAR` | Variable |
| `CEL` | Cell definition |
| `FIL` | Filter |
| `AXS` | Axis |
| `EXC` | Exception |
| `CON` | Condition |

## RSZELTDIR (Query Element Directory)

Directory of all elements used inside query components (rows, columns, filters, free characteristics).

| Field | Description | Type/Length |
|-------|-------------|------------|
| `ELTUID` (key) | Element UID | CHAR 25 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `MAPNAME` | Element technical name | CHAR 30 |
| `ELTTYPE` | Element type | CHAR 3 |
| `DEFTP` | Definition type | CHAR 1 |
| `COMPUID` | Parent component UID (links to RSZCOMPDIR) | CHAR 25 |
| `IOBJNM` | InfoObject reference | CHAR 30 |

## RSZELTXREF (Element Cross References)

Defines parent-child relationships between query elements (e.g., which selections belong to which structure).

| Field | Description | Type/Length |
|-------|-------------|------------|
| `TELTUID` (key) | Target element UID (parent) | CHAR 25 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `SELTUID` (key) | Source element UID (child) | CHAR 25 |
| `POSIT` | Position in parent | NUMC 4 |
| `LAYTP` | Layer type (ROW, COL, FRE, FIL) | CHAR 3 |

## RSZRANGE (Selection Ranges / Filter Values)

Stores the actual filter/selection values for query elements.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `ELTUID` (key) | Element UID | CHAR 25 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `POSIT` (key) | Position | NUMC 4 |
| `IOBJNM` | InfoObject | CHAR 30 |
| `SIGN` | Sign (I=Include, E=Exclude) | CHAR 1 |
| `OPT` | Option (EQ, BT, CP, etc.) | CHAR 2 |
| `LOW` | Low value | CHAR 60 |
| `HIGH` | High value | CHAR 60 |

## RSZGLOBV (Global Variables)

Properties and definitions of query variables.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `VNAM` (key) | Variable technical name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `COMPUID` | Component UID | CHAR 25 |
| `IOBJNM` | InfoObject the variable refers to | CHAR 30 |
| `VPARTP` | Variable processing type | CHAR 1 |
| `VINTPUTTP` | Variable input type | CHAR 1 |
| `VDTP` | Variable type | CHAR 1 |

### Variable Types (VDTP)
| Value | Description |
|-------|-------------|
| `1` | Characteristic value variable |
| `2` | Hierarchy variable |
| `3` | Hierarchy node variable |
| `4` | Text variable |
| `5` | Formula variable |

### Variable Processing Types (VPARTP)
| Value | Description |
|-------|-------------|
| `1` | User entry/default |
| `2` | Replacement path |
| `3` | SAP exit (customer exit) |
| `4` | Authorization |
| `5` | Pre-calculated value set |

## RSZCOMPIC (Component in InfoCube)

Maps components to their InfoProvider assignment.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `COMPUID` (key) | Component UID | CHAR 25 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `INFOCUBE` | InfoProvider name | CHAR 30 |

## RSRREPDIR (Query Runtime Directory)

Stores runtime metadata about queries.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `COMPUID` (key) | Component UID | CHAR 25 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `COMPID` | Query technical name | CHAR 30 |
| `INFOCUBE` | InfoProvider | CHAR 30 |
| `GENUNIID` | Generated unique ID | CHAR 30 |

## Relationships

```
RSZCOMPDIR (component directory)
  └── RSZELTDIR (elements in the component)
        ├── RSZELTXREF (cross-references between elements)
        └── RSZRANGE (filter/selection values)

RSZCOMPDIR.COMPUID → RSZGLOBV.COMPUID (variables)
RSZCOMPDIR.COMPUID → RSRREPDIR.COMPUID (runtime info)
```

## How to Find Query Elements

1. Find the query in `RSZCOMPDIR` by `COMPID` (technical name) with `COMPTYPE = 'REP'`
2. Get the `COMPUID`
3. Look up elements in `RSZELTXREF` where `TELTUID` = query's element UID
4. Read element details from `RSZELTDIR`
5. Read filter values from `RSZRANGE`

## Consistency Check

Report `ANALYZE_RSZ_TABLES` checks consistency across the 5 main RSZ tables: RSZCOMPIC, RSZCOMPDIR, RSZELTXREF, RSZGLOBV, RSZELTDIR, plus RSRREPDIR.
