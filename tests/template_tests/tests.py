# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

from django.contrib.auth.models import Group
from django.core import urlresolvers
from django.template import (
    Context, Template, TemplateSyntaxError, engines, loader,
)
from django.test import SimpleTestCase, override_settings
from django.utils._os import upath

TEMPLATES_DIR = os.path.join(os.path.dirname(upath(__file__)), 'templates')


class TemplateTests(SimpleTestCase):

    @override_settings(DEBUG=True)
    def test_string_origin(self):
        template = Template('string template')
        self.assertEqual(template.origin.source, 'string template')


class TemplateRegressionTests(SimpleTestCase):

    @override_settings(SETTINGS_MODULE=None, DEBUG=True)
    def test_url_reverse_no_settings_module(self):
        # Regression test for #9005
        t = Template('{% url will_not_match %}')
        c = Context()
        with self.assertRaises(urlresolvers.NoReverseMatch):
            t.render(c)

    @override_settings(
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'OPTIONS': {'string_if_invalid': '%s is invalid'},
        }],
        SETTINGS_MODULE='also_something',
    )
    def test_url_reverse_view_name(self):
        # Regression test for #19827
        t = Template('{% url will_not_match %}')
        c = Context()
        try:
            t.render(c)
        except urlresolvers.NoReverseMatch:
            tb = sys.exc_info()[2]
            depth = 0
            while tb.tb_next is not None:
                tb = tb.tb_next
                depth += 1
            self.assertGreater(depth, 5,
                "The traceback context was lost when reraising the traceback. See #19827")

    @override_settings(DEBUG=True)
    def test_no_wrapped_exception(self):
        """
        The template system doesn't wrap exceptions, but annotates them.
        Refs #16770
        """
        c = Context({"coconuts": lambda: 42 / 0})
        t = Template("{{ coconuts }}")
        with self.assertRaises(ZeroDivisionError) as cm:
            t.render(c)

        self.assertEqual(cm.exception.django_template_source[1], (0, 14))

    def test_invalid_block_suggestion(self):
        # See #7876
        try:
            Template("{% if 1 %}lala{% endblock %}{% endif %}")
        except TemplateSyntaxError as e:
            self.assertEqual(e.args[0], "Invalid block tag: 'endblock', expected 'elif', 'else' or 'endif'")

    def test_super_errors(self):
        """
        Test behavior of the raise errors into included blocks.
        See #18169
        """
        t = loader.get_template('included_content.html')
        with self.assertRaises(urlresolvers.NoReverseMatch):
            t.render()

    def test_debug_tag_non_ascii(self):
        """
        Test non-ASCII model representation in debug output (#23060).
        """
        Group.objects.create(name="清風")
        c1 = Context({"objs": Group.objects.all()})
        t1 = Template('{% debug %}')
        self.assertIn("清風", t1.render(c1))

    def test_extends_generic_template(self):
        """
        {% extends %} accepts django.template.backends.django.Template (#24338).
        """
        parent = engines['django'].from_string(
            '{% block content %}parent{% endblock %}')
        child = engines['django'].from_string(
            '{% extends parent %}{% block content %}child{% endblock %}')
        self.assertEqual(child.render({'parent': parent}), 'child')
