from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from .models import Problem, ProblemTestCase, SampleTestCase

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



