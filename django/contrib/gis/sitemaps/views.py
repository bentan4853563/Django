from __future__ import unicode_literals

from django.apps import apps
from django.http import Http404
from django.contrib.gis.db.models.fields import GeometryField
from django.contrib.gis.shortcuts import render_to_kml, render_to_kmz
from django.core.exceptions import FieldDoesNotExist
from django.db import connections, DEFAULT_DB_ALIAS


def kml(request, label, model, field_name=None, compress=False, using=DEFAULT_DB_ALIAS):
    """
    This view generates KML for the given app label, model, and field name.

    The model's default manager must be GeoManager, and the field name
    must be that of a geographic field.
    """
    placemarks = []
    try:
        klass = apps.get_model(label, model)
    except LookupError:
        raise Http404('You must supply a valid app label and module name.  Got "%s.%s"' % (label, model))

    if field_name:
        try:
            field = klass._meta.get_field(field_name)
            if not isinstance(field, GeometryField):
                raise FieldDoesNotExist
        except FieldDoesNotExist:
            raise Http404('Invalid geometry field.')

    connection = connections[using]

    if connection.ops.postgis:
        # PostGIS will take care of transformation.
        placemarks = klass._default_manager.using(using).kml(field_name=field_name)
    else:
        # There's no KML method on Oracle or MySQL, so we use the `kml`
        # attribute of the lazy geometry instead.
        placemarks = []
        if connection.ops.oracle:
            qs = klass._default_manager.using(using).transform(4326, field_name=field_name)
        else:
            qs = klass._default_manager.using(using).all()
        for mod in qs:
            mod.kml = getattr(mod, field_name).kml
            placemarks.append(mod)

    # Getting the render function and rendering to the correct.
    if compress:
        render = render_to_kmz
    else:
        render = render_to_kml
    return render('gis/kml/placemarks.kml', {'places': placemarks})


def kmz(request, label, model, field_name=None, using=DEFAULT_DB_ALIAS):
    """
    This view returns KMZ for the given app label, model, and field name.
    """
    return kml(request, label, model, field_name, compress=True, using=using)
