from django.db.models.sql import aggregates
from django.db.models.sql.aggregates import *  # NOQA
from django.contrib.gis.db.models.fields import GeometryField


__all__ = ['Collect', 'Extent', 'Extent3D', 'MakeLine', 'Union'] + aggregates.__all__


warnings.warn(
    "django.contrib.gis.db.models.sql.aggregates is deprecated. Use "
    "django.contrib.gis.db.models.aggregates instead.",
    RemovedInDjango20Warning, stacklevel=2)


class GeoAggregate(Aggregate):
    # Default SQL template for spatial aggregates.
    sql_template = '%(function)s(%(expressions)s)'

    # Flags for indicating the type of the aggregate.
    is_extent = False

    def __init__(self, col, source=None, is_summary=False, tolerance=0.05, **extra):
        super(GeoAggregate, self).__init__(col, source, is_summary, **extra)

        # Required by some Oracle aggregates.
        self.tolerance = tolerance

        # Can't use geographic aggregates on non-geometry fields.
        if not isinstance(self.source, GeometryField):
            raise ValueError('Geospatial aggregates only allowed on geometry fields.')

    def as_sql(self, compiler, connection):
        "Return the aggregate, rendered as SQL with parameters."

        if connection.ops.oracle:
            self.extra['tolerance'] = self.tolerance

        params = []

        if hasattr(self.col, 'as_sql'):
            field_name, params = self.col.as_sql(compiler, connection)
        elif isinstance(self.col, (list, tuple)):
            field_name = '.'.join(compiler.quote_name_unless_alias(c) for c in self.col)
        else:
            field_name = self.col

        sql_template, sql_function = connection.ops.spatial_aggregate_sql(self)

        substitutions = {
            'function': sql_function,
            'expressions': field_name
        }
        substitutions.update(self.extra)

        return sql_template % substitutions, params


class Collect(GeoAggregate):
    pass


class Extent(GeoAggregate):
    is_extent = '2D'


class Extent3D(GeoAggregate):
    is_extent = '3D'


class MakeLine(GeoAggregate):
    pass


class Union(GeoAggregate):
    pass
