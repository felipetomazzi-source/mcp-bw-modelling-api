# SAP BW Source System and Connection Tables

Reference for the database tables that store source system connections in SAP BW.

## RSBASIDOC (Source System to BW System Assignment)

Primary table in the **source system** that maps source systems to their connected BW systems. Also used in BW for "myself" connections.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `SLOGSYS` (key) | Logical system name of the sending (source) system | CHAR 10 |
| `RLOGSYS` (key) | Logical system name of the receiving (BW) system | CHAR 10 |
| `OBJSTAT` | Object status (should be A) | CHAR 1 |
| `BIDOCTYP` | Generated IDoc type for the connection | CHAR 30 |
| `TSPREFIX` | Two-letter prefix identifying the source system | CHAR 2 |
| `SRCTYPE` | Source system type | CHAR 1 |

### Source System Types (SRCTYPE)
| Value | Description |
|-------|-------------|
| `M` | Myself (own system) |
| `3` | SAP source system (RFC) |
| `D` | Other BW system |
| `F` | File source system |
| `B` | External system (partner/3rd party) |
| `G` | DB Connect |
| `S` | UD Connect |
| `I` | Web Service |

## RSLOGSYSDES (Source System Directory in BW)

Source system entries as known in the BW system. Equivalent to RSBASIDOC but from the BW perspective.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `LOGSYS` (key) | Logical system name | CHAR 10 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `SRCTYPE` | Source system type (same values as above) | CHAR 1 |
| `OBJSTAT` | Object status | CHAR 3 |
| `TSPREFIX` | Transfer structure prefix | CHAR 2 |
| `TSTPNM` | Last changed by | CHAR 12 |
| `TIMESTMP` | Timestamp | DEC 15 |

## RSLOGSYSDEST (RFC Destination Mapping)

Maps logical system names to RFC destinations (when a different RFC connection name is used).

| Field | Description | Type/Length |
|-------|-------------|------------|
| `LOGSYS` (key) | Logical system name | CHAR 10 |
| `DESTINATION` | RFC destination name | CHAR 32 |

Example: If logical system `DEV_100` uses RFC connection `DEV_RFC`, this table maps them.

## RSLOGSYSDESTT (Source System Texts)

Language-dependent descriptions of source systems.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `LOGSYS` (key) | Logical system | CHAR 10 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `LANGU` (key) | Language | LANG 1 |
| `TXTLG` | Description | CHAR 60 |

## TBDLS (Logical System Names - Basis)

SAP Basis table storing all logical system definitions (not BW-specific).

| Field | Description | Type/Length |
|-------|-------------|------------|
| `LOGSYS` (key) | Logical system name | CHAR 10 |
| `SYSNAM` | System name | CHAR 8 |

## T000 (Client Table)

Contains client-level information including the assigned logical system.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `MANDT` (key) | Client number | CHAR 3 |
| `LOGSYS` | Logical system assigned to client | CHAR 10 |

## Connection Verification

To verify a source system connection is intact:
1. Check `RSBASIDOC` in both source and BW systems
2. Verify RFC destination works (SM59)
3. Check `RSLOGSYSDES` in BW for the source entry
4. Optionally check `RSLOGSYSDEST` for custom RFC mapping

## Source System TLOGO

The transport object type for source systems is `LSYS`.

## "Myself" Connection

- The "myself" connection is auto-created when RSA1 is first accessed
- In RSBASIDOC: `SLOGSYS` = `RLOGSYS` (same system)
- `SRCTYPE` = `M`
- Cannot be manually created or deleted via RSA1

## Key Transactions

| TCode | Purpose |
|-------|---------|
| `RSA1` | Data Warehousing Workbench (source system tree) |
| `SM59` | RFC destination management |
| `SALE` | ALE configuration (logical systems) |
| `BD54` | Logical system maintenance |

## Related Tables

| Table | Purpose |
|-------|---------|
| `RFCDES` | RFC destination definitions (Basis table) |
| `RSBITRFC` | BW-specific RFC settings |
| `RSDS` | DataSources (linked via LOGSYS field) |
