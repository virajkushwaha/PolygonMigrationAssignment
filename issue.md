# Project Issues & Bug Analysis

This document details all the technical bugs, data integrity issues, and architectural challenges I encountered during the Polygon migration project, along with their root causes, impacts, fixes applied, and current statuses.

---

### Issue 1: Database Connection Failure (OS Environment Variable Override)

- **Severity**: Critical
- **Location**: `PolygonMigration/settings.py`
- **Problem**: When starting the application, Django refused to connect to PostgreSQL on port `5432` and repeatedly attempted to connect to port `3306` (MySQL), causing startup failure.
- **Root Cause**: Existing system-level environment variables on my local machine took precedence over the project's `.env` file because `load_dotenv()` was executed without the `override=True` parameter.
- **Impact**: The application could not establish a database connection, completely halting local development and migration runs.
- **Fix Applied**:
  Updated `PolygonMigration/settings.py` to force `.env` values to override system environment variables:
  ```python
  load_dotenv(os.path.join(BASE_DIR, '.env'), override=True)
  ```
- **Result / Status**: **Fixed & Verified**. Django connects to the correct local PostgreSQL instance on port `5432`.

---

### Issue 2: Missing Problem Model Migration (`notes` & `genie_chat`)

- **Severity**: High
- **Location**: `problems/models.py`, `problems/migrations/`
- **Problem**: Running `python manage.py migrate --check` exited with return code `1`, indicating that model changes existed without corresponding migration files.
- **Root Cause**: The `notes` (`models.TextField(null=True, blank=True)`) and `genie_chat` (`models.JSONField(null=True, blank=True)`) fields were added to the `Problem` model in `problems/models.py`, but the migration was never generated.
- **Impact**: Database drift between Django ORM models and the actual PostgreSQL schema. If unapplied, any query referencing `notes` or `genie_chat` would crash with `UndefinedColumn` database errors.
- **Fix Applied**:
  Generated and applied a dedicated migration:
  ```bash
  python manage.py makemigrations problems
  python manage.py migrate problems
  ```
  Created migration `0002_problem_genie_chat_problem_notes.py`.
- **Result / Status**: **Fixed & Verified**. PostgreSQL columns match model definitions, and `python manage.py migrate --check` passes cleanly.

---

### Issue 3: Test Case Data Truncation (260 Character Hard Limit)

- **Severity**: Critical
- **Location**: `problems/views.py` (inside test case processing)
- **Problem**: When migrating test cases from Polygon into the database, inputs and outputs were truncated to a maximum length of 260 characters.
- **Root Cause**: Hardcoded slicing in `views.py`:
  ```python
  input_data = input_data[:260]
  output_data = output_data[:260]
  ```
- **Impact**:
  - Test cases with inputs or outputs longer than 260 characters were permanently corrupted in the database.
  - Submissions evaluated against truncated test cases would produce false positives or false negatives.
- **Fix Applied**:
  Removed the hardcoded slicing entirely, allowing `input_data` and `output_data` to be saved in full.
- **Result / Status**: **Fixed & Verified**. Large test cases of arbitrary size are preserved in their entirety.

---

### Issue 4: Test Case Data Modification (Whitespace Stripping & Empty Value Dropping)

- **Severity**: High
- **Location**: `problems/views.py`
- **Problem**:
  1. Trailing spaces and newline characters were stripped from inputs and outputs.
  2. Test cases with valid empty inputs or outputs (`""`) were skipped and dropped from the database.
- **Root Cause**:
  1. `.rstrip()` was called on test case strings before saving.
  2. Truthiness checks like `if not input_data:` treated empty strings `""` as missing data.
- **Impact**: In competitive programming, exact whitespace and trailing newlines are strictly checked by diff evaluators. Stripping them corrupts the test data. Dropping empty inputs/outputs breaks problems where an empty input or output is an intentional edge case.
- **Fix Applied**:
  1. Removed `.rstrip()` to preserve exact byte-for-byte formatting.
  2. Changed truthiness checks to explicit `None` checks:
     ```python
     if input_data is None:
         continue
     ```
- **Result / Status**: **Fixed & Verified**. Exact whitespace formatting and valid empty test cases are saved accurately.

---

### Issue 5: Stale Test Cases After Re-migration (Test Case Synchronization)

- **Severity**: High
- **Location**: `problems/views.py`
- **Problem**: When a problem was re-migrated after test cases were deleted on Polygon, the deleted test cases remained in the local database.
  - *Example*: A problem originally had 20 test cases. On Polygon, 8 tests were removed, leaving 12. After re-migrating, the database still contained 20 test cases.
- **Root Cause**: The migration logic used `update_or_create` based on index numbers, but never deleted records with indices greater than the current count from Polygon.
- **Impact**: The local database accumulated obsolete, invalidated test cases that no longer belonged to the problem.
- **Fix Applied**:
  Implemented synchronization and pruning logic inside an atomic transaction:
  ```python
  ProblemTestCase.objects.filter(problem=db_problem, test_case_number__gt=current_count).delete()
  ```
  Applied equivalent pruning for `SampleTestCase`.
- **Result / Status**: **Fixed & Verified**. Re-migrating a problem strictly synchronizes the database with Polygon's active test set.

---

### Issue 6: Duplicate Problem Title Handling (Slug Collisions)

- **Severity**: High
- **Location**: `problems/views.py`, `problems/models.py`
- **Problem**: Two distinct Polygon problems with the same title (e.g. Polygon ID `1001` and `2002`, both titled "Two Sum") caused an `IntegrityError` due to a unique constraint violation on the `slug` field.
- **Root Cause**: The slug was generated solely using `slugify(title)`. When the second problem was saved, Django attempted to insert a duplicate slug.
- **Impact**: Any problem sharing a title with an existing record could not be migrated into the database.
- **Fix Applied**:
  1. Established Polygon Problem ID as the primary authoritative external identity.
  2. Implemented unique slug generation logic: if `slugify(title)` already belongs to a different problem, a unique suffix (such as `-{polygon_id}` or an incremental number) is appended without altering the user-facing title.
- **Result / Status**: **Fixed & Verified**. Multiple problems with identical titles now coexist without constraint violations.

---

### Issue 7: Cloud Storage: Service Account Quota Failure on Google Drive

- **Severity**: Critical
- **Location**: `problems/GoogleDriveStorage.py`
- **Problem**: When uploading test cases to Google Drive using a Google Service Account, the API failed with:
  ```
  HttpError 403: The user's Drive storage quota has been exceeded.
  ```
- **Root Cause**: Google Service Accounts have a default quota of 0 bytes on personal Google Drive accounts. They cannot own uploaded files unless part of an enterprise Google Workspace with domain-wide delegation.
- **Impact**: Cloud test case migration failed completely for personal Gmail users.
- **Fix Applied**:
  Replaced Service Account authentication with a **Google OAuth 2.0 Web Application Flow**:
  - Implemented authorization and callback endpoints.
  - Stored user refresh tokens securely in `google_drive_token.json`.
  - Implemented automatic token refresh when access tokens expire.
  - Maintained the required directory structure: `test_cases/{problem_id}/{test_number:02d}` and `{test_number:02d}.a`.
- **Result / Status**: **Fixed & Verified**. Test case files upload under the user's personal Google Drive quota (15 GB+).

---

### Issue 8: Django URL Routing Failure for Google OAuth Endpoints (404 Error)

- **Severity**: Medium
- **Location**: `PolygonMigration/urls.py`, `problems/urls.py`
- **Problem**: Accessing `http://localhost:8000/oauth/google/authorize/` returned a 404 Not Found error.
- **Root Cause**:
  1. The Django server process was started with `--noreload` before the OAuth routes were created, so the running server retained the old routing table in memory.
  2. The root URL configuration in `PolygonMigration/urls.py` did not explicitly expose the OAuth endpoints, and `problems/urls.py` declared `app_name = 'problems'`, preventing non-namespaced resolution.
  3. The callback route was named `oauth2callback` instead of `google_drive_oauth2callback`.
- **Fix Applied**:
  1. Registered `oauth/google/authorize/` and `oauth2callback/` directly in the root URL configuration.
  2. Set the pattern name to `google_drive_oauth2callback`.
  3. Restarted the development server process.
- **Result / Status**: **Fixed & Verified**. Both endpoints resolve cleanly with HTTP 302 redirects.

---

### Issue 9: Google OAuth PKCE Error (`invalid_grant: Missing code verifier`)

- **Severity**: Critical
- **Location**: `problems/GoogleDriveStorage.py`, `problems/views.py`
- **Problem**: After signing in and consenting on Google's OAuth screen, the redirect to `/oauth2callback/` failed during token exchange with:
  ```
  invalid_grant: Missing code verifier
  ```
- **Root Cause**: Google OAuth Web Application clients enforce **PKCE (Proof Key for Code Exchange, RFC 7636)**. The initial `Flow` instance generated a `code_verifier` and sent the challenge to Google. When Google redirected back, a new `Flow` instance was created with `code_verifier = None`. Without the verifier, Google rejected the token exchange.
- **Fix Applied**:
  1. In `google_drive_authorize`, preserved `flow.code_verifier` in the Django session:
     ```python
     request.session['google_drive_oauth_code_verifier'] = flow.code_verifier
     ```
  2. In `google_drive_oauth2callback`, retrieved `code_verifier` from the session.
  3. In `exchange_code_for_credentials`, restored `flow.code_verifier = code_verifier` before calling `flow.fetch_token()`.
  4. Removed the verifier from the session after successful credential persistence.
- **Result / Status**: **Fixed & Verified**. PKCE exchange succeeds seamlessly, and the refresh token is stored in `google_drive_token.json`.
