from __future__ import unicode_literals

import warnings

from django.core import management
from django.test import TestCase
from django.utils.six import StringIO

from .models import Article


class SampleTestCase(TestCase):
    fixtures = ['fixture1.json', 'fixture2.json']

    def testClassFixtures(self):
        "Test cases can load fixture objects into models defined in packages"
        self.assertEqual(Article.objects.count(), 3)
        self.assertQuerysetEqual(
            Article.objects.all(), [
                "Django conquers world!",
                "Copyright is fine the way it is",
                "Poker has no place on ESPN",
            ],
            lambda a: a.headline
        )


class FixtureTestCase(TestCase):

    def test_loaddata(self):
        "Fixtures can load data into models defined in packages"
        # Load fixture 1. Single JSON file, with two objects
        management.call_command("loaddata", "fixture1.json", verbosity=0)
        self.assertQuerysetEqual(
            Article.objects.all(), [
                "Time to reform copyright",
                "Poker has no place on ESPN",
            ],
            lambda a: a.headline,
        )

        # Load fixture 2. JSON file imported by default. Overwrites some
        # existing objects
        management.call_command("loaddata", "fixture2.json", verbosity=0)
        self.assertQuerysetEqual(
            Article.objects.all(), [
                "Django conquers world!",
                "Copyright is fine the way it is",
                "Poker has no place on ESPN",
            ],
            lambda a: a.headline,
        )

        # Load a fixture that doesn't exist
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            management.call_command("loaddata", "unknown.json", verbosity=0)
        self.assertEqual(len(w), 1)
        self.assertTrue(w[0].message, "No fixture named 'unknown' found.")

        self.assertQuerysetEqual(
            Article.objects.all(), [
                "Django conquers world!",
                "Copyright is fine the way it is",
                "Poker has no place on ESPN",
            ],
            lambda a: a.headline,
        )


class InitialSQLTests(TestCase):

    def test_custom_sql(self):
        """
        #14300 -- Verify that custom_sql_for_model searches `app/sql` and not
        `app/models/sql` (the old location will work until Django 1.9)
        """
        out = StringIO()
        management.call_command("sqlcustom", "fixtures_model_package", stdout=out)
        output = out.getvalue()
        self.assertIn("INSERT INTO fixtures_model_package_book (name) VALUES ('My Book')", output)
        # value from deprecated search path models/sql (remove in Django 1.9)
        self.assertIn("Deprecated Book", output)
