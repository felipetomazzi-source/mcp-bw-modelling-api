# SAP BW InfoObject Metadata Tables

Reference for the database tables that store InfoObject definitions in SAP BW.

## RSDIOBJ (Directory of All InfoObjects)

Master directory listing every InfoObject in the system.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `IOBJNM` (key) | InfoObject technical name | CHAR 30 |
| `OBJVERS` (key) | Object version (A=active, M=revised, D=content) | CHAR 1 |
| `IOBJTP` | InfoObject type: CHA (characteristic), KYF (key figure), TIM (time), UNI (unit), DPA (data packet) | CHAR 3 |
| `OBJSTAT` | Object status: ACT, INA, OFF, PRO | CHAR 3 |
| `OWNER` | Owner (person responsible) | CHAR 12 |
| `BWAPPL` | BW Application (namespace) | CHAR 10 |
| `FIELDNM` | Field name in structures | CHAR 30 |
| `ATRONLYFL` | Exclusively an attribute (not used in InfoCubes) | CHAR 1 |
| `TSTPNM` | Last changed by | CHAR 12 |
| `TIMESTMP` | UTC timestamp of last change | DEC 15 |

## RSDCHABAS (Basic Characteristic Properties)

Core properties for characteristics, time characteristics, and units.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `CHABASNM` (key) | Reference characteristic name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `CHATP` | Characteristic type: GEN (default), FIX (own implementation), REM (direct access) | CHAR 3 |
| `DATATP` | ABAP data type (CHAR, NUMC, DATS, DEC, INT4, etc.) | CHAR 4 |
| `INTLEN` | Internal length | NUMC 6 |
| `OUTPUTLEN` | Output length | NUMC 6 |
| `CONVEXIT` | Conversion routine name | CHAR 5 |
| `LOWERCASE` | Lowercase allowed flag | CHAR 1 |
| `INFOAREA` | InfoArea assignment | CHAR 30 |
| `ATTRIBFL` | Has attributes flag | CHAR 1 |
| `TIMDEPFL` | Master data is time-dependent | CHAR 1 |
| `TXTTABFL` | Text table exists | CHAR 1 |
| `TXTSHFL` | Short text exists | CHAR 1 |
| `TXTMDFL` | Medium text exists | CHAR 1 |
| `TXTLGFL` | Long text exists | CHAR 1 |
| `HIETABFL` | Has hierarchies | CHAR 1 |
| `UNINM` | Currency/unit attribute reference | CHAR 30 |
| `HIGH_CARDINALITY` | High cardinality flag (>2 million records) | NUMC 1 |

## RSDIOBJT (InfoObject Texts)

Stores language-dependent descriptions of InfoObjects.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `IOBJNM` (key) | InfoObject name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `LANGU` (key) | Language key | LANG 1 |
| `TXTSH` | Short description | CHAR 20 |
| `TXTMD` | Medium description | CHAR 40 |
| `TXTLG` | Long description | CHAR 60 |

## RSDCHA (Characteristics Directory)

Lists all characteristics with their assignment to an InfoObject catalog.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `CHANM` (key) | Characteristic name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `CHABASNM` | Reference characteristic (links to RSDCHABAS) | CHAR 30 |

## RSDKEYF (Key Figures)

Key figure-specific properties.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `KEYFNM` (key) | Key figure name | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `KYFTP` | Key figure type (AMT=amount, QTY=quantity, NUM=number) | CHAR 3 |
| `DATATP` | Data type (DEC, FLTP, INT4, etc.) | CHAR 4 |
| `INTLEN` | Internal length | NUMC 6 |
| `DECIMALS` | Number of decimal places | NUMC 6 |
| `UNINM` | Fixed unit reference | CHAR 30 |
| `AGGRFL` | Aggregation behavior (SUM, MIN, MAX, NOP) | CHAR 3 |

## RSDICHA (Characteristic Properties for InfoObjects)

Extended properties per characteristic in an InfoObject context.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `CHANM` (key) | Characteristic name | CHAR 30 |
| `CHABASNM` (key) | Base characteristic | CHAR 30 |
| `OBJVERS` (key) | Object version | CHAR 1 |

## Related Tables

| Table | Purpose |
|-------|---------|
| `RSDATRNAV` | Navigation attributes for characteristics |
| `RSDCHATR` | Display attributes for characteristics |
| `RSDCOMPIC` | Compounding InfoObjects |
| `RSDIOBC` | InfoObject catalogs |
| `RSDIOBCOBJ` | InfoObjects in catalogs |
| `RSDBCHATR` | Attribute properties (time-dependence, etc.) |
| `RSDUNI` | Units of measure InfoObjects |

## Master Data Table Naming Conventions

For a characteristic with technical name `<IOBJNM>`:
- Attribute table (SID): `/BI0/S<IOBJNM>` (SAP) or `/BIC/S<IOBJNM>` (custom)
- Text table: `/BI0/T<IOBJNM>` or `/BIC/T<IOBJNM>`
- Hierarchy table: `/BI0/H<IOBJNM>` or `/BIC/H<IOBJNM>`
- SID table: `/BI0/S<IOBJNM>` (maps external values to internal SID numbers)
- P table (attributes): `/BI0/P<IOBJNM>` or `/BIC/P<IOBJNM>`
- Q table (time-dep. attributes): `/BI0/Q<IOBJNM>` or `/BIC/Q<IOBJNM>`
