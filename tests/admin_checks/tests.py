from __future__ import unicode_literals

import warnings

from django import forms
from django.contrib import admin
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from .models import Song, Book, Album, TwoAlbumFKAndAnE, City, State


class SongForm(forms.ModelForm):
    pass


class ValidFields(admin.ModelAdmin):
    form = SongForm
    fields = ['title']


class ValidFormFieldsets(admin.ModelAdmin):
    def get_form(self, request, obj=None, **kwargs):
        class ExtraFieldForm(SongForm):
            name = forms.CharField(max_length=50)
        return ExtraFieldForm

    fieldsets = (
        (None, {
            'fields': ('name',),
        }),
    )


class SystemChecksTestCase(TestCase):

    def test_checks_are_performed(self):
        class MyAdmin(admin.ModelAdmin):
            @classmethod
            def check(self, model, **kwargs):
                return ['error!']

        admin.site.register(Song, MyAdmin)
        try:
            errors = checks.run_checks()
            expected = ['error!']
            self.assertEqual(errors, expected)
        finally:
            admin.site.unregister(Song)

    def test_readonly_and_editable(self):
        class SongAdmin(admin.ModelAdmin):
            readonly_fields = ["original_release"]
            list_display = ["pk", "original_release"]
            list_editable = ["original_release"]
            fieldsets = [
                (None, {
                    "fields": ["title", "original_release"],
                }),
            ]

        errors = SongAdmin.check(model=Song)
        expected = [
            checks.Error(
                ("The value of 'list_editable[0]' refers to 'original_release', "
                 "which is not editable through the admin."),
                hint=None,
                obj=SongAdmin,
                id='admin.E125',
            )
        ]
        self.assertEqual(errors, expected)

    def test_editable(self):
        class SongAdmin(admin.ModelAdmin):
            list_display = ["pk", "title"]
            list_editable = ["title"]
            fieldsets = [
                (None, {
                    "fields": ["title", "original_release"],
                }),
            ]

        errors = SongAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_custom_modelforms_with_fields_fieldsets(self):
        """
        # Regression test for #8027: custom ModelForms with fields/fieldsets
        """

        errors = ValidFields.check(model=Song)
        self.assertEqual(errors, [])

    def test_custom_get_form_with_fieldsets(self):
        """
        Ensure that the fieldsets checks are skipped when the ModelAdmin.get_form() method
        is overridden.
        Refs #19445.
        """

        errors = ValidFormFieldsets.check(model=Song)
        self.assertEqual(errors, [])

    def test_exclude_values(self):
        """
        Tests for basic system checks of 'exclude' option values (#12689)
        """

        class ExcludedFields1(admin.ModelAdmin):
            exclude = 'foo'

        errors = ExcludedFields1.check(model=Book)
        expected = [
            checks.Error(
                "The value of 'exclude' must be a list or tuple.",
                hint=None,
                obj=ExcludedFields1,
                id='admin.E014',
            )
        ]
        self.assertEqual(errors, expected)

    def test_exclude_duplicate_values(self):
        class ExcludedFields2(admin.ModelAdmin):
            exclude = ('name', 'name')

        errors = ExcludedFields2.check(model=Book)
        expected = [
            checks.Error(
                "The value of 'exclude' contains duplicate field(s).",
                hint=None,
                obj=ExcludedFields2,
                id='admin.E015',
            )
        ]
        self.assertEqual(errors, expected)

    def test_exclude_in_inline(self):
        class ExcludedFieldsInline(admin.TabularInline):
            model = Song
            exclude = 'foo'

        class ExcludedFieldsAlbumAdmin(admin.ModelAdmin):
            model = Album
            inlines = [ExcludedFieldsInline]

        errors = ExcludedFieldsAlbumAdmin.check(model=Album)
        expected = [
            checks.Error(
                "The value of 'exclude' must be a list or tuple.",
                hint=None,
                obj=ExcludedFieldsInline,
                id='admin.E014',
            )
        ]
        self.assertEqual(errors, expected)

    def test_exclude_inline_model_admin(self):
        """
        Regression test for #9932 - exclude in InlineModelAdmin should not
        contain the ForeignKey field used in ModelAdmin.model
        """

        class SongInline(admin.StackedInline):
            model = Song
            exclude = ['album']

        class AlbumAdmin(admin.ModelAdmin):
            model = Album
            inlines = [SongInline]

        errors = AlbumAdmin.check(model=Album)
        expected = [
            checks.Error(
                ("Cannot exclude the field 'album', because it is the foreign key "
                 "to the parent model 'admin_checks.Album'."),
                hint=None,
                obj=SongInline,
                id='admin.E201',
            )
        ]
        self.assertEqual(errors, expected)

    def test_app_label_in_admin_checks(self):
        """
        Regression test for #15669 - Include app label in admin system check messages
        """

        class RawIdNonexistingAdmin(admin.ModelAdmin):
            raw_id_fields = ('nonexisting',)

        errors = RawIdNonexistingAdmin.check(model=Album)
        expected = [
            checks.Error(
                ("The value of 'raw_id_fields[0]' refers to 'nonexisting', which is "
                 "not an attribute of 'admin_checks.Album'."),
                hint=None,
                obj=RawIdNonexistingAdmin,
                id='admin.E002',
            )
        ]
        self.assertEqual(errors, expected)

    def test_fk_exclusion(self):
        """
        Regression test for #11709 - when testing for fk excluding (when exclude is
        given) make sure fk_name is honored or things blow up when there is more
        than one fk to the parent model.
        """

        class TwoAlbumFKAndAnEInline(admin.TabularInline):
            model = TwoAlbumFKAndAnE
            exclude = ("e",)
            fk_name = "album1"

        class MyAdmin(admin.ModelAdmin):
            inlines = [TwoAlbumFKAndAnEInline]

        errors = MyAdmin.check(model=Album)
        self.assertEqual(errors, [])

    def test_inline_self_check(self):
        class TwoAlbumFKAndAnEInline(admin.TabularInline):
            model = TwoAlbumFKAndAnE

        class MyAdmin(admin.ModelAdmin):
            inlines = [TwoAlbumFKAndAnEInline]

        errors = MyAdmin.check(model=Album)
        expected = [
            checks.Error(
                "'admin_checks.TwoAlbumFKAndAnE' has more than one ForeignKey to 'admin_checks.Album'.",
                hint=None,
                obj=TwoAlbumFKAndAnEInline,
                id='admin.E202',
            )
        ]
        self.assertEqual(errors, expected)

    def test_inline_with_specified(self):
        class TwoAlbumFKAndAnEInline(admin.TabularInline):
            model = TwoAlbumFKAndAnE
            fk_name = "album1"

        class MyAdmin(admin.ModelAdmin):
            inlines = [TwoAlbumFKAndAnEInline]

        errors = MyAdmin.check(model=Album)
        self.assertEqual(errors, [])

    def test_readonly(self):
        class SongAdmin(admin.ModelAdmin):
            readonly_fields = ("title",)

        errors = SongAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_readonly_on_method(self):
        def my_function(obj):
            pass

        class SongAdmin(admin.ModelAdmin):
            readonly_fields = (my_function,)

        errors = SongAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_readonly_on_modeladmin(self):
        class SongAdmin(admin.ModelAdmin):
            readonly_fields = ("readonly_method_on_modeladmin",)

            def readonly_method_on_modeladmin(self, obj):
                pass

        errors = SongAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_readonly_method_on_model(self):
        class SongAdmin(admin.ModelAdmin):
            readonly_fields = ("readonly_method_on_model",)

        errors = SongAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_nonexistant_field(self):
        class SongAdmin(admin.ModelAdmin):
            readonly_fields = ("title", "nonexistant")

        errors = SongAdmin.check(model=Song)
        expected = [
            checks.Error(
                ("The value of 'readonly_fields[1]' is not a callable, an attribute "
                 "of 'SongAdmin', or an attribute of 'admin_checks.Song'."),
                hint=None,
                obj=SongAdmin,
                id='admin.E035',
            )
        ]
        self.assertEqual(errors, expected)

    def test_nonexistant_field_on_inline(self):
        class CityInline(admin.TabularInline):
            model = City
            readonly_fields = ['i_dont_exist']  # Missing attribute

        errors = CityInline.check(State)
        expected = [
            checks.Error(
                ("The value of 'readonly_fields[0]' is not a callable, an attribute "
                 "of 'CityInline', or an attribute of 'admin_checks.City'."),
                hint=None,
                obj=CityInline,
                id='admin.E035',
            )
        ]
        self.assertEqual(errors, expected)

    def test_extra(self):
        class SongAdmin(admin.ModelAdmin):
            def awesome_song(self, instance):
                if instance.title == "Born to Run":
                    return "Best Ever!"
                return "Status unknown."

        errors = SongAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_readonly_lambda(self):
        class SongAdmin(admin.ModelAdmin):
            readonly_fields = (lambda obj: "test",)

        errors = SongAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_graceful_m2m_fail(self):
        """
        Regression test for #12203/#12237 - Fail more gracefully when a M2M field that
        specifies the 'through' option is included in the 'fields' or the 'fieldsets'
        ModelAdmin options.
        """

        class BookAdmin(admin.ModelAdmin):
            fields = ['authors']

        errors = BookAdmin.check(model=Book)
        expected = [
            checks.Error(
                ("The value of 'fields' cannot include the ManyToManyField 'authors', "
                 "because that field manually specifies a relationship model."),
                hint=None,
                obj=BookAdmin,
                id='admin.E013',
            )
        ]
        self.assertEqual(errors, expected)

    def test_cannot_include_through(self):
        class FieldsetBookAdmin(admin.ModelAdmin):
            fieldsets = (
                ('Header 1', {'fields': ('name',)}),
                ('Header 2', {'fields': ('authors',)}),
            )

        errors = FieldsetBookAdmin.check(model=Book)
        expected = [
            checks.Error(
                ("The value of 'fieldsets[1][1][\"fields\"]' cannot include the ManyToManyField "
                 "'authors', because that field manually specifies a relationship model."),
                hint=None,
                obj=FieldsetBookAdmin,
                id='admin.E013',
            )
        ]
        self.assertEqual(errors, expected)

    def test_nested_fields(self):
        class NestedFieldsAdmin(admin.ModelAdmin):
            fields = ('price', ('name', 'subtitle'))

        errors = NestedFieldsAdmin.check(model=Book)
        self.assertEqual(errors, [])

    def test_nested_fieldsets(self):
        class NestedFieldsetAdmin(admin.ModelAdmin):
            fieldsets = (
                ('Main', {'fields': ('price', ('name', 'subtitle'))}),
            )

        errors = NestedFieldsetAdmin.check(model=Book)
        self.assertEqual(errors, [])

    def test_explicit_through_override(self):
        """
        Regression test for #12209 -- If the explicitly provided through model
        is specified as a string, the admin should still be able use
        Model.m2m_field.through
        """

        class AuthorsInline(admin.TabularInline):
            model = Book.authors.through

        class BookAdmin(admin.ModelAdmin):
            inlines = [AuthorsInline]

        errors = BookAdmin.check(model=Book)
        self.assertEqual(errors, [])

    def test_non_model_fields(self):
        """
        Regression for ensuring ModelAdmin.fields can contain non-model fields
        that broke with r11737
        """

        class SongForm(forms.ModelForm):
            extra_data = forms.CharField()

        class FieldsOnFormOnlyAdmin(admin.ModelAdmin):
            form = SongForm
            fields = ['title', 'extra_data']

        errors = FieldsOnFormOnlyAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_non_model_first_field(self):
        """
        Regression for ensuring ModelAdmin.field can handle first elem being a
        non-model field (test fix for UnboundLocalError introduced with r16225).
        """

        class SongForm(forms.ModelForm):
            extra_data = forms.CharField()

            class Meta:
                model = Song
                fields = '__all__'

        class FieldsOnFormOnlyAdmin(admin.ModelAdmin):
            form = SongForm
            fields = ['extra_data', 'title']

        errors = FieldsOnFormOnlyAdmin.check(model=Song)
        self.assertEqual(errors, [])

    def test_validator_compatibility(self):
        class MyValidator(object):
            def validate(self, cls, model):
                raise ImproperlyConfigured("error!")

        class MyModelAdmin(admin.ModelAdmin):
            validator_class = MyValidator

        with warnings.catch_warnings(record=True):
            warnings.filterwarnings('ignore', module='django.contrib.admin.options')
            errors = MyModelAdmin.check(model=Song)

            expected = [
                checks.Error(
                    'error!',
                    hint=None,
                    obj=MyModelAdmin,
                )
            ]
            self.assertEqual(errors, expected)

    def test_check_sublists_for_duplicates(self):
        class MyModelAdmin(admin.ModelAdmin):
            fields = ['state', ['state']]

        errors = MyModelAdmin.check(model=Song)
        expected = [
            checks.Error(
                "The value of 'fields' contains duplicate field(s).",
                hint=None,
                obj=MyModelAdmin,
                id='admin.E006'
            )
        ]
        self.assertEqual(errors, expected)

    def test_check_fieldset_sublists_for_duplicates(self):
        class MyModelAdmin(admin.ModelAdmin):
            fieldsets = [
                (None, {
                    'fields': ['title', 'album', ('title', 'album')]
                }),
            ]

        errors = MyModelAdmin.check(model=Song)
        expected = [
            checks.Error(
                "There are duplicate field(s) in 'fieldsets[0][1]'.",
                hint=None,
                obj=MyModelAdmin,
                id='admin.E012'
            )
        ]
        self.assertEqual(errors, expected)
