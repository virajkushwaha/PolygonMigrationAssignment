import os
import json
import logging
from django.conf import settings
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']


class GoogleDriveStorageError(Exception):
    """Exception raised for Google Drive storage and authentication errors."""
    pass


def get_oauth_client_file():
    """
    Returns the path to the OAuth 2.0 client secret JSON file from settings or environment.
    """
    return (
        getattr(settings, 'GOOGLE_DRIVE_OAUTH_CLIENT_FILE', None)
        or getattr(settings, 'GOOGLE_DRIVE_CREDENTIALS_FILE', None)
        or os.getenv('GOOGLE_DRIVE_OAUTH_CLIENT_FILE')
        or os.getenv('GOOGLE_DRIVE_CREDENTIALS_FILE')
    )


def get_token_file():
    """
    Returns the path to the file where authorized OAuth credentials/tokens are persisted.
    """
    base_dir = getattr(settings, 'BASE_DIR', os.getcwd())
    default_token_path = os.path.join(base_dir, 'google_drive_token.json')
    return (
        getattr(settings, 'GOOGLE_DRIVE_TOKEN_FILE', None)
        or os.getenv('GOOGLE_DRIVE_TOKEN_FILE')
        or default_token_path
    )


def get_redirect_uri():
    """
    Returns the authorized redirect URI for the OAuth 2.0 flow.
    """
    return (
        getattr(settings, 'GOOGLE_DRIVE_REDIRECT_URI', None)
        or os.getenv('GOOGLE_DRIVE_REDIRECT_URI')
        or 'http://localhost:8000/oauth2callback/'
    )


def create_oauth_flow(redirect_uri=None, state=None):
    """
    Creates and returns a google_auth_oauthlib Flow instance from the configured client secrets file.
    """
    client_file = get_oauth_client_file()
    if not client_file:
        raise GoogleDriveStorageError(
            "Google Drive OAuth client file is not configured. "
            "Please set GOOGLE_DRIVE_OAUTH_CLIENT_FILE in your .env file."
        )

    if not os.path.exists(client_file):
        raise FileNotFoundError(
            f"Google Drive OAuth client file not found at: {client_file}"
        )

    uri = redirect_uri or get_redirect_uri()
    # Allow OAuth over HTTP for local development / testing
    os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')

    try:
        flow = Flow.from_client_secrets_file(
            client_file,
            scopes=SCOPES,
            state=state,
            redirect_uri=uri
        )
        return flow
    except Exception as e:
        logger.error(f"Failed to create OAuth flow: {e}", exc_info=True)
        raise GoogleDriveStorageError(f"Failed to create OAuth flow: {e}") from e


def get_authorization_url(redirect_uri=None):
    """
    Initiates Google OAuth authorization with offline access and required Drive scopes.

    Returns:
        tuple: (authorization_url, state)
    """
    flow = create_oauth_flow(redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    return auth_url, state


def exchange_code_for_credentials(
    authorization_response=None,
    state=None,
    redirect_uri=None,
    token_file=None,
    code=None,
    code_verifier=None
):
    """
    Exchanges the authorization code for credentials and persists the refresh token.

    Args:
        authorization_response (str, optional): The full callback URL requested by Google.
        state (str, optional): The state token to verify against CSRF.
        redirect_uri (str, optional): The authorized redirect URI.
        token_file (str, optional): Target file path for persisting credentials.
        code (str, optional): The authorization code.
        code_verifier (str, optional): The PKCE code verifier string.

    Returns:
        Credentials: The authorized credentials instance.
    """
    flow = create_oauth_flow(redirect_uri=redirect_uri, state=state)
    if code_verifier:
        flow.code_verifier = code_verifier

    os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')

    try:
        fetch_kwargs = {}
        if code:
            fetch_kwargs['code'] = code
        if authorization_response:
            fetch_kwargs['authorization_response'] = authorization_response
        if code_verifier:
            fetch_kwargs['code_verifier'] = code_verifier

        flow.fetch_token(**fetch_kwargs)
        credentials = flow.credentials

        save_path = token_file or get_token_file()
        save_dir = os.path.dirname(os.path.abspath(save_path))
        if save_dir and not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)

        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(credentials.to_json())

        logger.info(f"Google Drive OAuth credentials saved successfully to {save_path}")
        return credentials
    except Exception as e:
        logger.error(f"Failed to exchange OAuth code for credentials: {e}", exc_info=True)
        raise GoogleDriveStorageError(f"OAuth code exchange failed: {e}") from e



def get_stored_credentials(token_file=None):
    """
    Loads persisted OAuth credentials from token_file and automatically refreshes them if expired.

    Returns:
        Credentials or None: Valid or refreshed credentials, or None if no token file exists.
    """
    path = token_file or get_token_file()
    if not path or not os.path.exists(path):
        return None

    try:
        credentials = Credentials.from_authorized_user_file(path, scopes=SCOPES)
        if credentials and credentials.expired and credentials.refresh_token:
            logger.info("Google Drive OAuth access token is expired. Refreshing using refresh token...")
            credentials.refresh(Request())
            # Save the refreshed credentials back to disk
            with open(path, 'w', encoding='utf-8') as f:
                f.write(credentials.to_json())
            logger.info("Google Drive OAuth token refreshed and saved successfully.")
        return credentials
    except Exception as e:
        logger.error(f"Error loading or refreshing stored Google Drive credentials: {e}", exc_info=True)
        raise GoogleDriveStorageError(f"Failed to load/refresh Google Drive credentials: {e}") from e


def is_authorized(token_file=None):
    """
    Returns True if valid or refreshable Google Drive OAuth credentials exist.
    """
    try:
        creds = get_stored_credentials(token_file=token_file)
        return creds is not None and (creds.valid or bool(creds.refresh_token))
    except Exception:
        return False


class GoogleDriveManager:
    """
    Manager class to handle read/write/delete operations for test cases on Google Drive
    using OAuth 2.0 user credentials.

    Maintains the folder hierarchy:
        {GOOGLE_DRIVE_FOLDER_ID}/test_cases/{db_problem_id}/
            ├── {test_number:02d}     (input file)
            ├── {test_number:02d}.a   (output file)
            └── custom_checker...     (checker file, if required)
    """

    def __init__(self, token_file=None, client_secrets_file=None, root_folder_id=None, service=None, credentials=None, **kwargs):
        """
        Initializes the Google Drive service client using OAuth 2.0 user credentials.

        Args:
            token_file (str, optional): Path to persisted OAuth token file.
            client_secrets_file (str, optional): Path to OAuth client secrets JSON.
            root_folder_id (str, optional): The ID of the root Google Drive folder.
            service (Resource, optional): Pre-configured googleapiclient service instance.
            credentials (Credentials, optional): Pre-authorized Credentials instance.
            **kwargs: Backward compatibility arguments (e.g. credentials_file).

        Raises:
            ValueError: If root_folder_id is missing.
            GoogleDriveStorageError: If OAuth credentials have not been authorized yet or initialization fails.
        """
        # Backward compatibility for credentials_file parameter
        if not client_secrets_file and 'credentials_file' in kwargs:
            client_secrets_file = kwargs['credentials_file']

        self.token_file = token_file or get_token_file()
        self.client_secrets_file = client_secrets_file or get_oauth_client_file()
        self.root_folder_id = root_folder_id or getattr(settings, 'GOOGLE_DRIVE_FOLDER_ID', None) or os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        self._folder_cache = {}

        if service is not None:
            self.service = service
            self.credentials = credentials
            logger.info("GoogleDriveManager initialized with injected service instance.")
            return

        if not self.root_folder_id:
            raise ValueError(
                "Google Drive root folder ID is not configured. "
                "Please set GOOGLE_DRIVE_FOLDER_ID in your environment or settings."
            )

        if credentials is not None:
            self.credentials = credentials
        else:
            self.credentials = get_stored_credentials(self.token_file)

        if not self.credentials:
            raise GoogleDriveStorageError(
                "Google Drive OAuth credentials have not been authorized yet. "
                "Please authorize access at http://localhost:8000/oauth/google/authorize/ "
                "to connect your Google account."
            )

        try:
            self.service = build('drive', 'v3', credentials=self.credentials, cache_discovery=False)
            logger.info("Google Drive service client initialized successfully with OAuth credentials.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive client: {e}", exc_info=True)
            raise GoogleDriveStorageError(f"Failed to initialize Google Drive client: {e}") from e

    def _get_or_create_folder(self, folder_name, parent_id):
        """
        Finds or creates a folder with folder_name inside parent_id.

        Args:
            folder_name (str): Name of the folder to resolve/create.
            parent_id (str): Google Drive folder ID of the parent.

        Returns:
            str: Folder ID of the resolved or newly created folder.
        """
        cache_key = (parent_id, folder_name)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        try:
            query = (
                f"name = '{folder_name}' and "
                f"'{parent_id}' in parents and "
                f"mimeType = 'application/vnd.google-apps.folder' and "
                f"trashed = false"
            )
            response = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                pageSize=10
            ).execute()
            files = response.get('files', [])

            if files:
                folder_id = files[0]['id']
                logger.debug(f"Resolved existing folder '{folder_name}' under parent '{parent_id}': {folder_id}")
            else:
                file_metadata = {
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [parent_id]
                }
                created_folder = self.service.files().create(
                    body=file_metadata,
                    fields='id'
                ).execute()
                folder_id = created_folder.get('id')
                logger.info(f"Created new folder '{folder_name}' under parent '{parent_id}': {folder_id}")

            self._folder_cache[cache_key] = folder_id
            return folder_id
        except Exception as e:
            logger.error(f"Error resolving or creating folder '{folder_name}' under parent '{parent_id}': {e}")
            raise GoogleDriveStorageError(f"Failed to resolve/create folder '{folder_name}': {e}") from e

    def _find_folder(self, folder_name, parent_id):
        """
        Finds a folder with folder_name under parent_id without creating it.

        Args:
            folder_name (str): Name of the folder to search for.
            parent_id (str): Google Drive folder ID of the parent.

        Returns:
            str or None: Folder ID if found, None otherwise.
        """
        cache_key = (parent_id, folder_name)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        try:
            query = (
                f"name = '{folder_name}' and "
                f"'{parent_id}' in parents and "
                f"mimeType = 'application/vnd.google-apps.folder' and "
                f"trashed = false"
            )
            response = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                pageSize=10
            ).execute()
            files = response.get('files', [])
            if files:
                folder_id = files[0]['id']
                self._folder_cache[cache_key] = folder_id
                return folder_id
            return None
        except Exception as e:
            logger.error(f"Error searching for folder '{folder_name}' under parent '{parent_id}': {e}")
            raise GoogleDriveStorageError(f"Failed to search for folder '{folder_name}': {e}") from e

    def _get_problem_folder(self, db_problem_id):
        """
        Ensures test_cases/{db_problem_id} folder hierarchy exists under root_folder_id.

        Args:
            db_problem_id (str|int): Database problem ID.

        Returns:
            str: Folder ID of the problem directory.
        """
        test_cases_folder_id = self._get_or_create_folder('test_cases', self.root_folder_id)
        problem_folder_id = self._get_or_create_folder(str(db_problem_id), test_cases_folder_id)
        return problem_folder_id

    def _find_problem_folder(self, db_problem_id):
        """
        Locates the problem folder test_cases/{db_problem_id} if it exists.

        Args:
            db_problem_id (str|int): Database problem ID.

        Returns:
            str or None: Folder ID if exists, None otherwise.
        """
        test_cases_folder_id = self._find_folder('test_cases', self.root_folder_id)
        if not test_cases_folder_id:
            return None
        return self._find_folder(str(db_problem_id), test_cases_folder_id)

    def upload_test_case(self, *args, **kwargs):
        """
        Uploads a test case's input and output to Google Drive as separate files
        under test_cases/{db_problem_id}/.

        Supports both direct and Azure-compatible parameter signatures:
            upload_test_case(db_problem_id, test_number, input_data, output_data)
            upload_test_case(container_name, db_problem_id, test_number, input_data, output_data)

        Args:
            db_problem_id (str|int): The database problem ID.
            test_number (int): The 1-based test case number.
            input_data (str|bytes): The input content.
            output_data (str|bytes): The output content.

        Raises:
            GoogleDriveStorageError: If the upload operation fails.
        """
        if len(args) == 5:
            _, db_problem_id, test_number, input_data, output_data = args
        elif len(args) == 4:
            db_problem_id, test_number, input_data, output_data = args
        else:
            db_problem_id = kwargs.get('db_problem_id')
            test_number = kwargs.get('test_number')
            input_data = kwargs.get('input_data', '')
            output_data = kwargs.get('output_data', '')

        if db_problem_id is None or test_number is None:
            raise ValueError("db_problem_id and test_number are required for upload_test_case.")

        problem_folder_id = self._get_problem_folder(db_problem_id)
        input_filename = f"{test_number:02d}"
        output_filename = f"{test_number:02d}.a"

        input_bytes = input_data.encode('utf-8') if isinstance(input_data, str) else bytes(input_data or b'')
        output_bytes = output_data.encode('utf-8') if isinstance(output_data, str) else bytes(output_data or b'')

        try:
            # Upload input file
            input_media = MediaInMemoryUpload(input_bytes, mimetype='text/plain', resumable=False)
            input_metadata = {
                'name': input_filename,
                'parents': [problem_folder_id]
            }
            self.service.files().create(
                body=input_metadata,
                media_body=input_media,
                fields='id, name'
            ).execute()
            logger.info(f"Uploaded input for test #{test_number} to test_cases/{db_problem_id}/{input_filename}")

            # Upload output file
            output_media = MediaInMemoryUpload(output_bytes, mimetype='text/plain', resumable=False)
            output_metadata = {
                'name': output_filename,
                'parents': [problem_folder_id]
            }
            self.service.files().create(
                body=output_metadata,
                media_body=output_media,
                fields='id, name'
            ).execute()
            logger.info(f"Uploaded output for test #{test_number} to test_cases/{db_problem_id}/{output_filename}")

        except Exception as e:
            logger.error(
                f"Error uploading test case #{test_number} for problem {db_problem_id} to Google Drive: {e}",
                exc_info=True
            )
            raise GoogleDriveStorageError(
                f"Failed to upload test case #{test_number} for problem {db_problem_id}: {e}"
            ) from e

    def empty_problem_files(self, problem_id):
        """
        Deletes all existing files for a given problem_id from Google Drive
        under test_cases/{problem_id}/ before re-uploading.

        Args:
            problem_id (str|int): The database problem ID.

        Raises:
            GoogleDriveStorageError: If deleting files fails.
        """
        try:
            problem_folder_id = self._find_problem_folder(problem_id)
            if not problem_folder_id:
                logger.info(f"No existing folder found for problem {problem_id} on Google Drive; nothing to delete.")
                return

            page_token = None
            deleted_count = 0
            while True:
                query = f"'{problem_folder_id}' in parents and trashed = false"
                response = self.service.files().list(
                    q=query,
                    spaces='drive',
                    fields='nextPageToken, files(id, name)',
                    pageToken=page_token
                ).execute()

                files = response.get('files', [])
                for f in files:
                    file_id = f['id']
                    file_name = f.get('name', 'unknown')
                    logger.info(f"Deleting file {file_name} ({file_id}) for problem {problem_id} on Google Drive")
                    self.service.files().delete(fileId=file_id).execute()
                    deleted_count += 1

                page_token = response.get('nextPageToken')
                if not page_token:
                    break

            logger.info(f"Deleted {deleted_count} files for problem {problem_id} from Google Drive.")
        except Exception as e:
            logger.error(f"Error deleting files for problem {problem_id} from Google Drive: {e}", exc_info=True)
            raise GoogleDriveStorageError(f"Failed to empty files for problem {problem_id}: {e}") from e

    def empty_blob(self, container_name, problem_id):
        """
        AzureBlobManager compatibility method for clearing problem files.

        Args:
            container_name (str): Container or root name (ignored for Google Drive).
            problem_id (str|int): The database problem ID.
        """
        return self.empty_problem_files(problem_id)

    def upload_file(self, db_problem_id, file_name, file_content, mimetype='text/plain'):
        """
        Uploads an individual file (such as custom checker source or compiled binary)
        into the problem's folder test_cases/{db_problem_id}/.

        Args:
            db_problem_id (str|int): The database problem ID.
            file_name (str): The name of the file (e.g. 'custom_checker.cpp', 'custom_checker.exe').
            file_content (str|bytes): Content of the file.
            mimetype (str, optional): MIME type of the file. Defaults to 'text/plain'.

        Returns:
            dict: The created file resource from Google Drive API.

        Raises:
            GoogleDriveStorageError: If upload fails.
        """
        try:
            problem_folder_id = self._get_problem_folder(db_problem_id)
            if isinstance(file_content, str):
                content_bytes = file_content.encode('utf-8')
            else:
                content_bytes = bytes(file_content or b'')

            media = MediaInMemoryUpload(content_bytes, mimetype=mimetype, resumable=False)
            metadata = {
                'name': file_name,
                'parents': [problem_folder_id]
            }
            result = self.service.files().create(
                body=metadata,
                media_body=media,
                fields='id, name'
            ).execute()
            logger.info(f"Uploaded file '{file_name}' for problem {db_problem_id} to Google Drive (ID: {result.get('id')})")
            return result
        except Exception as e:
            logger.error(f"Failed to upload file '{file_name}' for problem {db_problem_id} to Google Drive: {e}", exc_info=True)
            raise GoogleDriveStorageError(f"Failed to upload file '{file_name}' for problem {db_problem_id}: {e}") from e
