from django.contrib.postgres.fields import JSONField
from django.db.models.aggregates import Aggregate

__all__ = [
    'ArrayAgg', 'BitAnd', 'BitOr', 'BoolAnd', 'BoolOr', 'JsonAgg', 'StringAgg',
]


class ArrayAgg(Aggregate):
    function = 'ARRAY_AGG'

    def convert_value(self, value, expression, connection, context):
        if not value:
            return []
        return value


class BitAnd(Aggregate):
    function = 'BIT_AND'


class BitOr(Aggregate):
    function = 'BIT_OR'


class BoolAnd(Aggregate):
    function = 'BOOL_AND'


class BoolOr(Aggregate):
    function = 'BOOL_OR'


class JsonAgg(Aggregate):
    function = 'JSONB_AGG'
    _output_field = JSONField()

    def convert_value(self, value, expression, connection, context):
        if not value:
            return []
        return value


class StringAgg(Aggregate):
    function = 'STRING_AGG'
    template = "%(function)s(%(distinct)s%(expressions)s, '%(delimiter)s')"

    def __init__(self, expression, delimiter, distinct=False, **extra):
        distinct = 'DISTINCT ' if distinct else ''
        super(StringAgg, self).__init__(expression, delimiter=delimiter, distinct=distinct, **extra)

    def convert_value(self, value, expression, connection, context):
        if not value:
            return ''
        return value
