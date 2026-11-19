from unittest.mock import patch, MagicMock, ANY
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from .models import Problem, ProblemTestCase, SampleTestCase
from .GoogleDriveStorage import (
    GoogleDriveManager,
    GoogleDriveStorageError,
    get_authorization_url,
    exchange_code_for_credentials,
    is_authorized,
    SCOPES,
)
from .polygon_api import PolygonAPI

User = get_user_model()

class ProblemModelMigrationTests(TestCase):
    def test_problem_notes_and_genie_chat_fields(self):
        """Verify that notes and genie_chat fields exist, persist, and default properly in a fresh test DB."""
        problem = Problem.objects.create(
            polygon_id='test-migration-001',
            title='Test Problem for Migrations',
            slug='test-problem-for-migrations',
            difficulty='easy',
            notes='Sample polygon notes',
            genie_chat=True
        )
        fetched = Problem.objects.get(polygon_id='test-migration-001')
        self.assertEqual(fetched.notes, 'Sample polygon notes')
        self.assertTrue(fetched.genie_chat)

    def test_problem_default_genie_chat(self):
        """Verify default value for genie_chat is False and notes can be null/blank."""
        problem = Problem.objects.create(
            polygon_id='test-migration-002',
            title='Test Problem Default Values',
            slug='test-problem-default-values',
            difficulty='medium'
        )
        fetched = Problem.objects.get(polygon_id='test-migration-002')
        self.assertFalse(fetched.genie_chat)
        self.assertIsNone(fetched.notes)


class TestCaseDataIntegrityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            email='staff_tester@polygon.com',
            password='testpassword123',
            first_name='Staff',
            last_name='Tester',
            username='staff_tester',
            contact_number='1234567890',
            college='Test College',
            graduation_year=2024,
            gender='OTHER',
        )
        self.client.force_login(self.staff_user)
        self.problem = Problem.objects.create(
            polygon_id='99999',
            title='Data Integrity Problem',
            slug='data-integrity-problem',
            difficulty='medium'
        )

    def _post_migrate_test_cases(self, test_cases_data):
        with patch('problems.views.PolygonAPI') as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_test_cases_from_redis.return_value = test_cases_data
            mock_api.get_problem_info.return_value = {'name': 'Data Integrity Problem'}
            mock_api.download_and_extract_package.return_value = '<html><div class="title">Data Integrity Problem</div><div class="legend">Legend</div><div class="input-specification">Input</div><div class="output-specification">Output</div></html>'
            mock_api.get_test_cases.return_value = [{'index': i} for i in range(1, len(test_cases_data) + 1)]
            mock_api._make_request.return_value = 'ncmp'
            mock_api.get_custom_checker_info.return_value = None

            response = self.client.post('/', {
                'problem_id': '99999',
                'migrate_test_cases_to_db': '1'
            })
            return response

    def test_input_longer_than_260_chars_stored_completely(self):
        """Input longer than 260 characters must be stored in full without truncation."""
        long_input = "A" * 1500 + "\n"
        test_cases = [{'index': 1, 'input': long_input, 'output': 'OK\n', 'is_sample': False}]
        self._post_migrate_test_cases(test_cases)

        tc = ProblemTestCase.objects.get(problem=self.problem, order=1)
        self.assertEqual(len(tc.input), 1501)
        self.assertEqual(tc.input, long_input)

    def test_output_longer_than_260_chars_stored_completely(self):
        """Output longer than 260 characters must be stored in full without truncation."""
        long_output = "B" * 2000 + "\n"
        test_cases = [{'index': 1, 'input': '10\n', 'output': long_output, 'is_sample': False}]
        self._post_migrate_test_cases(test_cases)

        tc = ProblemTestCase.objects.get(problem=self.problem, order=1)
        self.assertEqual(len(tc.output), 2001)
        self.assertEqual(tc.output, long_output)

    def test_leading_and_trailing_whitespace_preserved(self):
        """Leading and trailing spaces, tabs, and indentation must not be stripped."""
        whitespace_input = "   \tleading and trailing spaces   \t"
        whitespace_output = "   line with spaces   "
        test_cases = [{'index': 1, 'input': whitespace_input, 'output': whitespace_output, 'is_sample': False}]
        self._post_migrate_test_cases(test_cases)

        tc = ProblemTestCase.objects.get(problem=self.problem, order=1)
        self.assertEqual(tc.input, whitespace_input)
        self.assertEqual(tc.output, whitespace_output)

    def test_newline_characters_preserved(self):
        """CRLF and LF newlines including trailing newlines must be preserved exactly."""
        multiline_input = "line1\r\nline2\r\nline3\r\n"
        multiline_output = "ans1\nans2\n\n"
        test_cases = [{'index': 1, 'input': multiline_input, 'output': multiline_output, 'is_sample': False}]
        self._post_migrate_test_cases(test_cases)

        tc = ProblemTestCase.objects.get(problem=self.problem, order=1)
        self.assertEqual(tc.input, multiline_input)
        self.assertEqual(tc.output, multiline_output)

    def test_empty_input_handled_correctly(self):
        """Valid test cases with empty input string must not be discarded."""
        test_cases = [{'index': 1, 'input': '', 'output': 'output for empty input\n', 'is_sample': False}]
        self._post_migrate_test_cases(test_cases)

        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 1)
        tc = ProblemTestCase.objects.get(problem=self.problem, order=1)
        self.assertEqual(tc.input, '')
        self.assertEqual(tc.output, 'output for empty input\n')

    def test_empty_output_handled_correctly(self):
        """Valid test cases with empty output string must not be discarded."""
        test_cases = [{'index': 1, 'input': 'input with no output expected\n', 'output': '', 'is_sample': False}]
        self._post_migrate_test_cases(test_cases)

        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 1)
        tc = ProblemTestCase.objects.get(problem=self.problem, order=1)
        self.assertEqual(tc.input, 'input with no output expected\n')
        self.assertEqual(tc.output, '')

    def test_normal_test_cases_migrate_correctly(self):
        """Standard test cases (mix of sample and regular) must migrate with correct order and classification."""
        test_cases = [
            {'index': 1, 'input': '1 2\n', 'output': '3\n', 'is_sample': True, 'description': 'Sample 1'},
            {'index': 2, 'input': '10 20\n', 'output': '30\n', 'is_sample': False, 'description': 'Regular 1'},
            {'index': 3, 'input': '100 200\n', 'output': '300\n', 'is_sample': False, 'description': 'Regular 2'},
        ]
        self._post_migrate_test_cases(test_cases)

        tcs = list(ProblemTestCase.objects.filter(problem=self.problem).order_by('order'))
        self.assertEqual(len(tcs), 3)
        self.assertEqual(tcs[0].order, 1)
        self.assertTrue(tcs[0].is_sample)
        self.assertEqual(tcs[0].input, '1 2\n')
        self.assertEqual(tcs[0].output, '3\n')
        self.assertEqual(tcs[0].description, 'Sample 1')

        self.assertEqual(tcs[1].order, 2)
        self.assertFalse(tcs[1].is_sample)
        self.assertEqual(tcs[1].input, '10 20\n')
        self.assertEqual(tcs[1].output, '30\n')

        self.assertEqual(tcs[2].order, 3)
        self.assertFalse(tcs[2].is_sample)
        self.assertEqual(tcs[2].input, '100 200\n')
        self.assertEqual(tcs[2].output, '300\n')


class TestCaseSynchronizationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            email='sync_tester@polygon.com',
            password='testpassword123',
            first_name='Sync',
            last_name='Tester',
            username='sync_tester',
            contact_number='1234567890',
            college='Test College',
            graduation_year=2024,
            gender='OTHER',
        )
        self.client.force_login(self.staff_user)
        self.problem = Problem.objects.create(
            polygon_id='77777',
            title='Sync Problem',
            slug='sync-problem',
            difficulty='easy'
        )

    def _post_sync(self, test_cases_data, api_error=None):
        with patch('problems.views.PolygonAPI') as mock_api_class:
            mock_api = mock_api_class.return_value
            if api_error:
                mock_api.get_test_cases_from_redis.return_value = None
                mock_api.get_all_test_cases.side_effect = api_error
            else:
                mock_api.get_test_cases_from_redis.return_value = test_cases_data
                mock_api.get_all_test_cases.return_value = test_cases_data
            
            mock_api.get_problem_info.return_value = {'name': 'Sync Problem'}
            mock_api.download_and_extract_package.return_value = (
                '<html><div class="title">Sync Problem</div>'
                '<div class="legend">Legend</div>'
                '<div class="input-specification">Input</div>'
                '<div class="output-specification">Output</div></html>'
            )
            mock_api._make_request.return_value = 'ncmp'
            mock_api.get_custom_checker_info.return_value = None

            response = self.client.post('/', {
                'problem_id': '77777',
                'migrate_test_cases_to_db': '1'
            })
            return response

    def test_initial_migration_of_20_test_cases(self):
        """Scenario A: Initial migration of 20 test cases creates exactly 20 records."""
        test_cases = [
            {'index': i, 'input': f'in_{i}\n', 'output': f'out_{i}\n', 'is_sample': (i == 1), 'description': f'TC {i}'}
            for i in range(1, 21)
        ]
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 0)
        
        response = self._post_sync(test_cases)
        
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 20)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.test_case_count, 20)
        
        orders = list(ProblemTestCase.objects.filter(problem=self.problem).order_by('order').values_list('order', flat=True))
        self.assertEqual(orders, list(range(1, 21)))

    def test_remigration_after_reducing_collection_from_20_to_12(self):
        """Scenario B: Re-migration after reducing collection from 20 to 12 prunes the 8 stale records."""
        initial_20 = [
            {'index': i, 'input': f'in_{i}\n', 'output': f'out_{i}\n', 'is_sample': (i == 1), 'description': f'TC {i}'}
            for i in range(1, 21)
        ]
        self._post_sync(initial_20)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 20)
        
        # Now Polygon has only 12 test cases
        reduced_12 = [
            {'index': i, 'input': f'in_updated_{i}\n', 'output': f'out_updated_{i}\n', 'is_sample': (i == 1), 'description': f'Updated TC {i}'}
            for i in range(1, 13)
        ]
        self._post_sync(reduced_12)
        
        # Exactly 12 must remain
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 12)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.test_case_count, 12)
        
        tcs = list(ProblemTestCase.objects.filter(problem=self.problem).order_by('order'))
        self.assertEqual(len(tcs), 12)
        self.assertEqual(tcs[0].input, 'in_updated_1\n')
        self.assertEqual(tcs[11].input, 'in_updated_12\n')
        self.assertEqual([tc.order for tc in tcs], list(range(1, 13)))

    def test_no_duplicate_records_on_repeated_migration(self):
        """Scenario C: Repeated migration with same collection does not create duplicates."""
        test_cases = [
            {'index': i, 'input': f'in_{i}\n', 'output': f'out_{i}\n', 'is_sample': False}
            for i in range(1, 13)
        ]
        self._post_sync(test_cases)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 12)
        
        # Run second time
        self._post_sync(test_cases)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 12)
        
        # Run third time
        self._post_sync(test_cases)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 12)

    def test_updating_existing_test_case_content(self):
        """Scenario D: Updating content of existing test cases updates the database records in-place."""
        initial = [
            {'index': 1, 'input': 'old input 1\n', 'output': 'old out 1\n', 'is_sample': False, 'description': 'old desc 1'},
            {'index': 2, 'input': 'old input 2\n', 'output': 'old out 2\n', 'is_sample': False, 'description': 'old desc 2'},
        ]
        self._post_sync(initial)
        tc1_id = ProblemTestCase.objects.get(problem=self.problem, order=1).id
        tc2_id = ProblemTestCase.objects.get(problem=self.problem, order=2).id
        
        updated = [
            {'index': 1, 'input': 'new input 1\n', 'output': 'new out 1\n', 'is_sample': True, 'description': 'new desc 1'},
            {'index': 2, 'input': 'new input 2\n', 'output': 'new out 2\n', 'is_sample': False, 'description': 'new desc 2'},
        ]
        self._post_sync(updated)
        
        tc1_after = ProblemTestCase.objects.get(problem=self.problem, order=1)
        tc2_after = ProblemTestCase.objects.get(problem=self.problem, order=2)
        
        # Record identity preserved (in-place update)
        self.assertEqual(tc1_after.id, tc1_id)
        self.assertEqual(tc2_after.id, tc2_id)
        
        # Content updated correctly
        self.assertEqual(tc1_after.input, 'new input 1\n')
        self.assertEqual(tc1_after.output, 'new out 1\n')
        self.assertTrue(tc1_after.is_sample)
        self.assertEqual(tc1_after.description, 'new desc 1')

    def test_failed_api_request_does_not_delete_existing_records(self):
        """Scenario E1: If API request fails (network error, timeout), existing records are not deleted."""
        initial = [
            {'index': i, 'input': f'in_{i}\n', 'output': f'out_{i}\n', 'is_sample': False}
            for i in range(1, 6)
        ]
        self._post_sync(initial)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 5)
        
        # Trigger sync with failed API
        response = self._post_sync(None, api_error=Exception("Network Timeout connecting to Polygon API"))
        
        # Must retain all 5 existing test cases
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 5)
        self.assertIn('Migration failed', response.context.get('error', ''))

    def test_invalid_or_incomplete_api_response_does_not_delete_existing_records(self):
        """Scenario E2: Invalid/incomplete API response does not delete existing database records."""
        initial = [
            {'index': i, 'input': f'in_{i}\n', 'output': f'out_{i}\n', 'is_sample': False}
            for i in range(1, 6)
        ]
        self._post_sync(initial)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 5)
        
        # Incomplete response: missing 'output' key
        incomplete = [{'index': 1, 'input': 'some input'}]
        response = self._post_sync(incomplete)
        
        # Must retain all 5 records
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 5)
        self.assertIn('Incomplete test case', response.context.get('error', ''))

        # Invalid response: None value for output
        invalid_none = [{'index': 1, 'input': 'some input', 'output': None}]
        response = self._post_sync(invalid_none)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 5)

        # Invalid response: string instead of list
        response = self._post_sync("error string")
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 5)

    def test_sample_and_regular_test_cases_remain_correctly_separated(self):
        """Scenario F: SampleTestCase and ProblemTestCase remain distinct and correctly synchronized."""
        test_cases = [
            {'index': 1, 'input': 'sample in 1\n', 'output': 'sample out 1\n', 'is_sample': True, 'description': 'Sample 1'},
            {'index': 2, 'input': 'sample in 2\n', 'output': 'sample out 2\n', 'is_sample': True, 'description': 'Sample 2'},
            {'index': 3, 'input': 'regular in 1\n', 'output': 'regular out 1\n', 'is_sample': False, 'description': 'Regular 1'},
            {'index': 4, 'input': 'regular in 2\n', 'output': 'regular out 2\n', 'is_sample': False, 'description': 'Regular 2'},
            {'index': 5, 'input': 'regular in 3\n', 'output': 'regular out 3\n', 'is_sample': False, 'description': 'Regular 3'},
        ]
        self._post_sync(test_cases)
        
        # ProblemTestCase has all 5
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 5)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem, is_sample=True).count(), 2)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem, is_sample=False).count(), 3)
        
        # SampleTestCase has exactly the 2 samples
        self.assertEqual(SampleTestCase.objects.filter(problem=self.problem).count(), 2)
        samples = list(SampleTestCase.objects.filter(problem=self.problem).order_by('order'))
        self.assertEqual(samples[0].input, 'sample in 1\n')
        self.assertEqual(samples[1].input, 'sample in 2\n')
        
        # Now re-migrate with 1 sample removed (now 1 sample and 2 regular = 3 total)
        reduced = [
            {'index': 1, 'input': 'sample in 1\n', 'output': 'sample out 1\n', 'is_sample': True, 'description': 'Sample 1'},
            {'index': 2, 'input': 'regular in 1\n', 'output': 'regular out 1\n', 'is_sample': False, 'description': 'Regular 1'},
            {'index': 3, 'input': 'regular in 2\n', 'output': 'regular out 2\n', 'is_sample': False, 'description': 'Regular 2'},
        ]
        self._post_sync(reduced)
        
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 3)
        self.assertEqual(SampleTestCase.objects.filter(problem=self.problem).count(), 1)
        self.assertEqual(SampleTestCase.objects.filter(problem=self.problem).first().input, 'sample in 1\n')

    def test_empty_test_case_collection_behavior_handled_explicitly_and_safely(self):
        """Scenario: Legitimate empty collection [] safely cleans records and updates count to 0 without errors."""
        initial = [
            {'index': 1, 'input': 'in\n', 'output': 'out\n', 'is_sample': True},
            {'index': 2, 'input': 'in2\n', 'output': 'out2\n', 'is_sample': False},
        ]
        self._post_sync(initial)
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 2)
        self.assertEqual(SampleTestCase.objects.filter(problem=self.problem).count(), 1)
        
        # Sync with legitimate empty list []
        response = self._post_sync([])
        
        self.assertEqual(ProblemTestCase.objects.filter(problem=self.problem).count(), 0)
        self.assertEqual(SampleTestCase.objects.filter(problem=self.problem).count(), 0)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.test_case_count, 0)
        self.assertIn('Test cases migrated to database successfully', response.context.get('success', ''))


class DuplicateProblemTitleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            email='title_tester@polygon.com',
            password='testpassword123',
            first_name='Title',
            last_name='Tester',
            username='title_tester',
            contact_number='1234567890',
            college='Test College',
            graduation_year=2024,
            gender='OTHER',
        )
        self.client.force_login(self.staff_user)

    def _post_migrate_problem(self, polygon_id, title, difficulty='medium', tags=None):
        with patch('problems.views.PolygonAPI') as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_problem_info.return_value = {'name': title}
            mock_api.download_and_extract_package.return_value = (
                f'<html><div class="title">{title}</div>'
                f'<div class="legend">Legend for {title}</div>'
                f'<div class="input-specification">Input</div>'
                f'<div class="output-specification">Output</div></html>'
            )
            mock_api.get_test_cases_from_redis.return_value = [
                {'index': 1, 'input': '1\n', 'output': '2\n', 'is_sample': True}
            ]
            mock_api.get_all_test_cases.return_value = [
                {'index': 1, 'input': '1\n', 'output': '2\n', 'is_sample': True}
            ]
            mock_api.get_test_cases.return_value = [{'index': 1}]
            mock_api._make_request.return_value = 'ncmp'
            mock_api.get_custom_checker_info.return_value = None

            post_data = {
                'problem_id': polygon_id,
                'migrate_to_db': '1',
                'difficulty': difficulty,
            }
            if tags:
                post_data['tags'] = tags

            response = self.client.post('/', post_data)
            return response

    def test_two_different_polygon_ids_with_same_title(self):
        """Two different Polygon problems with identical title must both save successfully without IntegrityError."""
        # 1. Migrate first problem: Polygon ID 1001, title "Two Sum"
        resp1 = self._post_migrate_problem('1001', 'Two Sum')
        self.assertEqual(resp1.status_code, 200)
        self.assertIn('created in database successfully', resp1.context.get('db_success', ''))

        # 2. Migrate second problem: Polygon ID 2002, same title "Two Sum"
        resp2 = self._post_migrate_problem('2002', 'Two Sum')
        self.assertEqual(resp2.status_code, 200)
        self.assertIn('created in database successfully', resp2.context.get('db_success', ''))

        # Both records must exist as separate Problem rows
        p1 = Problem.objects.get(polygon_id='1001')
        p2 = Problem.objects.get(polygon_id='2002')
        self.assertNotEqual(p1.id, p2.id)

        # Human-readable titles must be preserved exactly
        self.assertEqual(p1.title, 'Two Sum')
        self.assertEqual(p2.title, 'Two Sum')

        # Slugs must be unique and deterministic
        self.assertEqual(p1.slug, 'two-sum')
        self.assertEqual(p2.slug, 'two-sum-2002')

    def test_same_polygon_id_migrated_twice(self):
        """Re-migrating the same Polygon ID must update the existing record instead of creating duplicates."""
        resp1 = self._post_migrate_problem('1001', 'Two Sum', difficulty='easy')
        p1_orig = Problem.objects.get(polygon_id='1001')
        self.assertEqual(p1_orig.difficulty, 'easy')
        self.assertEqual(Problem.objects.count(), 1)

        # Re-migrate with updated difficulty
        resp2 = self._post_migrate_problem('1001', 'Two Sum', difficulty='hard')
        self.assertEqual(resp2.status_code, 200)
        self.assertIn('updated in database successfully', resp2.context.get('db_success', ''))

        # Record count remains 1
        self.assertEqual(Problem.objects.count(), 1)
        p1_updated = Problem.objects.get(polygon_id='1001')
        self.assertEqual(p1_updated.id, p1_orig.id)
        self.assertEqual(p1_updated.slug, 'two-sum')
        self.assertEqual(p1_updated.difficulty, 'hard')

    def test_unique_and_deterministic_slugs(self):
        """Slugs must be deterministic: first gets base slug, colliding gets base_slug-{polygon_id}."""
        self._post_migrate_problem('3001', 'Graph Traversal')
        self._post_migrate_problem('3002', 'Graph Traversal')
        self._post_migrate_problem('3003', 'Graph Traversal')

        p1 = Problem.objects.get(polygon_id='3001')
        p2 = Problem.objects.get(polygon_id='3002')
        p3 = Problem.objects.get(polygon_id='3003')

        self.assertEqual(p1.slug, 'graph-traversal')
        self.assertEqual(p2.slug, 'graph-traversal-3002')
        self.assertEqual(p3.slug, 'graph-traversal-3003')

    def test_existing_problem_title_preserved(self):
        """The original problem title must remain intact without appending polygon IDs into title."""
        self._post_migrate_problem('5001', 'Special Characters #!& Problem')
        p = Problem.objects.get(polygon_id='5001')
        self.assertEqual(p.title, 'Special Characters #!& Problem')

    def test_existing_records_and_relationships_remain_intact(self):
        """Re-migrating a problem preserves attached test cases and tags."""
        self._post_migrate_problem('6001', 'Problem With Relations', tags=['math', 'dp'])
        p = Problem.objects.get(polygon_id='6001')
        self.assertEqual(p.sample_test_cases.count(), 1)
        self.assertEqual(p.extra_tags.count(), 2)

        # Re-migrate
        self._post_migrate_problem('6001', 'Problem With Relations', tags=['math', 'dp', 'greedy'])
        p.refresh_from_db()
        self.assertEqual(p.sample_test_cases.count(), 1)
        self.assertEqual(p.extra_tags.count(), 3)


class GoogleDriveStorageTests(TestCase):
    """Automated unit and integration tests for Google Drive cloud storage provider."""

    def setUp(self):
        self.mock_service = MagicMock()
        self.root_folder_id = 'test-root-folder-id'
        self.manager = GoogleDriveManager(
            credentials_file='dummy.json',
            root_folder_id=self.root_folder_id,
            service=self.mock_service
        )
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            email='gdrive_tester@polygon.com',
            password='testpassword123',
            first_name='GDrive',
            last_name='Tester',
            username='gdrive_tester',
            contact_number='1234567890',
            college='Test College',
            graduation_year=2024,
            gender='OTHER',
        )
        self.client.force_login(self.staff_user)

    @override_settings(GOOGLE_DRIVE_FOLDER_ID=None)
    def test_init_missing_root_folder_raises_value_error(self):
        """Missing root folder ID must raise ValueError."""
        with patch('problems.GoogleDriveStorage.get_stored_credentials') as mock_creds:
            mock_creds.return_value = MagicMock()
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ValueError) as ctx:
                    GoogleDriveManager(root_folder_id=None)
                self.assertIn("root folder", str(ctx.exception).lower())

    def test_init_missing_stored_token_raises_storage_error(self):
        """Missing stored OAuth token raises GoogleDriveStorageError prompting authorization."""
        with patch('problems.GoogleDriveStorage.get_stored_credentials', return_value=None):
            with self.assertRaises(GoogleDriveStorageError) as ctx:
                GoogleDriveManager(root_folder_id='root-id')
            self.assertIn("not been authorized", str(ctx.exception).lower())

    def test_init_with_valid_stored_token_builds_service(self):
        """Valid stored OAuth token initializes the Drive service client successfully."""
        mock_credentials = MagicMock()
        with patch('problems.GoogleDriveStorage.get_stored_credentials', return_value=mock_credentials):
            with patch('problems.GoogleDriveStorage.build') as mock_build:
                mock_build.return_value = MagicMock()
                manager = GoogleDriveManager(root_folder_id='root-id')
                mock_build.assert_called_once_with('drive', 'v3', credentials=mock_credentials, cache_discovery=False)
                self.assertIsNotNone(manager.service)

    def test_init_with_expired_token_triggers_automatic_refresh(self):
        """Expired stored OAuth token with refresh_token is automatically refreshed."""
        mock_credentials = MagicMock()
        mock_credentials.expired = True
        mock_credentials.refresh_token = 'mock-refresh-token'
        mock_credentials.to_json.return_value = '{"token": "refreshed"}'

        with patch('os.path.exists', return_value=True), \
             patch('google.oauth2.credentials.Credentials.from_authorized_user_file', return_value=mock_credentials), \
             patch('builtins.open', create=True) as mock_open:

            from problems.GoogleDriveStorage import get_stored_credentials
            creds = get_stored_credentials('/dummy/token.json')
            mock_credentials.refresh.assert_called_once()
            self.assertEqual(creds, mock_credentials)

    def test_get_authorization_url_sets_offline_and_consent(self):
        """OAuth authorization URL requests offline access and consent prompt."""
        with patch('problems.GoogleDriveStorage.create_oauth_flow') as mock_flow_creator:
            mock_flow = MagicMock()
            mock_flow.authorization_url.return_value = ('https://accounts.google.com/o/oauth2/auth?test', 'test-state')
            mock_flow_creator.return_value = mock_flow

            auth_url, state = get_authorization_url(redirect_uri='http://localhost:8000/oauth2callback/')
            mock_flow.authorization_url.assert_called_once_with(
                access_type='offline',
                include_granted_scopes='true',
                prompt='consent'
            )
            self.assertEqual(state, 'test-state')
            self.assertIn('https://accounts.google.com', auth_url)

    def test_exchange_code_for_credentials_persists_token(self):
        """Code exchange restores code_verifier, fetches token, and writes JSON to persisted token file."""
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "test-token", "refresh_token": "test-refresh"}'

        with patch('problems.GoogleDriveStorage.create_oauth_flow') as mock_flow_creator, \
             patch('builtins.open', create=True) as mock_open:
            mock_flow = MagicMock()
            mock_flow.credentials = mock_creds
            mock_flow_creator.return_value = mock_flow

            creds = exchange_code_for_credentials(
                authorization_response='http://localhost:8000/oauth2callback/?code=test-code&state=test-state',
                code='test-code',
                code_verifier='test-verifier-xyz',
                state='test-state',
                redirect_uri='http://localhost:8000/oauth2callback/',
                token_file='test_token.json'
            )
            self.assertEqual(mock_flow.code_verifier, 'test-verifier-xyz')
            mock_flow.fetch_token.assert_called_once_with(
                code='test-code',
                authorization_response='http://localhost:8000/oauth2callback/?code=test-code&state=test-state',
                code_verifier='test-verifier-xyz'
            )
            self.assertEqual(creds, mock_creds)

    def test_view_google_drive_authorize_redirects_to_auth_url(self):
        """GET to /oauth/google/authorize/ redirects to Google and sets session state and code_verifier."""
        with patch('problems.views.create_oauth_flow') as mock_flow_creator:
            mock_flow = MagicMock()
            mock_flow.authorization_url.return_value = ('https://accounts.google.com/auth_test', 'session-state-123')
            mock_flow.code_verifier = 'session-code-verifier-123'
            mock_flow_creator.return_value = mock_flow

            response = self.client.get('/oauth/google/authorize/')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, 'https://accounts.google.com/auth_test')
            self.assertEqual(self.client.session.get('google_drive_oauth_state'), 'session-state-123')
            self.assertEqual(self.client.session.get('google_drive_oauth_code_verifier'), 'session-code-verifier-123')

    def test_view_oauth2callback_success_persists_credentials_and_redirects(self):
        """GET to /oauth2callback/ exchanges code and PKCE verifier, cleans session, and redirects."""
        session = self.client.session
        session['google_drive_oauth_state'] = 'match-state'
        session['google_drive_oauth_code_verifier'] = 'valid-verifier-abc'
        session.save()

        with patch('problems.views.exchange_code_for_credentials') as mock_exchange:
            mock_exchange.return_value = MagicMock()
            response = self.client.get('/oauth2callback/?code=mock-code&state=match-state')
            self.assertEqual(response.status_code, 302)
            self.assertIn('/', response.url)
            mock_exchange.assert_called_once()
            call_kwargs = mock_exchange.call_args[1]
            self.assertEqual(call_kwargs.get('code_verifier'), 'valid-verifier-abc')
            self.assertEqual(call_kwargs.get('code'), 'mock-code')
            self.assertNotIn('google_drive_oauth_code_verifier', self.client.session)
            self.assertNotIn('google_drive_oauth_state', self.client.session)

    def test_view_oauth2callback_missing_code_verifier_handles_error(self):
        """GET to /oauth2callback/ without session code_verifier shows clear error and redirects."""
        session = self.client.session
        session['google_drive_oauth_state'] = 'match-state'
        # deliberately omit google_drive_oauth_code_verifier
        session.save()

        with patch('problems.views.exchange_code_for_credentials') as mock_exchange:
            response = self.client.get('/oauth2callback/?code=mock-code&state=match-state')
            self.assertEqual(response.status_code, 302)
            mock_exchange.assert_not_called()
            messages_list = list(response.wsgi_request._messages)
            self.assertTrue(any('session verifier' in str(m).lower() or 'verifier' in str(m).lower() for m in messages_list))

    def test_view_oauth2callback_user_denial_handles_error(self):
        """GET to /oauth2callback/?error=access_denied redirects with error message."""
        response = self.client.get('/oauth2callback/?error=access_denied')
        self.assertEqual(response.status_code, 302)
        # Check messages
        messages_list = list(response.wsgi_request._messages)
        self.assertTrue(any('denied' in str(m).lower() for m in messages_list))

    def test_view_oauth2callback_state_mismatch_handles_error(self):
        """GET to /oauth2callback/ with state mismatch prevents CSRF and sets error message."""
        session = self.client.session
        session['google_drive_oauth_state'] = 'legit-state'
        session.save()

        response = self.client.get('/oauth2callback/?code=mock-code&state=hacker-state')
        self.assertEqual(response.status_code, 302)
        messages_list = list(response.wsgi_request._messages)
        self.assertTrue(any('mismatch' in str(m).lower() for m in messages_list))

    def test_is_authorized_helper(self):
        """is_authorized returns True when valid credentials exist and False otherwise."""
        with patch('problems.GoogleDriveStorage.get_stored_credentials') as mock_get:
            mock_creds = MagicMock()
            mock_creds.valid = True
            mock_get.return_value = mock_creds
            self.assertTrue(is_authorized())

            mock_creds.valid = False
            mock_creds.refresh_token = None
            self.assertFalse(is_authorized())

            mock_get.return_value = None
            self.assertFalse(is_authorized())

    def test_folder_resolution_and_creation(self):
        """Resolves test_cases folder and problem folder under root, creating if missing."""
        # First call: test_cases not found -> create; then problem folder 101 not found -> create
        list_mock = self.mock_service.files().list
        create_mock = self.mock_service.files().create

        list_mock.return_value.execute.side_effect = [
            {'files': []},  # test_cases does not exist
            {'files': []},  # '101' does not exist
        ]
        create_mock.return_value.execute.side_effect = [
            {'id': 'test-cases-folder-id'},
            {'id': 'problem-101-folder-id'},
        ]

        problem_folder_id = self.manager._get_problem_folder(101)
        self.assertEqual(problem_folder_id, 'problem-101-folder-id')

        # Verify created folders with correct parents
        create_calls = create_mock.call_args_list
        self.assertEqual(len(create_calls), 2)
        # Call 1: create 'test_cases' with parent self.root_folder_id
        self.assertEqual(create_calls[0].kwargs['body']['name'], 'test_cases')
        self.assertEqual(create_calls[0].kwargs['body']['parents'], [self.root_folder_id])
        # Call 2: create '101' with parent 'test-cases-folder-id'
        self.assertEqual(create_calls[1].kwargs['body']['name'], '101')
        self.assertEqual(create_calls[1].kwargs['body']['parents'], ['test-cases-folder-id'])

    def test_upload_test_case_naming_and_parents(self):
        """Test case input named {test_number:02d} and output named {test_number:02d}.a."""
        # Mock folder resolution returning cached problem folder
        self.manager._folder_cache[(self.root_folder_id, 'test_cases')] = 'test-cases-folder-id'
        self.manager._folder_cache[('test-cases-folder-id', '42')] = 'problem-42-folder-id'

        create_mock = self.mock_service.files().create
        create_mock.return_value.execute.side_effect = [
            {'id': 'input-file-id', 'name': '01'},
            {'id': 'output-file-id', 'name': '01.a'},
        ]

        self.manager.upload_test_case(42, 1, 'input data\n', 'output data\n')

        calls = create_mock.call_args_list
        self.assertEqual(len(calls), 2)

        # Input file check
        input_call = calls[0]
        self.assertEqual(input_call.kwargs['body']['name'], '01')
        self.assertEqual(input_call.kwargs['body']['parents'], ['problem-42-folder-id'])

        # Output file check
        output_call = calls[1]
        self.assertEqual(output_call.kwargs['body']['name'], '01.a')
        self.assertEqual(output_call.kwargs['body']['parents'], ['problem-42-folder-id'])

    def test_upload_test_case_preserves_empty_input_and_output(self):
        """Empty input and output strings are uploaded without being skipped or crashing."""
        self.manager._folder_cache[(self.root_folder_id, 'test_cases')] = 'tc-folder'
        self.manager._folder_cache[('tc-folder', '99')] = 'p-folder'

        create_mock = self.mock_service.files().create
        create_mock.return_value.execute.side_effect = [
            {'id': 'in-id', 'name': '05'},
            {'id': 'out-id', 'name': '05.a'},
        ]

        self.manager.upload_test_case(99, 5, '', '')

        calls = create_mock.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs['body']['name'], '05')
        self.assertEqual(calls[1].kwargs['body']['name'], '05.a')

    def test_empty_problem_files_deletes_existing_files(self):
        """empty_problem_files lists and deletes all files in problem directory."""
        list_mock = self.mock_service.files().list
        delete_mock = self.mock_service.files().delete

        # Mock folder lookup for test_cases, then 55, then files inside 55
        list_mock.return_value.execute.side_effect = [
            {'files': [{'id': 'tc-folder-id', 'name': 'test_cases'}]},
            {'files': [{'id': 'p-55-folder-id', 'name': '55'}]},
            {
                'files': [
                    {'id': 'file-1-id', 'name': '01'},
                    {'id': 'file-2-id', 'name': '01.a'},
                    {'id': 'file-3-id', 'name': 'custom_checker.exe'},
                ],
                'nextPageToken': None
            }
        ]
        delete_mock.return_value.execute.return_value = {}

        self.manager.empty_problem_files(55)

        # Ensure delete was called for each of the 3 files
        delete_calls = delete_mock.call_args_list
        self.assertEqual(len(delete_calls), 3)
        self.assertEqual(delete_calls[0].kwargs['fileId'], 'file-1-id')
        self.assertEqual(delete_calls[1].kwargs['fileId'], 'file-2-id')
        self.assertEqual(delete_calls[2].kwargs['fileId'], 'file-3-id')

    def test_empty_problem_files_nonexistent_folder_is_safe(self):
        """empty_problem_files does nothing when folder does not exist yet."""
        list_mock = self.mock_service.files().list
        list_mock.return_value.execute.return_value = {'files': []}
        delete_mock = self.mock_service.files().delete

        self.manager.empty_problem_files(999)
        delete_mock.assert_not_called()

    def test_upload_file_custom_checker(self):
        """upload_file uploads a named file with specified mime type into problem folder."""
        self.manager._folder_cache[(self.root_folder_id, 'test_cases')] = 'tc-folder'
        self.manager._folder_cache[('tc-folder', '88')] = 'p-88-folder'

        create_mock = self.mock_service.files().create
        create_mock.return_value.execute.return_value = {'id': 'checker-file-id', 'name': 'custom_checker.cpp'}

        res = self.manager.upload_file(88, 'custom_checker.cpp', '#include <iostream>', mimetype='text/plain')
        self.assertEqual(res['id'], 'checker-file-id')

        create_mock.assert_called_once()
        body = create_mock.call_args.kwargs['body']
        self.assertEqual(body['name'], 'custom_checker.cpp')
        self.assertEqual(body['parents'], ['p-88-folder'])

    def test_api_error_raises_google_drive_storage_error(self):
        """API failure during upload raises GoogleDriveStorageError and does not mask failure."""
        self.manager._folder_cache[(self.root_folder_id, 'test_cases')] = 'tc-folder'
        self.manager._folder_cache[('tc-folder', '77')] = 'p-77-folder'

        create_mock = self.mock_service.files().create
        create_mock.return_value.execute.side_effect = RuntimeError("Google Drive API quota exceeded")

        with self.assertRaises(GoogleDriveStorageError) as ctx:
            self.manager.upload_test_case(77, 1, 'in', 'out')

        self.assertIn("Failed to upload test case #1", str(ctx.exception))

    def test_polygon_api_migrate_to_google_drive(self):
        """PolygonAPI.migrate_to_google_drive orchestrates empty, upload, and custom checker."""
        api = PolygonAPI()
        mock_drive_manager = MagicMock()
        mock_test_cases = [
            {'index': 1, 'input': '1\n', 'output': '1\n'},
            {'index': 2, 'input': '2\n', 'output': '4\n'},
        ]

        with patch.object(api, 'get_test_cases_from_redis', return_value=mock_test_cases), \
             patch.object(api, 'upload_custom_checker_to_gdrive') as mock_checker_upload:

            count = api.migrate_to_google_drive(
                problem_id='poly-101',
                credentials_file='dummy.json',
                root_folder_id='root-id',
                db_problem_id=10,
                drive_manager=mock_drive_manager
            )

            self.assertEqual(count, 2)
            # empty_problem_files called before upload
            mock_drive_manager.empty_problem_files.assert_called_once_with(10)
            # Both test cases uploaded
            self.assertEqual(mock_drive_manager.upload_test_case.call_count, 2)
            mock_drive_manager.upload_test_case.assert_any_call(10, 1, '1\n', '1\n')
            mock_drive_manager.upload_test_case.assert_any_call(10, 2, '2\n', '4\n')
            # Custom checker upload attempted
            mock_checker_upload.assert_called_once_with('poly-101', mock_drive_manager, 10)

    @override_settings(STORAGE_PROVIDER='gdrive', GOOGLE_DRIVE_CREDENTIALS_FILE='dummy.json', GOOGLE_DRIVE_FOLDER_ID='root-id')
    def test_polygon_api_migrate_to_cloud_storage_gdrive(self):
        """migrate_to_cloud_storage dispatches to migrate_to_google_drive when provider is gdrive."""
        api = PolygonAPI()
        with patch.object(api, 'migrate_to_google_drive', return_value=5) as mock_gdrive:
            count = api.migrate_to_cloud_storage('poly-202', db_problem_id=20)
            self.assertEqual(count, 5)
            mock_gdrive.assert_called_once_with(
                problem_id='poly-202',
                credentials_file='dummy.json',
                root_folder_id='root-id',
                db_problem_id=20,
                testset='tests',
                drive_manager=None
            )

    @override_settings(STORAGE_PROVIDER='azure', AZURE_STORAGE_ACCOUNT_URL='http://example.com', AZURE_TENANT_ID='t', AZURE_CLIENT_ID='c', AZURE_USERNAME='u', AZURE_PASSWORD='p', AZURE_CONTAINER_NAME='cnt')
    def test_polygon_api_migrate_to_cloud_storage_azure(self):
        """migrate_to_cloud_storage dispatches to migrate_to_azure_blob when provider is azure."""
        api = PolygonAPI()
        with patch.object(api, 'migrate_to_azure_blob', return_value=None) as mock_azure:
            api.migrate_to_cloud_storage('poly-303', db_problem_id=30)
            mock_azure.assert_called_once_with(
                problem_id='poly-303',
                azure_account_url='http://example.com',
                azure_tenant_id='t',
                azure_client_id='c',
                azure_username='u',
                azure_password='p',
                container_name='cnt',
                db_problem_id=30,
                testset='tests'
            )

    @override_settings(STORAGE_PROVIDER='unknown_provider')
    def test_polygon_api_migrate_to_cloud_storage_unsupported_provider(self):
        """Unsupported STORAGE_PROVIDER raises ValueError."""
        api = PolygonAPI()
        with self.assertRaises(ValueError) as ctx:
            api.migrate_to_cloud_storage('poly-404')
        self.assertIn("Unsupported STORAGE_PROVIDER", str(ctx.exception))

    @override_settings(STORAGE_PROVIDER='gdrive', GOOGLE_DRIVE_CREDENTIALS_FILE='dummy.json', GOOGLE_DRIVE_FOLDER_ID='root-id')
    def test_views_migrate_to_google_drive_success(self):
        """View successfully executes Google Drive migration when problem exists in DB."""
        problem = Problem.objects.create(
            polygon_id='77777',
            title='GDrive Success Problem',
            slug='gdrive-success-problem',
            difficulty='easy'
        )

        with patch('problems.views.PolygonAPI') as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_custom_checker_info.return_value = None
            mock_api.get_problem_info.return_value = {'name': 'GDrive Success Problem'}
            mock_api.download_and_extract_package.return_value = '<html><div class="title">GDrive Success Problem</div><div class="legend">Legend</div><div class="input-specification">Input</div><div class="output-specification">Output</div></html>'
            mock_api.get_test_cases_from_redis.return_value = [{'index': 1, 'input': '1', 'output': '1'}]
            mock_api.get_test_cases.return_value = [{'index': 1}]
            mock_api._make_request.return_value = 'ncmp'
            mock_api.migrate_to_google_drive.return_value = 1

            response = self.client.post('/', {
                'problem_id': '77777',
                'migrate_to_azure': '1'  # Trigger cloud storage migration
            })

            self.assertEqual(response.status_code, 200)
            mock_api.migrate_to_google_drive.assert_called_once_with(
                '77777',
                'dummy.json',
                'root-id',
                problem.id
            )
            self.assertIn('Test cases migrated to Google Drive successfully.', response.context.get('success', ''))

    @override_settings(STORAGE_PROVIDER='gdrive')
    def test_views_migrate_to_google_drive_not_in_db_shows_error(self):
        """View blocks migration and prompts user if problem is not yet in database."""
        response = self.client.post('/', {
            'problem_id': '88888',
            'migrate_to_azure': '1'
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("has not been migrated to the database yet", response.context.get('error', ''))

    @override_settings(STORAGE_PROVIDER='gdrive', GOOGLE_DRIVE_CREDENTIALS_FILE='dummy.json', GOOGLE_DRIVE_FOLDER_ID='root-id')
    def test_views_migrate_to_google_drive_failure_displays_error(self):
        """When Google Drive migration fails, the error is displayed to the user without masking."""
        Problem.objects.create(
            polygon_id='99991',
            title='GDrive Failure Problem',
            slug='gdrive-failure-problem',
            difficulty='hard'
        )

        with patch('problems.views.PolygonAPI') as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_custom_checker_info.return_value = None
            mock_api.migrate_to_google_drive.side_effect = GoogleDriveStorageError("Permission denied on Google Drive folder")

            response = self.client.post('/', {
                'problem_id': '99991',
                'migrate_to_azure': '1'
            })

            self.assertEqual(response.status_code, 200)
            self.assertIn("Google Drive migration failed", response.context.get('error', ''))
            self.assertNotIn("successfully", response.context.get('success', ''))

    @override_settings(STORAGE_PROVIDER='gdrive')
    def test_ui_button_text_rendered_for_gdrive(self):
        """When STORAGE_PROVIDER is gdrive, button text says 'Migrate Test Cases to Google Drive'."""
        with patch('problems.views.PolygonAPI') as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_problem_info.return_value = {'name': 'Sample UI Problem'}
            mock_api.download_and_extract_package.return_value = '<html><div class="title">Sample UI Problem</div><div class="legend">Legend</div><div class="input-specification">Input</div><div class="output-specification">Output</div></html>'
            mock_api.get_all_test_cases.return_value = []
            mock_api._make_request.return_value = 'ncmp'
            mock_api.get_custom_checker_info.return_value = None

            response = self.client.post('/', {'problem_id': '101'})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Migrate Test Cases to Google Drive')
            self.assertNotContains(response, 'Migrate Test Cases to Azure')

    @override_settings(STORAGE_PROVIDER='azure')
    def test_ui_button_text_rendered_for_azure(self):
        """When STORAGE_PROVIDER is azure, button text says 'Migrate Test Cases to Azure'."""
        with patch('problems.views.PolygonAPI') as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_problem_info.return_value = {'name': 'Sample UI Problem'}
            mock_api.download_and_extract_package.return_value = '<html><div class="title">Sample UI Problem</div><div class="legend">Legend</div><div class="input-specification">Input</div><div class="output-specification">Output</div></html>'
            mock_api.get_all_test_cases.return_value = []
            mock_api._make_request.return_value = 'ncmp'
            mock_api.get_custom_checker_info.return_value = None

            response = self.client.post('/', {'problem_id': '101'})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Migrate Test Cases to Azure')
            self.assertNotContains(response, 'Migrate Test Cases to Google Drive')




