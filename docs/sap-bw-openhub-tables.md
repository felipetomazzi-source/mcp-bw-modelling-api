# SAP BW Open Hub / Data Export Tables

Reference for the database tables that store Open Hub Destination definitions in SAP BW.

## RSBOHDEST (Open Hub Destination Definition)

Main header table for Open Hub Destinations.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `OHDEST` (key) | Open Hub Destination technical name | CHAR 30 |
| `OBJVERS` (key) | Object version (A=active, M=revised) | CHAR 1 |
| `OHDESTTP` | Destination type | CHAR 1 |
| `INFOPROV` | Source InfoProvider | CHAR 30 |
| `OBJSTAT` | Object status | CHAR 3 |
| `OWNER` | Owner | CHAR 12 |
| `TSTPNM` | Last changed by | CHAR 12 |
| `TIMESTMP` | Timestamp | DEC 15 |

### Destination Types (OHDESTTP)
| Value | Description |
|-------|-------------|
| `T` | Database table |
| `F` | Flat file |
| `3` | Third-party tool (via API) |

## RSBOHDESTT (Open Hub Destination Texts)

Language-dependent descriptions.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `OHDEST` (key) | Destination name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `LANGU` (key) | Language | LANG 1 |
| `TXTLG` | Long description | CHAR 60 |

## RSBOHFIELDS (Open Hub Destination Fields)

Field definitions for the Open Hub Destination output structure.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `OHDEST` (key) | Destination name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `FIELDNM` (key) | Field name in destination | CHAR 30 |
| `POSIT` | Position in output | NUMC 4 |
| `IOBJNM` | Source InfoObject | CHAR 30 |
| `KEYFL` | Key field flag | CHAR 1 |

## RSBOHFIELDST (Open Hub Destination Field Texts)

| Field | Description | Type/Length |
|-------|-------------|------------|
| `OHDEST` (key) | Destination name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `FIELDNM` (key) | Field name | CHAR 30 |
| `LANGU` (key) | Language | LANG 1 |
| `TXTLG` | Field description | CHAR 60 |

## Generated Database Table

When an Open Hub Destination of type "Database table" is activated:
- Generated table name: `/BIC/OH<OHDEST_NAME>`
- Technical key fields added automatically:
  - `OHREQUID` — Open Hub request SID
  - `DATAPAKID` — Data package ID
  - `RECORD` — Sequential record number within package

## Open Hub and DTP

Open Hub Destinations are loaded via a DTP:
- The DTP's target type (`TGTTP`) = `DEST`
- The DTP's target TLOGO (`TGTTLOGO`) = `DEST`
- Process chain process type for Open Hub: `OPENHUB`

## Related Tables

| Table | Purpose |
|-------|---------|
| `RSBOHDESTS` | Open Hub Destination settings (additional properties) |
| `RSBOHSTAT` | Open Hub request status/statistics |
| `RSBOHLOG` | Open Hub execution logs |

## Open Hub TLOGO

The transport object type for Open Hub Destinations is `DEST`.

## Key APIs

| API/FM | Purpose |
|--------|---------|
| `RSB_API_OHS_DEST_SETPARAMS` | Set parameters for 3rd-party destination |
| `RSB_API_OHS_DEST_SEND_NOTIFICATION` | Notify 3rd-party system after extraction |
| `RSB_API_OHS_REQUEST_SETPARAM` | Set request-level parameters |
