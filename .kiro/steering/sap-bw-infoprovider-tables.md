# SAP BW InfoProvider / ADSO Metadata Tables

Reference for the database tables that store InfoProvider (ADSO, InfoCube, DSO) definitions in SAP BW.

## Advanced DataStore Object (ADSO)

The ADSO is the primary InfoProvider in BW/4HANA, replacing InfoCubes and classic DSOs.

### TLOGO Object Types
- `ADSO` — Advanced DataStore Object (active/modified version)
- `DDSO` — Advanced DataStore Object (D-version / content delivery)
- `ODSO` — Classic DataStore Object (legacy)
- `CUBE` — InfoCube (legacy, replaced by ADSO in BW/4HANA)

### RSOADSO (ADSO Definition)

Main definition table for Advanced DataStore Objects.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `ADSONM` (key) | ADSO technical name | CHAR 30 |
| `OBJVERS` (key) | Object version (A=active, M=modified) | CHAR 1 |
| `ADSOTP` | ADSO type/scenario | CHAR 4 |
| `INFOAREA` | InfoArea assignment | CHAR 30 |
| `OBJSTAT` | Object status | CHAR 3 |
| `OWNER` | Owner | CHAR 12 |
| `TSTPNM` | Last changed by | CHAR 12 |
| `TIMESTMP` | Timestamp of last change | DEC 15 |

### RSOADSOT (ADSO Texts)

| Field | Description | Type/Length |
|-------|-------------|------------|
| `ADSONM` (key) | ADSO name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `LANGU` (key) | Language | LANG 1 |
| `TXTLG` | Long text description | CHAR 60 |

### RSOADSOIOBJ (ADSO Field/InfoObject Assignment)

Maps InfoObjects and fields to the ADSO structure.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `ADSONM` (key) | ADSO name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `POSIT` (key) | Position in the structure | NUMC 4 |
| `IOBJNM` | InfoObject name (if field is an InfoObject) | CHAR 30 |
| `FIELDNM` | Field name (if not an InfoObject) | CHAR 30 |
| `KEYFL` | Key field flag | CHAR 1 |

## ADSO Generated Database Tables

When an ADSO is activated, three core tables are generated:

| Table Pattern | Purpose |
|---------------|---------|
| `/BIC/A<ADSONM>1` | Inbound table (new data queue) |
| `/BIC/A<ADSONM>2` | Active data table |
| `/BIC/A<ADSONM>3` | Change log table |

For SAP-delivered ADSOs, prefix is `/BI0/` instead of `/BIC/`.

Note: Not all three tables are always used. The modeling properties determine which tables are active:
- Write-optimized: Only inbound table (1)
- Standard: Inbound (1) + Active (2) + Change log (3)
- Direct update: Only active table (2)

## Classic InfoCube Tables (Legacy)

| Table | Purpose |
|-------|---------|
| `RSDCUBE` | InfoCube directory |
| `RSDCUBET` | InfoCube texts |
| `RSDCUBEIOBJ` | InfoCube dimension/key figure assignment |
| `RSDDIME` | Dimension definitions |
| `RSDDIMEIOBJ` | InfoObjects in dimensions |

### Generated Fact/Dimension Tables (InfoCube)
- `/BIC/F<CUBENAME>` — Fact table (compressed)
- `/BIC/E<CUBENAME>` — Fact table (uncompressed)
- `/BIC/D<CUBENAME><DIM_ID>` — Dimension tables

## Classic DSO Tables (Legacy)

| Table | Purpose |
|-------|---------|
| `RSDODSO` | Classic DSO directory |
| `RSDODSOT` | Classic DSO texts |
| `RSDODSOIOBJ` | DSO field assignment |

### Generated Tables (Classic DSO)
- `/BIC/A<DSONAME>00` — Active data table
- `/BIC/A<DSONAME>40` — Change log
- `/BIC/A<DSONAME>CL` — Activation queue

## InfoArea Tables

| Table | Purpose |
|-------|---------|
| `RSINFOAREA` | InfoArea definition |
| `RSINFOAREAT` | InfoArea texts (language-dependent) |

## Relationships

```
RSINFOAREA (InfoArea)
  └── RSOADSO / RSDCUBE / RSDODSO (InfoProviders assigned to area)
        └── RSOADSOIOBJ / RSDCUBEIOBJ (Fields/InfoObjects)
              └── RSDIOBJ (InfoObject definition)
```
