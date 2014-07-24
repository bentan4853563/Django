# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from datetime import datetime, timedelta
from unittest import TestCase, skipIf

try:
    import pytz
except ImportError:
    pytz = None

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.admin import widgets
from django.contrib.admin.tests import AdminSeleniumWebDriverTestCase
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import CharField, DateField
from django.test import TestCase as DjangoTestCase
from django.test import override_settings
from django.utils import six
from django.utils import translation

from . import models
from .widgetadmin import site as widget_admin_site


admin_static_prefix = lambda: {
    'ADMIN_STATIC_PREFIX': "%sadmin/" % settings.STATIC_URL,
}


class AdminFormfieldForDBFieldTests(TestCase):
    """
    Tests for correct behavior of ModelAdmin.formfield_for_dbfield
    """

    def assertFormfield(self, model, fieldname, widgetclass, **admin_overrides):
        """
        Helper to call formfield_for_dbfield for a given model and field name
        and verify that the returned formfield is appropriate.
        """
        # Override any settings on the model admin
        class MyModelAdmin(admin.ModelAdmin):
            pass
        for k in admin_overrides:
            setattr(MyModelAdmin, k, admin_overrides[k])

        # Construct the admin, and ask it for a formfield
        ma = MyModelAdmin(model, admin.site)
        ff = ma.formfield_for_dbfield(model._meta.get_field(fieldname), request=None)

        # "unwrap" the widget wrapper, if needed
        if isinstance(ff.widget, widgets.RelatedFieldWidgetWrapper):
            widget = ff.widget.widget
        else:
            widget = ff.widget

        # Check that we got a field of the right type
        self.assertTrue(
            isinstance(widget, widgetclass),
            "Wrong widget for %s.%s: expected %s, got %s" % (
                model.__class__.__name__,
                fieldname,
                widgetclass,
                type(widget),
            )
        )

        # Return the formfield so that other tests can continue
        return ff

    def test_DateField(self):
        self.assertFormfield(models.Event, 'start_date', widgets.AdminDateWidget)

    def test_DateTimeField(self):
        self.assertFormfield(models.Member, 'birthdate', widgets.AdminSplitDateTime)

    def test_TimeField(self):
        self.assertFormfield(models.Event, 'start_time', widgets.AdminTimeWidget)

    def test_TextField(self):
        self.assertFormfield(models.Event, 'description', widgets.AdminTextareaWidget)

    def test_URLField(self):
        self.assertFormfield(models.Event, 'link', widgets.AdminURLFieldWidget)

    def test_IntegerField(self):
        self.assertFormfield(models.Event, 'min_age', widgets.AdminIntegerFieldWidget)

    def test_CharField(self):
        self.assertFormfield(models.Member, 'name', widgets.AdminTextInputWidget)

    def test_EmailField(self):
        self.assertFormfield(models.Member, 'email', widgets.AdminEmailInputWidget)

    def test_FileField(self):
        self.assertFormfield(models.Album, 'cover_art', widgets.AdminFileWidget)

    def test_ForeignKey(self):
        self.assertFormfield(models.Event, 'main_band', forms.Select)

    def test_raw_id_ForeignKey(self):
        self.assertFormfield(models.Event, 'main_band', widgets.ForeignKeyRawIdWidget,
                             raw_id_fields=['main_band'])

    def test_radio_fields_ForeignKey(self):
        ff = self.assertFormfield(models.Event, 'main_band', widgets.AdminRadioSelect,
                                  radio_fields={'main_band': admin.VERTICAL})
        self.assertEqual(ff.empty_label, None)

    def test_many_to_many(self):
        self.assertFormfield(models.Band, 'members', forms.SelectMultiple)

    def test_raw_id_many_to_many(self):
        self.assertFormfield(models.Band, 'members', widgets.ManyToManyRawIdWidget,
                             raw_id_fields=['members'])

    def test_filtered_many_to_many(self):
        self.assertFormfield(models.Band, 'members', widgets.FilteredSelectMultiple,
                             filter_vertical=['members'])

    def test_formfield_overrides(self):
        self.assertFormfield(models.Event, 'start_date', forms.TextInput,
                             formfield_overrides={DateField: {'widget': forms.TextInput}})

    def test_formfield_overrides_widget_instances(self):
        """
        Test that widget instances in formfield_overrides are not shared between
        different fields. (#19423)
        """
        class BandAdmin(admin.ModelAdmin):
            formfield_overrides = {
                CharField: {'widget': forms.TextInput(attrs={'size': '10'})}
            }
        ma = BandAdmin(models.Band, admin.site)
        f1 = ma.formfield_for_dbfield(models.Band._meta.get_field('name'), request=None)
        f2 = ma.formfield_for_dbfield(models.Band._meta.get_field('style'), request=None)
        self.assertNotEqual(f1.widget, f2.widget)
        self.assertEqual(f1.widget.attrs['maxlength'], '100')
        self.assertEqual(f2.widget.attrs['maxlength'], '20')
        self.assertEqual(f2.widget.attrs['size'], '10')

    def test_field_with_choices(self):
        self.assertFormfield(models.Member, 'gender', forms.Select)

    def test_choices_with_radio_fields(self):
        self.assertFormfield(models.Member, 'gender', widgets.AdminRadioSelect,
                             radio_fields={'gender': admin.VERTICAL})

    def test_inheritance(self):
        self.assertFormfield(models.Album, 'backside_art', widgets.AdminFileWidget)

    def test_m2m_widgets(self):
        """m2m fields help text as it applies to admin app (#9321)."""
        class AdvisorAdmin(admin.ModelAdmin):
            filter_vertical = ['companies']

        self.assertFormfield(models.Advisor, 'companies', widgets.FilteredSelectMultiple,
                             filter_vertical=['companies'])
        ma = AdvisorAdmin(models.Advisor, admin.site)
        f = ma.formfield_for_dbfield(models.Advisor._meta.get_field('companies'), request=None)
        self.assertEqual(six.text_type(f.help_text), 'Hold down "Control", or "Command" on a Mac, to select more than one.')


@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
    ROOT_URLCONF='admin_widgets.urls')
class AdminFormfieldForDBFieldWithRequestTests(DjangoTestCase):
    fixtures = ["admin-widgets-users.xml"]

    def test_filter_choices_by_request_user(self):
        """
        Ensure the user can only see their own cars in the foreign key dropdown.
        """
        self.client.login(username="super", password="secret")
        response = self.client.get("/admin_widgets/cartire/add/")
        self.assertNotContains(response, "BMW M3")
        self.assertContains(response, "Volkswagon Passat")


@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
    ROOT_URLCONF='admin_widgets.urls')
class AdminForeignKeyWidgetChangeList(DjangoTestCase):
    fixtures = ["admin-widgets-users.xml"]

    def setUp(self):
        self.client.login(username="super", password="secret")

    def tearDown(self):
        self.client.logout()

    def test_changelist_ForeignKey(self):
        response = self.client.get('/admin_widgets/car/')
        self.assertContains(response, '/auth/user/add/')


@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
    ROOT_URLCONF='admin_widgets.urls')
class AdminForeignKeyRawIdWidget(DjangoTestCase):
    fixtures = ["admin-widgets-users.xml"]

    def setUp(self):
        self.client.login(username="super", password="secret")

    def tearDown(self):
        self.client.logout()

    def test_nonexistent_target_id(self):
        band = models.Band.objects.create(name='Bogey Blues')
        pk = band.pk
        band.delete()
        post_data = {
            "main_band": '%s' % pk,
        }
        # Try posting with a non-existent pk in a raw id field: this
        # should result in an error message, not a server exception.
        response = self.client.post('/admin_widgets/event/add/', post_data)
        self.assertContains(response,
            'Select a valid choice. That choice is not one of the available choices.')

    def test_invalid_target_id(self):

        for test_str in ('Iñtërnâtiônàlizætiøn', "1234'", -1234):
            # This should result in an error message, not a server exception.
            response = self.client.post('/admin_widgets/event/add/',
                {"main_band": test_str})

            self.assertContains(response,
                'Select a valid choice. That choice is not one of the available choices.')

    def test_url_params_from_lookup_dict_any_iterable(self):
        lookup1 = widgets.url_params_from_lookup_dict({'color__in': ('red', 'blue')})
        lookup2 = widgets.url_params_from_lookup_dict({'color__in': ['red', 'blue']})
        self.assertEqual(lookup1, {'color__in': 'red,blue'})
        self.assertEqual(lookup1, lookup2)

    def test_url_params_from_lookup_dict_callable(self):
        def my_callable():
            return 'works'
        lookup1 = widgets.url_params_from_lookup_dict({'myfield': my_callable})
        lookup2 = widgets.url_params_from_lookup_dict({'myfield': my_callable()})
        self.assertEqual(lookup1, lookup2)


class FilteredSelectMultipleWidgetTest(DjangoTestCase):
    def test_render(self):
        w = widgets.FilteredSelectMultiple('test', False)
        self.assertHTMLEqual(
            w.render('test', 'test'),
            '<select multiple="multiple" name="test" class="selectfilter">\n</select><script type="text/javascript">addEvent(window, "load", function(e) {SelectFilter.init("id_test", "test", 0, "%(ADMIN_STATIC_PREFIX)s"); });</script>\n' % admin_static_prefix()
        )

    def test_stacked_render(self):
        w = widgets.FilteredSelectMultiple('test', True)
        self.assertHTMLEqual(
            w.render('test', 'test'),
            '<select multiple="multiple" name="test" class="selectfilterstacked">\n</select><script type="text/javascript">addEvent(window, "load", function(e) {SelectFilter.init("id_test", "test", 1, "%(ADMIN_STATIC_PREFIX)s"); });</script>\n' % admin_static_prefix()
        )


class AdminDateWidgetTest(DjangoTestCase):
    def test_attrs(self):
        """
        Ensure that user-supplied attrs are used.
        Refs #12073.
        """
        w = widgets.AdminDateWidget()
        self.assertHTMLEqual(
            w.render('test', datetime(2007, 12, 1, 9, 30)),
            '<input value="2007-12-01" type="text" class="vDateField" name="test" size="10" />',
        )
        # pass attrs to widget
        w = widgets.AdminDateWidget(attrs={'size': 20, 'class': 'myDateField'})
        self.assertHTMLEqual(
            w.render('test', datetime(2007, 12, 1, 9, 30)),
            '<input value="2007-12-01" type="text" class="myDateField" name="test" size="20" />',
        )


class AdminTimeWidgetTest(DjangoTestCase):
    def test_attrs(self):
        """
        Ensure that user-supplied attrs are used.
        Refs #12073.
        """
        w = widgets.AdminTimeWidget()
        self.assertHTMLEqual(
            w.render('test', datetime(2007, 12, 1, 9, 30)),
            '<input value="09:30:00" type="text" class="vTimeField" name="test" size="8" />',
        )
        # pass attrs to widget
        w = widgets.AdminTimeWidget(attrs={'size': 20, 'class': 'myTimeField'})
        self.assertHTMLEqual(
            w.render('test', datetime(2007, 12, 1, 9, 30)),
            '<input value="09:30:00" type="text" class="myTimeField" name="test" size="20" />',
        )


class AdminSplitDateTimeWidgetTest(DjangoTestCase):
    def test_render(self):
        w = widgets.AdminSplitDateTime()
        self.assertHTMLEqual(
            w.render('test', datetime(2007, 12, 1, 9, 30)),
            '<p class="datetime">Date: <input value="2007-12-01" type="text" class="vDateField" name="test_0" size="10" /><br />Time: <input value="09:30:00" type="text" class="vTimeField" name="test_1" size="8" /></p>',
        )

    def test_localization(self):
        w = widgets.AdminSplitDateTime()

        with self.settings(USE_L10N=True), translation.override('de-at'):
            w.is_localized = True
            self.assertHTMLEqual(
                w.render('test', datetime(2007, 12, 1, 9, 30)),
                '<p class="datetime">Datum: <input value="01.12.2007" type="text" class="vDateField" name="test_0" size="10" /><br />Zeit: <input value="09:30:00" type="text" class="vTimeField" name="test_1" size="8" /></p>',
            )


class AdminURLWidgetTest(DjangoTestCase):
    def test_render(self):
        w = widgets.AdminURLFieldWidget()
        self.assertHTMLEqual(
            w.render('test', ''),
            '<input class="vURLField" name="test" type="url" />'
        )
        self.assertHTMLEqual(
            w.render('test', 'http://example.com'),
            '<p class="url">Currently:<a href="http://example.com">http://example.com</a><br />Change:<input class="vURLField" name="test" type="url" value="http://example.com" /></p>'
        )

    def test_render_idn(self):
        w = widgets.AdminURLFieldWidget()
        self.assertHTMLEqual(
            w.render('test', 'http://example-äüö.com'),
            '<p class="url">Currently: <a href="http://xn--example--7za4pnc.com">http://example-äüö.com</a><br />Change:<input class="vURLField" name="test" type="url" value="http://example-äüö.com" /></p>'
        )

    def test_render_quoting(self):
        # WARNING: Don't use assertHTMLEqual in that testcase!
        # assertHTMLEqual will get rid of some escapes which are tested here!
        w = widgets.AdminURLFieldWidget()
        self.assertEqual(
            w.render('test', 'http://example.com/<sometag>some text</sometag>'),
            '<p class="url">Currently: <a href="http://example.com/%3Csometag%3Esome%20text%3C/sometag%3E">http://example.com/&lt;sometag&gt;some text&lt;/sometag&gt;</a><br />Change: <input class="vURLField" name="test" type="url" value="http://example.com/&lt;sometag&gt;some text&lt;/sometag&gt;" /></p>'
        )
        self.assertEqual(
            w.render('test', 'http://example-äüö.com/<sometag>some text</sometag>'),
            '<p class="url">Currently: <a href="http://xn--example--7za4pnc.com/%3Csometag%3Esome%20text%3C/sometag%3E">http://example-äüö.com/&lt;sometag&gt;some text&lt;/sometag&gt;</a><br />Change: <input class="vURLField" name="test" type="url" value="http://example-äüö.com/&lt;sometag&gt;some text&lt;/sometag&gt;" /></p>'
        )
        self.assertEqual(
            w.render('test', 'http://www.example.com/%C3%A4"><script>alert("XSS!")</script>"'),
            '<p class="url">Currently: <a href="http://www.example.com/%C3%A4%22%3E%3Cscript%3Ealert(%22XSS!%22)%3C/script%3E%22">http://www.example.com/%C3%A4&quot;&gt;&lt;script&gt;alert(&quot;XSS!&quot;)&lt;/script&gt;&quot;</a><br />Change: <input class="vURLField" name="test" type="url" value="http://www.example.com/%C3%A4&quot;&gt;&lt;script&gt;alert(&quot;XSS!&quot;)&lt;/script&gt;&quot;" /></p>'
        )


class AdminFileWidgetTest(DjangoTestCase):
    def test_render(self):
        band = models.Band.objects.create(name='Linkin Park')
        album = band.album_set.create(
            name='Hybrid Theory', cover_art=r'albums\hybrid_theory.jpg'
        )

        w = widgets.AdminFileWidget()
        self.assertHTMLEqual(
            w.render('test', album.cover_art),
            '<p class="file-upload">Currently: <a href="%(STORAGE_URL)salbums/hybrid_theory.jpg">albums\hybrid_theory.jpg</a> <span class="clearable-file-input"><input type="checkbox" name="test-clear" id="test-clear_id" /> <label for="test-clear_id">Clear</label></span><br />Change: <input type="file" name="test" /></p>' % {
                'STORAGE_URL': default_storage.url('')
            },
        )

        self.assertHTMLEqual(
            w.render('test', SimpleUploadedFile('test', b'content')),
            '<input type="file" name="test" />',
        )


@override_settings(ROOT_URLCONF='admin_widgets.urls')
class ForeignKeyRawIdWidgetTest(DjangoTestCase):

    def test_render(self):
        band = models.Band.objects.create(name='Linkin Park')
        band.album_set.create(
            name='Hybrid Theory', cover_art=r'albums\hybrid_theory.jpg'
        )
        rel = models.Album._meta.get_field('band').rel

        w = widgets.ForeignKeyRawIdWidget(rel, widget_admin_site)
        self.assertHTMLEqual(
            w.render('test', band.pk, attrs={}), (
                '<input type="text" name="test" value="%(bandpk)s" class="vForeignKeyRawIdAdminField" />'
                '<a href="/admin_widgets/band/?_to_field=id" class="related-lookup" id="lookup_id_test" title="Lookup"></a>'
                '&nbsp;<strong>Linkin Park</strong>'
            ) % {'bandpk': band.pk}
        )

    def test_relations_to_non_primary_key(self):
        # Check that ForeignKeyRawIdWidget works with fields which aren't
        # related to the model's primary key.
        apple = models.Inventory.objects.create(barcode=86, name='Apple')
        models.Inventory.objects.create(barcode=22, name='Pear')
        core = models.Inventory.objects.create(
            barcode=87, name='Core', parent=apple
        )
        rel = models.Inventory._meta.get_field('parent').rel
        w = widgets.ForeignKeyRawIdWidget(rel, widget_admin_site)
        self.assertHTMLEqual(
            w.render('test', core.parent_id, attrs={}), (
                '<input type="text" name="test" value="86" class="vForeignKeyRawIdAdminField" />'
                '<a href="/admin_widgets/inventory/?_to_field=barcode" class="related-lookup" id="lookup_id_test" title="Lookup">'
                '</a>&nbsp;<strong>Apple</strong>'
            )
        )

    def test_fk_related_model_not_in_admin(self):
        # FK to a model not registered with admin site. Raw ID widget should
        # have no magnifying glass link. See #16542
        big_honeycomb = models.Honeycomb.objects.create(location='Old tree')
        big_honeycomb.bee_set.create()
        rel = models.Bee._meta.get_field('honeycomb').rel

        w = widgets.ForeignKeyRawIdWidget(rel, widget_admin_site)
        self.assertHTMLEqual(
            w.render('honeycomb_widget', big_honeycomb.pk, attrs={}),
            '<input type="text" name="honeycomb_widget" value="%(hcombpk)s" />&nbsp;<strong>Honeycomb object</strong>' % {'hcombpk': big_honeycomb.pk}
        )

    def test_fk_to_self_model_not_in_admin(self):
        # FK to self, not registered with admin site. Raw ID widget should have
        # no magnifying glass link. See #16542
        subject1 = models.Individual.objects.create(name='Subject #1')
        models.Individual.objects.create(name='Child', parent=subject1)
        rel = models.Individual._meta.get_field('parent').rel

        w = widgets.ForeignKeyRawIdWidget(rel, widget_admin_site)
        self.assertHTMLEqual(
            w.render('individual_widget', subject1.pk, attrs={}),
            '<input type="text" name="individual_widget" value="%(subj1pk)s" />&nbsp;<strong>Individual object</strong>' % {'subj1pk': subject1.pk}
        )

    def test_proper_manager_for_label_lookup(self):
        # see #9258
        rel = models.Inventory._meta.get_field('parent').rel
        w = widgets.ForeignKeyRawIdWidget(rel, widget_admin_site)

        hidden = models.Inventory.objects.create(
            barcode=93, name='Hidden', hidden=True
        )
        child_of_hidden = models.Inventory.objects.create(
            barcode=94, name='Child of hidden', parent=hidden
        )
        self.assertHTMLEqual(
            w.render('test', child_of_hidden.parent_id, attrs={}), (
                '<input type="text" name="test" value="93" class="vForeignKeyRawIdAdminField" />'
                '<a href="/admin_widgets/inventory/?_to_field=barcode" class="related-lookup" id="lookup_id_test" title="Lookup">'
                '</a>&nbsp;<strong>Hidden</strong>'
            )
        )


@override_settings(ROOT_URLCONF='admin_widgets.urls')
class ManyToManyRawIdWidgetTest(DjangoTestCase):

    def test_render(self):
        band = models.Band.objects.create(name='Linkin Park')

        m1 = models.Member.objects.create(name='Chester')
        m2 = models.Member.objects.create(name='Mike')
        band.members.add(m1, m2)
        rel = models.Band._meta.get_field('members').rel

        w = widgets.ManyToManyRawIdWidget(rel, widget_admin_site)
        self.assertHTMLEqual(
            w.render('test', [m1.pk, m2.pk], attrs={}), (
                '<input type="text" name="test" value="%(m1pk)s,%(m2pk)s" class="vManyToManyRawIdAdminField" />'
                '<a href="/admin_widgets/member/" class="related-lookup" id="lookup_id_test" title="Lookup"></a>'
            ) % dict(m1pk=m1.pk, m2pk=m2.pk)
        )

        self.assertHTMLEqual(
            w.render('test', [m1.pk]), (
                '<input type="text" name="test" value="%(m1pk)s" class="vManyToManyRawIdAdminField">'
                '<a href="/admin_widgets/member/" class="related-lookup" id="lookup_id_test" title="Lookup"></a>'
            ) % dict(m1pk=m1.pk)
        )

    def test_m2m_related_model_not_in_admin(self):
        # M2M relationship with model not registered with admin site. Raw ID
        # widget should have no magnifying glass link. See #16542
        consultor1 = models.Advisor.objects.create(name='Rockstar Techie')

        c1 = models.Company.objects.create(name='Doodle')
        c2 = models.Company.objects.create(name='Pear')
        consultor1.companies.add(c1, c2)
        rel = models.Advisor._meta.get_field('companies').rel

        w = widgets.ManyToManyRawIdWidget(rel, widget_admin_site)
        self.assertHTMLEqual(
            w.render('company_widget1', [c1.pk, c2.pk], attrs={}),
            '<input type="text" name="company_widget1" value="%(c1pk)s,%(c2pk)s" />' % {'c1pk': c1.pk, 'c2pk': c2.pk}
        )

        self.assertHTMLEqual(
            w.render('company_widget2', [c1.pk]),
            '<input type="text" name="company_widget2" value="%(c1pk)s" />' % {'c1pk': c1.pk}
        )


class RelatedFieldWidgetWrapperTests(DjangoTestCase):
    def test_no_can_add_related(self):
        rel = models.Individual._meta.get_field('parent').rel
        w = widgets.AdminRadioSelect()
        # Used to fail with a name error.
        w = widgets.RelatedFieldWidgetWrapper(w, rel, widget_admin_site)
        self.assertFalse(w.can_add_related)


@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
    ROOT_URLCONF='admin_widgets.urls')
class DateTimePickerSeleniumFirefoxTests(AdminSeleniumWebDriverTestCase):

    available_apps = ['admin_widgets'] + AdminSeleniumWebDriverTestCase.available_apps
    fixtures = ['admin-widgets-users.xml']
    webdriver_class = 'selenium.webdriver.firefox.webdriver.WebDriver'

    def test_show_hide_date_time_picker_widgets(self):
        """
        Ensure that pressing the ESC key closes the date and time picker
        widgets.
        Refs #17064.
        """
        from selenium.webdriver.common.keys import Keys

        self.admin_login(username='super', password='secret', login_url='/')
        # Open a page that has a date and time picker widgets
        self.selenium.get('%s%s' % (self.live_server_url,
            '/admin_widgets/member/add/'))

        # First, with the date picker widget ---------------------------------
        # Check that the date picker is hidden
        self.assertEqual(
            self.get_css_value('#calendarbox0', 'display'), 'none')
        # Click the calendar icon
        self.selenium.find_element_by_id('calendarlink0').click()
        # Check that the date picker is visible
        self.assertEqual(
            self.get_css_value('#calendarbox0', 'display'), 'block')
        # Press the ESC key
        self.selenium.find_element_by_tag_name('body').send_keys([Keys.ESCAPE])
        # Check that the date picker is hidden again
        self.assertEqual(
            self.get_css_value('#calendarbox0', 'display'), 'none')

        # Then, with the time picker widget ----------------------------------
        # Check that the time picker is hidden
        self.assertEqual(
            self.get_css_value('#clockbox0', 'display'), 'none')
        # Click the time icon
        self.selenium.find_element_by_id('clocklink0').click()
        # Check that the time picker is visible
        self.assertEqual(
            self.get_css_value('#clockbox0', 'display'), 'block')
        # Press the ESC key
        self.selenium.find_element_by_tag_name('body').send_keys([Keys.ESCAPE])
        # Check that the time picker is hidden again
        self.assertEqual(
            self.get_css_value('#clockbox0', 'display'), 'none')

    def test_calendar_nonday_class(self):
        """
        Ensure cells that are not days of the month have the `nonday` CSS class.
        Refs #4574.
        """
        self.admin_login(username='super', password='secret', login_url='/')
        # Open a page that has a date and time picker widgets
        self.selenium.get('%s%s' % (self.live_server_url,
            '/admin_widgets/member/add/'))

        # fill in the birth date.
        self.selenium.find_element_by_id('id_birthdate_0').send_keys('2013-06-01')

        # Click the calendar icon
        self.selenium.find_element_by_id('calendarlink0').click()

        # get all the tds within the calendar
        calendar0 = self.selenium.find_element_by_id('calendarin0')
        tds = calendar0.find_elements_by_tag_name('td')

        # make sure the first and last 6 cells have class nonday
        for td in tds[:6] + tds[-6:]:
            self.assertEqual(td.get_attribute('class'), 'nonday')

    def test_calendar_selected_class(self):
        """
        Ensure cell for the day in the input has the `selected` CSS class.
        Refs #4574.
        """
        self.admin_login(username='super', password='secret', login_url='/')
        # Open a page that has a date and time picker widgets
        self.selenium.get('%s%s' % (self.live_server_url,
            '/admin_widgets/member/add/'))

        # fill in the birth date.
        self.selenium.find_element_by_id('id_birthdate_0').send_keys('2013-06-01')

        # Click the calendar icon
        self.selenium.find_element_by_id('calendarlink0').click()

        # get all the tds within the calendar
        calendar0 = self.selenium.find_element_by_id('calendarin0')
        tds = calendar0.find_elements_by_tag_name('td')

        # verify the selected cell
        selected = tds[6]
        self.assertEqual(selected.get_attribute('class'), 'selected')

        self.assertEqual(selected.text, '1')

    def test_calendar_no_selected_class(self):
        """
        Ensure no cells are given the selected class when the field is empty.
        Refs #4574.
        """
        self.admin_login(username='super', password='secret', login_url='/')
        # Open a page that has a date and time picker widgets
        self.selenium.get('%s%s' % (self.live_server_url,
            '/admin_widgets/member/add/'))

        # Click the calendar icon
        self.selenium.find_element_by_id('calendarlink0').click()

        # get all the tds within the calendar
        calendar0 = self.selenium.find_element_by_id('calendarin0')
        tds = calendar0.find_elements_by_tag_name('td')

        # verify there are no cells with the selected class
        selected = [td for td in tds if td.get_attribute('class') == 'selected']

        self.assertEqual(len(selected), 0)


class DateTimePickerSeleniumChromeTests(DateTimePickerSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.chrome.webdriver.WebDriver'


class DateTimePickerSeleniumIETests(DateTimePickerSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.ie.webdriver.WebDriver'


@skipIf(pytz is None, "this test requires pytz")
@override_settings(TIME_ZONE='Asia/Singapore')
@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
    ROOT_URLCONF='admin_widgets.urls')
class DateTimePickerShortcutsSeleniumFirefoxTests(AdminSeleniumWebDriverTestCase):
    available_apps = ['admin_widgets'] + AdminSeleniumWebDriverTestCase.available_apps
    fixtures = ['admin-widgets-users.xml']
    webdriver_class = 'selenium.webdriver.firefox.webdriver.WebDriver'

    def test_date_time_picker_shortcuts(self):
        """
        Ensure that date/time/datetime picker shortcuts work in the current time zone.
        Refs #20663.

        This test case is fairly tricky, it relies on selenium still running the browser
        in the default time zone "America/Chicago" despite `override_settings` changing
        the time zone to "Asia/Singapore".
        """
        self.admin_login(username='super', password='secret', login_url='/')

        error_margin = timedelta(seconds=10)

        # If we are neighbouring a DST, we add an hour of error margin.
        tz = pytz.timezone('America/Chicago')
        utc_now = datetime.now(pytz.utc)
        tz_yesterday = (utc_now - timedelta(days=1)).astimezone(tz).tzname()
        tz_tomorrow = (utc_now + timedelta(days=1)).astimezone(tz).tzname()
        if tz_yesterday != tz_tomorrow:
            error_margin += timedelta(hours=1)

        now = datetime.now()

        self.selenium.get('%s%s' % (self.live_server_url,
            '/admin_widgets/member/add/'))

        self.selenium.find_element_by_id('id_name').send_keys('test')

        # Click on the "today" and "now" shortcuts.
        shortcuts = self.selenium.find_elements_by_css_selector(
            '.field-birthdate .datetimeshortcuts')

        for shortcut in shortcuts:
            shortcut.find_element_by_tag_name('a').click()

        # Check that there is a time zone mismatch warning.
        # Warning: This would effectively fail if the TIME_ZONE defined in the
        # settings has the same UTC offset as "Asia/Singapore" because the
        # mismatch warning would be rightfully missing from the page.
        self.selenium.find_elements_by_css_selector(
            '.field-birthdate .timezonewarning')

        # Submit the form.
        self.selenium.find_element_by_tag_name('form').submit()
        self.wait_page_loaded()

        # Make sure that "now" in javascript is within 10 seconds
        # from "now" on the server side.
        member = models.Member.objects.get(name='test')
        self.assertGreater(member.birthdate, now - error_margin)
        self.assertLess(member.birthdate, now + error_margin)


class DateTimePickerShortcutsSeleniumChromeTests(DateTimePickerShortcutsSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.chrome.webdriver.WebDriver'


class DateTimePickerShortcutsSeleniumIETests(DateTimePickerShortcutsSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.ie.webdriver.WebDriver'


@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
    ROOT_URLCONF='admin_widgets.urls')
class HorizontalVerticalFilterSeleniumFirefoxTests(AdminSeleniumWebDriverTestCase):

    available_apps = ['admin_widgets'] + AdminSeleniumWebDriverTestCase.available_apps
    fixtures = ['admin-widgets-users.xml']
    webdriver_class = 'selenium.webdriver.firefox.webdriver.WebDriver'

    def setUp(self):
        self.lisa = models.Student.objects.create(name='Lisa')
        self.john = models.Student.objects.create(name='John')
        self.bob = models.Student.objects.create(name='Bob')
        self.peter = models.Student.objects.create(name='Peter')
        self.jenny = models.Student.objects.create(name='Jenny')
        self.jason = models.Student.objects.create(name='Jason')
        self.cliff = models.Student.objects.create(name='Cliff')
        self.arthur = models.Student.objects.create(name='Arthur')
        self.school = models.School.objects.create(name='School of Awesome')
        super(HorizontalVerticalFilterSeleniumFirefoxTests, self).setUp()

    def assertActiveButtons(self, mode, field_name, choose, remove,
            choose_all=None, remove_all=None):
        choose_link = '#id_%s_add_link' % field_name
        choose_all_link = '#id_%s_add_all_link' % field_name
        remove_link = '#id_%s_remove_link' % field_name
        remove_all_link = '#id_%s_remove_all_link' % field_name
        self.assertEqual(self.has_css_class(choose_link, 'active'), choose)
        self.assertEqual(self.has_css_class(remove_link, 'active'), remove)
        if mode == 'horizontal':
            self.assertEqual(self.has_css_class(choose_all_link, 'active'), choose_all)
            self.assertEqual(self.has_css_class(remove_all_link, 'active'), remove_all)

    def execute_basic_operations(self, mode, field_name):
        from_box = '#id_%s_from' % field_name
        to_box = '#id_%s_to' % field_name
        choose_link = 'id_%s_add_link' % field_name
        choose_all_link = 'id_%s_add_all_link' % field_name
        remove_link = 'id_%s_remove_link' % field_name
        remove_all_link = 'id_%s_remove_all_link' % field_name

        # Initial positions ---------------------------------------------------
        self.assertSelectOptions(from_box,
                        [str(self.arthur.id), str(self.bob.id),
                         str(self.cliff.id), str(self.jason.id),
                         str(self.jenny.id), str(self.john.id)])
        self.assertSelectOptions(to_box,
                        [str(self.lisa.id), str(self.peter.id)])
        self.assertActiveButtons(mode, field_name, False, False, True, True)

        # Click 'Choose all' --------------------------------------------------
        if mode == 'horizontal':
            self.selenium.find_element_by_id(choose_all_link).click()
        elif mode == 'vertical':
            # There 's no 'Choose all' button in vertical mode, so individually
            # select all options and click 'Choose'.
            for option in self.selenium.find_elements_by_css_selector(from_box + ' > option'):
                option.click()
            self.selenium.find_element_by_id(choose_link).click()
        self.assertSelectOptions(from_box, [])
        self.assertSelectOptions(to_box,
                        [str(self.lisa.id), str(self.peter.id),
                         str(self.arthur.id), str(self.bob.id),
                         str(self.cliff.id), str(self.jason.id),
                         str(self.jenny.id), str(self.john.id)])
        self.assertActiveButtons(mode, field_name, False, False, False, True)

        # Click 'Remove all' --------------------------------------------------
        if mode == 'horizontal':
            self.selenium.find_element_by_id(remove_all_link).click()
        elif mode == 'vertical':
            # There 's no 'Remove all' button in vertical mode, so individually
            # select all options and click 'Remove'.
            for option in self.selenium.find_elements_by_css_selector(to_box + ' > option'):
                option.click()
            self.selenium.find_element_by_id(remove_link).click()
        self.assertSelectOptions(from_box,
                        [str(self.lisa.id), str(self.peter.id),
                         str(self.arthur.id), str(self.bob.id),
                         str(self.cliff.id), str(self.jason.id),
                         str(self.jenny.id), str(self.john.id)])
        self.assertSelectOptions(to_box, [])
        self.assertActiveButtons(mode, field_name, False, False, True, False)

        # Choose some options ------------------------------------------------
        from_lisa_select_option = self.get_select_option(from_box, str(self.lisa.id))

        # Check the title attribute is there for tool tips: ticket #20821
        self.assertEqual(from_lisa_select_option.get_attribute('title'), from_lisa_select_option.get_attribute('text'))

        from_lisa_select_option.click()
        self.get_select_option(from_box, str(self.jason.id)).click()
        self.get_select_option(from_box, str(self.bob.id)).click()
        self.get_select_option(from_box, str(self.john.id)).click()
        self.assertActiveButtons(mode, field_name, True, False, True, False)
        self.selenium.find_element_by_id(choose_link).click()
        self.assertActiveButtons(mode, field_name, False, False, True, True)

        self.assertSelectOptions(from_box,
                        [str(self.peter.id), str(self.arthur.id),
                         str(self.cliff.id), str(self.jenny.id)])
        self.assertSelectOptions(to_box,
                        [str(self.lisa.id), str(self.bob.id),
                         str(self.jason.id), str(self.john.id)])

        # Check the tooltip is still there after moving: ticket #20821
        to_lisa_select_option = self.get_select_option(to_box, str(self.lisa.id))
        self.assertEqual(to_lisa_select_option.get_attribute('title'), to_lisa_select_option.get_attribute('text'))

        # Remove some options -------------------------------------------------
        self.get_select_option(to_box, str(self.lisa.id)).click()
        self.get_select_option(to_box, str(self.bob.id)).click()
        self.assertActiveButtons(mode, field_name, False, True, True, True)
        self.selenium.find_element_by_id(remove_link).click()
        self.assertActiveButtons(mode, field_name, False, False, True, True)

        self.assertSelectOptions(from_box,
                        [str(self.peter.id), str(self.arthur.id),
                         str(self.cliff.id), str(self.jenny.id),
                         str(self.lisa.id), str(self.bob.id)])
        self.assertSelectOptions(to_box,
                        [str(self.jason.id), str(self.john.id)])

        # Choose some more options --------------------------------------------
        self.get_select_option(from_box, str(self.arthur.id)).click()
        self.get_select_option(from_box, str(self.cliff.id)).click()
        self.selenium.find_element_by_id(choose_link).click()

        self.assertSelectOptions(from_box,
                        [str(self.peter.id), str(self.jenny.id),
                         str(self.lisa.id), str(self.bob.id)])
        self.assertSelectOptions(to_box,
                        [str(self.jason.id), str(self.john.id),
                         str(self.arthur.id), str(self.cliff.id)])

    def test_basic(self):
        self.school.students = [self.lisa, self.peter]
        self.school.alumni = [self.lisa, self.peter]
        self.school.save()

        self.admin_login(username='super', password='secret', login_url='/')
        self.selenium.get(
            '%s%s' % (self.live_server_url, '/admin_widgets/school/%s/' % self.school.id))

        self.wait_page_loaded()
        self.execute_basic_operations('vertical', 'students')
        self.execute_basic_operations('horizontal', 'alumni')

        # Save and check that everything is properly stored in the database ---
        self.selenium.find_element_by_xpath('//input[@value="Save"]').click()
        self.wait_page_loaded()
        self.school = models.School.objects.get(id=self.school.id)  # Reload from database
        self.assertEqual(list(self.school.students.all()),
                         [self.arthur, self.cliff, self.jason, self.john])
        self.assertEqual(list(self.school.alumni.all()),
                         [self.arthur, self.cliff, self.jason, self.john])

    def test_filter(self):
        """
        Ensure that typing in the search box filters out options displayed in
        the 'from' box.
        """
        from selenium.webdriver.common.keys import Keys

        self.school.students = [self.lisa, self.peter]
        self.school.alumni = [self.lisa, self.peter]
        self.school.save()

        self.admin_login(username='super', password='secret', login_url='/')
        self.selenium.get(
            '%s%s' % (self.live_server_url, '/admin_widgets/school/%s/' % self.school.id))

        for field_name in ['students', 'alumni']:
            from_box = '#id_%s_from' % field_name
            to_box = '#id_%s_to' % field_name
            choose_link = '#id_%s_add_link' % field_name
            remove_link = '#id_%s_remove_link' % field_name
            input = self.selenium.find_element_by_css_selector('#id_%s_input' % field_name)

            # Initial values
            self.assertSelectOptions(from_box,
                        [str(self.arthur.id), str(self.bob.id),
                         str(self.cliff.id), str(self.jason.id),
                         str(self.jenny.id), str(self.john.id)])

            # Typing in some characters filters out non-matching options
            input.send_keys('a')
            self.assertSelectOptions(from_box, [str(self.arthur.id), str(self.jason.id)])
            input.send_keys('R')
            self.assertSelectOptions(from_box, [str(self.arthur.id)])

            # Clearing the text box makes the other options reappear
            input.send_keys([Keys.BACK_SPACE])
            self.assertSelectOptions(from_box, [str(self.arthur.id), str(self.jason.id)])
            input.send_keys([Keys.BACK_SPACE])
            self.assertSelectOptions(from_box,
                        [str(self.arthur.id), str(self.bob.id),
                         str(self.cliff.id), str(self.jason.id),
                         str(self.jenny.id), str(self.john.id)])

            # -----------------------------------------------------------------
            # Check that chosing a filtered option sends it properly to the
            # 'to' box.
            input.send_keys('a')
            self.assertSelectOptions(from_box, [str(self.arthur.id), str(self.jason.id)])
            self.get_select_option(from_box, str(self.jason.id)).click()
            self.selenium.find_element_by_css_selector(choose_link).click()
            self.assertSelectOptions(from_box, [str(self.arthur.id)])
            self.assertSelectOptions(to_box,
                        [str(self.lisa.id), str(self.peter.id),
                         str(self.jason.id)])

            self.get_select_option(to_box, str(self.lisa.id)).click()
            self.selenium.find_element_by_css_selector(remove_link).click()
            self.assertSelectOptions(from_box,
                        [str(self.arthur.id), str(self.lisa.id)])
            self.assertSelectOptions(to_box,
                        [str(self.peter.id), str(self.jason.id)])

            input.send_keys([Keys.BACK_SPACE])  # Clear text box
            self.assertSelectOptions(from_box,
                        [str(self.arthur.id), str(self.bob.id),
                         str(self.cliff.id), str(self.jenny.id),
                         str(self.john.id), str(self.lisa.id)])
            self.assertSelectOptions(to_box,
                        [str(self.peter.id), str(self.jason.id)])

        # Save and check that everything is properly stored in the database ---
        self.selenium.find_element_by_xpath('//input[@value="Save"]').click()
        self.wait_page_loaded()
        self.school = models.School.objects.get(id=self.school.id)  # Reload from database
        self.assertEqual(list(self.school.students.all()),
                         [self.jason, self.peter])
        self.assertEqual(list(self.school.alumni.all()),
                         [self.jason, self.peter])


class HorizontalVerticalFilterSeleniumChromeTests(HorizontalVerticalFilterSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.chrome.webdriver.WebDriver'


class HorizontalVerticalFilterSeleniumIETests(HorizontalVerticalFilterSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.ie.webdriver.WebDriver'


@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
    ROOT_URLCONF='admin_widgets.urls')
class AdminRawIdWidgetSeleniumFirefoxTests(AdminSeleniumWebDriverTestCase):
    available_apps = ['admin_widgets'] + AdminSeleniumWebDriverTestCase.available_apps
    fixtures = ['admin-widgets-users.xml']
    webdriver_class = 'selenium.webdriver.firefox.webdriver.WebDriver'

    def setUp(self):
        models.Band.objects.create(id=42, name='Bogey Blues')
        models.Band.objects.create(id=98, name='Green Potatoes')
        super(AdminRawIdWidgetSeleniumFirefoxTests, self).setUp()

    def test_ForeignKey(self):
        self.admin_login(username='super', password='secret', login_url='/')
        self.selenium.get(
            '%s%s' % (self.live_server_url, '/admin_widgets/event/add/'))
        main_window = self.selenium.current_window_handle

        # No value has been selected yet
        self.assertEqual(
            self.selenium.find_element_by_id('id_main_band').get_attribute('value'),
            '')

        # Open the popup window and click on a band
        self.selenium.find_element_by_id('lookup_id_main_band').click()
        self.selenium.switch_to.window('id_main_band')
        self.wait_page_loaded()
        link = self.selenium.find_element_by_link_text('Bogey Blues')
        self.assertTrue('/band/42/' in link.get_attribute('href'))
        link.click()

        # The field now contains the selected band's id
        self.selenium.switch_to.window(main_window)
        self.wait_for_value('#id_main_band', '42')

        # Reopen the popup window and click on another band
        self.selenium.find_element_by_id('lookup_id_main_band').click()
        self.selenium.switch_to.window('id_main_band')
        self.wait_page_loaded()
        link = self.selenium.find_element_by_link_text('Green Potatoes')
        self.assertTrue('/band/98/' in link.get_attribute('href'))
        link.click()

        # The field now contains the other selected band's id
        self.selenium.switch_to.window(main_window)
        self.wait_for_value('#id_main_band', '98')

    def test_many_to_many(self):
        self.admin_login(username='super', password='secret', login_url='/')
        self.selenium.get(
            '%s%s' % (self.live_server_url, '/admin_widgets/event/add/'))
        main_window = self.selenium.current_window_handle

        # No value has been selected yet
        self.assertEqual(
            self.selenium.find_element_by_id('id_supporting_bands').get_attribute('value'),
            '')

        # Open the popup window and click on a band
        self.selenium.find_element_by_id('lookup_id_supporting_bands').click()
        self.selenium.switch_to.window('id_supporting_bands')
        self.wait_page_loaded()
        link = self.selenium.find_element_by_link_text('Bogey Blues')
        self.assertTrue('/band/42/' in link.get_attribute('href'))
        link.click()

        # The field now contains the selected band's id
        self.selenium.switch_to.window(main_window)
        self.wait_for_value('#id_supporting_bands', '42')

        # Reopen the popup window and click on another band
        self.selenium.find_element_by_id('lookup_id_supporting_bands').click()
        self.selenium.switch_to.window('id_supporting_bands')
        self.wait_page_loaded()
        link = self.selenium.find_element_by_link_text('Green Potatoes')
        self.assertTrue('/band/98/' in link.get_attribute('href'))
        link.click()

        # The field now contains the two selected bands' ids
        self.selenium.switch_to.window(main_window)
        self.wait_for_value('#id_supporting_bands', '42,98')


class AdminRawIdWidgetSeleniumChromeTests(AdminRawIdWidgetSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.chrome.webdriver.WebDriver'


class AdminRawIdWidgetSeleniumIETests(AdminRawIdWidgetSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.ie.webdriver.WebDriver'


@override_settings(PASSWORD_HASHERS=('django.contrib.auth.hashers.SHA1PasswordHasher',),
                   ROOT_URLCONF='admin_widgets.urls')
class RelatedFieldWidgetSeleniumFirefoxTests(AdminSeleniumWebDriverTestCase):
    available_apps = ['admin_widgets'] + AdminSeleniumWebDriverTestCase.available_apps
    fixtures = ['admin-widgets-users.xml']
    webdriver_class = 'selenium.webdriver.firefox.webdriver.WebDriver'

    def test_ForeignKey_using_to_field(self):
        self.admin_login(username='super', password='secret', login_url='/')
        self.selenium.get('%s%s' % (
            self.live_server_url,
            '/admin_widgets/profile/add/'))

        main_window = self.selenium.current_window_handle
        # Click the Add User button to add new
        self.selenium.find_element_by_id('add_id_user').click()
        self.selenium.switch_to.window('id_user')
        self.wait_page_loaded()
        password_field = self.selenium.find_element_by_id('id_password')
        password_field.send_keys('password')

        username_field = self.selenium.find_element_by_id('id_username')
        username_value = 'newuser'
        username_field.send_keys(username_value)

        save_button_css_selector = '.submit-row > input[type=submit]'
        self.selenium.find_element_by_css_selector(save_button_css_selector).click()
        self.selenium.switch_to.window(main_window)
        # The field now contains the new user
        self.wait_for('#id_user option[value="newuser"]')

        # Go ahead and submit the form to make sure it works
        self.selenium.find_element_by_css_selector(save_button_css_selector).click()
        self.wait_for_text('li.success', 'The profile "newuser" was added successfully.')
        profiles = models.Profile.objects.all()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].user.username, username_value)


class RelatedFieldWidgetSeleniumChromeTests(RelatedFieldWidgetSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.chrome.webdriver.WebDriver'


class RelatedFieldWidgetSeleniumIETests(RelatedFieldWidgetSeleniumFirefoxTests):
    webdriver_class = 'selenium.webdriver.ie.webdriver.WebDriver'
