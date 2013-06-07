from django.test import TransactionTestCase
from django.db import connection, models, migrations
from django.db.migrations.state import ProjectState


class OperationTests(TransactionTestCase):
    """
    Tests running the operations and making sure they do what they say they do.
    Each test looks at their state changing, and then their database operation -
    both forwards and backwards.
    """

    def assertTableExists(self, table):
        self.assertIn(table, connection.introspection.get_table_list(connection.cursor()))

    def assertTableNotExists(self, table):
        self.assertNotIn(table, connection.introspection.get_table_list(connection.cursor()))

    def assertColumnExists(self, table, column):
        self.assertIn(column, [c.name for c in connection.introspection.get_table_description(connection.cursor(), table)])

    def assertColumnNotExists(self, table, column):
        self.assertNotIn(column, [c.name for c in connection.introspection.get_table_description(connection.cursor(), table)])

    def set_up_test_model(self, app_label):
        """
        Creates a test model state and database table.
        """
        # Make the "current" state
        creation = migrations.CreateModel(
            "Pony",
            [
                ("id", models.AutoField(primary_key=True)),
                ("pink", models.BooleanField(default=True)),
            ],
        )
        project_state = ProjectState()
        creation.state_forwards(app_label, project_state)
        # Set up the database
        with connection.schema_editor() as editor:
            creation.database_forwards(app_label, editor, ProjectState(), project_state)
        return project_state

    def test_create_model(self):
        """
        Tests the CreateModel operation.
        Most other tests use this as part of setup, so check failures here first.
        """
        operation = migrations.CreateModel(
            "Pony",
            [
                ("id", models.AutoField(primary_key=True)),
                ("pink", models.BooleanField(default=True)),
            ],
        )
        # Test the state alteration
        project_state = ProjectState()
        new_state = project_state.clone()
        operation.state_forwards("test_crmo", new_state)
        self.assertEqual(new_state.models["test_crmo", "pony"].name, "Pony")
        self.assertEqual(len(new_state.models["test_crmo", "pony"].fields), 2)
        # Test the database alteration
        self.assertTableNotExists("test_crmo_pony")
        with connection.schema_editor() as editor:
            operation.database_forwards("test_crmo", editor, project_state, new_state)
        self.assertTableExists("test_crmo_pony")
        # And test reversal
        with connection.schema_editor() as editor:
            operation.database_backwards("test_crmo", editor, new_state, project_state)
        self.assertTableNotExists("test_crmo_pony")
        # And deconstruction
        definition = operation.deconstruct()
        self.assertEqual(definition[0], "CreateModel")
        self.assertEqual(len(definition[1]), 2)
        self.assertEqual(len(definition[2]), 0)
        self.assertEqual(definition[1][0], "Pony")

    def test_delete_model(self):
        """
        Tests the DeleteModel operation.
        """
        project_state = self.set_up_test_model("test_dlmo")
        # Test the state alteration
        operation = migrations.DeleteModel("Pony")
        new_state = project_state.clone()
        operation.state_forwards("test_dlmo", new_state)
        self.assertNotIn(("test_dlmo", "pony"), new_state.models)
        # Test the database alteration
        self.assertTableExists("test_dlmo_pony")
        with connection.schema_editor() as editor:
            operation.database_forwards("test_dlmo", editor, project_state, new_state)
        self.assertTableNotExists("test_dlmo_pony")
        # And test reversal
        with connection.schema_editor() as editor:
            operation.database_backwards("test_dlmo", editor, new_state, project_state)
        self.assertTableExists("test_dlmo_pony")

    def test_add_field(self):
        """
        Tests the AddField operation.
        """
        project_state = self.set_up_test_model("test_adfl")
        # Test the state alteration
        operation = migrations.AddField("Pony", "height", models.FloatField(null=True))
        new_state = project_state.clone()
        operation.state_forwards("test_adfl", new_state)
        self.assertEqual(len(new_state.models["test_adfl", "pony"].fields), 3)
        # Test the database alteration
        self.assertColumnNotExists("test_adfl_pony", "height")
        with connection.schema_editor() as editor:
            operation.database_forwards("test_adfl", editor, project_state, new_state)
        self.assertColumnExists("test_adfl_pony", "height")
        # And test reversal
        with connection.schema_editor() as editor:
            operation.database_backwards("test_adfl", editor, new_state, project_state)
        self.assertColumnNotExists("test_adfl_pony", "height")
