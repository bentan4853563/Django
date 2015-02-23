from __future__ import unicode_literals

import datetime

from django.contrib.admin.utils import quote
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.template.response import TemplateResponse
from django.test import TestCase, override_settings

from .models import Action, Car, Person


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.SHA1PasswordHasher'],
                   ROOT_URLCONF='admin_custom_urls.urls',)
class AdminCustomUrlsTest(TestCase):
    """
    Remember that:
    * The Action model has a CharField PK.
    * The ModelAdmin for Action customizes the add_view URL, it's
      '<app name>/<model name>/!add/'
    """

    @classmethod
    def setUpTestData(cls):
        # password = "secret"
        User.objects.create(
            pk=100, username='super', first_name='Super', last_name='User', email='super@example.com',
            password='sha1$995a3$6011485ea3834267d719b4c801409b8b1ddd0158', is_active=True, is_superuser=True,
            is_staff=True, last_login=datetime.datetime(2007, 5, 30, 13, 20, 10),
            date_joined=datetime.datetime(2007, 5, 30, 13, 20, 10)
        )
        Action.objects.create(name='delete', description='Remove things.')
        Action.objects.create(name='rename', description='Gives things other names.')
        Action.objects.create(name='add', description='Add things.')
        Action.objects.create(name='path/to/file/', description="An action with '/' in its name.")
        Action.objects.create(
            name='path/to/html/document.html',
            description='An action with a name similar to a HTML doc path.'
        )
        Action.objects.create(
            name='javascript:alert(\'Hello world\');">Click here</a>',
            description='An action with a name suspected of being a XSS attempt'
        )

    def setUp(self):
        self.client.login(username='super', password='secret')

    def test_basic_add_GET(self):
        """
        Ensure GET on the add_view works.
        """
        add_url = reverse('admin:admin_custom_urls_action_add')
        self.assertTrue(add_url.endswith('/!add/'))
        response = self.client.get(add_url)
        self.assertIsInstance(response, TemplateResponse)
        self.assertEqual(response.status_code, 200)

    def test_add_with_GET_args(self):
        """
        Ensure GET on the add_view plus specifying a field value in the query
        string works.
        """
        response = self.client.get(reverse('admin:admin_custom_urls_action_add'), {'name': 'My Action'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="My Action"')

    def test_basic_add_POST(self):
        """
        Ensure POST on add_view works.
        """
        post_data = {
            '_popup': '1',
            "name": 'Action added through a popup',
            "description": "Description of added action",
        }
        response = self.client.post(reverse('admin:admin_custom_urls_action_add'), post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dismissAddRelatedObjectPopup')
        self.assertContains(response, 'Action added through a popup')

    def test_admin_URLs_no_clash(self):
        """
        Test that some admin URLs work correctly.
        """
        # Should get the change_view for model instance with PK 'add', not show
        # the add_view
        url = reverse('admin:%s_action_change' % Action._meta.app_label,
                args=(quote('add'),))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change action')

        # Should correctly get the change_view for the model instance with the
        # funny-looking PK (the one with a 'path/to/html/document.html' value)
        url = reverse('admin:%s_action_change' % Action._meta.app_label,
                args=(quote("path/to/html/document.html"),))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change action')
        self.assertContains(response, 'value="path/to/html/document.html"')

    def test_post_save_add_redirect(self):
        """
        Ensures that ModelAdmin.response_post_save_add() controls the
        redirection after the 'Save' button has been pressed when adding a
        new object.
        Refs 8001, 18310, 19505.
        """
        post_data = {'name': 'John Doe'}
        self.assertEqual(Person.objects.count(), 0)
        response = self.client.post(
            reverse('admin:admin_custom_urls_person_add'), post_data)
        persons = Person.objects.all()
        self.assertEqual(len(persons), 1)
        self.assertRedirects(
            response, reverse('admin:admin_custom_urls_person_history', args=[persons[0].pk]))

    def test_post_save_change_redirect(self):
        """
        Ensures that ModelAdmin.response_post_save_change() controls the
        redirection after the 'Save' button has been pressed when editing an
        existing object.
        Refs 8001, 18310, 19505.
        """
        Person.objects.create(name='John Doe')
        self.assertEqual(Person.objects.count(), 1)
        person = Person.objects.all()[0]
        post_data = {'name': 'Jack Doe'}
        response = self.client.post(
            reverse('admin:admin_custom_urls_person_change', args=[person.pk]), post_data)
        self.assertRedirects(
            response, reverse('admin:admin_custom_urls_person_delete', args=[person.pk]))

    def test_post_url_continue(self):
        """
        Ensures that the ModelAdmin.response_add()'s parameter `post_url_continue`
        controls the redirection after an object has been created.
        """
        post_data = {'name': 'SuperFast', '_continue': '1'}
        self.assertEqual(Car.objects.count(), 0)
        response = self.client.post(
            reverse('admin:admin_custom_urls_car_add'), post_data)
        cars = Car.objects.all()
        self.assertEqual(len(cars), 1)
        self.assertRedirects(
            response, reverse('admin:admin_custom_urls_car_history', args=[cars[0].pk]))
