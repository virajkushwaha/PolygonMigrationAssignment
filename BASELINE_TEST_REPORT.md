# Baseline Test Report — Phase 2 Execution & Real-World Testing

**Date**: November 19, 2026  
**Environment**: Windows 11, Python 3.12.3, Django 5.2.3, PostgreSQL 15, Redis 7  
**Branch**: `fix/baseline-application-verification`  
**Target Repository**: `PolygonMigrationAssignment`  

---

## 1. Executive Summary

This report documents the baseline execution and real-world testing of the existing Polygon Migration application. Testing was conducted on the original implementation without applying unauthorized code rewrites. The testing successfully diagnosed startup blockers, verified the operational status of the PostgreSQL database and Polygon API integration, reproduced critical data integrity and edge-case bugs through direct execution, and established a verified baseline for Phase 3 fixes.

---

## 2. Setup Verification

| Component | Status | Details & Observations |
| :--- | :--- | :--- |
| **Git Working Tree** | Verified | Baseline commit: `9f5cc6a` (*Viraj:Initial Code Base*). Switched to branch `fix/baseline-application-verification`. |
| **Python Virtual Environment** | Verified | Python 3.12.3 in `PolygonMigration/.venv`. All dependencies from `requirement.txt` (Django 5.2.3, psycopg2-binary 2.9.10, azure-storage-blob 12.25.1, requests, redis) are installed. |
| **PostgreSQL Database** | Verified | Active on `localhost:5432`. Target database `polygon_migration` contains existing schema tables (`problems_problem`, `problems_problemtestcase`, `problems_sampletestcase`, `users_user`, etc.). |
| **Redis Cache** | Verified | Active on `localhost:6379`. Successfully responded to `PING` (`PONG`). |
| **Database Migrations** | Partial Divergence | Existing migrations up to `0001_initial` applied. However, `Problem.notes` and `Problem.genie_chat` exist on model and in PostgreSQL table, but are missing from migration files (`makemigrations --check` detects pending migration `0002`). |
| **Polygon API Credentials** | Verified | Configured in `PolygonMigration/.env`. Successfully authenticated and fetched live problem metadata and test packages. |
| **Azure Blob Storage** | Not Configured | Placeholder variables in `.env` are commented out (`AZURE_STORAGE_ACCOUNT_URL=None`). Azure credentials must be provided before upload testing. |

---

## 3. Application Startup & Root Cause Diagnosis

### Initial Failure Captured
When running `python manage.py runserver` or `python manage.py showmigrations`, the application immediately crashed with:
```
django.db.utils.OperationalError: connection to server at "localhost" (::1), port 3306 failed: received invalid response to SSL negotiation: J
```

### Root Cause Analysis
- **Classification**: Environment + Configuration Code Precedence Issue.
- **Cause**: In `PolygonMigration/settings.py:19`, `dotenv.load_dotenv(os.path.join(BASE_DIR, '.env'))` was called without `override=True`.
- On the host machine, system environment variables existed from another project (`DB_PORT=3306`, `DB_NAME=employee_db`, `DB_USER=employee_admin`).
- Because `override=False` by default, Django prioritized the OS environment variables over `.env` (`DB_PORT=5432`, `DB_NAME=polygon_migration`).
- Django attempted to send PostgreSQL SSL negotiation packets to port 3306, where a MySQL service was listening. The MySQL service responded with handshake byte `0x4a` (`'J'`), triggering the `OperationalError`.

### Minimal Fix Applied
In `PolygonMigration/settings.py:19`:
```python
load_dotenv(os.path.join(BASE_DIR, '.env'), override=True)
```
Additionally, an empty directory `PolygonMigration/static/` was created to satisfy the `STATICFILES_DIRS` configuration warning.

### Post-Fix Startup Result
- `python manage.py check`: `System check identified no issues (0 silenced).`
- `python manage.py runserver`: Running successfully at `http://127.0.0.1:8000/`.

---

## 4. End-to-End Workflow Testing

### Test 1: User Authentication & Access Control
- **Action**: Submitted POST request to `/users/login/` with staff credentials (`staff@polygon.com`).
- **Expected Result**: Authenticate user, set session cookie, redirect (302) to `/`, and display staff dashboard.
- **Actual Result**: **PASSED**. Received HTTP 200 after redirect; `Welcome, Staff Tester` rendered in navigation bar.

### Test 2: Polygon Problem Fetching
- **Action**: Submitted Polygon Problem ID `69933` (*Guess the Number*) via fetch form.
- **Expected Result**: Retrieve problem metadata, download/extract `problem.html`, fetch all test cases, store test cases in Redis, and render problem details.
- **Actual Result**: **PASSED**.
  - Title: *Guess the Number*
  - Polygon ID: `69933`
  - Statement, Input Format, Output Format extracted cleanly from `problem.html`.
  - Custom checker detected: `check.cpp` (type `custom`).
  - All 21 test cases fetched and displayed in the table with preview values.
  - Test cases cached in Redis under `polygon_migration_test_cases_69933`.

### Test 3: Problem Metadata Persistence (`migrate_to_db`)
- **Action**: Selected difficulty `medium`, added tags `binary-search` and `interactive`, and submitted *"Create/Update problem in Database"*.
- **Expected Result**: Problem record created/updated in PostgreSQL table `problems_problem`; ManyToMany tags linked; sample test cases saved in `problems_sampletestcase`.
- **Actual Result**: **PASSED**.
  - Database row verified: `id: 1, polygon_id: '69933', title: 'Guess the Number', slug: 'guess-the-number', difficulty: 'medium'`.
  - Sample test case verified: 1 sample case in `problems_sampletestcase` (`order: 1`, input: `12 20`, output: `4`).
  - Tags verified: `binary-search`, `interactive` linked in `problems_problem_extra_tags`.

### Test 4: Test Case DB Migration (`migrate_test_cases_to_db`)
- **Action**: Submitted *"Migrate Test Description to Database"* for problem `69933`.
- **Expected Result**: 21 test cases saved into `problems_problemtestcase` with full fidelity.
- **Actual Result**: **PARTIAL / DATA LOSS REPRODUCED**.
  - UI message: `Test cases description migrated to database Successfully.`
  - Database verified: 21 rows present in `problems_problemtestcase`.
  - **Defect Confirmed**: In `problems/views.py:540`, input and output were truncated to 260 characters (`input_data[:260]`).
  - **Defect Confirmed**: Trailing whitespace and line terminators (`\r\n`) were stripped by `.rstrip()`, altering input/output structure.

### Test 5: Azure Blob Storage Migration (`migrate_to_azure`)
- **Action**: Submitted *"Migrate Test Cases to Azure"* with unconfigured Azure settings.
- **Expected Result**: Graceful validation failure informing user that Azure storage configuration is missing.
- **Actual Result**: **FAILED AS EXPECTED (Unconfigured)**.
  - UI Alert: `Migration failed and all changes have been rolled back. Reason: Account URL must be a string.`
  - **Side-Effect Defect Confirmed**: The view's exception handler called `api.clear_test_cases_from_redis(polygon_id)`, purging the cached test cases from Redis.

---

## 5. Classification of Bugs and Issues

### Category A: Bugs Directly Reproduced Through Execution

1. **Environment Loading Precedence (Startup Failure)**
   - **Location**: `PolygonMigration/settings.py:19`
   - **Evidence**: `load_dotenv` without `override=True` loaded `DB_PORT=3306` from host OS, failing PostgreSQL connection.
   - **Impact**: Application crashed on startup.

2. **Test Case Input/Output Data Truncation (260-character limit)**
   - **Location**: `problems/views.py:540-541`
   - **Evidence**: `truncated_input = input_data[:260]`. Tested against database; inputs/outputs longer than 260 characters are permanently clipped.
   - **Impact**: Corrupts test cases in database; competitive programming solutions cannot be judged against truncated inputs.

3. **Stale Test Case Accumulation on Problem Re-migration**
   - **Location**: `problems/views.py:530-563`
   - **Evidence**: Simulated migrating a problem reduced from 21 test cases to 12. The update loop modified rows 0–11; the remaining 9 rows were untouched. Database retained 21 rows.
   - **Impact**: Deleted test cases in Polygon remain active in PostgreSQL, causing synchronization drift.

4. **Duplicate Title / Slug Collision IntegrityError**
   - **Location**: `problems/views.py:298, 385`
   - **Evidence**: Attempted to create a second problem with title *"Guess the Number"* (`slug='guess-the-number'`). Threw `IntegrityError: duplicate key value violates unique constraint "problems_problem_slug_key"`.
   - **Impact**: Unhandled 500 error when migrating any two Polygon problems that share the same title.

5. **Silent Upload Failure Swallowing in Azure Blob Manager**
   - **Location**: `problems/AzureTestcase.py:80-82`
   - **Evidence**: `upload_test_case` catches `Exception` and logs error without re-raising.
   - **Impact**: User receives false success message even if blobs failed to upload to cloud storage.

6. **Missing Method Crash in View Exception Handler**
   - **Location**: `problems/views.py:572`
   - **Evidence**: Code calls `api.delete_azure_blob(azure_blob_problem_id)`. `delete_azure_blob` does not exist on `PolygonAPI`.
   - **Impact**: An Azure failure triggers a secondary `AttributeError`, masking the root cause.

7. **Aggressive Cache Invalidation on Failure**
   - **Location**: `problems/views.py:574`
   - **Evidence**: Any failure in `views.py` calls `api.clear_test_cases_from_redis(polygon_id)`.
   - **Impact**: Forces redundant, slow re-fetching of all test cases from Polygon on subsequent attempts.

---

### Category B: Issues Identified Through Code Inspection

1. **Unmigrated Model Fields**: `Problem.notes` and `Problem.genie_chat` are declared in `problems/models.py`, but missing from `problems/migrations/0001_initial.py`.
2. **Blocking Network I/O Inside Database Transaction**: In `views.py:186`, `api.migrate_to_azure_blob` and package downloading run inside `with transaction.atomic():`, keeping database transaction locks open during long HTTP calls.
3. **Fragile Azure Authentication**: `AzureBlobManager` relies exclusively on `UsernamePasswordCredential` (ROPC), which fails on modern Azure AD tenants with MFA. Needs support for Connection String or Storage Account Key.
4. **OS-Dependent Custom Checker Artifact Naming**: In `polygon_api.py:655`, blob naming switches between `.exe` and extensionless binary based on the host OS of the developer machine.
5. **Empty Test Case Dropping**: In `views.py:538`, `if input_data and output_data:` drops valid test cases where input or expected output is an empty string.

---

### Category C: Features Not Tested Due to Missing Configuration

1. **Live Azure Blob Upload & Container Verification**:
   - Reason: Azure credentials in `.env` are currently template placeholders (`AZURE_STORAGE_ACCOUNT_URL=None`).
   - Action Required: Real Azure Storage Connection String or Account URL + Key must be configured to execute live blob uploads in Phase 4.

---

## 6. Summary of Baseline Status

| Workflow Step | Verified Working? | Issues Found |
| :--- | :--- | :--- |
| Application Startup | **YES** (after `override=True`) | Fixed environment loading precedence |
| Staff Authentication | **YES** | Works as intended |
| Fetch Polygon Metadata | **YES** | Works; takes ~40s due to full package download |
| Fetch Polygon Test Cases | **YES** | Works; successfully cached in Redis |
| Save Problem to DB | **YES** | Works; fails on duplicate title / slug collision |
| Save Test Cases to DB | **YES** (with data corruption) | Truncates to 260 chars; leaves stale test cases |
| Migrate to Azure | **NO** | Missing Azure credentials; silent swallowing in manager |

---

## 7. Next Steps

With the baseline verified and all issues categorized with empirical evidence, we are ready to proceed to:
1. **Step 1 (Fix Missing Migration)**: Generate and apply `0002_problem_notes_genie_chat.py`.
2. **Step 2 (Fix Test Case Data Loss)**: Remove 260-byte truncation, preserve empty test cases, and retain whitespace.
3. **Step 3 (Implement Safe Synchronization)**: Cleanly delete removed test cases and sample cases without stale accumulation.
4. **Step 4 (Handle Duplicate Problem Titles)**: Generate collision-free unique slugs using Polygon ID as external identity.
