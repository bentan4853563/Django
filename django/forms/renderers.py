import functools
import warnings
from pathlib import Path

from django.conf import settings
from django.template.backends.django import DjangoTemplates
from django.template.loader import get_template
from django.utils.deprecation import RemovedInDjango60Warning
from django.utils.functional import cached_property
from django.utils.module_loading import import_string


@functools.lru_cache
def get_default_renderer():
    renderer_class = import_string(settings.FORM_RENDERER)
    return renderer_class()


class BaseRenderer:
    form_template_name = "django/forms/div.html"
    formset_template_name = "django/forms/formsets/div.html"
    field_template_name = "django/forms/field.html"

    def get_template(self, template_name):
        raise NotImplementedError("subclasses must implement get_template()")

    def render(self, template_name, context, request=None):
        template = self.get_template(template_name)
        return template.render(context, request=request).strip()


class EngineMixin:
    def get_template(self, template_name):
        return self.engine.get_template(template_name)

    @cached_property
    def engine(self):
        return self.backend(
            {
                "APP_DIRS": True,
                "DIRS": [Path(__file__).parent / self.backend.app_dirname],
                "NAME": "djangoforms",
                "OPTIONS": {},
            }
        )


class DjangoTemplates(EngineMixin, BaseRenderer):
    """
    Load Django templates from the built-in widget templates in
    django/forms/templates and from apps' 'templates' directory.
    """

    backend = DjangoTemplates


class Jinja2(EngineMixin, BaseRenderer):
    """
    Load Jinja2 templates from the built-in widget templates in
    django/forms/jinja2 and from apps' 'jinja2' directory.
    """

    @cached_property
    def backend(self):
        from django.template.backends.jinja2 import Jinja2

        return Jinja2


# RemovedInDjango60Warning.
class DjangoDivFormRenderer(DjangoTemplates):
    """
    Load Django templates from django/forms/templates and from apps'
    'templates' directory and use the 'div.html' template to render forms and
    formsets.
    """

    def __init__(self, *args, **kwargs):
        warnings.warn(
            "The DjangoDivFormRenderer transitional form renderer is deprecated. Use "
            "DjangoTemplates instead.",
            RemovedInDjango60Warning,
        )
        super().__init__(*args, **kwargs)


# RemovedInDjango60Warning.
class Jinja2DivFormRenderer(Jinja2):
    """
    Load Jinja2 templates from the built-in widget templates in
    django/forms/jinja2 and from apps' 'jinja2' directory.
    """

    def __init__(self, *args, **kwargs):
        warnings.warn(
            "The Jinja2DivFormRenderer transitional form renderer is deprecated. Use "
            "Jinja2 instead.",
            RemovedInDjango60Warning,
        )
        super().__init__(*args, **kwargs)


class TemplatesSetting(BaseRenderer):
    """
    Load templates using template.loader.get_template() which is configured
    based on settings.TEMPLATES.
    """

    def get_template(self, template_name):
        return get_template(template_name)
