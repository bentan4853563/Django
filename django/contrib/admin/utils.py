import datetime
import decimal
from collections import defaultdict

from django.contrib.auth import get_permission_codename
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models.constants import LOOKUP_SEP
from django.db.models.deletion import Collector
from django.db.models.sql.constants import QUERY_TERMS
from django.forms.utils import pretty_name
from django.urls import NoReverseMatch, reverse
from django.utils import formats, timezone
from django.utils.encoding import force_text, smart_text
from django.utils.html import format_html
from django.utils.text import capfirst
from django.utils.translation import (
    override as translation_override, ungettext,
)


class FieldIsAForeignKeyColumnName(Exception):
    """A field is a foreign key attname, i.e. <FK>_id."""
    pass


def lookup_needs_distinct(opts, lookup_path):
    """
    Returns True if 'distinct()' should be used to query the given lookup path.
    """
    lookup_fields = lookup_path.split(LOOKUP_SEP)
    # Remove the last item of the lookup path if it is a query term
    if lookup_fields[-1] in QUERY_TERMS:
        lookup_fields = lookup_fields[:-1]
    # Now go through the fields (following all relations) and look for an m2m
    for field_name in lookup_fields:
        field = opts.get_field(field_name)
        if hasattr(field, 'get_path_info'):
            # This field is a relation, update opts to follow the relation
            path_info = field.get_path_info()
            opts = path_info[-1].to_opts
            if any(path.m2m for path in path_info):
                # This field is a m2m relation so we know we need to call distinct
                return True
    return False


def prepare_lookup_value(key, value):
    """
    Returns a lookup value prepared to be used in queryset filtering.
    """
    # if key ends with __in, split parameter into separate values
    if key.endswith('__in'):
        value = value.split(',')
    # if key ends with __isnull, special case '' and the string literals 'false' and '0'
    if key.endswith('__isnull'):
        if value.lower() in ('', 'false', '0'):
            value = False
        else:
            value = True
    return value


def quote(s):
    """
    Ensure that primary key values do not confuse the admin URLs by escaping
    any '/', '_' and ':' and similarly problematic characters.
    Similar to urllib.quote, except that the quoting is slightly different so
    that it doesn't get automatically unquoted by the Web browser.
    """
    if not isinstance(s, str):
        return s
    res = list(s)
    for i in range(len(res)):
        c = res[i]
        if c in """:/_#?;@&=+$,"[]<>%\n\\""":
            res[i] = '_%02X' % ord(c)
    return ''.join(res)


def unquote(s):
    """
    Undo the effects of quote(). Based heavily on urllib.unquote().
    """
    mychr = chr
    myatoi = int
    list = s.split('_')
    res = [list[0]]
    myappend = res.append
    del list[0]
    for item in list:
        if item[1:2]:
            try:
                myappend(mychr(myatoi(item[:2], 16)) + item[2:])
            except ValueError:
                myappend('_' + item)
        else:
            myappend('_' + item)
    return "".join(res)


def flatten(fields):
    """Returns a list which is a single level of flattening of the
    original list."""
    flat = []
    for field in fields:
        if isinstance(field, (list, tuple)):
            flat.extend(field)
        else:
            flat.append(field)
    return flat


def flatten_fieldsets(fieldsets):
    """Returns a list of field names from an admin fieldsets structure."""
    field_names = []
    for name, opts in fieldsets:
        field_names.extend(
            flatten(opts['fields'])
        )
    return field_names


def get_deleted_objects(objs, opts, user, admin_site, using):
    """
    Find all objects related to ``objs`` that should also be deleted. ``objs``
    must be a homogeneous iterable of objects (e.g. a QuerySet).

    Returns a nested list of strings suitable for display in the
    template with the ``unordered_list`` filter.
    """
    collector = NestedObjects(using=using)
    collector.collect(objs)
    perms_needed = set()

    def format_callback(obj):
        has_admin = obj.__class__ in admin_site._registry
        opts = obj._meta

        no_edit_link = '%s: %s' % (capfirst(opts.verbose_name),
                                   force_text(obj))

        if has_admin:
            try:
                admin_url = reverse('%s:%s_%s_change'
                                    % (admin_site.name,
                                       opts.app_label,
                                       opts.model_name),
                                    None, (quote(obj._get_pk_val()),))
            except NoReverseMatch:
                # Change url doesn't exist -- don't display link to edit
                return no_edit_link

            p = '%s.%s' % (opts.app_label,
                           get_permission_codename('delete', opts))
            if not user.has_perm(p):
                perms_needed.add(opts.verbose_name)
            # Display a link to the admin page.
            return format_html('{}: <a href="{}">{}</a>',
                               capfirst(opts.verbose_name),
                               admin_url,
                               obj)
        else:
            # Don't display link to edit, because it either has no
            # admin or is edited inline.
            return no_edit_link

    to_delete = collector.nested(format_callback)

    protected = [format_callback(obj) for obj in collector.protected]
    model_count = {model._meta.verbose_name_plural: len(objs) for model, objs in collector.model_objs.items()}

    return to_delete, model_count, perms_needed, protected


class NestedObjects(Collector):
    def __init__(self, *args, **kwargs):
        super(NestedObjects, self).__init__(*args, **kwargs)
        self.edges = {}  # {from_instance: [to_instances]}
        self.protected = set()
        self.model_objs = defaultdict(set)

    def add_edge(self, source, target):
        self.edges.setdefault(source, []).append(target)

    def collect(self, objs, source=None, source_attr=None, **kwargs):
        for obj in objs:
            if source_attr and not source_attr.endswith('+'):
                related_name = source_attr % {
                    'class': source._meta.model_name,
                    'app_label': source._meta.app_label,
                }
                self.add_edge(getattr(obj, related_name), obj)
            else:
                self.add_edge(None, obj)
            self.model_objs[obj._meta.model].add(obj)
        try:
            return super(NestedObjects, self).collect(objs, source_attr=source_attr, **kwargs)
        except models.ProtectedError as e:
            self.protected.update(e.protected_objects)

    def related_objects(self, related, objs):
        qs = super(NestedObjects, self).related_objects(related, objs)
        return qs.select_related(related.field.name)

    def _nested(self, obj, seen, format_callback):
        if obj in seen:
            return []
        seen.add(obj)
        children = []
        for child in self.edges.get(obj, ()):
            children.extend(self._nested(child, seen, format_callback))
        if format_callback:
            ret = [format_callback(obj)]
        else:
            ret = [obj]
        if children:
            ret.append(children)
        return ret

    def nested(self, format_callback=None):
        """
        Return the graph as a nested list.
        """
        seen = set()
        roots = []
        for root in self.edges.get(None, ()):
            roots.extend(self._nested(root, seen, format_callback))
        return roots

    def can_fast_delete(self, *args, **kwargs):
        """
        We always want to load the objects into memory so that we can display
        them to the user in confirm page.
        """
        return False


def model_format_dict(obj):
    """
    Return a `dict` with keys 'verbose_name' and 'verbose_name_plural',
    typically for use with string formatting.

    `obj` may be a `Model` instance, `Model` subclass, or `QuerySet` instance.
    """
    if isinstance(obj, (models.Model, models.base.ModelBase)):
        opts = obj._meta
    elif isinstance(obj, models.query.QuerySet):
        opts = obj.model._meta
    else:
        opts = obj
    return {
        'verbose_name': force_text(opts.verbose_name),
        'verbose_name_plural': force_text(opts.verbose_name_plural)
    }


def model_ngettext(obj, n=None):
    """
    Return the appropriate `verbose_name` or `verbose_name_plural` value for
    `obj` depending on the count `n`.

    `obj` may be a `Model` instance, `Model` subclass, or `QuerySet` instance.
    If `obj` is a `QuerySet` instance, `n` is optional and the length of the
    `QuerySet` is used.
    """
    if isinstance(obj, models.query.QuerySet):
        if n is None:
            n = obj.count()
        obj = obj.model
    d = model_format_dict(obj)
    singular, plural = d["verbose_name"], d["verbose_name_plural"]
    return ungettext(singular, plural, n or 0)


def lookup_field(name, obj, model_admin=None):
    opts = obj._meta
    try:
        f = _get_non_gfk_field(opts, name)
    except (FieldDoesNotExist, FieldIsAForeignKeyColumnName):
        # For non-field values, the value is either a method, property or
        # returned via a callable.
        if callable(name):
            attr = name
            value = attr(obj)
        elif (model_admin is not None and
                hasattr(model_admin, name) and
                not name == '__str__' and
                not name == '__unicode__'):
            attr = getattr(model_admin, name)
            value = attr(obj)
        else:
            attr = getattr(obj, name)
            if callable(attr):
                value = attr()
            else:
                value = attr
        f = None
    else:
        attr = None
        value = getattr(obj, name)
    return f, attr, value


def _get_non_gfk_field(opts, name):
    """
    For historical reasons, the admin app relies on GenericForeignKeys as being
    "not found" by get_field(). This could likely be cleaned up.

    Reverse relations should also be excluded as these aren't attributes of the
    model (rather something like `foo_set`).
    """
    field = opts.get_field(name)
    if (field.is_relation and
            # Generic foreign keys OR reverse relations
            ((field.many_to_one and not field.related_model) or field.one_to_many)):
        raise FieldDoesNotExist()

    # Avoid coercing <FK>_id fields to FK
    if field.is_relation and not field.many_to_many and hasattr(field, 'attname') and field.attname == name:
        raise FieldIsAForeignKeyColumnName()

    return field


def label_for_field(name, model, model_admin=None, return_attr=False):
    """
    Returns a sensible label for a field name. The name can be a callable,
    property (but not created with @property decorator) or the name of an
    object's attribute, as well as a genuine fields. If return_attr is
    True, the resolved attribute (which could be a callable) is also returned.
    This will be None if (and only if) the name refers to a field.
    """
    attr = None
    try:
        field = _get_non_gfk_field(model._meta, name)
        try:
            label = field.verbose_name
        except AttributeError:
            # field is likely a ForeignObjectRel
            label = field.related_model._meta.verbose_name
    except FieldDoesNotExist:
        if name == "__str__":
            label = force_text(model._meta.verbose_name)
            attr = str
        else:
            if callable(name):
                attr = name
            elif model_admin is not None and hasattr(model_admin, name):
                attr = getattr(model_admin, name)
            elif hasattr(model, name):
                attr = getattr(model, name)
            else:
                message = "Unable to lookup '%s' on %s" % (name, model._meta.object_name)
                if model_admin:
                    message += " or %s" % (model_admin.__class__.__name__,)
                raise AttributeError(message)

            if hasattr(attr, "short_description"):
                label = attr.short_description
            elif (isinstance(attr, property) and
                  hasattr(attr, "fget") and
                  hasattr(attr.fget, "short_description")):
                label = attr.fget.short_description
            elif callable(attr):
                if attr.__name__ == "<lambda>":
                    label = "--"
                else:
                    label = pretty_name(attr.__name__)
            else:
                label = pretty_name(name)
    except FieldIsAForeignKeyColumnName:
        label = pretty_name(name)
        attr = name

    if return_attr:
        return (label, attr)
    else:
        return label


def help_text_for_field(name, model):
    help_text = ""
    try:
        field = _get_non_gfk_field(model._meta, name)
    except (FieldDoesNotExist, FieldIsAForeignKeyColumnName):
        pass
    else:
        if hasattr(field, 'help_text'):
            help_text = field.help_text
    return smart_text(help_text)


def display_for_field(value, field, empty_value_display):
    from django.contrib.admin.templatetags.admin_list import _boolean_icon

    if getattr(field, 'flatchoices', None):
        return dict(field.flatchoices).get(value, empty_value_display)
    # NullBooleanField needs special-case null-handling, so it comes
    # before the general null test.
    elif isinstance(field, models.BooleanField) or isinstance(field, models.NullBooleanField):
        return _boolean_icon(value)
    elif value is None:
        return empty_value_display
    elif isinstance(field, models.DateTimeField):
        return formats.localize(timezone.template_localtime(value))
    elif isinstance(field, (models.DateField, models.TimeField)):
        return formats.localize(value)
    elif isinstance(field, models.DecimalField):
        return formats.number_format(value, field.decimal_places)
    elif isinstance(field, (models.IntegerField, models.FloatField)):
        return formats.number_format(value)
    elif isinstance(field, models.FileField) and value:
        return format_html('<a href="{}">{}</a>', value.url, value)
    else:
        return display_for_value(value, empty_value_display)


def display_for_value(value, empty_value_display, boolean=False):
    from django.contrib.admin.templatetags.admin_list import _boolean_icon

    if boolean:
        return _boolean_icon(value)
    elif value is None:
        return empty_value_display
    elif isinstance(value, datetime.datetime):
        return formats.localize(timezone.template_localtime(value))
    elif isinstance(value, (datetime.date, datetime.time)):
        return formats.localize(value)
    elif isinstance(value, (int, decimal.Decimal, float)):
        return formats.number_format(value)
    elif isinstance(value, (list, tuple)):
        return ', '.join(force_text(v) for v in value)
    else:
        return force_text(value)


class NotRelationField(Exception):
    pass


def get_model_from_relation(field):
    if hasattr(field, 'get_path_info'):
        return field.get_path_info()[-1].to_opts.model
    else:
        raise NotRelationField


def reverse_field_path(model, path):
    """ Create a reversed field path.

    E.g. Given (Order, "user__groups"),
    return (Group, "user__order").

    Final field must be a related model, not a data field.
    """
    reversed_path = []
    parent = model
    pieces = path.split(LOOKUP_SEP)
    for piece in pieces:
        field = parent._meta.get_field(piece)
        # skip trailing data field if extant:
        if len(reversed_path) == len(pieces) - 1:  # final iteration
            try:
                get_model_from_relation(field)
            except NotRelationField:
                break

        # Field should point to another model
        if field.is_relation and not (field.auto_created and not field.concrete):
            related_name = field.related_query_name()
            parent = field.remote_field.model
        else:
            related_name = field.field.name
            parent = field.related_model
        reversed_path.insert(0, related_name)
    return (parent, LOOKUP_SEP.join(reversed_path))


def get_fields_from_path(model, path):
    """ Return list of Fields given path relative to model.

    e.g. (ModelX, "user__groups__name") -> [
        <django.db.models.fields.related.ForeignKey object at 0x...>,
        <django.db.models.fields.related.ManyToManyField object at 0x...>,
        <django.db.models.fields.CharField object at 0x...>,
    ]
    """
    pieces = path.split(LOOKUP_SEP)
    fields = []
    for piece in pieces:
        if fields:
            parent = get_model_from_relation(fields[-1])
        else:
            parent = model
        fields.append(parent._meta.get_field(piece))
    return fields


def construct_change_message(form, formsets, add):
    """
    Construct a JSON structure describing changes from a changed object.
    Translations are deactivated so that strings are stored untranslated.
    Translation happens later on LogEntry access.
    """
    change_message = []
    if add:
        change_message.append({'added': {}})
    elif form.changed_data:
        change_message.append({'changed': {'fields': form.changed_data}})

    if formsets:
        with translation_override(None):
            for formset in formsets:
                for added_object in formset.new_objects:
                    change_message.append({
                        'added': {
                            'name': force_text(added_object._meta.verbose_name),
                            'object': force_text(added_object),
                        }
                    })
                for changed_object, changed_fields in formset.changed_objects:
                    change_message.append({
                        'changed': {
                            'name': force_text(changed_object._meta.verbose_name),
                            'object': force_text(changed_object),
                            'fields': changed_fields,
                        }
                    })
                for deleted_object in formset.deleted_objects:
                    change_message.append({
                        'deleted': {
                            'name': force_text(deleted_object._meta.verbose_name),
                            'object': force_text(deleted_object),
                        }
                    })
    return change_message
