from django.test import TestCase
from .models import Problem

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

