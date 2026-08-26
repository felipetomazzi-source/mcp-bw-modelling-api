# SAP BW Data Transfer Process (DTP) Tables

Reference for the database tables that store DTP definitions and configuration in SAP BW.

## RSBKDTP (DTP Header Data)

Main table containing the DTP definition including source, target, and processing settings.

| Field | Description | Type/Length | Values |
|-------|-------------|------------|--------|
| `DTP` (key) | DTP ID (technical name, e.g. DTP_...) | CHAR 30 | |
| `OBJVERS` (key) | Object version (A=active, M=revised) | CHAR 1 | |
| `SRC` | Source object name | CHAR 45 | |
| `SRCTP` | Source object type | CHAR 6 | DTASRC, CUBE, MPRO, ODSO, ISET, DTP, IOBJA, IOBJT, IOBJH |
| `SRCTLOGO` | Source TLOGO type | CHAR 4 | RSDS, ODSO, CUBE, TRFN, DTPA, IOBJ, etc. |
| `TGT` | Target object name | CHAR 45 | |
| `TGTTP` | Target object type | CHAR 6 | CUBE, ODSO, IOBJA, IOBJT, IOBJH, DEST |
| `TGTTLOGO` | Target TLOGO type | CHAR 4 | ODSO, CUBE, IOBJ, DEST, etc. |
| `UPDMODE` | Extraction mode | CHAR 2 | F=Full, D=Delta, I=Init Non-Cumulative |
| `PROCESSMODE` | Processing mode | CHAR 1 | (blank)=Serial background, 1=Serial+parallel, 3=Parallel, 4=Dialog debug |
| `ERRORHANDLING` | Error handling type | CHAR 1 | (blank)=Deactivated, 1=Update valid/red, 2=Update valid/green |
| `DTPTYPE` | DTP type | CHAR 4 | (blank)=Standard, REAL=Real-time, REMT=Direct access, EDTP=Error DTP |
| `AUTOQUALOK` | Auto set quality to OK | CHAR 1 | X=Yes |
| `OBJSTAT` | Object status (ACT, INA) | CHAR 3 | |
| `OWNER` | Owner | CHAR 12 | |
| `TSTPNM` | Last changed by | CHAR 12 | |
| `TIMESTMP` | UTC timestamp | DEC 15 | |

## RSBKDTPFILT (DTP Filter Conditions)

Stores the filter/selection conditions configured on a DTP.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `DTP` (key) | DTP ID | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `FILTNO` (key) | Filter number | NUMC 4 |
| `IOBJNM` | InfoObject used as filter | CHAR 30 |
| `SIGN` | Sign (I=Include, E=Exclude) | CHAR 1 |
| `OPTION` | Option (EQ, BT, CP, etc.) | CHAR 2 |
| `LOW` | Low value | CHAR 60 |
| `HIGH` | High value | CHAR 60 |

## RSBKDTPSTAT (DTP Status Information)

Runtime status of DTP executions.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `DTP` (key) | DTP ID | CHAR 30 |
| `REQUEST` (key) | Request ID | CHAR 30 |
| `TSTATE` | Request status | CHAR 1 |

## RSBKDTPT (DTP Texts)

Language-dependent DTP descriptions.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `DTP` (key) | DTP ID | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `LANGU` (key) | Language key | LANG 1 |
| `TXTLG` | Long description | CHAR 60 |

## Related Tables

| Table | Purpose |
|-------|---------|
| `RSBKDTPH` | DTP header (alternate/historical) |
| `RSBKDTPLOG` | DTP execution log entries |
| `RSBKDTPSTP` | DTP processing steps |
| `RSBKREQUEST` | Request information for DTP loads |
| `RSREQDONE` | Completed requests tracking |

## DTP and Transformation Relationship

A DTP always references a transformation (TRFN) between its source and target:
- The transformation is identified by source type + source name + target type + target name
- Table `RSTRAN` stores the transformation definition
- The DTP triggers the transformation during data load execution

## DTP Naming Convention

DTP technical names follow the pattern: `DTP_<GUID>` where GUID is a system-generated identifier.
