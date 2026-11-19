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


