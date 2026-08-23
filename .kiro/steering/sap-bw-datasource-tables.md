# SAP BW DataSource / Extraction Tables

Reference for the database tables that store DataSource definitions in SAP BW.

## RSDS (DataSource in BW)

Main header table for DataSources in the BW system.

| Field | Description | Type/Length | Values |
|-------|-------------|------------|--------|
| `DATASOURCE` (key) | DataSource technical name | CHAR 30 | |
| `LOGSYS` (key) | Source system logical name | CHAR 10 | |
| `OBJVERS` (key) | Object version (A=active, M=revised) | CHAR 1 | |
| `OBJSTAT` | Object status | CHAR 3 | ACT, INA, OFF, PRO |
| `TYPE` | DataSource data type | CHAR 1 | D=Transaction, T=Text, M=Attributes, H=Hierarchies, S=Segmented |
| `DELTA` | Delta process type | CHAR 4 | |
| `REALTIME` | Real-time data acquisition supported | CHAR 1 | X=Yes |
| `TIMDEPFL` | Data is time-dependent | CHAR 1 | X=Yes |
| `LANGUDEPFL` | Data is language-dependent | CHAR 1 | X=Yes |
| `EXSTRUCTURE` | Extraction structure name | CHAR 30 | |
| `APPLNM` | Application component | CHAR 30 | |
| `BASOSOURCE` | Internal name of DataSource | CHAR 30 | |
| `VIRTCUBE` | Direct access support | CHAR 1 | 1=without preagg, 2=with preagg, D=not supported |
| `STOCKUPD` | Opening balance creation supported | CHAR 1 | X=Yes |
| `PACKGUPD` | Repeated data packet request supported | CHAR 1 | X=Yes |
| `TFMETHODS` | Transfer methods | CHAR 1 | (blank)=Default, 1=IDoc only, 2=PSA only, 3=Both |

## RSDSSEG (DataSource Segments)

Defines segments within a DataSource (multi-segment DataSources).

| Field | Description | Type/Length |
|-------|-------------|------------|
| `DATASOURCE` (key) | DataSource name | CHAR 30 |
| `LOGSYS` (key) | Source system | CHAR 10 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `SEGID` (key) | Segment ID | NUMC 4 |
| `SEGTP` | Segment type | CHAR 1 |

## RSDSSEGFD (DataSource Segment Fields)

Field definitions within each DataSource segment.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `DATASOURCE` (key) | DataSource name | CHAR 30 |
| `LOGSYS` (key) | Source system | CHAR 10 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `SEGID` (key) | Segment ID | NUMC 4 |
| `FIELDNM` (key) | Field name | CHAR 30 |
| `POSIT` | Position in segment | NUMC 4 |
| `IOBJNM` | Assigned InfoObject | CHAR 30 |
| `DATATP` | Data type | CHAR 4 |
| `INTLEN` | Internal length | NUMC 6 |
| `DECIMALS` | Decimal places | NUMC 6 |
| `SELFL` | Selection flag (field available for filtering) | CHAR 1 |
| `HIDEFL` | Hidden field flag | CHAR 1 |
| `KEYFIGFL` | Key figure flag | CHAR 1 |

## RSDST (DataSource Texts)

Language-dependent descriptions of DataSources.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `DATASOURCE` (key) | DataSource name | CHAR 30 |
| `LOGSYS` (key) | Source system | CHAR 10 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `LANGU` (key) | Language key | LANG 1 |
| `TXTLG` | Long description | CHAR 60 |

## Source System Tables (in OLTP / Source System)

| Table | Purpose |
|-------|---------|
| `ROOSOURCE` | Header table for DataSources in the source system |
| `ROOSFIELD` | Fields of a DataSource in the source system |
| `ROOSGEN` | Generated objects for a DataSource in the source system |

## BW-Side Replication Tables

| Table | Purpose |
|-------|---------|
| `RSOLTPSOURCE` | Replicated OLTP source metadata in BW |
| `RSOLTPSOURCEFIE` | Replicated OLTP source fields in BW |
| `RSOLTPSOURCET` | Replicated OLTP source texts |

## PSA Table Naming Convention

PSA (Persistent Staging Area) tables are generated per DataSource:
- Pattern: `/BIC/B<7-digit number>` or named based on DataSource
- The PSA stores raw extracted data before transformation

## DataSource TLOGO

The transport object type for DataSources is `RSDS`.
