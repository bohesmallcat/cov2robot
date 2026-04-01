# Storage Platform Automation — Robot Framework Patterns Reference

This reference documents the conventions, keyword libraries, and test patterns used in the **Storage Platform Automation** framework (`workspace/automation`). Use these patterns when generating Robot Framework E2E test case suggestions from coverage gaps.

---

## Repository Structure

```
automation/
├── runner.py                         # Main entrypoint – parses CLI flags, launches Robot tests
├── Makefile                          # Developer shortcuts: lint, unit-test, run-tests
├── requirements.txt                  # Top-level Python dependencies
├── devkit/
│   ├── requirements.txt              # Full versioned Python dependencies
│   └── devkit.Dockerfile
│
├── library/                          # Python keyword libraries (reusable Robot keywords)
│   ├── object/
│   │   ├── shared/                   # Cross-component helpers
│   │   │   ├── core/                 # Low-level: SSHClient, restclient, utils
│   │   │   ├── storage/                  # storage cluster connectors, CLI/API/Portal clients, enums
│   │   │   └── s3connectors/         # S3 connector abstractions (s5cmd, mongoose, rdma, etc.)
│   │   ├── storagedriver/            # Storage driver keywords
│   │   │   ├── engine_server_manager_keywords.py
│   │   │   ├── hal_services_keywords.py
│   │   │   └── s3_over_rdma_keywords.py
│   │   ├── blocklayer/              # Block-layer keywords (write, read, recovery, compression, etc.)
│   │   │   ├── blocklayer_shared_keywords.py   # Base class for all block-layer keywords
│   │   │   ├── chunk_write_and_read_keywords.py  # Block write/read operations
│   │   │   ├── compression_keywords.py         # Compression algorithm testing (ZSTD, LZ4, SNAPPY, DEFLATE)
│   │   │   ├── chunk_allocation_keywords.py    # Block allocation, negative hints
│   │   │   ├── chunk_recovery_keywords.py      # EC recovery configuration
│   │   │   ├── block_fragmentation_keywords.py # Block defragmentation metrics
│   │   │   ├── cm_ec_keywords.py               # Block Manager EC process
│   │   │   ├── cm_fi_keyword.py                # CM fault injection (service restarts)
│   │   │   ├── communicator_keywords.py        # RDMA communicator, large message, listing stress
│   │   │   ├── improve_cm_coverage_keywords.py # CM startup/shutdown coverage
│   │   │   ├── recovery_enhancement_keywords.py  # Recovery status, partition mock
│   │   │   ├── recovery_api_verify_keywords.py   # Recovery API remaining-time verification
│   │   │   ├── state_machine_and_deleting_balance_keywords.py  # SS state machine, deleting
│   │   │   ├── jacoco_keywords.py              # JaCoCo agent integration for coverage
│   │   │   └── chunkinfo_data_class.py         # Data classes (ChunkProcessingMode, CopyInfo, EC items)
│   │   ├── replication/             # Cross-region replication keywords
│   │   ├── spacereclaim/            # Space reclaim / GC keywords
│   │   └── ecs_shared_object_keywords.py  # Base class for all storage cluster object keywords
│   ├── cluster_communicator.py      # SSH/cluster communication (BaremetalCluster, ClassicEcsCluster)
│   ├── ecs_rest_api.py              # cluster REST API client
│   └── config.py                    # Repository-wide configuration
│
├── robot/                           # Robot Framework test suites and resources
│   ├── common_keywords/             # Shared Robot keywords
│   │   ├── acceptance_keywords.robot
│   │   ├── data_path_services_keywords.robot
│   │   ├── encryption_keywords.robot
│   │   └── ...
│   ├── general_test_framework_keywords/    # General reusable keywords
│   │   ├── ecs_setup_keywords.robot
│   │   ├── general_s3_keywords/     # S3 bucket, object, replication rule keywords
│   │   ├── general_IAM_keywords/    # IAM account, user, role, tenant keywords
│   │   └── ...
│   ├── object/
│   │   ├── storagedriver/           # Storage driver test suites
│   │   ├── blocklayer/              # Block-layer test suites (17 suites)
│   │   │   ├── block_read.robot                  # Type-1 block read (single/cross segment, sealed, async recovery)
│   │   │   ├── chunk_write.robot                 # Type-1 block write (single/cross segment, sealed)
│   │   │   ├── new_type2_block_read.robot         # New Type-2 block read (44M boundary, 128M old type-2)
│   │   │   ├── new_type2_chunk_write.robot        # New Type-2 block write (44M boundary)
│   │   │   ├── compression.robot                 # Compression algorithm tests (ZSTD, LZ4, SNAPPY, DEFLATE)
│   │   │   ├── chunk_recovery_enhance_test.robot  # EC recovery thread configuration
│   │   │   ├── communicator.robot                # RDMA communicator, listing stress
│   │   │   ├── ecs_flex_component_test_chunk_allocation.robot  # Block allocation, negative hints
│   │   │   ├── ecs_flex_component_test_fragmentation.robot     # Block defragmentation metrics
│   │   │   ├── ecs_flex_component_test_sharelib_cm_ec_for_ecs.robot  # CM EC process
│   │   │   ├── ecs_flex_regression_test_cm_fi_for_ecs.robot    # CM fault injection
│   │   │   ├── ecs_flex_deleting.robot            # Block deleting (TypeI, Btree, Journal)
│   │   │   ├── ecs_flex_state_machine_component.robot  # SS state machine transitions
│   │   │   ├── ecs_flex_smart_rebalance.robot     # Smart rebalance
│   │   │   ├── improve_block_manager_coverage.robot  # CM startup/shutdown coverage
│   │   │   ├── recovery_enhancement.robot         # Recovery enhancement (disk down, free block scan)
│   │   │   └── recovery_api_verify.robot          # Recovery API remaining-time verification
│   │   ├── replication/             # Replication test suites
│   │   ├── spacereclaim/            # Space-reclaim / GC test suites
│   │   └── acceptance/              # Acceptance test suites (CRR, etc.)
│   └── fi/                          # Fault injection suites
```

---

## Architecture: Two-Layer Design

1. **`library/`** — Python keyword libraries implementing reusable actions.
   - Each component has a `*_keywords.py` class that extends `SharedObjectKeywords`.
   - Keywords are decorated with `@keyword("Keyword Name")` from `robot.api.deco`.
   - Class scope is `ROBOT_LIBRARY_SCOPE = 'TEST SUITE'`.

2. **`robot/`** — Robot Framework test suites that import and call the Python keyword libraries.
   - Mirrors the `library/` structure by component.
   - `robot/common_keywords/` provides shared setup/teardown and infrastructure keywords.
   - `robot/general_test_framework_keywords/` provides general S3 and IAM keywords.

---

## Python Keyword Library Conventions

### Base Class: `SharedObjectKeywords`

All component keyword classes extend this base (`library/object/ecs_shared_object_keywords.py`):

```python
from robot.api.deco import keyword
from robot.version import get_version
from library.object.ecs_shared_object_keywords import SharedObjectKeywords

class MyComponentKeywords(SharedObjectKeywords):
    ROBOT_LIBRARY_VERSION = get_version()
    ROBOT_LIBRARY_SCOPE = 'TEST SUITE'

    def __init__(self):
        super(MyComponentKeywords, self).__init__()
        # component-specific state

    @keyword("My Custom Action")
    def my_custom_action(self, arg1, arg2=None):
        # implementation
        pass
```

### Key Base Keywords (from `SharedObjectKeywords`)

| Python Method | Robot Keyword | Purpose |
|---|---|---|
| `the_ecs_cluster_is_reachable()` | `The storage cluster Cluster Is Reachable` | Initialize cluster connection, get nodes/credentials |
| `user_has_access_to_the_data_node()` | `User Has Access to The Data Node` | Select random target node, verify drive technology |
| `user_has_access_to_the_nvme_data_node()` | `User Has Access to The NVMe Data Node` | Select NVMe node specifically |
| `prepare_for_s5cmd_client()` | `Prepare for S5CMD Client` | Set up S5CMD S3 connector |
| `cleanup_s5cmd_client()` | `Cleanup S5CMD Client` | Clean up S5CMD connector |
| `enable_dt_query()` | `Enable DT Query` | Enable DT query for diagnostics |
| `disable_dt_query()` | `Disable DT Query` | Restore DT query settings |
| `set_slave_node_as_target_node()` | `Set Slave Node As Target Node` | Select non-master node |
| `configure_service_logging_level()` | `Configure Service Logging Level` | Change log4j2 level on services |

### Key Imports in Python Libraries

```python
from library.cluster_communicator import ClassicEcsCluster
from library.config import config
from library.object.shared.core import SSHClient
from library.object.shared.storage.storagecliclient import StorageCliClient
from library.object.shared.storage.storageportalapiclient import StoragePortalAPIClient
from library.object.shared.storage.connectors.sscconnector import SSCConnector
from library.object.shared.s3connectors.s3connectorfactory import S3ConnectorFactory
from library.object.shared.s3connectors.s5cmdconnector import S5CMDConnector
from library.object.shared.s3connectors.supporteds3connectors import SupportedS3Connectors
```

### Python 2/3 Compatibility Rules

- No f-strings; use `.format()` or `%`
- Use `super(ClassName, self).__init__()` instead of `super().__init__()`
- All code must pass Pylint under both Python 2.7 and 3.x

---

## Robot Framework Test Suite Conventions

### File Naming

- Test suite files: `ecs_<component>_<type>_test_suite.robot` or `<component>_test.robot`
- Resource/keyword files: `<component>_keywords.robot`
- Suite list files: `<name>.suitelist` (one suite path per line, for CI tiers)

### Settings Section Pattern

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Resource    robot/common_keywords/data_path_services_keywords.robot

Library     library.object.engine.engine_manager_keywords.EngineManagerKeywords

Default Tags    storage_driver

Suite Setup       Establish Connection To Platform    ${PLATFORM}

Test Setup        Run Keywords
...                   The storage cluster Cluster Is Reachable
...    AND            All Nodes Are Online
...    AND            storage cluster Management Service Is Up In Time

Test Teardown     Run Keywords
...                   Stop Data Ingestion
...    AND            All Nodes Are Online

Suite Teardown    Close Clients
```

**Key patterns:**
- Libraries are imported with full Python dotted path: `library.object.<component>.<module>.<ClassName>`
- Resources reference Robot keyword files with relative paths from project root
- `Establish Connection To Platform    ${PLATFORM}` is the standard Suite Setup
- `Close Clients` is the standard Suite Teardown

### Variables Section

```robotframework
*** Variables ***
${CLIENT_IP}
${CLIENT_USERNAME}
${CLIENT_PASSWORD}
${BUCKET_NAME}
${OBJECT_COUNT}        ${1000}
${LARGE_OBJECT_COUNT}  ${100}
${NAMESPACE}           storage

@{services_to_be_restarted}    ssm  cm  blobsvc  sr
@{numa_core_processes}         nvmeengine  dataheadsvc  blobsvc
```

**Notes:**
- Variables without defaults are set at runtime (via `runner.py` CLI flags or Suite Setup)
- `${PLATFORM}` is passed from runner.py (values: `dev`, `staging`, `production`, `cloud`, `bare-metal`)
- Use `Set Suite Variable` / `Set Global Variable` to share state

### Test Case Naming: `[Scenario-N]` Convention

```robotframework
*** Test Cases ***
[Scenario-1] storage cluster S3 objects CRUD validation
    [Documentation]    As an end user, with storage cluster has been provisioned,
    ...     I want to perform S3 object CRUD operations,
    ...     So that I can validate the primary features of storage cluster are working properly.
    ...     Prerequisites:
    ...     - storage cluster has been deployed and provisioned with shared memory enabled
    [Tags]    Tier1    Parallel    E2E    S3
    Given The storage cluster Cluster Is Reachable
      And User Has Access to The Data Node
      And User Performs Initial Configurations
     When User Ingests Objects To Bucket Through S3
     Then Objects Ingested to Bucket are Accessible
```

**Conventions:**
- Test names follow `[Scenario-N] DescriptiveName` format
- Documentation uses BDD-style "As a... I want to... So that..." + Prerequisites
- Tests use Given/When/Then BDD keywords
- Indentation with 4 spaces, continuation with `...`

### Tag System

| Tag | Meaning |
|---|---|
| `Tier1` | First-tier acceptance tests (run in CI always) |
| `Tier2` | Second-tier tests (run in extended CI) |
| `Parallel` | Can run concurrently with other tests |
| `Standalone` | Must run in isolation (modifies cluster state) |
| `E2E` | End-to-end test |
| `CI` | Part of CI pipeline |
| `S3` | S3 operations |
| `IO` | Data path / IO operations |
| `AFA` | AFA (NVMe) specific |
| `API` | API validation |
| `Disk` | Disk operations |
| `FI` | Fault injection |
| `Network` | Network operations |
| `Configuration` | Configuration validation |
| `Coverage` | Coverage-driven test |
| `Slow` | Long-running test |
| `WIP` | Work in progress |
| `Greenfield` | New deployment only |

**Coverage-specific tags (add when generating from coverage gaps):**
- `Coverage` — marks test as coverage-driven
- `coverage-<class-name>` — links to specific Java class
- `coverage-<method-name>` — links to specific uncovered method
- `fault-injection` — requires fault injection infrastructure
- `large-object` — requires large data transfer

### Setup/Teardown Patterns

**Test-level with `Run Keywords`:**
```robotframework
[Setup]    Run Keywords
...            The storage cluster Cluster Is Reachable
...    AND     Enable DT Query
[Teardown]    Run Keywords
...               Disable DT Query
...    AND        Removed Disks Are Restored
...    AND        Stop Data Ingestion
```

**Suite-level:**
```robotframework
Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients
```

**Custom per-test teardown:**
```robotframework
[Teardown]    User Modifies Disk Json Location    disk_json_location=${None}
```

### Keyword Definition Patterns

Keywords are defined in two ways:

**1. Robot keyword wrappers (in .robot files):**
```robotframework
*** Keywords ***
User Creates Bucket Through S5CMD
    [Documentation]    User creates bucket through s5cmd and raise error if it's failed.
    [Arguments]    ${bucket_name}=${BUCKET_NAME}
    ${BUCKET_NAME} =    Create Bucket Through S5CMD     ${bucket_name}
    Set Suite Variable    ${BUCKET_NAME}

User Ingests 64MB Large Size Objects without TLS
    User Performs Object Ingestion

User Performs Object Ingestion
    [Arguments]    ${bucket_name}=${BUCKET_NAME}
    ...            ${number_of_objects}=${OBJECT_COUNT}
    ...            ${object_size}=64mb
    ...            ${tls}=${False}
    ...            ${use_dynamic_timeout}=${False}
    Ingest Objects To Bucket Through S5CMD    ${bucket_name}    ${number_of_objects}
    ...                                       ${object_size}    tls=${tls}  use_dynamic_timeout=${use_dynamic_timeout}
```

**2. Python keywords (in `*_keywords.py` files):**
```python
@keyword("User Can Get Targets")
def user_can_get_targets(self):
    # implementation using self.shared_object_library
    pass
```

---

## Test Patterns by Component

### Storage Engine Test Pattern (Acceptance)

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     library.object.engine.engine_manager_keywords.EngineManagerKeywords

Default Tags    storage_driver
Suite Setup    Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-N] DescriptiveTestName
    [Documentation]    As an storage cluster customer, I want to <action>,
    ...     So that I can validate <feature>
    [Tags]    Tier1    Parallel    E2E    <component-tag>
    Given The storage cluster Cluster Is Reachable
     When User Has Access to The Data Node
     Then <Verification Keyword>
```

### Data Path Test Pattern (S5CMD-based IO)

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     library.object.shared.s3connectors.s5cmdconnector.S5CMDConnector
Library     library.object.ecs_shared_object_keywords.SharedObjectKeywords

Default Tags    storage_driver
Suite Setup       Establish Connection To Platform    ${PLATFORM}

Test Setup        Run Keywords
...                   The storage cluster Cluster Is Reachable
...    AND            Prepare for S5CMD Client

Test Teardown     Run Keywords
...                   Delete Bucket Through S5CMD  bucket_name=${bucket_name}
...    AND            Cleanup S5CMD Client

*** Variables ***
${OBJECT_COUNT}        ${1000}
${LARGE_OBJECT_COUNT}  ${100}

*** Test Cases ***
[Scenario-N] 64M Large write path without tls no MPU
    [Tags]      Tier1    Parallel    E2E    IO
    Given User Has Access to The Data Node
      And User Creates Bucket Through S5CMD
      And User Removes All Objects In Bucket
     When User Ingests 64MB Large Size Objects without TLS
     Then User Validates Objects In Bucket
      And User Removes All Objects In Bucket
      And User Validates Objects In Bucket
```

### Fault Injection Test Pattern

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     library.object.engine.engine_manager_keywords.EngineManagerKeywords

Default Tags    storage_driver
Suite Setup       Establish Connection To Platform    ${PLATFORM}

Test Setup        Run Keywords
...                   The storage cluster Cluster Is Reachable
...    AND            All Nodes Are Online
...    AND            storage cluster Management Service Is Up In Time
...    AND            Enable DT Query

Test Teardown     Run Keywords
...                   Disable DT Query
...    AND            Removed Disks Are Restored
...    AND            Restore Disk Powered Off
...    AND            Stop Data Ingestion
...    AND            storage cluster Management Service Is Up In Time
...    AND            All Nodes Are Online

Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-N] RemoveDisksFromNodeAndCheckRecovery
    [Documentation]    As an storage cluster customer, I want to remove disks from one node,
    ...     So that I can validate relevant behaviors
    [Tags]    Tier2    Standalone    E2E    Disk    FI
    Given User Has Access to The Data Node
      And The Client Access Info Are Resolved
     When User Launches Object Ingestion Before Starting FI
      And User Removes Disks From Node With Less Than Three Disks Left
     Then Wait Until DT Gets Ready
      And Check Removed Disk Operational State
      And User Monitors Pass Rate In Fault Injection Period
      And User Validates That Pass Rate Gets Recovered    ${0.7}
      And Removed Disks Are Restored
      And Restore Disk Powered Off
      And Pass Rate Recovered to Threshold
      And User Stops Object Ingestion After Stopping FI
```

### S3 Over RDMA Test Pattern

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     library.object.storagedriver.s3_over_rdma_keywords.S3OverRDMAKeywords

Default Tags    storage_driver
Suite Setup    Establish Connection To Platform    ${PLATFORM}

Test Setup         Run Keywords
...                    The storage cluster Cluster Is Reachable
...    AND             All Nodes Are Online
Test Teardown      All Nodes Are Online
Suite Teardown    Close Clients

*** Variables ***
${SMALL_OBJECT_SIZE}      100KB
${MEDIUM_OBJECT_SIZE}     10MB
${LARGE_OBJECT_SIZE}      100MB
${MPU_OBJECT_SIZE}        500MB
${MPU_PART_SIZE}          100MB

*** Test Cases ***
[Scenario-N] Upload Objects With RDMA PUT Operations
    [Documentation]    As an end user with RDMA-enabled client, I want to upload objects using RDMA PUT,
    ...    So that I can achieve high-throughput data ingestion with low latency.
    [Tags]    Tier1    RDMA_PUT    Data_Path
    [Setup]    Run Keywords
    ...        The storage cluster Cluster Is Reachable
    ...    AND    User Enables S3 Over RDMA Feature
    Given User Has Access to The Data Node
      And User Has Access To RDMA S3 Client
     When User Uploads Objects With RDMA PUT    object_sizes=${SMALL_OBJECT_SIZE},${MEDIUM_OBJECT_SIZE}
     Then Response Should Contain RDMA Success Headers
      And Objects Should Be Created Successfully
      And No Services Should Restart
```

### Block Layer Test Patterns

The block layer is one of the most comprehensive test areas in the automation framework, with **17 Robot test suites** and **16 Python keyword libraries**. All block-layer keyword classes inherit from `BlockLayerSharedKeywords` (which extends `SharedObjectKeywords`).

#### Architecture: Merged SegmentLocation & Compression

The block layer uses an extended `SegmentLocation` Protobuf with three critical fields for merged write blocks:

| Field | ID | Description |
|:------|:---|:------------|
| **indexGranularity** | 14 | Logical size of each object unit (base unit) |
| **objectOffsets** | 15 | Boundaries of each object block within the logical object |
| **blockOffsets** | 16 | Boundaries of each write block within the physical SegmentLocation |

The **Big Fat Writer (BFW)** merges adjacent SegmentLocations when two consecutive blocks are in the same block and physically adjacent. Merge limit: **1,000** entries per merged SegmentLocation. Write block max size: **2,236,960 bytes**.

**Compression scenarios:**
- **No compression** — `objectOffsets`/`blockOffsets` omitted; `indexGranularity` alone determines offsets
- **Compression applied** — `objectOffsets`/`blockOffsets` mandatory; `compressInfo` specifies algorithm (ZSTD, LZ4, SNAPPY, DEFLATE)

#### Block Types and Size Boundaries

| Block Type | Description | Size Threshold |
|:-----------|:------------|:---------------|
| **Type-1** (small) | Single block, fits in one segment | < 44 MB |
| **Type-2** (new) | Active/sealed multi-segment block | = 44 MB boundary |
| **Type-2** (old/legacy) | Legacy large block format | = 128 MB |
| **BTREE** | B-tree index block | Varies |
| **JOURNAL** | Journal block | Varies |
| **REPO** | Repository data block | Varies |

#### Block-Layer Keyword Libraries (import paths)

| Library | Import Path | Purpose |
|:--------|:------------|:--------|
| `BlockLayerSharedKeywords` | `library.object.blocklayer.blocklayer_shared_keywords.BlockLayerSharedKeywords` | Base class for all block-layer keywords; extends `SharedObjectKeywords` |
| `BlockWriteAndReadKeywords` | `library.object.blocklayer.block_write_and_read_keywords.BlockWriteAndReadKeywords` | Block write/read operations, encrypted bucket creation |
| `CompressionKeywords` | `library.object.blocklayer.compression_keywords.CompressionKeywords` | Compression algorithm tests, corpus download, CMF validation |
| `ChunkAllocationKeywords` | `library.object.blocklayer.chunk_allocation_keywords.ChunkAllocationKeywords` | Block allocation, negative hints, CM refresh during FI |
| `ChunkRecoveryKeywords` | `library.object.blocklayer.chunk_recovery_keywords.ChunkRecoveryKeywords` | EC recovery executor/buffer configuration |
| `BlockFragmentationKeywords` | `library.object.blocklayer.block_fragmentation_keywords.BlockFragmentationKeywords` | Block defragmentation metrics, EC scheme, DT query |
| `CMECKeywords` | `library.object.blocklayer.cm_ec_keywords.CMECKeywords` | Block Manager EC process (sealed/active, TypeI/TypeII) |
| `CMFIKeywords` | `library.object.blocklayer.cm_fi_keyword.CMFIKeywords` | CM fault injection (rolling restart CM/SSM/SS) |
| `CommunicatorKeywords` | `library.object.blocklayer.communicator_keywords.CommunicatorKeywords` | RDMA communicator, large messages >12 KB, listing stress |
| `ImproveCMCoverageKeywords` | `library.object.blocklayer.improve_cm_coverage_keywords.ImproveCMCoverageKeywords` | CM startup/shutdown, service prefix, page table mode |
| `RecoveryEnhanceMent` | `library.object.blocklayer.recovery_enhancement_keywords.RecoveryEnhanceMent` | Recovery status, partition mock, disk-down triggers |
| `RecoveryEnhancementApiVerifyKeywords` | `library.object.blocklayer.recovery_api_verify_keywords.RecoveryEnhancementApiVerifyKeywords` | Recovery API remaining-time verification |
| `StateMachineKeyword` | `library.object.blocklayer.state_machine_and_deleting_balance_keywords.StateMachineKeyword` | SS state machine (TRANSIENT/PERMANENT DOWN), block deleting |
| `SharedObjectServicesKeywords` | `library.object.blocklayer.shared_object_services_keywords.SharedObjectServicesKeywords` | Shared service-level keywords (used by many suites) |

#### Common CMF Parameters

Many block-layer tests configure system behavior via CMF (Configuration Management Framework) parameters:

| CMF Key | Usage |
|:--------|:------|
| `com.example.storage.ssm.allocation.forceDefragmentationAllocationOnAllDevices` | Enable/disable forced defragmentation allocation |
| `com.example.storage.bufferedFileWriter.compress_ratio_threshold` | Compression ratio threshold |
| `com.example.storage.bufferedFileWriter.compress_algorithm` | Compression algorithm (ZSTD/LZ4/SNAPPY/DEFLATE) |
| `com.example.storage.block.recover.ec_recover_executor_num` | EC recovery executor thread count |
| `com.example.storage.block.recover.total_data_buffer_number` | EC recovery data buffer count |
| `com.example.storage.block.recover.total_code_buffer_number` | EC recovery code buffer count |
| `com.example.storage.block.recover.thread_per_disk_group` | EC recovery threads per disk group |
| `com.example.storage.blobsvc.private.rdma.enabled` | Enable/disable RDMA communicator |
| `servicePrefix` | CM service prefix configuration |
| `suggestPageTableModeEnabled` | Enable/disable page table mode |

#### Common Block-Layer API Endpoints

| Endpoint | Purpose |
|:---------|:--------|
| `http://{host}:9101/cm/recover/{cos_id}/{level}/{data_type}/{repo_type}/{seal_chunk}` | Trigger block recovery |
| `http://{host}:9101/stats/ssm/varraycapacity` | Get VArray capacity / COS info |
| `http://{host}:9101/ssm/recovery-status/{node_uuid}` | Get recovery status with remainingTime |
| `http://{host}:9101/diagnostic/SS/1/DumpAllKeys/SSTABLE_KEY` | Dump all keys for diagnostics |

#### Block-Layer Execution Tags

In addition to the standard tags (`Tier1`/`Tier2`, `Parallel`/`Standalone`, `E2E`), block-layer suites use:

| Tag | Meaning |
|:----|:--------|
| `block_layer` | General block-layer test |
| `Repo_Chunk_Read` / `Repo_Chunk_Write` | REPO block read/write operations |
| `Single_Segment_Read` / `Cross_Segment_Read` | Single vs. cross-segment reads |
| `NewTypeII_Chunk_Read` / `NewTypeII_Chunk_Write` | New Type-2 block operations |
| `Compression` | Compression algorithm tests |
| `Block_Defragmentation` | Block defragmentation metrics |
| `CMEC` | Block Manager EC process |
| `FI` | Fault injection tests |
| `RecoveryEnhancement` | Recovery enhancement |
| `FC`, `ZBB`, `RC`, `Regression` | Test environment/pipeline markers |
| `CMF` | CMF parameter configuration tests |
| `chunk_write_read_component` | Component-level block write/read |

#### Pattern A: Block Write / Read Test

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     library.object.blocklayer.block_write_and_read_keywords.BlockWriteAndReadKeywords
Library     library.object.blocklayer.shared_object_services_keywords.SharedObjectServicesKeywords

Default Tags    block_layer

Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Variables ***
${DEFRAGMENTATION_ALLOCATION_CMF}    com.example.storage.ssm.allocation.forceDefragmentationAllocationOnAllDevices
${COMPRESS_RATIO_CMF}                com.example.storage.bufferedFileWriter.compress_ratio_threshold
${DEVICE_BLACKLIST_CMF}              com.example.storage.block.allocation.persistent_device_blacklist

*** Test Cases ***
[Scenario-1] Type-1 block read with single segment
    [Tags]    Repo_Chunk_Read    Single_Segment_Read    Tire1    FC    ZBB    RC
    Given User Has Access to The Data Node
     When User Create Block  count=${1}  level=${1}  type=BTREE
      And User Read Block Data
     Then Block Read Result Should Be Success

[Scenario-N] Write Three Copies
    [Tags]      Tier2    Parallel    E2E    IO
    Given User Has Access to The Data Node
     When User Create BTREE AND JOURNAL Block

*** Keywords ***
User Create BTREE AND JOURNAL Block
    User Create Block  count=${1}  level=${1}  type=BTREE
    User Create Block  count=${1}  level=${1}  type=JOURNAL
    User Create Block  count=${1}  level=${2}  type=BTREE
    User Create Block  count=${1}  level=${2}  type=JOURNAL
```

#### Pattern B: Compression Test

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     DateTime
Library     library.object.blocklayer.compression_keywords.CompressionKeywords
Library     library.object.blocklayer.shared_object_services_keywords.SharedObjectServicesKeywords

Default Tags    block_layer    Compression

Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-1] Default compression algorithm is ZSTD on HDD
    [Tags]    Compression    Default    Tire1    FC    ZBB    RC
    Given User Has Access to The Data Node
     When User Validates Default Value Of CMF Parameter
     Then Compression Algorithm Should Be    ZSTD

[Scenario-4] Change compression algorithm by CMF
    [Tags]    Compression    CMF    Tire2
    Given User Has Access to The Data Node
     When User Changes Compression Algorithm To    LZ4
     Then Compression Algorithm Should Be    LZ4
```

#### Pattern C: EC (Erasure Coding) Process Test

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     OperatingSystem
Library     library.object.blocklayer.cm_ec_keywords.CMECKeywords
Library     library.object.blocklayer.shared_object_services_keywords.SharedObjectServicesKeywords

Default Tags    CMEC    block_layer

Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-2] Normal EC process - Created not Sealed TypeI
    [Tags]    Test002    CMEC    USER    FC    Regression    ZBB    RC    Tier2
    Given User Has Access to The Data Node
     When User Injects Blocks And Waits For EC
     Then EC Status Should Be COMPLETED

[Scenario-33] FI case - Remove disk for Read Error
    [Tags]    Test033    CMEC    FI    Tier2
    Given User Has Access to The Data Node
     When User Removes Disk To Trigger Read Error
     Then Recovery Should Complete Successfully
```

#### Pattern D: Block Manager Fault Injection Test

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     OperatingSystem
Library     library.object.blocklayer.cm_fi_keyword.CMFIKeywords
Library     library.object.blocklayer.shared_object_services_keywords.SharedObjectServicesKeywords

Default Tags    block_layer    FI

Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-1] FI - Rolling restart CM Service during inject data
    [Tags]    FI01    FI    Tier2    USER    FC    Regression    ZBB    RC
    Given User Has Access to The Data Node
      And User Starts Data Ingestion
     When User Performs Rolling Restart Of CM Service
     Then Pass Rate Should Recover To Threshold
```

#### Pattern E: Recovery Enhancement Test

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     OperatingSystem
Library     library.object.blocklayer.recovery_enhancement_keywords.RecoveryEnhanceMent
Library     Collections
Library     String

Default Tags    block_layer    RecoveryEnhancement

Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-0] Trigger Recovery By Down Disk
    [Tags]    E2E    RecoveryEnhancement    Tier2    RecoveryDiskDown
    Given User Has Access to The Data Node
     When User Triggers Recovery By Downing Disk
     Then Recovery Status Should Show Progress
```

#### Pattern F: New Type-2 Block Read/Write Test

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     DateTime
Library     library.object.blocklayer.block_write_and_read_keywords.BlockWriteAndReadKeywords
Library     library.object.blocklayer.shared_object_services_keywords.SharedObjectServicesKeywords

Default Tags    block_layer    NewTypeII_Chunk_Read

Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-1] Type-1 block read (size < 44M)
    [Tags]    NewTypeII_Chunk_Read    Tire2    FC    ZBB    RC
    Given User Has Access to The Data Node
     When User Writes Block With Size Less Than 44M
     Then Block Should Be Type 1

[Scenario-2] Active new type-2 block read (size = 44M)
    [Tags]    NewTypeII_Chunk_Read    Tire2
    Given User Has Access to The Data Node
     When User Writes Block With Size 44M
     Then Block Should Be Active New Type 2

[Scenario-4] Old type-2 block read (size = 128M)
    [Tags]    NewTypeII_Chunk_Read    Tire2
    Given User Has Access to The Data Node
     When User Writes Block With Size 128M
     Then Block Should Be Old Type 2
```

#### Pattern G: State Machine and Block Deleting Test

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Library     OperatingSystem
Library     library.object.blocklayer.state_machine_and_deleting_balance_keywords.StateMachineKeyword

Default Tags    block_layer

Suite Setup       Establish Connection To Platform    ${PLATFORM}
Suite Teardown    Close Clients

*** Test Cases ***
[Scenario-1] Set state machine interval time and check TRANSIENT DOWN
    [Tags]    block_layer    StateMachine    Tier2
    Given User Has Access to The Data Node
     When User Sets State Machine Interval Time
      And User Kills Storage Service
     Then SS Down Type Should Be    TRANSIENT

[Scenario-2] Check USER TypeI Block Deleting
    [Tags]    block_layer    Deleting    Tier2
    Given User Has Access to The Data Node
      And Config Defragmentation Allocation To True
     When User Triggers TypeI Block Deletion
     Then Block Should Be Deleted
```

#### Block.java Coverage Gap Context

When generating tests targeting `Block.java` (38% line coverage, 15.4% branch coverage), the highest-impact uncovered areas are:

| Category | Key Methods | Est. Uncov Lines | E2E Trigger |
|:---------|:-----------|:-----------------|:------------|
| **Geo-replication / Remote Read** | `readRemote`, `tryRemoteCorrection`, `readCopyBlockRemote`, `handleTsoOrPso` | ~280 | S3 GET in multi-zone with local failure |
| **EC Recovery** | `readDataFromEcBtreeBlock`, `triageAndHandleCopiesReadError` | ~220 | S3 GET on EC-encoded blocks with degraded copies |
| **Write Path (async/retry)** | `write3Copies`, `write` (sync+native), `flush`, `AsyncBFWWrite3CopyDataTask` | ~170 | S3 PUT >= 64 MB (BFW 3-copy), writes with transient failures |
| **Read Retry / Block Refresh** | `getCopyListForRead` (retry), `triageAndHandleChunkReadError`, `RefreshChunkInfoTask` | ~90 | S3 GET when copies temporarily unavailable |
| **Copy Selection** | `getCopyListForRead`, `getNormalCopy`, `getReadCopies`, `isNoPotentiallyReadableCopy` | ~60 | S3 GET with blacklisted/hot-disk copies |

### Replication Test Pattern

```robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Resource    robot/general_test_framework_keywords/ecs_setup_keywords.robot
Resource    robot/general_test_framework_keywords/general_s3_keywords/general_s3_bucket_keywords.robot
Resource    robot/general_test_framework_keywords/general_s3_keywords/general_s3_object_keywords.robot

*** Test Cases ***
[Scenario-N] Cross Region Replication Test
    [Tags]    Tier2    Standalone    E2E    Replication
    Given Setup One Cluster
      And Init Global Vars for init    ${node_list1}    ${node_list2}
      And Create Provision Config    ${node1}    ${node2}
     When Create Source Bucket With Versioning Enabled On Source Scale
      And Create Target Bucket with Versioning Enabled On Target Scale
     Then <Replication Verification Keywords>
```

---

## Mapping Java Code Paths to E2E Triggers

### Component-to-Service Reference

The `code-coverage-configs` repo maps Java packages to storage cluster services and components. Key mappings relevant for E2E test triggering:

| Component | Services | Java Package Pattern | E2E Test Area |
|:----------|:---------|:---------------------|:--------------|
| **cm** (Block Manager) | blobsvc, cm, dataheadsvc, ssm | `datasvc/cm/`, `datasvc/ssm/`, `datasvc/libs/client/`, `datasvc/libs/communication/` | Block Layer suites |
| **sr** (Space Reclaim) | blobsvc, cm, rm, sr | `datasvc/sr/`, GC-related packages | Space Reclaim suites |
| **dt-engine** | blobsvc, cm, dataheadsvc, ssm, sr, rm, dtquery, georeceiver | `datasvc/rm/`, `datasvc/libs/directorytable/`, `datasvc/libs/directoryserver/` | DT Query, Replication |
| **geo** | cm, rm, blobsvc, georeceiver | `datasvc/geoservice/`, `datasvc/georeceiver/`, geo-replayer | Replication suites |
| **xor** | cm, blobsvc, georeceiver | XOR encode/decode packages | EC / Replication suites |
| **pso** | cm, rm, blobsvc, georeceiver | Geo-replay and recovery packages | PSO failover suites |
| **tso** | resourcesvc, georeceiver, dtquery | Temp zone failure packages | TSO / zone failure suites |
| **nvmeengine** | dtquery, dataheadsvc, cm, blobsvc, ssm | `datasvc/libs/nvmfcommunicator/`, `datasvc/libs/sscommunicator/`, `datasvc/libs/controller/` | Storage Engine (AFA) |
| **blobsvc** | blobsvc | `datasvc/blobsvc/`, `datasvc/libs/blobclient/` | Object data path |
| **dataheadsvc** | dataheadsvc | `datasvc/heads/dataheadsvc/`, `datasvc/libs/nfsclient/` | S3/CAS head service |
| **objheadsvc** | dataheadsvc | `datasvc/objheadsvc/` | Object head service |
| **dmsvc** | dm, objcontrolsvc, resourcesvc | `datasvc/dm/` | DM service tests |
| **vnest** | vnest | `datasvc/vnest/`, `datasvc/libs/btree/`, `datasvc/libs/paxos/` | VNest / Paxos suites |
| **metering** | blobsvc, metering, dtquery, rm, objcontrolsvc | `datasvc/metering/` | Metering suites |

> **Note:** All components run in the `service-main` container. A change in a multi-service component (e.g., `dt-engine` runs in 8 services) may require E2E tests across multiple suites.

### Detailed Java Code Path → E2E Trigger Mapping

#### Block Client Library (`datasvc/libs/client`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `Block.read(ReadContext)` | S3 GET, read from local copies | Block Layer Pattern A (block_read.robot) | Any |
| `Block.readRemote(ReadContext, ...)` | S3 GET with remote fallback, geo-copy selection | Block Layer Pattern A (multi-zone) | Any |
| `Block.readAhead(...)` | S3 GET read-ahead prefetch | Data Path (large sequential reads) | >= 64MB |
| `Block.readDataFromEcBtreeBlock` | S3 GET on EC-encoded BTree blocks with corruption | Block Layer Pattern C (EC process) | Any |
| `Block.tryRemoteCorrection` / `Block.handleTsoOrPso` | 2-zone remote correction, TSO/PSO inline XOR decode | Block Layer (multi-zone read recovery) | Any |
| `Block.readCopyBlockRemote` | Remote read fallback for COPY blocks | Block Layer (multi-zone) | Any |
| `Block.write(WriteContext)` / `Block.write(NativeWriteContext)` | S3 PUT sync/native write | Block Layer Pattern A (chunk_write.robot) | Any |
| `Block.write3Copies(WriteContext, ...)` | S3 PUT with BFW 3-copy async write | Block Layer Pattern A | >= 64MB |
| `Block.flush(FlushContext, ...)` | Async flush to all copies | Block Layer Pattern A | >= 64MB |
| `Block.getCopyListForRead` / `Block.getNormalCopy` / `Block.getEcCopy` | S3 GET with copy refresh, blacklisted/hot-disk copies | Block Layer Pattern A | Any |
| `Block.triageAndHandleCopiesReadError` | Multi-copy read error with EC sync recovery | Block Layer Pattern C | Any |
| `Block.isBlockAlignmentChunk` | Block-aligned block operations | Block Layer (block alignment tests) | Any |
| `ChunkObject.seal` / `blockSeal` / `fanoutSeal` | Block seal after write completion | Block Layer Pattern A | Any |
| `ChunkObject.isRotationNeeded` | Block rotation trigger on large writes | Data Path (large objects) | >= 128MB |
| `ChunkObject.isTypeI` / `isTypeII` / `isNewTypeII` | Type-specific block behavior | Block Layer (type boundary tests) | Type-1 <44MB, Type-2 >=44MB |
| `ChunkObject.isReplicated` / `isEcEncoded` | Replication/EC status checks | Block Layer Pattern C / Replication | Any |
| `ChunkObject.setEcEncodeInClient` / `shouldDoClientEcEncode` | Client-side EC encoding decision | Block Layer Pattern C | Any |
| `ChunkClient.getNewChunk(ChunkClientWriteContext)` | Block creation (4 overloads by context) | Block Layer (chunk_allocation.robot) | Any |
| `ChunkClientImpl.refreshAndGetChunk` / `refreshAndGetNewTypeIIChunk` | Block metadata refresh on stale cache | Block Layer Pattern A | Any |
| `ChunkClientImpl.fanoutRefreshAndGetChunk` | Fanout refresh for geo blocks | Block Layer (multi-zone) | Any |
| `ChunkClientImpl.isNoSpaceError` | No-space error handling during allocation | Block Layer (chunk_allocation.robot) | Any |
| `ChunkClientImpl.getStoragePolicy` / `getCosByStoragePolicy` | Storage policy resolution | Block Layer (storage tier tests) | Any |

#### Block Writer FSM (`akkaimpl/actor/blockwriter`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `AbstractBlockData.handleFlush` | Large object write (forces block flush) | Data Path (64MB/256MB objects) | >= 64MB |
| `AbstractBlockData.handleCopyWriteResponse` | Object copy / geo-replication deep copy | Replication / CRD Operations | Any |
| `AbstractBlockData.rotateChunk` | Multi-block write (object > block size) | Data Path (large objects) | >= 128MB |
| `ActiveChunkData.handleChunkWrite` | Active block write event processing | Data Path (any S3 PUT) | Any |
| `ActiveChunkData.handleSealChunk` / `sealChunk` | Seal triggered by rotation or completion | Block Layer Pattern A | Any |
| `SealingChunkData.handleCopyWriteResponse` | Final writes during sealing state | Data Path (large objects) | >= 64MB |
| `SealedChunkData.handleChunkWrite` | Write forwarded to next block after seal | Data Path (multi-block writes) | >= 128MB |
| `InitialChunkData.handleActiveChunk` | First block activation in write pipeline | Data Path (any S3 PUT) | Any |
| `BlockWriterFSM` (INITIAL→ACTIVE→SEALING→SEALED) | Full block lifecycle state transitions | Block Layer Pattern G (state_machine.robot) | Any |
| `AsyncChunkWriteResult.isSuccess` / `isNodeDown` | Async write result with node-down detection | Block Layer Pattern F / FI | Any |
| `WriteWindow.getStashedEventNumber` | Write stashing under backpressure | Data Path (high throughput) | Any |

#### Buffered File Writer (`impl/bufferedfilewriter`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `BufferedFileWriter.WriteItem.addBufferItem` | BFW write aggregation | Data Path / Block Layer Pattern A | Any |
| `FanoutBufferedFileWriter.write` | Parallel fanout block writes | Data Path (large objects, EC) | >= 64MB |
| `BufferedFileWriterSharedChunkPrefetcher.getWriteChunk` / `getWriteChunkForFanout` | Shared block prefetch for DT/PageTable | Block Layer (BFW prefetch tests) | Any |
| `BufferedFileWriterSpecificChunkPrefetcher.getSpecificChunk` | Specific block prefetch with cache | Block Layer (chunk_allocation.robot) | Any |
| `BufferedFileWriterRepoSpecificChunkPrefetcher.bondGeoSender` | Geo sender attachment for repo writes | Replication suites | Any |
| `BufferedFileWriterCallbackUtils.preRotateAllCallers` | Pre-rotation callback for all DT callers | Block Layer (block rotation tests) | Any |
| `BufferWriter.write` (Akka) | Akka-based buffer write dispatching | Data Path (any S3 PUT) | Any |
| `RawBlockWriter.write` | Direct raw block writes (geo write mode) | Replication / Geo suites | Any |

#### Block Manager Service (`datasvc/cm`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `BlockManager.start` / `stop` | CM service lifecycle | improve_block_manager_coverage.robot | N/A |
| `BlockServer.init` | CM initialization with configuration | improve_block_manager_coverage.robot | N/A |
| `BlockServer.deleteChunk` / `deleteChunkObject` | Block deletion via GC/space reclaim | Space Reclaim suites | Any |
| `BlockServer.deleteEcCopy` | EC copy deletion | Block Layer Pattern C | Any |
| `BlockServer.queryChunk` | Block metadata query | Block Layer (CM query tests) | Any |
| `BlockServer.updateChecksum` | Checksum update after verification | Block Layer Pattern D (CM FI) | Any |
| `BlockServer.findValidDataLength` | Valid data length discovery for recovery | Block Layer Pattern E (recovery) | Any |
| `BlockServer.updatePendingReplicationBytes` | Pending replication tracking | Replication suites | Any |
| `BlockServer.checkChunkRangeReplication` | Block range replication check | Replication suites | Any |
| `BlockServer.markBlocksAsForceGcVerificationCandidates` | Force GC verification | Space Reclaim (gc_verified_chunk.robot) | Any |
| `BlockServer.handleSetIsEcEncodedRequest` | Toggle EC encoding status | Block Layer Pattern C | Any |
| `BlockServer.handleSetEcRetiring` / `handleSetSealingInProgressStatusRequest` | EC retirement / sealing flags | Block Layer Pattern C | Any |
| `BlockServer.handleSetSegmentRecoveryStatus` | Segment recovery status update | Block Layer Pattern E (recovery) | Any |
| `BlockServer.handleCheckSealTask` | Seal task verification | Block Layer Pattern A | Any |
| `BlockServer.handleFreeRedundantBtree` | Free redundant BTree copies | Block Layer (BTree reclaim tests) | Any |
| `BlockServer.setShouldSealForChunk` / `setShouldSealForMigrationLocalChunk` | Seal for migration/rebalance | Tech Refresh / Migration suites | Any |
| `BlockServer.evictChunk` | Block eviction from cache | Block Layer (geo block cache tests) | Any |
| `BlockServer.listUnrecoverableBlocks` / `listUnrecoverableObjectsInfo` | Unrecoverable block/object listing | Block Layer Pattern E (recovery) | Any |
| `BlockServer.getChunkTableFtt` / `getChunkNodeFtt` | FTT calculation and reporting | Block Layer (FTT tests) | N/A |
| `BlockServer.refreshHierarchyAndCheckDevice` / `refreshRackStatus` | Device/rack hierarchy refresh | Block Layer Pattern G (state_machine) | N/A |
| `BlockServer.updateCopyChunkAllowRemoteRead` | Allow/disallow remote read for copy blocks | Block Layer (multi-zone) | Any |
| `ChunkStreamServer.closeStream` / `collectStreamRetentionDataStats` | Stream operations and retention stats | Stream test suites | Any |
| `JobScheduler.scheduleJob` / `runJobImmediately` | CM job scheduling (copy, EC, recovery) | Block Layer (all CM operations) | Any |
| `Allocator.allocateBlocks` / `findPlacement` | Block allocation with placement constraints | Block Layer (chunk_allocation.robot) | Any |
| `RecoverUtil.getRecoverPriority` | Recovery priority calculation | Block Layer Pattern E (recovery) | Any |
| `FTTCalculator.getChunkLayerFtt` | Fault tolerance threshold calculation | Block Layer (FTT tests) | N/A |
| `FTTMonitor.getStoragePolicy` / `getDefaultPolicy` | Storage policy determination by FTT | Block Layer (storage tier tests) | N/A |
| `GeoChunkCacheManager.addChunkForEviction` / `addHeartBeatInfo` | Geo block cache eviction/heartbeat | Block Layer (geo cache tests) | Any |
| `CapacityManagerImpl.evaluateEligiblePartition` | Storage capacity and partition evaluation | Block Layer (capacity tests) | N/A |

#### Block Server (`datasvc/blockserver/cs-core`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `Type1ChunkObject.write` / `read` | Type-1 block I/O (objects < 44MB) | Block Layer Pattern A | < 44MB |
| `Type2ChunkObject.write` / `read` | Type-2 block I/O (objects >= 44MB) | Block Layer Pattern A | >= 44MB |
| `Type2ChunkObject.replaceBlocks` | Block replacement in Type-2 blocks | Block Layer (block replacement tests) | >= 44MB |
| `NormalChunkObject.write` / `read` | Normal (non-EC) block I/O | Block Layer Pattern A | Any |
| `NormalChunkObject.findValidLogicalLength` | Logical length validation for recovery | Block Layer Pattern E (recovery) | Any |
| `ChunkBinaryReader.read` / `shouldDoCsRead` | Binary read with CS-level decision | Data Path (read validation) | Any |
| `ChunkBinaryWriter.write` / `open` / `close` | Binary write lifecycle | Data Path (write validation) | Any |
| `CSBufferWriter.write` | CS-level buffered write | Data Path (any S3 PUT) | Any |
| `ChunkMirrorCopy.write` | Mirror copy write operations | Block Layer (copy write tests) | Any |
| `ChunkECCopy` | EC copy operations at CS level | Block Layer Pattern C | Any |
| `ChunkStreamWriter.append` / `appendStream` / `open` / `close` | Stream write operations | Stream suites | Any |
| `ChunkStreamReader.open` / `nextBuffer` / `close` | Stream read operations | Stream suites | Any |
| `ChunkStreamManager.delete` / `truncate` | Stream lifecycle management | Stream suites | Any |
| `SegmentDataCache.offer` / `getSegmentData` | Segment data caching | Block Layer (cache tests) | Any |
| `ECBufferCache.borrow` / `giveBack` | EC buffer pool management | Block Layer Pattern C | Any |

#### EC Library (`datasvc/libs/ec`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `ISALEcCodeHelper.encode` / `decode` | ISA-L EC encode/decode (default) | Block Layer Pattern C (cm_ec.robot) | Any |
| `ISALEcCodeHelper.incencode` / `incencodeAnyLength` | Incremental EC encoding | Block Layer Pattern C | Any |
| `NativeEcCodeHelper.encode` / `decode` | Jerasure EC encode/decode (legacy) | Block Layer Pattern C | Any |
| `CompatibleEcCodeHelper.encode` / `decode` | Dynamic ISA-L/Jerasure switching | Block Layer Pattern C | Any |
| `JavaEcCodeHelper.encode` / `decode` | Pure Java EC fallback (2+1 only) | Block Layer Pattern C (degraded) | Any |
| `CRC.crc32` | CRC32 checksum for EC data integrity | Block Layer (checksum tests) | Any |
| EC schemes: 9+3, 6+2, 10+2, 12+4, 24+2, 24+4 | Scheme-specific EC encode/decode | Block Layer Pattern C (per scheme) | Any |
| `EcChunkWorker.init` (CM) | EC block worker initialization | Block Layer Pattern C | N/A |
| `EcRecoverWorker` (CM) | EC recovery execution | Block Layer Pattern C / Pattern E | Any |

#### Geo-Replication (`datasvc/geoservice`, `datasvc/georeceiver`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `GeoService.start` | Geo service lifecycle startup | Replication suites (setup) | N/A |
| `CASReplicationManagerImpl.start` / `shutdown` | CAS replication pipeline lifecycle | CAS Replication suites | Any |
| `CASReplicationPipelineImpl` (EVENT_LISTEN→METADATA_INTERACTION→DATA_SEND→METADATA_UPDATE) | CAS replication pipeline stages | CAS Replication suites | Any |
| `CASMetadataReadStageWorker.process` | CAS metadata read before replication | CAS Replication suites | Any |
| `CASDataSendStageWorker.process` | CAS blob data send to remote zones | CAS Replication suites | Any |
| `GeoReceiverServiceImpl.start` / `shutdown` | Geo receiver server lifecycle | Replication suites (setup) | N/A |
| `ChunkDataGeoReceiver.processChunkDataRequest` | Block data replication across zones | Replication (block shipping tests) | Any |
| `ChunkDataGeoReceiver.processChunkDataRequestWithEC` | EC block replication across zones | Replication (EC geo tests) | Any |
| `ChunkDataGeoReceiver.processRemoteDecodeBlocksForRead` | Remote EC decode for cross-zone read | Block Layer (multi-zone EC read) | Any |
| `ObjectGeoReceiver.processObjectRequest` / `processCheckObjectReplication` | Object metadata replication/verification | Replication suites | Any |
| `ObjectGcGeoReceiver.processObjectReferenceInsert` / `processObjectReferenceList` | Object GC reference replication | Space Reclaim (geo GC tests) | Any |
| `ChunkGcScanGeoReceiver.processGetChunkGcScanTaskRequest` | GC scan task distribution across zones | Space Reclaim (geo GC scan) | Any |
| `BucketGeoReceiver` | Bucket metadata replication | Replication (bucket tests) | Any |
| `KVGeoReceiver.processInsertKV` / `processGetKV` / `processListKV` | KV store replication | Replication (KV tests) | Any |
| `PaxosGeoReceiver` | Paxos consensus replication | Replication (paxos tests) | Any |
| `TempFailureDetector` / `LocalZoneHealthMonitor` | Temporary zone failure detection | TSO test suites | N/A |
| `GeoLivenessMonitor` | Geo-replication liveness monitoring | Replication (liveness tests) | N/A |

#### Geo-Receiver REST Endpoints

| REST Path | Handler Class | E2E Trigger | Robot Keyword Area |
|:----------|:-------------|:-----------|:-------------------|
| `/geo/data` | `ChunkDataService` | Block data replication POST | `geoservice_keywords.py` |
| `/geo/chunkgcscan` | `ChunkGcScanService` | Block GC scan task distribution | `ecs_gc_keywords.py` / `ecs_geo_site_keywords.py` |
| `/geo/objreferences` | `ObjectGcService` | Object GC reference insert/list/progress | `ecs_gc_keywords.py` |
| `/geo/object` | `ObjectService` | Object metadata replication | `ecs_test_replication_geo_keyword.py` |
| `/geo/bucket` | `BucketService` | Bucket metadata replication | `ecs_test_replication_geo_keyword.py` |
| `/geo/kv` | `KVService` | KV store replication | `ecs_test_replication_geo_keyword.py` |
| `/geo/heartbeat` | `HeartbeatService` | TSO heartbeat for zone failure detection | `geoservice_keywords.py` |
| `/compression` | `CompressionConfigService` | Compression config (ZSTD, LZ4, DEFLATE, SNAPPY) | `compression_keywords.py` / `flexible_dataindex_compress_keywords.py` |

#### Space Reclaim / GC (`datasvc/blobsvc/.../gc`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `ObjectLifecycleTaskScanner.scan` | Delete job processing (respects TSO state) | Space Reclaim (gc_function_sanity.robot) | Any |
| `DeleteJobScanner.scan` | Expired delete job scanning | Space Reclaim suites | Any |
| `DeleteJobTable.insertDeleteJob` / `removeDeleteJob` | Delete job management | Space Reclaim suites | Any |
| `DeleteJobTable.listExpiredDeleteJobs` / `listExpiredCleanupJobs` | Expired job listing | Space Reclaim suites | Any |
| `ObjectGcGeoClient.getGlobalReferenceCollectionProgress` | Cross-zone GC progress tracking | Space Reclaim (geo GC tests) | Any |
| `ChunkGcScanTasksImpl.addChunkForCopy` / `addChunkForVerification` | Block GC scan task queuing | Space Reclaim (btree_gcverification.robot) | Any |
| `RebuildReferenceTableScanner` / `RebuildReferenceTableProcessor` | Reference table rebuild | Space Reclaim (rebuild tests) | Any |
| `RepoChunkReferenceScanner` | Repo block reference scanning | Space Reclaim (gc_verified_chunk.robot) | Any |

#### Compression (`datasvc/libs/ni`, `datasvc/objcontrolsvc`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `ISALCompressor.compress` / `uncompress` | ISA-L compression operations | Block Layer Pattern B (compression.robot) | Any |
| `ZstdCompressor.compress` / `uncompress` | Zstandard compression | Block Layer Pattern B | Any |
| `Lz4Compressor.compress` / `uncompress` | LZ4 compression | Block Layer Pattern B | Any |
| `CompressionConfigService.setCompressOption` / `getCompressOption` | Compression algorithm config via REST | Block Layer Pattern B | Any |
| `CompressionConfigService.setCompressRatioThreshold` | Compression ratio threshold config | Block Layer Pattern B | Any |
| `CompressUtils` | Compression utility operations | Block Layer Pattern B | Any |

#### SSM / Storage Server (`datasvc/ssm`, `datasvc/libs/ssmclient`)

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `SSManager.start` / `shutdown` | SSM service lifecycle | Storage Engine suites | N/A |
| `SSManagerClient.allocateBlock` / `confirmAllocation` / `freeBlock` | Block allocation/deallocation | Block Layer (chunk_allocation.robot) | Any |
| `SSManagerClient.listStorageDevices` / `getStorageDeviceCapacity` | Device discovery and capacity | Storage Engine (engine tests) | N/A |
| `SSConfigurationTracker` | Storage server configuration tracking | Storage Engine suites | N/A |
| `DeviceCreationHelper` / `DeviceDeletionHelper` | Device lifecycle management | Storage Engine (device tests) | N/A |
| `DtCreationProgressTracker` | DT creation progress on SSM | DT Query suites | N/A |

#### Utility Classes

| Java Class / Method | E2E Trigger | Test Pattern | Object Size |
|:---------------------|:-----------|:-------------|:------------|
| `ChunkRangeBitHandler.union` / `intersection` / `negation` | Range bit operations for partial reads | Block Layer (partial read tests) | Any |
| `ChunkRangeBitHandler.getRangeBitFromCopies` | Copy availability checking | Block Layer Pattern A | Any |
| `ChunkTypeMapper.mapDataType` | Write type to block data type mapping | Block Layer (type boundary tests) | Any |
| `WriteResult.getSegmentLocationList` | Segment location tracking for writes | Data Path (write verification) | Any |

### Wildcard Pattern Triggers (glob matching)

For Java classes not listed above, match using these glob patterns:

| Java Code Pattern | E2E Trigger | Test Pattern | Object Size |
|:-------------------|:-----------|:-------------|:------------|
| `*ChunkAllocation*` | Block allocation with negative hints, CM refresh | chunk_allocation.robot | Any |
| `*BlockFragmentation*` / `*Defragmentation*` | Block defragmentation, free block metrics | fragmentation.robot | Any |
| `*StateMachine*` / `*TransientDown*` / `*PermanentDown*` | SS state machine transitions, maintenance mode | state_machine.robot | N/A |
| `*SmartRebalance*` | Smart rebalance, node rebalance, marker validation | smart_rebalance.robot | N/A |
| `*Encryption*` / `*Encrypted*` | Operations on D@RE-encrypted buckets | encryption_keywords.robot | Any |
| `*Compress*` / `*CompressInfo*` | Compressed block operations, algorithm switch | compression.robot | Any |
| `*Multipart*` / `*MPU*` | Multipart upload/copy | Data Path (MPU pattern) | >= 256MB |
| `*Versioning*` | Versioned bucket operations | S3 bucket keywords | Any |
| `*Lifecycle*` | Bucket lifecycle operations | General S3 keywords | Any |
| `*BFW*` / `*BufferWriter*` / `*indexGranularity*` | Write path, merged SegmentLocation, BFW merging | Block Layer Pattern A | Any |
| `*SpaceReclaim*` / `*GC*` / `*BigHammer*` | GC/space reclaim, direct reclamation of REPO blocks | Space Reclaim suites | Any |
| `*DT*` / `*DirectoryTable*` | DT initialization/recovery | Enable/Disable DT Query | N/A |
| `*NvmeEngine*` / `*NVMe*` | NVMe storage engine operations | Storage Engine (AFA-specific) | Any |
| `*RDMA*` / `*Communicator*` | RDMA data transfer, large messages >12 KB | communicator.robot / S3 Over RDMA | 100KB-500MB |
| `*SSM*` / `*StorageServerManager*` | Storage server management | FI (restart services) | N/A |
| `*Fabric*` | Fabric agent operations | FI (fabric stop/recover) | N/A |
| `*HAL*` / `*SMART*` | Disk health monitoring | Storage Engine (HAL suite) | N/A |
| `*ECRecover*` / `*ErasureCoding*` | EC recovery executor config, thread/buffer tuning | Pattern C / recovery_enhance_test.robot | N/A |
| `*RecoveryEnhancement*` | Recovery status API, partition mock, disk-down recovery | Pattern E (recovery_enhancement.robot) | N/A |
| `*Replication*` / `*Geo*` | Geo-replication across sites | Replication test pattern | Any |
| `*Stream*` | Stream write/read/truncate operations | Stream suites | Any |
| `*Paxos*` | Paxos consensus operations | VNest / Paxos suites | Any |
| `*Metering*` | Billing/metering operations | Metering suites | Any |

### Key Robot Keyword Libraries for E2E Triggers

| Keyword Library File | Keywords | Primary Java Components Covered |
|:---------------------|:---------|:-------------------------------|
| `geoservice_keywords.py` | ~5160 lines | GeoService, GeoReceiver, CAS replication, zone failure |
| `ecs_test_replication_geo_keyword.py` | 134 keywords | Object/Bucket/KV geo replication, PSO failover |
| `tech_refresh_data_migration_keywords.py` | 119 keywords | Data migration, CM/SSM consistency, block creation |
| `engine_server_manager_keywords.py` | 104 keywords | SSM, disk management, DT readiness, service restart |
| `s3_over_rdma_keywords.py` | 77 keywords | RDMA PUT/GET, multipart RDMA, S5CMD |
| `improve_cm_coverage_keywords.py` | 70 keywords | BlockManager startup, service prefix, page table mode |
| `flexible_dataindex_compress_keywords.py` | 63 keywords | Compression, encryption, partial GC |
| `chunk_allocation_keywords.py` | 59 keywords | Allocator, block creation, negative hints |
| `ecs_geo_site_keywords.py` | 52 keywords | Storage pool, license, GC verification, zone setup |
| `block_fragmentation_keywords.py` | 51 keywords | Block defrag, free block metrics |
| `btree_reclaim_verification_keywords.py` | 45 keywords | BTree GC verification, newSchema GC |
| `spacereclaim_shared_keywords.py` | 41 keywords | Shared GC utilities |
| `ecs_gc_stats_keywords.py` | 41 keywords | GC statistics collection |
| `gc_reduce_metadata_overhead_keywords.py` | 33 keywords | Metadata overhead reduction |
| `ecs_gc_keywords.py` | 24 keywords | Core GC operations |
| `cm_ec_keywords.py` | 29 keywords | EC encode/decode, EC scheme config |
| `chunk_write_and_read_keywords.py` | 29 keywords | Block write/read, BFW operations |
| `communicator_keywords.py` | 25 keywords | RDMA, IAM, stress testing |
| `hal_services_keywords.py` | 23 keywords | HAL, SMART data, disk health |
| `state_machine_and_deleting_balance_keywords.py` | 19 keywords | SS state machine, maintenance mode |
| `blocklayer_shared_keywords.py` | 19 keywords | Shared blocklayer utilities |
| `recovery_enhancement_keywords.py` | 15 keywords | Recovery API, partition mock |
| `chunk_recovery_keywords.py` | 12 keywords | Block recovery operations |
| `cm_fi_keyword.py` | 10 keywords | CM fault injection |

## Object Size Thresholds

These sizes are important for triggering specific code paths:
- **< 1 MB** — single block, no rotation
- **32 MB** — "normal size" in traditional path tests
- **44 MB** — **Type-2 block boundary**: objects >= 44 MB use new Type-2 blocks; < 44 MB use Type-1 blocks
- **64 MB** — "large size" in data path tests, primary write path validation; triggers BFW 3-copy write
- **128 MB** — **old Type-2 block size**: legacy large block format; may trigger block rotation depending on config
- **256 MB** — used for MPU part size validation, multi-block writes
- **500 MB** — MPU full object size
- **1 GB** — large MPU with 256MB parts
- **> 5 GB** — must use multipart upload
- **2,236,960 bytes (~2.1 MB)** — BFW write block max size; `indexGranularity` + overhead (Header + Checksum + Epoch)

---

## Key Shared Keywords Reference

### From `acceptance_keywords.robot`

| Keyword | Purpose |
|---|---|
| `Establish Connection To Platform    ${PLATFORM}` | Connect to target cluster (vsphere/storage/openshift/atlantic/baremetal) |
| `Provisioning    ${settings}` | Full storage cluster provisioning: pool, VDC, RG, namespace, user, bucket |
| `Validate S3 IO for Acceptance` | Get secrets, run S3 acceptance IO, run Mongoose load |
| `Cleanup All Resources Before Testing` | Full cleanup of helm releases, pods, PVCs, CRDs, etc. |
| `Setup Common Environment` | Prepare charts, set release name, set namespace |
| `Prepare Source And Target Settings` | Multi-cluster setup for replication tests |

### From `data_path_services_keywords.robot`

| Keyword | Purpose |
|---|---|
| `The Storage Platform Is Deployed` | Get Storage Platform service endpoints |
| `The ObjectStore Is Deployed and Available` | Full ObjectStore setup: endpoints, token, credentials, S3 client |
| `User Ingests Objects to Bucket    ${bucket_name}` | Ingest objects via S3 connector |
| `Locate Available ObjectStore` | Find and select an available ObjectStore |

### From `general_s3_bucket_keywords.robot`

| Keyword | Purpose |
|---|---|
| `Create a Bucket` | Create S3 bucket (with optional object lock) |
| `Enable Bucket Versioning On Bucket    ${bucket_name}` | Enable versioning |
| `Suspend Bucket Versioning On Bucket    ${bucket_name}` | Suspend versioning |
| `Delete a Bucket    ${bucket_name}` | Delete bucket |
| `Put Bucket Policy    ${bucket_name}    ${policy}` | Attach bucket policy |
| `Create Source Bucket With Versioning Enabled On Source Scale` | Create versioned bucket on source cluster |
| `Create Target Bucket with Versioning Enabled On Target Scale` | Create versioned bucket on target cluster |

### From `SharedObjectKeywords` (Python)

| Keyword | Purpose |
|---|---|
| `The storage cluster Cluster Is Reachable` | Initialize cluster connection |
| `User Has Access to The Data Node` | Select and verify any data node |
| `User Has Access to The NVMe Data Node` | Select and verify NVMe-specific node |
| `Prepare for S5CMD Client` | Initialize S5CMD S3 connector |
| `Cleanup S5CMD Client` | Teardown S5CMD connector |
| `The Client Access Info Are Resolved` | Set CLIENT_IP, CLIENT_USERNAME, CLIENT_PASSWORD globals |
| `Enable DT Query` / `Disable DT Query` | DT query management |
| `Configure Service Logging Level    <service>    <level>` | Change log level (DEBUG/INFO) |
| `All Nodes Are Online` | Verify all cluster nodes are accessible |
| `storage cluster Management Service Is Up In Time` | Wait for management service readiness |

### From `EngineServerManagerKeywords` (Python)

| Keyword | Purpose |
|---|---|
| `User Can Get Targets` | Get NVMe targets via API |
| `User Can Get Partitions` | Get partition info |
| `User Can Get Disks By Node` | Get disk info per node |
| `Modify Disk Num Of One Target    ${count}` | Modify disk count on target |
| `Removed Disks Are Restored` | Restore previously removed disks |
| `User Mocks Disk Failure` | Simulate disk failure |
| `User Removes One Disk From Target Node` | Physical disk removal |
| `User Restarts Services On Nodes    @{services}` | Restart specified services |
| `User Reboots One Slave Node` | Reboot a non-master node |
| `User Launches Object Ingestion Before Starting FI` | Start background IO |
| `User Stops Object Ingestion After Stopping FI` | Stop background IO |
| `User Monitors Pass Rate In Fault Injection Period` | Monitor IO success rate during FI |
| `User Validates That Pass Rate Gets Recovered    ${threshold}` | Assert pass rate recovery |
| `Pass Rate Recovered to Threshold` | Wait for full pass rate recovery |
| `Wait Until DT Gets Ready` | Wait for Directory Table readiness |
| `Process Restarted in Time    <process>    timeout_seconds=${N}` | Assert process restart |
| `Check Removed Disk Operational State` | Verify disk state after removal |

### From `BlockLayerSharedKeywords` (Python)

Base class for all block-layer keyword libraries. Extends `SharedObjectKeywords`. Key constants and capabilities:

| Keyword / Constant | Purpose |
|---|---|
| `KW_CHUNK_READ_RESULT_SUCCESS = "Success"` | Expected success result for block reads |
| `KW_CHUNK_READ_RESULT_NO_CHUNK = "ERROR_NO_SUCH_CHUNK"` | Expected error when block doesn't exist |
| Block size = `128 MB - 128 bytes` | Default block size constant |
| `EcsComponents` enum | Component names: `datahead`, `blob`, `cm`, `rm`, `sr`, `ssm`, `ons`, `dtquery`, `dtsm`, `event`, `resource`, `objcontrol`, `geoservice`, `georeceiver`, `storageserver`, `metering`, `stat`, `vnest`, `zk` |

### From `BlockWriteAndReadKeywords` (Python)

| Keyword | Purpose |
|---|---|
| `User Create Block    count=N    level=N    type=TYPE` | Create blocks (BTREE/JOURNAL/REPO) at specified level |
| `User Read Block Data` | Read block data and validate |
| `Create A New Bucket With Encryption Enabled` | Create encrypted S3 bucket with `x-server-side-encryption-enabled` header |
| `Get Repo Blocks Of Objects In Bucket` | Get REPO blocks by type for objects in bucket |

### From `CompressionKeywords` (Python)

| Keyword | Purpose |
|---|---|
| `Download Corpus Files` | Download Canterbury test corpus for compression testing |
| `Validate Default Value Of CMF Parameter` | Validate default CMF compression settings |
| `Validate Configured Value Of CMF Parameter` | Validate configured CMF values after change |
| `User Changes Compression Algorithm To    algorithm` | Switch compression algorithm (ZSTD/LZ4/SNAPPY/DEFLATE) |

### From `CMECKeywords` (Python)

| Keyword | Purpose |
|---|---|
| `User Injects Blocks And Waits For EC` | Inject blocks and wait for EC process completion |
| `EC Status Should Be COMPLETED` | Assert EC status is `COMPLETED` |
| Expected parameters: `SEALED_NO_EC`, `SEALED_EC_DONE`, `ACTIVE` | Block state validation templates |

### From `RecoveryEnhanceMent` (Python)

| Keyword | Purpose |
|---|---|
| `User Triggers Recovery By Downing Disk` | Trigger recovery by taking a disk offline |
| `Get Recovery Status` | Query `http://{ip}:9101/ssm/recovery-status/{uuid}` |
| `Mock One Partition As Bad By Partition UUID` | Mock a partition as bad for recovery testing |
| `Verify Recovery Remaining Time Non Negative` | Assert `remainingTime >= 0` from recovery API |

---

## CI Tier Organization

Tests are organized into suitelists for CI pipelines:

**Tier 1** (`acceptance-automation-tier1.suitelist`):
```
robot/object/storagedriver/ecs_hal_smart_data_test_suite
robot/object/storagedriver/ecs_data_path_test_suite
robot/object/storagedriver/ecs_storage_engine_test_suite
```

**Tier 2** (`acceptance-automation-tier2.suitelist`):
```
robot/object/storagedriver/ecs_hal_smart_data_test_suite
robot/object/storagedriver/ecs_data_path_test_suite
robot/object/storagedriver/ecs_storage_engine_test_suite
robot/object/storagedriver/ecs_storage_engine_fault_injection_test_suite
```

**Block Layer suites** (most are Tier 2; use `--tag` filtering):
```
robot/object/blocklayer/block_read
robot/object/blocklayer/chunk_write
robot/object/blocklayer/new_type2_block_read
robot/object/blocklayer/new_type2_chunk_write
robot/object/blocklayer/compression
robot/object/blocklayer/chunk_recovery_enhance_test
robot/object/blocklayer/communicator
robot/object/blocklayer/ecs_flex_component_test_chunk_allocation
robot/object/blocklayer/ecs_flex_component_test_fragmentation
robot/object/blocklayer/ecs_flex_component_test_sharelib_cm_ec_for_ecs
robot/object/blocklayer/ecs_flex_regression_test_cm_fi_for_ecs
robot/object/blocklayer/ecs_flex_deleting
robot/object/blocklayer/ecs_flex_state_machine_component
robot/object/blocklayer/ecs_flex_smart_rebalance
robot/object/blocklayer/improve_block_manager_coverage
robot/object/blocklayer/recovery_enhancement
robot/object/blocklayer/recovery_api_verify
```

When adding new test suites, register them in the appropriate `.suitelist` file.

---

## Test Execution

```bash
python3 ./runner.py \
    --environment <ENV> \
    --cluster <CLUSTER_NAME> \
    --profile small \
    --output-dir report \
    --suite <SUITE_NAME> \
    --robot-path robot/<path/to/suite> \
    --include-test-tag <TAG> \
    --exclude-test-tag <TAG>
```

---

## Important Guidelines for Coverage-to-Robot Generation

1. **E2E tests, not unit tests** — every suggested test must be executable against a running storage cluster via S3 API or management API.
2. **Follow the `[Scenario-N]` naming convention** — number new scenarios sequentially after existing ones.
3. **Use BDD style** — Given/When/Then with `And` continuations.
4. **Use existing keywords** — search the keyword libraries before creating new ones.
5. **Always include Setup/Teardown** — clean up all created resources.
6. **Specify object sizes precisely** — many code paths only trigger above certain size thresholds.
7. **Encryption matters** — encrypted vs non-encrypted objects follow different code paths; always specify.
8. **Tag appropriately** — use `Tier1`/`Tier2`, `Parallel`/`Standalone`, and component tags.
9. **Mark FI tests as `Standalone`** — fault injection tests modify cluster state.
10. **Never hardcode credentials or IPs** — use variables from `runner.py` flags or `config.py`.
11. **Python 2/3 compatibility** — if creating new Python keywords, avoid f-strings and Python 3-only syntax.
12. **Run `make lint`** — Pylint compliance is required for all `library/` changes.
