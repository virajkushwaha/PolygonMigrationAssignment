# Issues Analysis

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| Product Issues | 0 | 0 | 3 | 0 | 3 |
| Code Issues | 0 | 3 | 0 | 0 | 3 |

---

## Product Issues

### [P1] Misleading Rollback Error Message During Read-Only Polygon Fetch

**Severity**: Medium

**Location**: `PolygonMigration/problems/views.py:699-702`, `PolygonMigration/problems/templates/problems/index.html:150-155`

**Description**:
When a user enters an invalid or inaccessible Polygon Problem ID and clicks "Fetch Problem from Polygon", the UI displays an error alert stating that a database migration failed and all changes were rolled back. 

In `views.py:425`, `api.get_problem_info(polygon_id)` throws an exception when the Polygon API returns `status: FAILED` (or an HTTP error). Because the view's outer exception handler (`views.py:699-702`) wraps all operations, it unconditionally prepends `"Migration failed and all changes have been rolled back. Reason: "` to `context['error']`. 

A fetch operation is strictly a read-only preview that performs no database writes or transactional operations. Displaying a rollback message for a read-only fetch failure misleads users about what action actually occurred.

**Impact**:
- Users are led to believe a database write was attempted and aborted, causing confusion during routine problem lookups.
- Technical support and instructors receive misleading bug reports referencing failed rollbacks during read operations.
- Masks the root problem (e.g., non-existent problem ID or missing permissions on Polygon) under database failure terminology.

**Suggested Fix**:
Implement context-specific error handling based on the user action. For read-only fetch requests (when no migration flags such as `migrate_to_db` or `migrate_to_azure` are present in `request.POST`), format the error message specifically as a fetch error (e.g., `f"Failed to fetch problem from Polygon: {str(e)}"`). Reserve the `"Migration failed and all changes have been rolled back"` message strictly for failed database transaction blocks.

---

### [P2] Existing Problem Tags Cleared When Metadata Re-Migration POST Omits Tags

**Severity**: Medium

**Location**: `PolygonMigration/problems/views.py:601-605`, `PolygonMigration/problems/templates/problems/index.html:259-263`

**Description**:
When a problem's metadata is re-migrated to the database (`migrate_to_db`), the backend unconditionally clears all existing tags before processing the submitted tags:
```python
# Clear existing tags
problem_obj.extra_tags.clear()
```
In standard browser usage with JavaScript enabled, the page populates hidden input fields inside `#hidden-tags-container` (`index.html:259-263`) via `updateHiddenInputs()` (`index.html:429, 614-623`), preserving the selected tags. 

However, if a POST request omits the `tags` parameter—such as an automated script, API client, curl call, or a browser where JavaScript failed to populate the hidden container—`request.POST.getlist('tags')` returns an empty list `[]`. The backend executes `extra_tags.clear()`, finds no tags in the payload, and commits, permanently stripping all existing tags from the problem.

**Impact**:
- Problems can unintentionally lose all categorized tags (`ProblemTag`) during automated metadata updates.
- Reliance on client-side JavaScript to maintain database relations creates data loss risk when forms are submitted outside normal UI flows.
- No omitted-tags regression test currently exists in the test suite to guard against this data loss.

**Suggested Fix**:
Update `views.py:601` to distinguish between an intentional tag removal and an omitted field:
1. Only call `problem_obj.extra_tags.clear()` if the form explicitly indicates that tag management was part of the submission (e.g., checking `'tags' in request.POST` or an explicit `tags_submitted` hidden flag).
2. If `tags` is omitted from the POST payload, preserve existing `extra_tags` associations.
3. Add an automated regression test verifying that re-migrating metadata with an omitted `tags` payload preserves existing tag relationships.

---

### [P3] No Meaningful Progress Information During Bulk Google Drive Upload

**Severity**: Medium

**Location**: `PolygonMigration/problems/polygon_api.py:907-917`, `PolygonMigration/problems/templates/problems/index.html:446-476`

**Description**:
When migrating test cases to Google Drive, `migrate_to_google_drive()` runs a sequential loop uploading both input (`{test_number:02d}`) and output (`{test_number:02d}.a`) files across $2 \times N$ individual blocking Google Drive API HTTP requests:
```python
for idx, test in enumerate(test_cases, start=1):
    drive_manager.upload_test_case(problem_id_for_naming, idx, input_data, output_data)
```
On the frontend, submitting `#migrate-azure-form` executes `showLoading('Migrating to Google Drive...')` (`index.html:448-450`), displaying a full-page modal with a static spinning circle.

Because the entire upload executes synchronously within a single blocking HTTP POST request, the UI provides zero granular progress information: no current test case index (e.g., "Uploading test case 14 of 40"), no progress bar, and no elapsed or estimated time.

**Impact**:
- For problems with 30+ test cases, uploads can take 20–60+ seconds over standard networks. Users cannot tell whether the upload is actively progressing, running slowly, or frozen.
- Without feedback, users may refresh the page or click repeatedly out of uncertainty, potentially triggering redundant operations.
- Increases the risk of client or gateway HTTP timeout errors (504 Gateway Timeout) on large test sets.

**Suggested Fix**:
1. Short-term: Utilize Server-Sent Events (SSE) or streaming responses to stream incremental upload progress (e.g., test $i$ of $N$ completed) to the frontend.
2. Long-term: Offload cloud storage uploads to a background task worker (e.g., Celery or Django-Q) and poll a status endpoint (`/migration/status/<task_id>/`) with an animated progress bar and detailed status updates.

---

## Code Issues

### [C1] Polygon API HTTP 429 Rate Limiting Handled Without Retry or Exponential Backoff

**Severity**: High

**Location**: `PolygonMigration/problems/polygon_api.py:95-115`, `PolygonMigration/problems/polygon_api.py:365-401`

**Description**:
In `polygon_api.py:365-401`, `get_all_test_cases()` fetches test inputs and answers sequentially in a tight loop:
```python
for test in tests:
    input_res = self._make_plain_request('problem.testInput', ...)
    output_res = self._make_plain_request('problem.testAnswer', ...)
```
For a problem with 50 test cases, this issues 101 consecutive HTTP requests with zero delay (`time.sleep`) between calls. 

In `_make_request()` (`polygon_api.py:96-97`):
```python
response = requests.post(f"{self.API_URL}{method_name}", data=post_params)
response.raise_for_status()
```
If Polygon returns `HTTP 429 Too Many Requests`, `response.raise_for_status()` raises `requests.exceptions.HTTPError`. The method does not inspect `response.status_code == 429`, does not check the `Retry-After` header, and contains no exponential backoff or retry logic. The exception immediately bubbles up and aborts the entire fetch.

**Impact**:
- During manual testing with larger test sets, unthrottled sequential requests encountered an HTTP 429 rate-limit error at test case 16, causing the entire fetch operation to terminate and discarding all previously fetched test cases in that run.
- The lack of retry or backoff makes fetching problems with large test sets vulnerable to transient rate limiting.

**Suggested Fix**:
Configure a retry strategy using `urllib3.util.retry.Retry` mounted onto a `requests.Session` in `PolygonAPI`:
```python
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

retry_strategy = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    respect_retry_after_header=True
)
```
Add explicit handling for 429 responses with exponential backoff and introduce a small configurable delay (e.g., 0.25–0.5s) between sequential test-case requests.

---

### [C2] Redis Cache Invalidation Key Pattern and Argument Mismatch

**Severity**: High

**Location**: `PolygonMigration/problems/views.py:374-379`, `PolygonMigration/problems/polygon_api.py:956-974`

**Description**:
Before migrating test cases to cloud storage, `views.py:379` attempts to clear cached test cases from Redis:
```python
problem_id = problem_obj.id if problem_obj else None
...
if problem_obj:
    api.delete_problem_test_case_cache(problem_id)
```
Notice that `problem_id` passed here is `problem_obj.id`—the **database integer primary key** (e.g., `42`).

Inside `delete_problem_test_case_cache()` (`polygon_api.py:963`):
```python
pattern = f"oj_dev_with_redis_storage_test_cases_{db_problem_id}*"
for key in r.scan_iter(match=pattern):
    r.delete(key)
```
However, `store_test_cases_in_redis()` (`polygon_api.py:1008`) and `get_test_cases_from_redis()` (`polygon_api.py:1055`) use a completely different prefix keyed by the **Polygon problem ID** (e.g., `1001`):
```python
prefix = f"polygon_migration_test_cases_{polygon_id}"
```
Because `oj_dev_with_redis_storage_test_cases_42*` does not match `polygon_migration_test_cases_1001_*`, the deletion scan matches zero keys and deletes nothing.

**Impact**:
- `delete_problem_test_case_cache()` is completely ineffective and acts as a silent no-op.
- When `migrate_to_google_drive()` runs at `polygon_api.py:880`, `get_test_cases_from_redis(polygon_id)` reads the uninvalidated cache if keys are still within their 30-minute TTL, bypassing Polygon.
- If problem test cases were updated on Polygon prior to migration, the cloud storage migration may upload stale test cases from the cache.

**Suggested Fix**:
Standardize the Redis key prefix and function signature:
1. Update `delete_problem_test_case_cache` to use the active platform prefix:
   ```python
   pattern = f"polygon_migration_test_cases_{polygon_id}*"
   ```
2. In `views.py:379`, pass `polygon_id` (the Polygon problem identifier) rather than `problem_obj.id`.
3. Alternatively, deprecate `delete_problem_test_case_cache()` and call `clear_test_cases_from_redis(polygon_id)` (`polygon_api.py:1086`), which already uses the correct `polygon_migration_test_cases_{polygon_id}*` pattern.

---

### [C3] Potential Race Condition in Test-Case Synchronization Under Concurrent Requests

**Severity**: High

**Location**: `PolygonMigration/problems/models.py:147-158`, `PolygonMigration/problems/views.py:127-156`

**Description**:
In `views.py:127-156`, `sync_problem_test_cases()` synchronizes test cases within a `with transaction.atomic():` block. However, it queries existing records without row-level locking (`select_for_update()`):
```python
existing_tcs = list(ProblemTestCase.objects.filter(problem=problem_obj).order_by('order'))
```
Furthermore, the `ProblemTestCase` model (`models.py:147-158`) defines no database-level unique constraint on `(problem, order)`:
```python
class Meta:
    ordering = ['problem', 'order']
```
*(Notice the absence of `unique_together = ('problem', 'order')` or `UniqueConstraint`.)*

Under PostgreSQL's default `READ COMMITTED` isolation, if two concurrent requests migrate the same problem at the same moment, both transactions can read zero existing test cases and both execute `ProblemTestCase.objects.create()`. Because the database schema enforces no uniqueness constraint on `(problem, order)`, both transactions can commit, creating a potential race condition where duplicate test case records with identical order values may be persisted in PostgreSQL.

*(Note: Sequential repeated migration is fully verified and safe via automated unit tests; this issue represents an unhandled concurrency hazard identified through code inspection and database schema analysis, and has not been reproduced under live concurrent execution.)*

**Impact**:
- If concurrent requests migrate test cases for the same problem simultaneously, interleaved execution may insert duplicate `ProblemTestCase` records with duplicate `order` values, leading to data inconsistency in problem test case collections.
- Corrupts test case count and order sequence used by online judge grading workers.

**Suggested Fix**:
1. Add a unique constraint to `ProblemTestCase`:
   ```python
   class Meta:
       ordering = ['problem', 'order']
       constraints = [
           models.UniqueConstraint(fields=['problem', 'order'], name='unique_problem_test_case_order')
       ]
   ```
2. Lock the parent problem row or test-case rows during synchronization using `select_for_update()`:
   ```python
   problem_obj = Problem.objects.select_for_update().get(id=problem_obj.id)
   ```
3. Generate and run the corresponding database migration.

---

## Edge Case Analysis

### Q1

> A Polygon problem has 0 sample test cases but 15 regular test cases. What happens when you migrate this problem?

**Answer**:
1. **API Retrieval**: In `polygon_api.py:372`, `get_all_test_cases()` inspects `test.get('useInStatements', False)`. Because all 15 tests are regular tests, `is_sample` is set to `False` for all 15 test dictionaries.
2. **Database Migration**:
   - `sync_sample_test_cases()` (`views.py:177`) filters `sample_tests = [test for test in test_cases if test.get('is_sample', False)]`, which evaluates to an empty list `[]`. Any pre-existing `SampleTestCase` rows for this problem are pruned via `.delete()`. `SampleTestCase` has exactly **0** records.
   - `sync_problem_test_cases()` (`views.py:130-156`) iterates through all 15 test dictionaries and creates 15 `ProblemTestCase` rows with `is_sample=False` and sequential `order=1..15`.
   - `problem_obj.test_case_count` is set to 15.
3. **Cloud Storage Migration (If Separately Executed)**:
   - If the user separately triggers cloud migration (via the "Migrate Test Cases to Google Drive" button), `migrate_to_google_drive()` (`polygon_api.py:907-917`) iterates through all 15 test cases and uploads 30 files: 15 input files (`01` through `15`) and 15 output files (`01.a` through `15.a`) under `test_cases/{db_problem_id}/`.
4. **Verification Status**:
   - The separation mechanism of sample and regular test cases is verified by automated test `test_sample_and_regular_test_cases_remain_correctly_separated` in `tests.py:353-386` (which tests 2 sample + 3 regular cases, and 1 sample + 2 regular cases).
   - Behavior for 0 sample + 15 regular test cases is verified via **Code Inspection** of `sync_sample_test_cases()` and `sync_problem_test_cases()`.

---

### Q2

> A problem is migrated with 20 test cases. Later, the problem setter removes 8 test cases on Polygon (now 12 remain). The problem is re-migrated. What happens?

**Answer**:
1. **Database Re-Migration**:
   - `sync_problem_test_cases()` (`views.py:127-165`) loads the 20 existing `ProblemTestCase` records ordered by `order`.
   - For indices `0` to `11` (the 12 active tests), it updates the existing records in place (updating `input`, `output`, `description`, `is_sample`) and collects their primary keys in `active_ids`.
   - At line 158, it queries stale records:
     ```python
     stale_qs = ProblemTestCase.objects.filter(problem=problem_obj).exclude(id__in=active_ids)
     deleted_count, _ = stale_qs.delete()
     ```
     This permanently deletes the remaining 8 records (`order=13..20`).
   - Line 163 updates `problem_obj.test_case_count = 12`.
2. **Cloud Storage Re-Migration (Requires Separate Execution)**:
   - Google Drive cleanup and re-upload require the cloud migration action (`migrate_to_azure` / `migrate_to_gdrive`) to be triggered separately. A database-only re-migration does not automatically update cloud storage, meaning Google Drive will continue to hold all 20 test cases until the cloud migration button is explicitly executed.
   - When cloud migration is triggered, `drive_manager.empty_problem_files(problem_id_for_naming)` executes first (`polygon_api.py:905`), deleting all existing files under `test_cases/{db_problem_id}/`. The upload loop (`polygon_api.py:907-917`) then uploads the 12 active test cases (24 files: `01`–`12` and `01.a`–`12.a`).
3. **Verification Status**:
   - **Automated Test Verified**: The database reduction from 20 to 12 is fully verified by `test_remigration_after_reducing_collection_from_20_to_12` in `tests.py:238-264`. Google Drive pre-cleanup deletion is verified by `test_empty_problem_files_deletes_existing_files` in `tests.py:797-824`.

---

### Q3

> Two different Polygon problems have the exact same title: "Two Sum". You migrate the first one successfully. Then you try to migrate the second one. What happens?

**Answer**:
1. **Slug Disambiguation**:
   - When the first problem (`polygon_id='101'`, title `"Two Sum"`) is migrated, `generate_unique_slug()` (`views.py#L209-L252`) generates `two-sum`. Because no other problem uses this slug, `slug='two-sum'` is assigned.
   - When the second problem (`polygon_id='202'`, title `"Two Sum"`) is migrated, line 237 detects that `slug='two-sum'` is already taken by problem `101`.
   - Line 241 appends the Polygon ID to create a deterministic slug: `two-sum-202`. Because this slug is available, it is assigned.
2. **Database Integrity**:
   - Both `Problem` records are successfully created and saved in PostgreSQL.
   - `polygon_id` is unique (`unique=True` on `Problem.polygon_id`), and both slugs are unique, satisfying the unique database constraints without raising `IntegrityError`.
3. **Cloud Storage Paths**:
   - Test cases are stored under folder paths keyed by the database problem ID or polygon ID (`test_cases/{db_id}/`), ensuring no file collisions occur between the two problems in Google Drive.
4. **Verification Status**:
   - **Automated Test Verified**: Fully covered and verified by `test_two_different_polygon_ids_with_same_title` in `tests.py:454-477`.

---

### Q4

> When test cases are saved to the database via "Migrate Test Cases to DB", some data is intentionally discarded. What data is lost? Why might this cause problems?

**Answer**:
1. **What Polygon Provides vs. What Is Stored**:
   - In `polygon_api.py:369-374`, `get_all_test_cases()` parses Polygon's test objects and extracts `'index'`, `'manual'`, `'useInStatements'`, `'description'`, `'input'`, and `'output'`.
   - In `models.py:147-158`, `ProblemTestCase` only has columns for `problem`, `is_sample`, `input`, `output`, `description`, and `order`.
2. **Specific Data Discarded**:
   - **Extracted but Not Persisted**:
     - `manual`: The boolean flag indicating whether the test case was hand-written or generated by a script is extracted at `polygon_api.py:371` (`test.get('manual', False)`), but dropped because `ProblemTestCase` has no `manual` column.
     - `index`: Polygon's internal test index is discarded; `views.py:131` replaces it with sequential 1-based array ordering (`order = idx + 1`).
   - **Not Extracted by Client**:
     - Subtask / test group designations (`group`).
     - Point values / test case weights (`points`).
     - Test generation script commands and parameters (`cmd`).
3. **Why This Causes Problems**:
   - **Potential Limitation for Subtask and Partial Scoring**: Modern competitive programming platforms (such as IOI-style evaluations) often evaluate submissions using subtask groupings and weighted points. Because `group` and `points` are not captured or persisted in `ProblemTestCase`, extending the platform to support partial scoring or subtask dependencies in the future would require modifying the schema and API extraction layer.
   - **Loss of Auditability and Test Provenance**: Dropping `manual` and generator command parameters means engineers cannot verify whether a failing test case was hand-crafted for an edge case or generated via randomized stress testing, making test regeneration impossible from database records alone.
4. **Verification Status**:
   - Verified via **Code Inspection & Model Schema Audit** by comparing dictionary extraction in `polygon_api.py:369-374` against `ProblemTestCase` columns in `models.py:147-158`.

---

## Severity Guidelines

Use these definitions when assigning severity:

| Severity | Definition | Examples |
|----------|------------|----------|
| **Critical** | System broken, security vulnerability, data loss | SQL injection, authentication bypass, data corruption |
| **High** | Major functionality broken, significant data integrity risk | Feature doesn't work, orphaned records, race conditions |
| **Medium** | Feature partially broken, poor UX, code maintainability | Missing validation, confusing errors, code duplication |
| **Low** | Minor issues, cosmetic, best practice violations | Unused imports, inconsistent formatting, missing logs |

---

## Notes

1. **Decoupled Three-Step Migration Workflow**: Metadata migration (`migrate_to_db`), DB test case migration (`migrate_test_cases_to_db`), and Cloud Storage migration (`migrate_to_azure`/`migrate_to_gdrive`) are implemented as three independent buttons in the UI. If a problem setter updates test cases on Polygon and re-migrates only to the database, Google Drive remains on the old test set without warning the user of data drift.
2. **Shared OAuth Token File and Authorization Consideration**:
   - *Shared Token File*: In `GoogleDriveStorage.py:38-44`, authorized Google Drive OAuth credentials are saved to a single server-wide file at `PolygonMigration/google_drive_token.json`. Consequently, all staff users share the same authorized Google Drive account for problem migrations rather than individual user-specific accounts.
   - *Route Authorization*: In `PolygonMigration/problems/views.py:720, 747`, the OAuth initiation and callback views (`google_drive_authorize` and `google_drive_oauth2callback`) currently lack Django staff authorization decorators (`@user_passes_test` or `@login_required`). While the OAuth flow validates CSRF state against the user session, securing these views with staff permission checks would ensure only authenticated staff members can initiate or complete Google account authorization.
3. **Host-Dependent Custom Checker Compilation**: In `polygon_api.py:523-527`, custom checker compilation compiles a Windows `.exe` when executed on Windows. If judge worker nodes run on Linux containers, compiled binaries will fail to execute unless cross-compiled or built inside a standardized Linux container.
