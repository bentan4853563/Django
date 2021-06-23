import re

from django.conf import settings
from django.contrib.sessions.backends.cache import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import (
    CSRF_ALLOWED_CHARS, CSRF_SESSION_KEY, CSRF_TOKEN_LENGTH, REASON_BAD_ORIGIN,
    REASON_CSRF_TOKEN_MISSING, REASON_NO_CSRF_COOKIE, CsrfViewMiddleware,
    RejectRequest, _compare_masked_tokens as equivalent_tokens,
    _mask_cipher_secret, _unmask_cipher_token, get_token,
)
from django.test import SimpleTestCase, override_settings
from django.views.decorators.csrf import csrf_exempt, requires_csrf_token

from .views import (
    ensure_csrf_cookie_view, non_token_view_using_request_processor,
    post_form_view, token_view,
)

# This is a test (unmasked) CSRF cookie / secret.
TEST_SECRET = 'lcccccccX2kcccccccY2jcccccccssIC'
# Two masked versions of TEST_SECRET for testing purposes.
MASKED_TEST_SECRET1 = '1bcdefghij2bcdefghij3bcdefghij4bcdefghij5bcdefghij6bcdefghijABCD'
MASKED_TEST_SECRET2 = '2JgchWvM1tpxT2lfz9aydoXW9yT1DN3NdLiejYxOOlzzV4nhBbYqmqZYbAV3V5Bf'


class CsrfFunctionTests(SimpleTestCase):

    def test_unmask_cipher_token(self):
        cases = [
            (TEST_SECRET, MASKED_TEST_SECRET1),
            (TEST_SECRET, MASKED_TEST_SECRET2),
            (
                32 * 'a',
                'vFioG3XOLyGyGsPRFyB9iYUs341ufzIEvFioG3XOLyGyGsPRFyB9iYUs341ufzIE',
            ),
            (32 * 'a', 64 * 'a'),
            (32 * 'a', 64 * 'b'),
            (32 * 'b', 32 * 'a' + 32 * 'b'),
            (32 * 'b', 32 * 'b' + 32 * 'c'),
            (32 * 'c', 32 * 'a' + 32 * 'c'),
        ]
        for secret, masked_secret in cases:
            with self.subTest(masked_secret=masked_secret):
                actual = _unmask_cipher_token(masked_secret)
                self.assertEqual(actual, secret)

    # This method depends on _unmask_cipher_token() being correct.
    def assertMaskedSecretCorrect(self, masked_secret, secret):
        """Test that a string is a valid masked version of a secret."""
        self.assertEqual(len(masked_secret), CSRF_TOKEN_LENGTH)
        self.assertTrue(
            set(masked_secret).issubset(set(CSRF_ALLOWED_CHARS)),
            msg=f'invalid characters in {masked_secret!r}',
        )
        actual = _unmask_cipher_token(masked_secret)
        self.assertEqual(actual, secret)

    def test_mask_cipher_secret(self):
        cases = [
            32 * 'a',
            TEST_SECRET,
            'da4SrUiHJYoJ0HYQ0vcgisoIuFOxx4ER',
        ]
        for secret in cases:
            with self.subTest(secret=secret):
                masked = _mask_cipher_secret(secret)
                self.assertMaskedSecretCorrect(masked, secret)


class TestingHttpRequest(HttpRequest):
    """
    A version of HttpRequest that allows us to change some things
    more easily
    """
    def __init__(self):
        super().__init__()
        self.session = SessionStore()

    def is_secure(self):
        return getattr(self, '_is_secure_override', False)


class CsrfViewMiddlewareTestMixin:
    """
    Shared methods and tests for session-based and cookie-based tokens.
    """

    _csrf_id_cookie = MASKED_TEST_SECRET1
    _csrf_id_token = MASKED_TEST_SECRET2

    def _set_csrf_cookie(self, req, cookie):
        raise NotImplementedError('This method must be implemented by a subclass.')

    def _get_request(self):
        req = TestingHttpRequest()
        req.method = 'GET'
        return req

    def _get_GET_no_csrf_cookie_request(self):
        return self._get_request()

    def _get_GET_csrf_cookie_request(self, cookie=None):
        """The cookie argument defaults to the valid test cookie."""
        if cookie is None:
            cookie = self._csrf_id_cookie
        req = self._get_request()
        self._set_csrf_cookie(req, cookie)
        return req

    def _get_POST_csrf_cookie_request(
        self, cookie=None, post_token=None, meta_token=None, token_header=None,
    ):
        """
        The cookie argument defaults to this class's default test cookie. The
        post_token and meta_token arguments are included in the request's
        req.POST and req.META headers, respectively, when that argument is
        provided and non-None. The token_header argument is the header key to
        use for req.META, defaults to "HTTP_X_CSRFTOKEN".
        """
        if token_header is None:
            token_header = 'HTTP_X_CSRFTOKEN'
        req = self._get_GET_csrf_cookie_request(cookie=cookie)
        req.method = "POST"
        if post_token is not None:
            req.POST['csrfmiddlewaretoken'] = post_token
        if meta_token is not None:
            req.META[token_header] = meta_token
        return req

    def _get_POST_no_csrf_cookie_request(self):
        req = self._get_GET_no_csrf_cookie_request()
        req.method = "POST"
        return req

    def _get_POST_request_with_token(self, cookie=None):
        """The cookie argument defaults to this class's default test cookie."""
        return self._get_POST_csrf_cookie_request(cookie=cookie, post_token=self._csrf_id_token)

    def _check_token_present(self, response, csrf_id=None):
        text = str(response.content, response.charset)
        match = re.search('name="csrfmiddlewaretoken" value="(.*?)"', text)
        csrf_token = csrf_id or self._csrf_id_token
        self.assertTrue(
            match and equivalent_tokens(csrf_token, match[1]),
            "Could not find csrfmiddlewaretoken to match %s" % csrf_token
        )

    def test_process_response_get_token_not_used(self):
        """
        If get_token() is not called, the view middleware does not
        add a cookie.
        """
        # This is important to make pages cacheable.  Pages which do call
        # get_token(), assuming they use the token, are not cacheable because
        # the token is specific to the user
        req = self._get_GET_no_csrf_cookie_request()
        # non_token_view_using_request_processor does not call get_token(), but
        # does use the csrf request processor.  By using this, we are testing
        # that the view processor is properly lazy and doesn't call get_token()
        # until needed.
        mw = CsrfViewMiddleware(non_token_view_using_request_processor)
        mw.process_request(req)
        mw.process_view(req, non_token_view_using_request_processor, (), {})
        resp = mw(req)

        csrf_cookie = resp.cookies.get(settings.CSRF_COOKIE_NAME, False)
        self.assertIs(csrf_cookie, False)

    def _check_bad_or_missing_cookie(self, cookie, expected):
        """Passing None for cookie includes no cookie."""
        if cookie is None:
            req = self._get_POST_no_csrf_cookie_request()
        else:
            req = self._get_POST_csrf_cookie_request(cookie=cookie)
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            resp = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(403, resp.status_code)
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % expected)

    def test_no_csrf_cookie(self):
        """
        If no CSRF cookies is present, the middleware rejects the incoming
        request. This will stop login CSRF.
        """
        self._check_bad_or_missing_cookie(None, REASON_NO_CSRF_COOKIE)

    def _check_bad_or_missing_token(
        self, expected, post_token=None, meta_token=None, token_header=None,
    ):
        req = self._get_POST_csrf_cookie_request(
            post_token=post_token,
            meta_token=meta_token,
            token_header=token_header,
        )
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            resp = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(403, resp.status_code)
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % expected)

    def test_csrf_cookie_bad_or_missing_token(self):
        """
        If a CSRF cookie is present but the token is missing or invalid, the
        middleware rejects the incoming request.
        """
        cases = [
            (None, None, REASON_CSRF_TOKEN_MISSING),
            (16 * 'a', None, 'CSRF token from POST has incorrect length.'),
            (64 * '*', None, 'CSRF token from POST has invalid characters.'),
            (64 * 'a', None, 'CSRF token from POST incorrect.'),
            (
                None,
                16 * 'a',
                "CSRF token from the 'X-Csrftoken' HTTP header has incorrect length.",
            ),
            (
                None,
                64 * '*',
                "CSRF token from the 'X-Csrftoken' HTTP header has invalid characters.",
            ),
            (
                None,
                64 * 'a',
                "CSRF token from the 'X-Csrftoken' HTTP header incorrect.",
            ),
        ]
        for post_token, meta_token, expected in cases:
            with self.subTest(post_token=post_token, meta_token=meta_token):
                self._check_bad_or_missing_token(
                    expected,
                    post_token=post_token,
                    meta_token=meta_token,
                )

    @override_settings(CSRF_HEADER_NAME='HTTP_X_CSRFTOKEN_CUSTOMIZED')
    def test_csrf_cookie_bad_token_custom_header(self):
        """
        If a CSRF cookie is present and an invalid token is passed via a
        custom CSRF_HEADER_NAME, the middleware rejects the incoming request.
        """
        expected = (
            "CSRF token from the 'X-Csrftoken-Customized' HTTP header has "
            "incorrect length."
        )
        self._check_bad_or_missing_token(
            expected,
            meta_token=16 * 'a',
            token_header='HTTP_X_CSRFTOKEN_CUSTOMIZED',
        )

    def test_process_request_csrf_cookie_and_token(self):
        """
        If both a cookie and a token is present, the middleware lets it through.
        """
        req = self._get_POST_request_with_token()
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    def test_process_request_csrf_cookie_no_token_exempt_view(self):
        """
        If a CSRF cookie is present and no token, but the csrf_exempt decorator
        has been applied to the view, the middleware lets it through
        """
        req = self._get_POST_csrf_cookie_request()
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, csrf_exempt(post_form_view), (), {})
        self.assertIsNone(resp)

    def test_csrf_token_in_header(self):
        """
        The token may be passed in a header instead of in the form.
        """
        req = self._get_POST_csrf_cookie_request(meta_token=self._csrf_id_token)
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    @override_settings(CSRF_HEADER_NAME='HTTP_X_CSRFTOKEN_CUSTOMIZED')
    def test_csrf_token_in_header_with_customized_name(self):
        """
        settings.CSRF_HEADER_NAME can be used to customize the CSRF header name
        """
        req = self._get_POST_csrf_cookie_request(
            meta_token=self._csrf_id_token,
            token_header='HTTP_X_CSRFTOKEN_CUSTOMIZED',
        )
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    def test_put_and_delete_rejected(self):
        """
        HTTP PUT and DELETE methods have protection
        """
        req = TestingHttpRequest()
        req.method = 'PUT'
        mw = CsrfViewMiddleware(post_form_view)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            resp = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(403, resp.status_code)
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % REASON_NO_CSRF_COOKIE)

        req = TestingHttpRequest()
        req.method = 'DELETE'
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            resp = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(403, resp.status_code)
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % REASON_NO_CSRF_COOKIE)

    def test_put_and_delete_allowed(self):
        """
        HTTP PUT and DELETE can get through with X-CSRFToken and a cookie.
        """
        req = self._get_POST_csrf_cookie_request(meta_token=self._csrf_id_token)
        req.method = 'PUT'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

        req = self._get_POST_csrf_cookie_request(meta_token=self._csrf_id_token)
        req.method = 'DELETE'
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    # Tests for the template tag method
    def test_token_node_no_csrf_cookie(self):
        """
        CsrfTokenNode works when no CSRF cookie is set.
        """
        req = self._get_GET_no_csrf_cookie_request()
        resp = token_view(req)

        token = get_token(req)
        self.assertIsNotNone(token)
        self._check_token_present(resp, token)

    def test_token_node_empty_csrf_cookie(self):
        """
        A new token is sent if the csrf_cookie is the empty string.
        """
        req = self._get_GET_no_csrf_cookie_request()
        req.COOKIES[settings.CSRF_COOKIE_NAME] = ""
        mw = CsrfViewMiddleware(token_view)
        mw.process_view(req, token_view, (), {})
        resp = token_view(req)

        token = get_token(req)
        self.assertIsNotNone(token)
        self._check_token_present(resp, token)

    def test_token_node_with_csrf_cookie(self):
        """
        CsrfTokenNode works when a CSRF cookie is set.
        """
        req = self._get_GET_csrf_cookie_request()
        mw = CsrfViewMiddleware(token_view)
        mw.process_request(req)
        mw.process_view(req, token_view, (), {})
        resp = token_view(req)
        self._check_token_present(resp)

    def test_get_token_for_exempt_view(self):
        """
        get_token still works for a view decorated with 'csrf_exempt'.
        """
        req = self._get_GET_csrf_cookie_request()
        mw = CsrfViewMiddleware(token_view)
        mw.process_request(req)
        mw.process_view(req, csrf_exempt(token_view), (), {})
        resp = token_view(req)
        self._check_token_present(resp)

    def test_get_token_for_requires_csrf_token_view(self):
        """
        get_token() works for a view decorated solely with requires_csrf_token.
        """
        req = self._get_GET_csrf_cookie_request()
        resp = requires_csrf_token(token_view)(req)
        self._check_token_present(resp)

    def test_token_node_with_new_csrf_cookie(self):
        """
        CsrfTokenNode works when a CSRF cookie is created by
        the middleware (when one was not already present)
        """
        req = self._get_GET_no_csrf_cookie_request()
        mw = CsrfViewMiddleware(token_view)
        mw.process_view(req, token_view, (), {})
        resp = mw(req)
        csrf_cookie = resp.cookies[settings.CSRF_COOKIE_NAME]
        self._check_token_present(resp, csrf_id=csrf_cookie.value)

    def test_cookie_not_reset_on_accepted_request(self):
        """
        The csrf token used in posts is changed on every request (although
        stays equivalent). The csrf cookie should not change on accepted
        requests. If it appears in the response, it should keep its value.
        """
        req = self._get_POST_request_with_token()
        mw = CsrfViewMiddleware(token_view)
        mw.process_request(req)
        mw.process_view(req, token_view, (), {})
        resp = mw(req)
        csrf_cookie = resp.cookies.get(settings.CSRF_COOKIE_NAME, None)
        if csrf_cookie:
            self.assertEqual(
                csrf_cookie.value, self._csrf_id_cookie,
                "CSRF cookie was changed on an accepted request"
            )

    @override_settings(DEBUG=True, ALLOWED_HOSTS=['www.example.com'])
    def test_https_bad_referer(self):
        """
        A POST HTTPS request with a bad referer is rejected
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_REFERER'] = 'https://www.evil.org/somepage'
        req.META['SERVER_PORT'] = '443'
        mw = CsrfViewMiddleware(post_form_view)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(
            response,
            'Referer checking failed - https://www.evil.org/somepage does not '
            'match any trusted origins.',
            status_code=403,
        )

    def _check_referer_rejects(self, mw, req):
        with self.assertRaises(RejectRequest):
            mw._check_referer(req)

    @override_settings(DEBUG=True)
    def test_https_no_referer(self):
        """A POST HTTPS request with a missing referer is rejected."""
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(
            response,
            'Referer checking failed - no Referer.',
            status_code=403,
        )

    def test_https_malformed_host(self):
        """
        CsrfViewMiddleware generates a 403 response if it receives an HTTPS
        request with a bad host.
        """
        req = self._get_POST_no_csrf_cookie_request()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = '@malformed'
        req.META['HTTP_REFERER'] = 'https://www.evil.org/somepage'
        req.META['SERVER_PORT'] = '443'
        mw = CsrfViewMiddleware(token_view)
        expected = (
            'Referer checking failed - https://www.evil.org/somepage does not '
            'match any trusted origins.'
        )
        with self.assertRaisesMessage(RejectRequest, expected):
            mw._check_referer(req)
        response = mw.process_view(req, token_view, (), {})
        self.assertEqual(response.status_code, 403)

    def test_origin_malformed_host(self):
        req = self._get_POST_no_csrf_cookie_request()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = '@malformed'
        req.META['HTTP_ORIGIN'] = 'https://www.evil.org'
        mw = CsrfViewMiddleware(token_view)
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, token_view, (), {})
        self.assertEqual(response.status_code, 403)

    @override_settings(DEBUG=True)
    def test_https_malformed_referer(self):
        """
        A POST HTTPS request with a bad referer is rejected.
        """
        malformed_referer_msg = 'Referer checking failed - Referer is malformed.'
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_REFERER'] = 'http://http://www.example.com/'
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(
            response,
            'Referer checking failed - Referer is insecure while host is secure.',
            status_code=403,
        )
        # Empty
        req.META['HTTP_REFERER'] = ''
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(response, malformed_referer_msg, status_code=403)
        # Non-ASCII
        req.META['HTTP_REFERER'] = 'ØBöIß'
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(response, malformed_referer_msg, status_code=403)
        # missing scheme
        # >>> urlparse('//example.com/')
        # ParseResult(scheme='', netloc='example.com', path='/', params='', query='', fragment='')
        req.META['HTTP_REFERER'] = '//example.com/'
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(response, malformed_referer_msg, status_code=403)
        # missing netloc
        # >>> urlparse('https://')
        # ParseResult(scheme='https', netloc='', path='', params='', query='', fragment='')
        req.META['HTTP_REFERER'] = 'https://'
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(response, malformed_referer_msg, status_code=403)
        # Invalid URL
        # >>> urlparse('https://[')
        # ValueError: Invalid IPv6 URL
        req.META['HTTP_REFERER'] = 'https://['
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(response, malformed_referer_msg, status_code=403)

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_https_good_referer(self):
        """
        A POST HTTPS request with a good referer is accepted.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_REFERER'] = 'https://www.example.com/somepage'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_https_good_referer_2(self):
        """
        A POST HTTPS request with a good referer is accepted where the referer
        contains no trailing slash.
        """
        # See ticket #15617
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_REFERER'] = 'https://www.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    def _test_https_good_referer_behind_proxy(self):
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META.update({
            'HTTP_HOST': '10.0.0.2',
            'HTTP_REFERER': 'https://www.example.com/somepage',
            'SERVER_PORT': '8080',
            'HTTP_X_FORWARDED_HOST': 'www.example.com',
            'HTTP_X_FORWARDED_PORT': '443',
        })
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    @override_settings(CSRF_TRUSTED_ORIGINS=['https://dashboard.example.com'])
    def test_https_good_referer_malformed_host(self):
        """
        A POST HTTPS request is accepted if it receives a good referer with
        a bad host.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = '@malformed'
        req.META['HTTP_REFERER'] = 'https://dashboard.example.com/somepage'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    @override_settings(ALLOWED_HOSTS=['www.example.com'], CSRF_TRUSTED_ORIGINS=['https://dashboard.example.com'])
    def test_https_csrf_trusted_origin_allowed(self):
        """
        A POST HTTPS request with a referer added to the CSRF_TRUSTED_ORIGINS
        setting is accepted.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_REFERER'] = 'https://dashboard.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

    @override_settings(ALLOWED_HOSTS=['www.example.com'], CSRF_TRUSTED_ORIGINS=['https://*.example.com'])
    def test_https_csrf_wildcard_trusted_origin_allowed(self):
        """
        A POST HTTPS request with a referer that matches a CSRF_TRUSTED_ORIGINS
        wildcard is accepted.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_REFERER'] = 'https://dashboard.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(response)

    def _test_https_good_referer_matches_cookie_domain(self):
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_REFERER'] = 'https://foo.example.com/'
        req.META['SERVER_PORT'] = '443'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(response)

    def _test_https_good_referer_matches_cookie_domain_with_different_port(self):
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_REFERER'] = 'https://foo.example.com:4443/'
        req.META['SERVER_PORT'] = '4443'
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(response)

    def test_ensures_csrf_cookie_no_logging(self):
        """
        ensure_csrf_cookie() doesn't log warnings (#19436).
        """
        with self.assertNoLogs('django.request', 'WARNING'):
            req = self._get_GET_no_csrf_cookie_request()
            ensure_csrf_cookie_view(req)

    def test_post_data_read_failure(self):
        """
        OSErrors during POST data reading are caught and treated as if the
        POST data wasn't there (#20128).
        """
        class CsrfPostRequest(HttpRequest):
            """
            HttpRequest that can raise an OSError when accessing POST data
            """
            def __init__(self, token, raise_error):
                super().__init__()
                self.method = 'POST'

                self.raise_error = False
                self.COOKIES[settings.CSRF_COOKIE_NAME] = token

                # Handle both cases here to prevent duplicate code in the
                # session tests.
                self.session = {}
                self.session[CSRF_SESSION_KEY] = token

                self.POST['csrfmiddlewaretoken'] = token
                self.raise_error = raise_error

            def _load_post_and_files(self):
                raise OSError('error reading input data')

            def _get_post(self):
                if self.raise_error:
                    self._load_post_and_files()
                return self._post

            def _set_post(self, post):
                self._post = post

            POST = property(_get_post, _set_post)

        token = ('ABC' + self._csrf_id_token)[:CSRF_TOKEN_LENGTH]

        req = CsrfPostRequest(token, raise_error=False)
        mw = CsrfViewMiddleware(post_form_view)
        mw.process_request(req)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)

        req = CsrfPostRequest(token, raise_error=True)
        mw.process_request(req)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            resp = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            cm.records[0].getMessage(),
            'Forbidden (%s): ' % REASON_CSRF_TOKEN_MISSING,
        )

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_bad_origin_bad_domain(self):
        """A request with a bad origin is rejected."""
        req = self._get_POST_request_with_token()
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'https://www.evil.org'
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        self.assertIs(mw._origin_verified(req), False)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            response = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(response.status_code, 403)
        msg = REASON_BAD_ORIGIN % req.META['HTTP_ORIGIN']
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % msg)

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_bad_origin_null_origin(self):
        """A request with a null origin is rejected."""
        req = self._get_POST_request_with_token()
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'null'
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        self.assertIs(mw._origin_verified(req), False)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            response = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(response.status_code, 403)
        msg = REASON_BAD_ORIGIN % req.META['HTTP_ORIGIN']
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % msg)

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_bad_origin_bad_protocol(self):
        """A request with an origin with wrong protocol is rejected."""
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'http://example.com'
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        self.assertIs(mw._origin_verified(req), False)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            response = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(response.status_code, 403)
        msg = REASON_BAD_ORIGIN % req.META['HTTP_ORIGIN']
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % msg)

    @override_settings(
        ALLOWED_HOSTS=['www.example.com'],
        CSRF_TRUSTED_ORIGINS=[
            'http://no-match.com',
            'https://*.example.com',
            'http://*.no-match.com',
            'http://*.no-match-2.com',
        ],
    )
    def test_bad_origin_csrf_trusted_origin_bad_protocol(self):
        """
        A request with an origin with the wrong protocol compared to
        CSRF_TRUSTED_ORIGINS is rejected.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'http://foo.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        self.assertIs(mw._origin_verified(req), False)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            response = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(response.status_code, 403)
        msg = REASON_BAD_ORIGIN % req.META['HTTP_ORIGIN']
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % msg)
        self.assertEqual(mw.allowed_origins_exact, {'http://no-match.com'})
        self.assertEqual(mw.allowed_origin_subdomains, {
            'https': ['.example.com'],
            'http': ['.no-match.com', '.no-match-2.com'],
        })

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_bad_origin_cannot_be_parsed(self):
        """
        A POST request with an origin that can't be parsed by urlparse() is
        rejected.
        """
        req = self._get_POST_request_with_token()
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'https://['
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        self.assertIs(mw._origin_verified(req), False)
        with self.assertLogs('django.security.csrf', 'WARNING') as cm:
            response = mw.process_view(req, post_form_view, (), {})
        self.assertEqual(response.status_code, 403)
        msg = REASON_BAD_ORIGIN % req.META['HTTP_ORIGIN']
        self.assertEqual(cm.records[0].getMessage(), 'Forbidden (%s): ' % msg)

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_good_origin_insecure(self):
        """A POST HTTP request with a good origin is accepted."""
        req = self._get_POST_request_with_token()
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'http://www.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        self.assertIs(mw._origin_verified(req), True)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(response)

    @override_settings(ALLOWED_HOSTS=['www.example.com'])
    def test_good_origin_secure(self):
        """A POST HTTPS request with a good origin is accepted."""
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'https://www.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        self.assertIs(mw._origin_verified(req), True)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(response)

    @override_settings(ALLOWED_HOSTS=['www.example.com'], CSRF_TRUSTED_ORIGINS=['https://dashboard.example.com'])
    def test_good_origin_csrf_trusted_origin_allowed(self):
        """
        A POST request with an origin added to the CSRF_TRUSTED_ORIGINS
        setting is accepted.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'https://dashboard.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        self.assertIs(mw._origin_verified(req), True)
        resp = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(resp)
        self.assertEqual(mw.allowed_origins_exact, {'https://dashboard.example.com'})
        self.assertEqual(mw.allowed_origin_subdomains, {})

    @override_settings(ALLOWED_HOSTS=['www.example.com'], CSRF_TRUSTED_ORIGINS=['https://*.example.com'])
    def test_good_origin_wildcard_csrf_trusted_origin_allowed(self):
        """
        A POST request with an origin that matches a CSRF_TRUSTED_ORIGINS
        wildcard is accepted.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_HOST'] = 'www.example.com'
        req.META['HTTP_ORIGIN'] = 'https://foo.example.com'
        mw = CsrfViewMiddleware(post_form_view)
        self.assertIs(mw._origin_verified(req), True)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertIsNone(response)
        self.assertEqual(mw.allowed_origins_exact, set())
        self.assertEqual(mw.allowed_origin_subdomains, {'https': ['.example.com']})


class CsrfViewMiddlewareTests(CsrfViewMiddlewareTestMixin, SimpleTestCase):

    def _set_csrf_cookie(self, req, cookie):
        req.COOKIES[settings.CSRF_COOKIE_NAME] = cookie

    def test_ensures_csrf_cookie_no_middleware(self):
        """
        The ensure_csrf_cookie() decorator works without middleware.
        """
        req = self._get_GET_no_csrf_cookie_request()
        resp = ensure_csrf_cookie_view(req)
        self.assertTrue(resp.cookies.get(settings.CSRF_COOKIE_NAME, False))
        self.assertIn('Cookie', resp.get('Vary', ''))

    def test_ensures_csrf_cookie_with_middleware(self):
        """
        The ensure_csrf_cookie() decorator works with the CsrfViewMiddleware
        enabled.
        """
        req = self._get_GET_no_csrf_cookie_request()
        mw = CsrfViewMiddleware(ensure_csrf_cookie_view)
        mw.process_view(req, ensure_csrf_cookie_view, (), {})
        resp = mw(req)
        self.assertTrue(resp.cookies.get(settings.CSRF_COOKIE_NAME, False))
        self.assertIn('Cookie', resp.get('Vary', ''))

    def test_csrf_cookie_age(self):
        """
        CSRF cookie age can be set using settings.CSRF_COOKIE_AGE.
        """
        req = self._get_GET_no_csrf_cookie_request()

        MAX_AGE = 123
        with self.settings(CSRF_COOKIE_NAME='csrfcookie',
                           CSRF_COOKIE_DOMAIN='.example.com',
                           CSRF_COOKIE_AGE=MAX_AGE,
                           CSRF_COOKIE_PATH='/test/',
                           CSRF_COOKIE_SECURE=True,
                           CSRF_COOKIE_HTTPONLY=True):
            # token_view calls get_token() indirectly
            mw = CsrfViewMiddleware(token_view)
            mw.process_view(req, token_view, (), {})
            resp = mw(req)
            max_age = resp.cookies.get('csrfcookie').get('max-age')
            self.assertEqual(max_age, MAX_AGE)

    def test_csrf_cookie_age_none(self):
        """
        CSRF cookie age does not have max age set and therefore uses
        session-based cookies.
        """
        req = self._get_GET_no_csrf_cookie_request()

        MAX_AGE = None
        with self.settings(CSRF_COOKIE_NAME='csrfcookie',
                           CSRF_COOKIE_DOMAIN='.example.com',
                           CSRF_COOKIE_AGE=MAX_AGE,
                           CSRF_COOKIE_PATH='/test/',
                           CSRF_COOKIE_SECURE=True,
                           CSRF_COOKIE_HTTPONLY=True):
            # token_view calls get_token() indirectly
            mw = CsrfViewMiddleware(token_view)
            mw.process_view(req, token_view, (), {})
            resp = mw(req)
            max_age = resp.cookies.get('csrfcookie').get('max-age')
            self.assertEqual(max_age, '')

    def test_csrf_cookie_samesite(self):
        req = self._get_GET_no_csrf_cookie_request()
        with self.settings(CSRF_COOKIE_NAME='csrfcookie', CSRF_COOKIE_SAMESITE='Strict'):
            mw = CsrfViewMiddleware(token_view)
            mw.process_view(req, token_view, (), {})
            resp = mw(req)
            self.assertEqual(resp.cookies['csrfcookie']['samesite'], 'Strict')

    def test_bad_csrf_cookie_characters(self):
        """
        If the CSRF cookie has invalid characters in a POST request, the
        middleware rejects the incoming request.
        """
        self._check_bad_or_missing_cookie(64 * '*', 'CSRF cookie has invalid characters.')

    def test_bad_csrf_cookie_length(self):
        """
        If the CSRF cookie has an incorrect length in a POST request, the
        middleware rejects the incoming request.
        """
        self._check_bad_or_missing_cookie(16 * 'a', 'CSRF cookie has incorrect length.')

    def test_process_view_token_too_long(self):
        """
        If the token is longer than expected, it is ignored and a new token is
        created.
        """
        req = self._get_GET_no_csrf_cookie_request()
        req.COOKIES[settings.CSRF_COOKIE_NAME] = 'x' * 100000
        mw = CsrfViewMiddleware(token_view)
        mw.process_view(req, token_view, (), {})
        resp = mw(req)
        csrf_cookie = resp.cookies.get(settings.CSRF_COOKIE_NAME, False)
        self.assertEqual(len(csrf_cookie.value), CSRF_TOKEN_LENGTH)

    def test_process_view_token_invalid_chars(self):
        """
        If the token contains non-alphanumeric characters, it is ignored and a
        new token is created.
        """
        token = ('!@#' + self._csrf_id_token)[:CSRF_TOKEN_LENGTH]
        req = self._get_GET_no_csrf_cookie_request()
        req.COOKIES[settings.CSRF_COOKIE_NAME] = token
        mw = CsrfViewMiddleware(token_view)
        mw.process_view(req, token_view, (), {})
        resp = mw(req)
        csrf_cookie = resp.cookies.get(settings.CSRF_COOKIE_NAME, False)
        self.assertEqual(len(csrf_cookie.value), CSRF_TOKEN_LENGTH)
        self.assertNotEqual(csrf_cookie.value, token)

    def test_masked_unmasked_combinations(self):
        """
        All combinations are allowed of (1) masked and unmasked cookies,
        (2) masked and unmasked tokens, and (3) tokens provided via POST and
        the X-CSRFToken header.
        """
        cases = [
            (TEST_SECRET, TEST_SECRET, None),
            (TEST_SECRET, MASKED_TEST_SECRET2, None),
            (TEST_SECRET, None, TEST_SECRET),
            (TEST_SECRET, None, MASKED_TEST_SECRET2),
            (MASKED_TEST_SECRET1, TEST_SECRET, None),
            (MASKED_TEST_SECRET1, MASKED_TEST_SECRET2, None),
            (MASKED_TEST_SECRET1, None, TEST_SECRET),
            (MASKED_TEST_SECRET1, None, MASKED_TEST_SECRET2),
        ]
        for args in cases:
            with self.subTest(args=args):
                cookie, post_token, meta_token = args
                req = self._get_POST_csrf_cookie_request(
                    cookie=cookie, post_token=post_token, meta_token=meta_token,
                )
                mw = CsrfViewMiddleware(token_view)
                mw.process_request(req)
                resp = mw.process_view(req, token_view, (), {})
                self.assertIsNone(resp)

    def test_bare_secret_accepted_and_replaced(self):
        """
        The csrf token is reset from a bare secret.
        """
        req = self._get_POST_request_with_token(cookie=TEST_SECRET)
        mw = CsrfViewMiddleware(token_view)
        mw.process_request(req)
        resp = mw.process_view(req, token_view, (), {})
        self.assertIsNone(resp)
        resp = mw(req)
        self.assertIn(settings.CSRF_COOKIE_NAME, resp.cookies, "Cookie was not reset from bare secret")
        csrf_cookie = resp.cookies[settings.CSRF_COOKIE_NAME]
        self.assertEqual(len(csrf_cookie.value), CSRF_TOKEN_LENGTH)
        self._check_token_present(resp, csrf_id=csrf_cookie.value)

    @override_settings(ALLOWED_HOSTS=['www.example.com'], CSRF_COOKIE_DOMAIN='.example.com', USE_X_FORWARDED_PORT=True)
    def test_https_good_referer_behind_proxy(self):
        """
        A POST HTTPS request is accepted when USE_X_FORWARDED_PORT=True.
        """
        self._test_https_good_referer_behind_proxy()

    @override_settings(ALLOWED_HOSTS=['www.example.com'], CSRF_COOKIE_DOMAIN='.example.com')
    def test_https_good_referer_matches_cookie_domain(self):
        """
        A POST HTTPS request with a good referer should be accepted from a
        subdomain that's allowed by CSRF_COOKIE_DOMAIN.
        """
        self._test_https_good_referer_matches_cookie_domain()

    @override_settings(ALLOWED_HOSTS=['www.example.com'], CSRF_COOKIE_DOMAIN='.example.com')
    def test_https_good_referer_matches_cookie_domain_with_different_port(self):
        """
        A POST HTTPS request with a good referer should be accepted from a
        subdomain that's allowed by CSRF_COOKIE_DOMAIN and a non-443 port.
        """
        self._test_https_good_referer_matches_cookie_domain_with_different_port()

    @override_settings(CSRF_COOKIE_DOMAIN='.example.com', DEBUG=True)
    def test_https_reject_insecure_referer(self):
        """
        A POST HTTPS request from an insecure referer should be rejected.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_REFERER'] = 'http://example.com/'
        req.META['SERVER_PORT'] = '443'
        mw = CsrfViewMiddleware(post_form_view)
        self._check_referer_rejects(mw, req)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(
            response,
            'Referer checking failed - Referer is insecure while host is secure.',
            status_code=403,
        )


@override_settings(CSRF_USE_SESSIONS=True, CSRF_COOKIE_DOMAIN=None)
class CsrfViewMiddlewareUseSessionsTests(CsrfViewMiddlewareTestMixin, SimpleTestCase):
    """
    CSRF tests with CSRF_USE_SESSIONS=True.
    """

    def _set_csrf_cookie(self, req, cookie):
        req.session[CSRF_SESSION_KEY] = cookie

    def test_no_session_on_request(self):
        msg = (
            'CSRF_USE_SESSIONS is enabled, but request.session is not set. '
            'SessionMiddleware must appear before CsrfViewMiddleware in MIDDLEWARE.'
        )
        with self.assertRaisesMessage(ImproperlyConfigured, msg):
            mw = CsrfViewMiddleware(lambda req: HttpResponse())
            mw.process_request(HttpRequest())

    def test_masked_unmasked_combinations(self):
        """
        Masked and unmasked tokens are allowed both as POST and as the
        X-CSRFToken header.
        """
        cases = [
            # Bare secrets are not allowed when CSRF_USE_SESSIONS=True.
            (MASKED_TEST_SECRET1, TEST_SECRET, None),
            (MASKED_TEST_SECRET1, MASKED_TEST_SECRET2, None),
            (MASKED_TEST_SECRET1, None, TEST_SECRET),
            (MASKED_TEST_SECRET1, None, MASKED_TEST_SECRET2),
        ]
        for args in cases:
            with self.subTest(args=args):
                cookie, post_token, meta_token = args
                req = self._get_POST_csrf_cookie_request(
                    cookie=cookie, post_token=post_token, meta_token=meta_token,
                )
                mw = CsrfViewMiddleware(token_view)
                mw.process_request(req)
                resp = mw.process_view(req, token_view, (), {})
                self.assertIsNone(resp)

    def test_process_response_get_token_used(self):
        """The ensure_csrf_cookie() decorator works without middleware."""
        req = self._get_GET_no_csrf_cookie_request()
        ensure_csrf_cookie_view(req)
        self.assertTrue(req.session.get(CSRF_SESSION_KEY, False))

    def test_session_modify(self):
        """The session isn't saved if the CSRF cookie is unchanged."""
        req = self._get_GET_no_csrf_cookie_request()
        mw = CsrfViewMiddleware(ensure_csrf_cookie_view)
        mw.process_view(req, ensure_csrf_cookie_view, (), {})
        mw(req)
        self.assertIsNotNone(req.session.get(CSRF_SESSION_KEY))
        req.session.modified = False
        mw.process_view(req, ensure_csrf_cookie_view, (), {})
        mw(req)
        self.assertFalse(req.session.modified)

    def test_ensures_csrf_cookie_with_middleware(self):
        """
        The ensure_csrf_cookie() decorator works with the CsrfViewMiddleware
        enabled.
        """
        req = self._get_GET_no_csrf_cookie_request()
        mw = CsrfViewMiddleware(ensure_csrf_cookie_view)
        mw.process_view(req, ensure_csrf_cookie_view, (), {})
        mw(req)
        self.assertTrue(req.session.get(CSRF_SESSION_KEY, False))

    def test_token_node_with_new_csrf_cookie(self):
        """
        CsrfTokenNode works when a CSRF cookie is created by the middleware
        (when one was not already present).
        """
        req = self._get_GET_no_csrf_cookie_request()
        mw = CsrfViewMiddleware(token_view)
        mw.process_view(req, token_view, (), {})
        resp = mw(req)
        csrf_cookie = req.session[CSRF_SESSION_KEY]
        self._check_token_present(resp, csrf_id=csrf_cookie)

    @override_settings(
        ALLOWED_HOSTS=['www.example.com'],
        SESSION_COOKIE_DOMAIN='.example.com',
        USE_X_FORWARDED_PORT=True,
        DEBUG=True,
    )
    def test_https_good_referer_behind_proxy(self):
        """
        A POST HTTPS request is accepted when USE_X_FORWARDED_PORT=True.
        """
        self._test_https_good_referer_behind_proxy()

    @override_settings(ALLOWED_HOSTS=['www.example.com'], SESSION_COOKIE_DOMAIN='.example.com')
    def test_https_good_referer_matches_cookie_domain(self):
        """
        A POST HTTPS request with a good referer should be accepted from a
        subdomain that's allowed by SESSION_COOKIE_DOMAIN.
        """
        self._test_https_good_referer_matches_cookie_domain()

    @override_settings(ALLOWED_HOSTS=['www.example.com'], SESSION_COOKIE_DOMAIN='.example.com')
    def test_https_good_referer_matches_cookie_domain_with_different_port(self):
        """
        A POST HTTPS request with a good referer should be accepted from a
        subdomain that's allowed by SESSION_COOKIE_DOMAIN and a non-443 port.
        """
        self._test_https_good_referer_matches_cookie_domain_with_different_port()

    @override_settings(SESSION_COOKIE_DOMAIN='.example.com', DEBUG=True)
    def test_https_reject_insecure_referer(self):
        """
        A POST HTTPS request from an insecure referer should be rejected.
        """
        req = self._get_POST_request_with_token()
        req._is_secure_override = True
        req.META['HTTP_REFERER'] = 'http://example.com/'
        req.META['SERVER_PORT'] = '443'
        mw = CsrfViewMiddleware(post_form_view)
        response = mw.process_view(req, post_form_view, (), {})
        self.assertContains(
            response,
            'Referer checking failed - Referer is insecure while host is secure.',
            status_code=403,
        )


@override_settings(ROOT_URLCONF='csrf_tests.csrf_token_error_handler_urls', DEBUG=False)
class CsrfInErrorHandlingViewsTests(SimpleTestCase):
    def test_csrf_token_on_404_stays_constant(self):
        response = self.client.get('/does not exist/')
        # The error handler returns status code 599.
        self.assertEqual(response.status_code, 599)
        token1 = response.content
        response = self.client.get('/does not exist/')
        self.assertEqual(response.status_code, 599)
        token2 = response.content
        self.assertTrue(equivalent_tokens(token1.decode('ascii'), token2.decode('ascii')))
