# SAP BW Process Chain Tables

Reference for the database tables that store Process Chain definitions and execution logs in SAP BW.

## RSPCCHAINATTR (Process Chain Attributes)

Main definition table for process chains.

| Field | Description | Type/Length | Values |
|-------|-------------|------------|--------|
| `CHAIN_ID` (key) | Process chain ID | CHAR 25 | |
| `OBJVERS` (key) | Object version | CHAR 1 | A=Active, M=Revised |
| `OBJSTAT` | Object status | CHAR 3 | ACT, INA, OFF, PRO |
| `TSTPNM` | Last changed by | CHAR 12 | |
| `TIMESTMP` | Last change timestamp | DEC 15 | |
| `SERVER` | Application server name | CHAR 40 | |
| `SERVERTYPE` | Server type | CHAR 1 | S=Servers, G=Group, H=Host |
| `POLLING` | Main process waits for sub-processes | CHAR 1 | X=True |
| `BATCHUSER` | Background job user | CHAR 12 | |
| `BATCHCLASS` | Background job class | CHAR 1 | |
| `ALERTS` | Send alerts on errors | CHAR 1 | X=True |
| `REDTOGREEN` | Red outcome renders chain green | CHAR 1 | X=True |
| `UNWATCHED` | Chain not monitored automatically | CHAR 1 | X=True |
| `APPLNM` | Application component | CHAR 30 | |
| `TLOGO_OWNED_BY` | Owner TLOGO type | CHAR 4 | |
| `OBJNM_OWNED_BY` | Owner object name | CHAR 40 | |

## RSPCCHAIN (Process Chain Steps)

Defines the individual steps (processes) within a process chain and their sequence.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `CHAIN_ID` (key) | Process chain ID | CHAR 25 |
| `OBJVERS` (key) | Object version | CHAR 1 |
| `TYPE` (key) | Process type | CHAR 10 |
| `VARIANTE` (key) | Process variant name | CHAR 30 |
| `EVENTP_PRED` | Predecessor event parameter | CHAR 64 |
| `EVENT_SUCC` | Successor event | CHAR 32 |
| `EVENTP_SUCC` | Successor event parameter | CHAR 64 |

## RSPCCHAINT (Process Chain Texts)

Language-dependent descriptions.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `CHAIN_ID` (key) | Process chain ID | CHAR 25 |
| `LANGU` (key) | Language | LANG 1 |
| `TXTLG` | Long description | CHAR 60 |

## RSPCPROCESSLOG (Process Chain Execution Log)

Runtime execution log for process chain runs.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `LOG_ID` (key) | Log ID | CHAR 25 |
| `TYPE` | Process type | CHAR 10 |
| `VARIANT` | Process variant | CHAR 30 |
| `INSTANCE` | Instance ID | CHAR 30 |
| `STATE` | Execution status | CHAR 1 |
| `EVENT_START` | Start event | CHAR 32 |
| `EVENTP_START` | Start event parameter | CHAR 64 |
| `JOB_COUNT` | Background job count | CHAR 8 |
| `BATCHDATE` | Batch execution date | DATS 8 |
| `BATCHTIME` | Batch execution time | TIMS 6 |
| `STARTTIMESTAMP` | Start timestamp | DEC 21 |
| `ENDTIMESTAMP` | End timestamp | DEC 21 |
| `SYSID` | System ID | CHAR 10 |

### Status Values (STATE)
- `G` — Successfully completed (Green)
- `R` — Failed (Red)
- `C` — Completed
- (blank) — Undefined / Running

## RSPCLOGCHAIN (Log-to-Chain Cross Reference)

Links log entries to their parent chain.

| Field | Description | Type/Length |
|-------|-------------|------------|
| `LOG_ID` (key) | Log ID | CHAR 25 |
| `CHAIN_ID` | Process chain ID | CHAR 25 |

## Additional Process Chain Tables

| Table | Purpose |
|-------|---------|
| `RSPCCHAINEVENTS` | Multiple start events for process chains |
| `RSPCLOGS` | Application logs for process chains |
| `RSPCRUNVARIABLES` | Runtime variables for process chains |
| `RSPCVARIANT` | Process variant definitions |
| `RSPCVARIANTT` | Process variant texts |
| `RSEVENTHEAD` | Header for event chain triggers |
| `RSPC_BUFFER` | Shared buffer for processes |
| `RSPC_MONITOR` | Monitor individual process chain runs |
| `RSPCCOMMANDLOG` | System command execution logs |

## Common Process Types (TYPE field values)

| Type | Description |
|------|-------------|
| `TRIGGER` | Start process (event trigger) |
| `DTP_LOAD` | DTP data load |
| `ABAP` | ABAP program execution |
| `CHAIN` | Sub-chain (nested process chain) |
| `OPENHUB` | Open Hub extraction |
| `ACTIVATE` | DSO activation |
| `DELETE` | Delete requests |
| `COMPRESS` | InfoCube compression |
| `ATTRIBUTE` | Attribute change run |
| `INDEX` | Index maintenance |

## Process Chain TLOGO

The transport object type for process chains is `RSPC`.

## Key Function Modules

| FM | Purpose |
|----|---------|
| `RSPC_PROCESS_FINISH` | Manually set process status (used for restart/repair) |
| `RSPC_CHAIN_START` | Programmatically start a process chain |

## Transaction Codes

| TCode | Purpose |
|-------|---------|
| `RSPC` | Process chain maintenance |
| `RSPCM` | Process chain monitoring |
