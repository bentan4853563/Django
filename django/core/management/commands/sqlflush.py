from __future__ import unicode_literals

from django.core.management.base import NoArgsCommand
from django.core.management.sql import sql_flush
from django.db import connections, DEFAULT_DB_ALIAS


class Command(NoArgsCommand):
    help = "Returns a list of the SQL statements required to return all tables in the database to the state they were in just after they were installed."

    output_transaction = True

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)
        parser.add_argument('--database', default=DEFAULT_DB_ALIAS,
            help='Nominates a database to print the SQL for. Defaults to the '
                 '"default" database.')

    def handle_noargs(self, **options):
        return '\n'.join(sql_flush(self.style, connections[options['database']], only_django=True))
