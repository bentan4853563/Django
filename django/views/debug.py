from __future__ import unicode_literals

import datetime
import os
import re
import sys
import types

from django.conf import settings
from django.core.urlresolvers import resolve, Resolver404
from django.http import (HttpResponse, HttpResponseNotFound, HttpRequest,
    build_request_repr)
from django.template import Template, Context, TemplateDoesNotExist
from django.template.defaultfilters import force_escape, pprint
from django.utils.datastructures import MultiValueDict
from django.utils.html import escape
from django.utils.encoding import force_bytes, smart_text
from django.utils.module_loading import import_string
from django.utils import six
from django.utils.translation import ugettext as _

HIDDEN_SETTINGS = re.compile('API|TOKEN|KEY|SECRET|PASS|SIGNATURE')

CLEANSED_SUBSTITUTE = '********************'


def linebreak_iter(template_source):
    yield 0
    p = template_source.find('\n')
    while p >= 0:
        yield p + 1
        p = template_source.find('\n', p + 1)
    yield len(template_source) + 1


class CallableSettingWrapper(object):
    """ Object to wrap callable appearing in settings

    * Not to call in the debug page (#21345).
    * Not to break the debug page if the callable forbidding to set attributes (#23070).
    """
    def __init__(self, callable_setting):
        self._wrapped = callable_setting

    def __repr__(self):
        return repr(self._wrapped)


def cleanse_setting(key, value):
    """Cleanse an individual setting key/value of sensitive content.

    If the value is a dictionary, recursively cleanse the keys in
    that dictionary.
    """
    try:
        if HIDDEN_SETTINGS.search(key):
            cleansed = CLEANSED_SUBSTITUTE
        else:
            if isinstance(value, dict):
                cleansed = dict((k, cleanse_setting(k, v)) for k, v in value.items())
            else:
                cleansed = value
    except TypeError:
        # If the key isn't regex-able, just return as-is.
        cleansed = value

    if callable(cleansed):
        # For fixing #21345 and #23070
        cleansed = CallableSettingWrapper(cleansed)

    return cleansed


def get_safe_settings():
    "Returns a dictionary of the settings module, with sensitive settings blurred out."
    settings_dict = {}
    for k in dir(settings):
        if k.isupper():
            settings_dict[k] = cleanse_setting(k, getattr(settings, k))
    return settings_dict


def technical_500_response(request, exc_type, exc_value, tb, status_code=500):
    """
    Create a technical server error response. The last three arguments are
    the values returned from sys.exc_info() and friends.
    """
    reporter = ExceptionReporter(request, exc_type, exc_value, tb)
    if request.is_ajax():
        text = reporter.get_traceback_text()
        return HttpResponse(text, status=status_code, content_type='text/plain')
    else:
        html = reporter.get_traceback_html()
        return HttpResponse(html, status=status_code, content_type='text/html')

# Cache for the default exception reporter filter instance.
default_exception_reporter_filter = None


def get_exception_reporter_filter(request):
    global default_exception_reporter_filter
    if default_exception_reporter_filter is None:
        # Load the default filter for the first time and cache it.
        default_exception_reporter_filter = import_string(
            settings.DEFAULT_EXCEPTION_REPORTER_FILTER)()
    if request:
        return getattr(request, 'exception_reporter_filter', default_exception_reporter_filter)
    else:
        return default_exception_reporter_filter


class ExceptionReporterFilter(object):
    """
    Base for all exception reporter filter classes. All overridable hooks
    contain lenient default behaviors.
    """

    def get_request_repr(self, request):
        if request is None:
            return repr(None)
        else:
            return build_request_repr(request, POST_override=self.get_post_parameters(request))

    def get_post_parameters(self, request):
        if request is None:
            return {}
        else:
            return request.POST

    def get_traceback_frame_variables(self, request, tb_frame):
        return list(six.iteritems(tb_frame.f_locals))


class SafeExceptionReporterFilter(ExceptionReporterFilter):
    """
    Use annotations made by the sensitive_post_parameters and
    sensitive_variables decorators to filter out sensitive information.
    """

    def is_active(self, request):
        """
        This filter is to add safety in production environments (i.e. DEBUG
        is False). If DEBUG is True then your site is not safe anyway.
        This hook is provided as a convenience to easily activate or
        deactivate the filter on a per request basis.
        """
        return settings.DEBUG is False

    def get_cleansed_multivaluedict(self, request, multivaluedict):
        """
        Replaces the keys in a MultiValueDict marked as sensitive with stars.
        This mitigates leaking sensitive POST parameters if something like
        request.POST['nonexistent_key'] throws an exception (#21098).
        """
        sensitive_post_parameters = getattr(request, 'sensitive_post_parameters', [])
        if self.is_active(request) and sensitive_post_parameters:
            multivaluedict = multivaluedict.copy()
            for param in sensitive_post_parameters:
                if param in multivaluedict:
                    multivaluedict[param] = CLEANSED_SUBSTITUTE
        return multivaluedict

    def get_post_parameters(self, request):
        """
        Replaces the values of POST parameters marked as sensitive with
        stars (*********).
        """
        if request is None:
            return {}
        else:
            sensitive_post_parameters = getattr(request, 'sensitive_post_parameters', [])
            if self.is_active(request) and sensitive_post_parameters:
                cleansed = request.POST.copy()
                if sensitive_post_parameters == '__ALL__':
                    # Cleanse all parameters.
                    for k, v in cleansed.items():
                        cleansed[k] = CLEANSED_SUBSTITUTE
                    return cleansed
                else:
                    # Cleanse only the specified parameters.
                    for param in sensitive_post_parameters:
                        if param in cleansed:
                            cleansed[param] = CLEANSED_SUBSTITUTE
                    return cleansed
            else:
                return request.POST

    def cleanse_special_types(self, request, value):
        if isinstance(value, HttpRequest):
            # Cleanse the request's POST parameters.
            value = self.get_request_repr(value)
        elif isinstance(value, MultiValueDict):
            # Cleanse MultiValueDicts (request.POST is the one we usually care about)
            value = self.get_cleansed_multivaluedict(request, value)
        return value

    def get_traceback_frame_variables(self, request, tb_frame):
        """
        Replaces the values of variables marked as sensitive with
        stars (*********).
        """
        # Loop through the frame's callers to see if the sensitive_variables
        # decorator was used.
        current_frame = tb_frame.f_back
        sensitive_variables = None
        while current_frame is not None:
            if (current_frame.f_code.co_name == 'sensitive_variables_wrapper'
                    and 'sensitive_variables_wrapper' in current_frame.f_locals):
                # The sensitive_variables decorator was used, so we take note
                # of the sensitive variables' names.
                wrapper = current_frame.f_locals['sensitive_variables_wrapper']
                sensitive_variables = getattr(wrapper, 'sensitive_variables', None)
                break
            current_frame = current_frame.f_back

        cleansed = {}
        if self.is_active(request) and sensitive_variables:
            if sensitive_variables == '__ALL__':
                # Cleanse all variables
                for name, value in tb_frame.f_locals.items():
                    cleansed[name] = CLEANSED_SUBSTITUTE
            else:
                # Cleanse specified variables
                for name, value in tb_frame.f_locals.items():
                    if name in sensitive_variables:
                        value = CLEANSED_SUBSTITUTE
                    else:
                        value = self.cleanse_special_types(request, value)
                    cleansed[name] = value
        else:
            # Potentially cleanse the request and any MultiValueDicts if they
            # are one of the frame variables.
            for name, value in tb_frame.f_locals.items():
                cleansed[name] = self.cleanse_special_types(request, value)

        if (tb_frame.f_code.co_name == 'sensitive_variables_wrapper'
                and 'sensitive_variables_wrapper' in tb_frame.f_locals):
            # For good measure, obfuscate the decorated function's arguments in
            # the sensitive_variables decorator's frame, in case the variables
            # associated with those arguments were meant to be obfuscated from
            # the decorated function's frame.
            cleansed['func_args'] = CLEANSED_SUBSTITUTE
            cleansed['func_kwargs'] = CLEANSED_SUBSTITUTE

        return cleansed.items()


class ExceptionReporter(object):
    """
    A class to organize and coordinate reporting on exceptions.
    """
    def __init__(self, request, exc_type, exc_value, tb, is_email=False):
        self.request = request
        self.filter = get_exception_reporter_filter(self.request)
        self.exc_type = exc_type
        self.exc_value = exc_value
        self.tb = tb
        self.is_email = is_email

        self.template_info = None
        self.template_does_not_exist = False
        self.loader_debug_info = None

        # Handle deprecated string exceptions
        if isinstance(self.exc_type, six.string_types):
            self.exc_value = Exception('Deprecated String Exception: %r' % self.exc_type)
            self.exc_type = type(self.exc_value)

    def format_path_status(self, path):
        if not os.path.exists(path):
            return "File does not exist"
        if not os.path.isfile(path):
            return "Not a file"
        if not os.access(path, os.R_OK):
            return "File is not readable"
        return "File exists"

    def get_traceback_data(self):
        """Return a dictionary containing traceback information."""

        if self.exc_type and issubclass(self.exc_type, TemplateDoesNotExist):
            from django.template.loader import template_source_loaders
            self.template_does_not_exist = True
            self.loader_debug_info = []
            # If the template_source_loaders haven't been populated yet, you need
            # to provide an empty list for this for loop to not fail.
            if template_source_loaders is None:
                template_source_loaders = []
            for loader in template_source_loaders:
                try:
                    source_list_func = loader.get_template_sources
                    # NOTE: This assumes exc_value is the name of the template that
                    # the loader attempted to load.
                    template_list = [{
                        'name': t,
                        'status': self.format_path_status(t),
                    } for t in source_list_func(str(self.exc_value))]
                except AttributeError:
                    template_list = []
                loader_name = loader.__module__ + '.' + loader.__class__.__name__
                self.loader_debug_info.append({
                    'loader': loader_name,
                    'templates': template_list,
                })
        if (settings.TEMPLATE_DEBUG and
                hasattr(self.exc_value, 'django_template_source')):
            self.get_template_exception_info()

        frames = self.get_traceback_frames()
        for i, frame in enumerate(frames):
            if 'vars' in frame:
                frame_vars = []
                for k, v in frame['vars']:
                    v = pprint(v)
                    # The force_escape filter assume unicode, make sure that works
                    if isinstance(v, six.binary_type):
                        v = v.decode('utf-8', 'replace')  # don't choke on non-utf-8 input
                    # Trim large blobs of data
                    if len(v) > 4096:
                        v = '%s... <trimmed %d bytes string>' % (v[0:4096], len(v))
                    frame_vars.append((k, force_escape(v)))
                frame['vars'] = frame_vars
            frames[i] = frame

        unicode_hint = ''
        if self.exc_type and issubclass(self.exc_type, UnicodeError):
            start = getattr(self.exc_value, 'start', None)
            end = getattr(self.exc_value, 'end', None)
            if start is not None and end is not None:
                unicode_str = self.exc_value.args[1]
                unicode_hint = smart_text(unicode_str[max(start - 5, 0):min(end + 5, len(unicode_str))], 'ascii', errors='replace')
        from django import get_version
        c = {
            'is_email': self.is_email,
            'unicode_hint': unicode_hint,
            'frames': frames,
            'request': self.request,
            'filtered_POST': self.filter.get_post_parameters(self.request),
            'settings': get_safe_settings(),
            'sys_executable': sys.executable,
            'sys_version_info': '%d.%d.%d' % sys.version_info[0:3],
            'server_time': datetime.datetime.now(),
            'django_version_info': get_version(),
            'sys_path': sys.path,
            'template_info': self.template_info,
            'template_does_not_exist': self.template_does_not_exist,
            'loader_debug_info': self.loader_debug_info,
        }
        # Check whether exception info is available
        if self.exc_type:
            c['exception_type'] = self.exc_type.__name__
        if self.exc_value:
            c['exception_value'] = smart_text(self.exc_value, errors='replace')
        if frames:
            c['lastframe'] = frames[-1]
        return c

    def get_traceback_html(self):
        "Return HTML version of debug 500 HTTP error page."
        t = Template(TECHNICAL_500_TEMPLATE, name='Technical 500 template')
        c = Context(self.get_traceback_data(), use_l10n=False)
        return t.render(c)

    def get_traceback_text(self):
        "Return plain text version of debug 500 HTTP error page."
        t = Template(TECHNICAL_500_TEXT_TEMPLATE, name='Technical 500 template')
        c = Context(self.get_traceback_data(), autoescape=False, use_l10n=False)
        return t.render(c)

    def get_template_exception_info(self):
        origin, (start, end) = self.exc_value.django_template_source
        template_source = origin.reload()
        context_lines = 10
        line = 0
        upto = 0
        source_lines = []
        before = during = after = ""
        for num, next in enumerate(linebreak_iter(template_source)):
            if start >= upto and end <= next:
                line = num
                before = escape(template_source[upto:start])
                during = escape(template_source[start:end])
                after = escape(template_source[end:next])
            source_lines.append((num, escape(template_source[upto:next])))
            upto = next
        total = len(source_lines)

        top = max(1, line - context_lines)
        bottom = min(total, line + 1 + context_lines)

        # In some rare cases, exc_value.args might be empty.
        try:
            message = self.exc_value.args[0]
        except IndexError:
            message = '(Could not get exception message)'

        self.template_info = {
            'message': message,
            'source_lines': source_lines[top:bottom],
            'before': before,
            'during': during,
            'after': after,
            'top': top,
            'bottom': bottom,
            'total': total,
            'line': line,
            'name': origin.name,
        }

    def _get_lines_from_file(self, filename, lineno, context_lines, loader=None, module_name=None):
        """
        Returns context_lines before and after lineno from file.
        Returns (pre_context_lineno, pre_context, context_line, post_context).
        """
        source = None
        if loader is not None and hasattr(loader, "get_source"):
            try:
                source = loader.get_source(module_name)
            except ImportError:
                pass
            if source is not None:
                source = source.splitlines()
        if source is None:
            try:
                with open(filename, 'rb') as fp:
                    source = fp.read().splitlines()
            except (OSError, IOError):
                pass
        if source is None:
            return None, [], None, []

        # If we just read the source from a file, or if the loader did not
        # apply tokenize.detect_encoding to decode the source into a Unicode
        # string, then we should do that ourselves.
        if isinstance(source[0], six.binary_type):
            encoding = 'ascii'
            for line in source[:2]:
                # File coding may be specified. Match pattern from PEP-263
                # (http://www.python.org/dev/peps/pep-0263/)
                match = re.search(br'coding[:=]\s*([-\w.]+)', line)
                if match:
                    encoding = match.group(1).decode('ascii')
                    break
            source = [six.text_type(sline, encoding, 'replace') for sline in source]

        lower_bound = max(0, lineno - context_lines)
        upper_bound = lineno + context_lines

        pre_context = source[lower_bound:lineno]
        context_line = source[lineno]
        post_context = source[lineno + 1:upper_bound]

        return lower_bound, pre_context, context_line, post_context

    def get_traceback_frames(self):
        frames = []
        tb = self.tb
        while tb is not None:
            # Support for __traceback_hide__ which is used by a few libraries
            # to hide internal frames.
            if tb.tb_frame.f_locals.get('__traceback_hide__'):
                tb = tb.tb_next
                continue
            filename = tb.tb_frame.f_code.co_filename
            function = tb.tb_frame.f_code.co_name
            lineno = tb.tb_lineno - 1
            loader = tb.tb_frame.f_globals.get('__loader__')
            module_name = tb.tb_frame.f_globals.get('__name__') or ''
            pre_context_lineno, pre_context, context_line, post_context = self._get_lines_from_file(filename, lineno, 7, loader, module_name)
            if pre_context_lineno is not None:
                frames.append({
                    'tb': tb,
                    'type': 'django' if module_name.startswith('django.') else 'user',
                    'filename': filename,
                    'function': function,
                    'lineno': lineno + 1,
                    'vars': self.filter.get_traceback_frame_variables(self.request, tb.tb_frame),
                    'id': id(tb),
                    'pre_context': pre_context,
                    'context_line': context_line,
                    'post_context': post_context,
                    'pre_context_lineno': pre_context_lineno + 1,
                })
            tb = tb.tb_next

        return frames

    def format_exception(self):
        """
        Return the same data as from traceback.format_exception.
        """
        import traceback
        frames = self.get_traceback_frames()
        tb = [(f['filename'], f['lineno'], f['function'], f['context_line']) for f in frames]
        list = ['Traceback (most recent call last):\n']
        list += traceback.format_list(tb)
        list += traceback.format_exception_only(self.exc_type, self.exc_value)
        return list


def technical_404_response(request, exception):
    "Create a technical 404 error response. The exception should be the Http404."
    try:
        error_url = exception.args[0]['path']
    except (IndexError, TypeError, KeyError):
        error_url = request.path_info[1:]  # Trim leading slash

    try:
        tried = exception.args[0]['tried']
    except (IndexError, TypeError, KeyError):
        tried = []
    else:
        if (not tried                           # empty URLconf
            or (request.path == '/'
                and len(tried) == 1             # default URLconf
                and len(tried[0]) == 1
                and getattr(tried[0][0], 'app_name', '') == getattr(tried[0][0], 'namespace', '') == 'admin')):
            return default_urlconf(request)

    urlconf = getattr(request, 'urlconf', settings.ROOT_URLCONF)
    if isinstance(urlconf, types.ModuleType):
        urlconf = urlconf.__name__

    caller = ''
    try:
        resolver_match = resolve(request.path)
    except Resolver404:
        pass
    else:
        obj = resolver_match.func

        if hasattr(obj, '__name__'):
            caller = obj.__name__
        elif hasattr(obj, '__class__') and hasattr(obj.__class__, '__name__'):
            caller = obj.__class__.__name__

        if hasattr(obj, '__module__'):
            module = obj.__module__
            caller = '%s.%s' % (module, caller)

    t = Template(TECHNICAL_404_TEMPLATE, name='Technical 404 template')
    c = Context({
        'urlconf': urlconf,
        'root_urlconf': settings.ROOT_URLCONF,
        'request_path': error_url,
        'urlpatterns': tried,
        'reason': force_bytes(exception, errors='replace'),
        'request': request,
        'settings': get_safe_settings(),
        'raising_view_name': caller,
    })
    return HttpResponseNotFound(t.render(c), content_type='text/html')


def default_urlconf(request):
    "Create an empty URLconf 404 error response."
    t = Template(DEFAULT_URLCONF_TEMPLATE, name='Default URLconf template')

    c = Context({
        "title": _("Welcome to Django"),
        "heading": _("It worked!"),
        "subheading": _("Congratulations on your first Django-powered page."),
        "instructions": _("Of course, you haven't actually done any work yet. "
            "Next, start your first app by running <code>python manage.py startapp [app_label]</code>."),
        "explanation": _("You're seeing this message because you have <code>DEBUG = True</code> in your "
            "Django settings file and you haven't configured any URLs. Get to work!"),
    })

    return HttpResponse(t.render(c), content_type='text/html')

#
# Templates are embedded in the file so that we know the error handler will
# always work even if the template loader is broken.
#

TECHNICAL_500_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="content-type" content="text/html; charset=utf-8">
  <meta name="robots" content="NONE,NOARCHIVE">
  <title>{% if exception_type %}{{ exception_type }}{% else %}Report{% endif %}{% if request %} at {{ request.path_info|escape }}{% endif %}</title>
  <style type="text/css">
    html * { padding:0; margin:0; }
    body * { padding:10px 20px; }
    body * * { padding:0; }
    body { font:small sans-serif; }
    body>div { border-bottom:1px solid #ddd; }
    h1 { font-weight:normal; }
    h2 { margin-bottom:.8em; }
    h2 span { font-size:80%; color:#666; font-weight:normal; }
    h3 { margin:1em 0 .5em 0; }
    h4 { margin:0 0 .5em 0; font-weight: normal; }
    code, pre { font-size: 100%; white-space: pre-wrap; }
    table { border:1px solid #ccc; border-collapse: collapse; width:100%; background:white; }
    tbody td, tbody th { vertical-align:top; padding:2px 3px; }
    thead th { padding:1px 6px 1px 3px; background:#fefefe; text-align:left; font-weight:normal; font-size:11px; border:1px solid #ddd; }
    tbody th { width:12em; text-align:right; color:#666; padding-right:.5em; }
    table.vars { margin:5px 0 2px 40px; }
    table.vars td, table.req td { font-family:monospace; }
    table td.code { width:100%; }
    table td.code pre { overflow:hidden; }
    table.source th { color:#666; }
    table.source td { font-family:monospace; white-space:pre; border-bottom:1px solid #eee; }
    ul.traceback { list-style-type:none; color: #222; }
    ul.traceback li.frame { padding-bottom:1em; color:#666; }
    ul.traceback li.user { background-color:#e0e0e0; color:#000 }
    div.context { padding:10px 0; overflow:hidden; }
    div.context ol { padding-left:30px; margin:0 10px; list-style-position: inside; }
    div.context ol li { font-family:monospace; white-space:pre; color:#777; cursor:pointer; }
    div.context ol li pre { display:inline; }
    div.context ol.context-line li { color:#505050; background-color:#dfdfdf; }
    div.context ol.context-line li span { position:absolute; right:32px; }
    .user div.context ol.context-line li { background-color:#bbb; color:#000; }
    .user div.context ol li { color:#666; }
    div.commands { margin-left: 40px; }
    div.commands a { color:#555; text-decoration:none; }
    .user div.commands a { color: black; }
    #summary { background: #ffc; }
    #summary h2 { font-weight: normal; color: #666; }
    #explanation { background:#eee; }
    #template, #template-not-exist { background:#f6f6f6; }
    #template-not-exist ul { margin: 0 0 0 20px; }
    #unicode-hint { background:#eee; }
    #traceback { background:#eee; }
    #requestinfo { background:#f6f6f6; padding-left:120px; }
    #summary table { border:none; background:transparent; }
    #requestinfo h2, #requestinfo h3 { position:relative; margin-left:-100px; }
    #requestinfo h3 { margin-bottom:-1em; }
    .error { background: #ffc; }
    .specific { color:#cc3300; font-weight:bold; }
    h2 span.commands { font-size:.7em;}
    span.commands a:link {color:#5E5694;}
    pre.exception_value { font-family: sans-serif; color: #666; font-size: 1.5em; margin: 10px 0 10px 0; }
  </style>
  {% if not is_email %}
  <script type="text/javascript">
  //<!--
    function getElementsByClassName(oElm, strTagName, strClassName){
        // Written by Jonathan Snook, http://www.snook.ca/jon; Add-ons by Robert Nyman, http://www.robertnyman.com
        var arrElements = (strTagName == "*" && document.all)? document.all :
        oElm.getElementsByTagName(strTagName);
        var arrReturnElements = new Array();
        strClassName = strClassName.replace(/\-/g, "\\-");
        var oRegExp = new RegExp("(^|\\s)" + strClassName + "(\\s|$)");
        var oElement;
        for(var i=0; i<arrElements.length; i++){
            oElement = arrElements[i];
            if(oRegExp.test(oElement.className)){
                arrReturnElements.push(oElement);
            }
        }
        return (arrReturnElements)
    }
    function hideAll(elems) {
      for (var e = 0; e < elems.length; e++) {
        elems[e].style.display = 'none';
      }
    }
    window.onload = function() {
      hideAll(getElementsByClassName(document, 'table', 'vars'));
      hideAll(getElementsByClassName(document, 'ol', 'pre-context'));
      hideAll(getElementsByClassName(document, 'ol', 'post-context'));
      hideAll(getElementsByClassName(document, 'div', 'pastebin'));
    }
    function toggle() {
      for (var i = 0; i < arguments.length; i++) {
        var e = document.getElementById(arguments[i]);
        if (e) {
          e.style.display = e.style.display == 'none' ? 'block': 'none';
        }
      }
      return false;
    }
    function varToggle(link, id) {
      toggle('v' + id);
      var s = link.getElementsByTagName('span')[0];
      var uarr = String.fromCharCode(0x25b6);
      var darr = String.fromCharCode(0x25bc);
      s.innerHTML = s.innerHTML == uarr ? darr : uarr;
      return false;
    }
    function switchPastebinFriendly(link) {
      s1 = "Switch to copy-and-paste view";
      s2 = "Switch back to interactive view";
      link.innerHTML = link.innerHTML == s1 ? s2: s1;
      toggle('browserTraceback', 'pastebinTraceback');
      return false;
    }
    //-->
  </script>
  {% endif %}
</head>
<body>
<div id="summary">
  <h1>{% if exception_type %}{{ exception_type }}{% else %}Report{% endif %}{% if request %} at {{ request.path_info|escape }}{% endif %}</h1>
  <pre class="exception_value">{% if exception_value %}{{ exception_value|force_escape }}{% else %}No exception message supplied{% endif %}</pre>
  <table class="meta">
{% if request %}
    <tr>
      <th>Request Method:</th>
      <td>{{ request.META.REQUEST_METHOD }}</td>
    </tr>
    <tr>
      <th>Request URL:</th>
      <td>{{ request.build_absolute_uri|escape }}</td>
    </tr>
{% endif %}
    <tr>
      <th>Django Version:</th>
      <td>{{ django_version_info }}</td>
    </tr>
{% if exception_type %}
    <tr>
      <th>Exception Type:</th>
      <td>{{ exception_type }}</td>
    </tr>
{% endif %}
{% if exception_type and exception_value %}
    <tr>
      <th>Exception Value:</th>
      <td><pre>{{ exception_value|force_escape }}</pre></td>
    </tr>
{% endif %}
{% if lastframe %}
    <tr>
      <th>Exception Location:</th>
      <td>{{ lastframe.filename|escape }} in {{ lastframe.function|escape }}, line {{ lastframe.lineno }}</td>
    </tr>
{% endif %}
    <tr>
      <th>Python Executable:</th>
      <td>{{ sys_executable|escape }}</td>
    </tr>
    <tr>
      <th>Python Version:</th>
      <td>{{ sys_version_info }}</td>
    </tr>
    <tr>
      <th>Python Path:</th>
      <td><pre>{{ sys_path|pprint }}</pre></td>
    </tr>
    <tr>
      <th>Server time:</th>
      <td>{{server_time|date:"r"}}</td>
    </tr>
  </table>
</div>
{% if unicode_hint %}
<div id="unicode-hint">
    <h2>Unicode error hint</h2>
    <p>The string that could not be encoded/decoded was: <strong>{{ unicode_hint|force_escape }}</strong></p>
</div>
{% endif %}
{% if template_does_not_exist %}
<div id="template-not-exist">
    <h2>Template-loader postmortem</h2>
    {% if loader_debug_info %}
        <p>Django tried loading these templates, in this order:</p>
        <ul>
        {% for loader in loader_debug_info %}
            <li>Using loader <code>{{ loader.loader }}</code>:
                <ul>
                {% for t in loader.templates %}<li><code>{{ t.name }}</code> ({{ t.status }})</li>{% endfor %}
                </ul>
            </li>
        {% endfor %}
        </ul>
    {% else %}
        <p>Django couldn't find any templates because your <code>TEMPLATE_LOADERS</code> setting is empty!</p>
    {% endif %}
</div>
{% endif %}
{% if template_info %}
<div id="template">
   <h2>Error during template rendering</h2>
   <p>In template <code>{{ template_info.name }}</code>, error at line <strong>{{ template_info.line }}</strong></p>
   <h3>{{ template_info.message }}</h3>
   <table class="source{% if template_info.top %} cut-top{% endif %}{% ifnotequal template_info.bottom template_info.total %} cut-bottom{% endifnotequal %}">
   {% for source_line in template_info.source_lines %}
   {% ifequal source_line.0 template_info.line %}
       <tr class="error"><th>{{ source_line.0 }}</th>
       <td>{{ template_info.before }}<span class="specific">{{ template_info.during }}</span>{{ template_info.after }}</td></tr>
   {% else %}
      <tr><th>{{ source_line.0 }}</th>
      <td>{{ source_line.1 }}</td></tr>
   {% endifequal %}
   {% endfor %}
   </table>
</div>
{% endif %}
{% if frames %}
<div id="traceback">
  <h2>Traceback <span class="commands">{% if not is_email %}<a href="#" onclick="return switchPastebinFriendly(this);">Switch to copy-and-paste view</a></span>{% endif %}</h2>
  {% autoescape off %}
  <div id="browserTraceback">
    <ul class="traceback">
      {% for frame in frames %}
        <li class="frame {{ frame.type }}">
          <code>{{ frame.filename|escape }}</code> in <code>{{ frame.function|escape }}</code>

          {% if frame.context_line %}
            <div class="context" id="c{{ frame.id }}">
              {% if frame.pre_context and not is_email %}
                <ol start="{{ frame.pre_context_lineno }}" class="pre-context" id="pre{{ frame.id }}">{% for line in frame.pre_context %}<li onclick="toggle('pre{{ frame.id }}', 'post{{ frame.id }}')"><pre>{{ line|escape }}</pre></li>{% endfor %}</ol>
              {% endif %}
              <ol start="{{ frame.lineno }}" class="context-line"><li onclick="toggle('pre{{ frame.id }}', 'post{{ frame.id }}')"><pre>{{ frame.context_line|escape }}</pre>{% if not is_email %} <span>...</span>{% endif %}</li></ol>
              {% if frame.post_context and not is_email  %}
                <ol start='{{ frame.lineno|add:"1" }}' class="post-context" id="post{{ frame.id }}">{% for line in frame.post_context %}<li onclick="toggle('pre{{ frame.id }}', 'post{{ frame.id }}')"><pre>{{ line|escape }}</pre></li>{% endfor %}</ol>
              {% endif %}
            </div>
          {% endif %}

          {% if frame.vars %}
            <div class="commands">
                {% if is_email %}
                    <h2>Local Vars</h2>
                {% else %}
                    <a href="#" onclick="return varToggle(this, '{{ frame.id }}')"><span>&#x25b6;</span> Local vars</a>
                {% endif %}
            </div>
            <table class="vars" id="v{{ frame.id }}">
              <thead>
                <tr>
                  <th>Variable</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {% for var in frame.vars|dictsort:"0" %}
                  <tr>
                    <td>{{ var.0|force_escape }}</td>
                    <td class="code"><pre>{{ var.1 }}</pre></td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          {% endif %}
        </li>
      {% endfor %}
    </ul>
  </div>
  {% endautoescape %}
  <form action="http://dpaste.com/" name="pasteform" id="pasteform" method="post">
{% if not is_email %}
  <div id="pastebinTraceback" class="pastebin">
    <input type="hidden" name="language" value="PythonConsole">
    <input type="hidden" name="title" value="{{ exception_type|escape }}{% if request %} at {{ request.path_info|escape }}{% endif %}">
    <input type="hidden" name="source" value="Django Dpaste Agent">
    <input type="hidden" name="poster" value="Django">
    <textarea name="content" id="traceback_area" cols="140" rows="25">
Environment:

{% if request %}
Request Method: {{ request.META.REQUEST_METHOD }}
Request URL: {{ request.build_absolute_uri|escape }}
{% endif %}
Django Version: {{ django_version_info }}
Python Version: {{ sys_version_info }}
Installed Applications:
{{ settings.INSTALLED_APPS|pprint }}
Installed Middleware:
{{ settings.MIDDLEWARE_CLASSES|pprint }}

{% if template_does_not_exist %}Template Loader Error:
{% if loader_debug_info %}Django tried loading these templates, in this order:
{% for loader in loader_debug_info %}Using loader {{ loader.loader }}:
{% for t in loader.templates %}{{ t.name }} ({{ t.status }})
{% endfor %}{% endfor %}
{% else %}Django couldn't find any templates because your TEMPLATE_LOADERS setting is empty!
{% endif %}
{% endif %}{% if template_info %}
Template error:
In template {{ template_info.name }}, error at line {{ template_info.line }}
   {{ template_info.message }}{% for source_line in template_info.source_lines %}{% ifequal source_line.0 template_info.line %}
   {{ source_line.0 }} : {{ template_info.before }} {{ template_info.during }} {{ template_info.after }}
{% else %}
   {{ source_line.0 }} : {{ source_line.1 }}
{% endifequal %}{% endfor %}{% endif %}
Traceback:
{% for frame in frames %}File "{{ frame.filename|escape }}" in {{ frame.function|escape }}
{% if frame.context_line %}  {{ frame.lineno }}. {{ frame.context_line|escape }}{% endif %}
{% endfor %}
Exception Type: {{ exception_type|escape }}{% if request %} at {{ request.path_info|escape }}{% endif %}
Exception Value: {{ exception_value|force_escape }}
</textarea>
  <br><br>
  <input type="submit" value="Share this traceback on a public Web site">
  </div>
</form>
</div>
{% endif %}
{% endif %}

<div id="requestinfo">
  <h2>Request information</h2>

{% if request %}
  <h3 id="get-info">GET</h3>
  {% if request.GET %}
    <table class="req">
      <thead>
        <tr>
          <th>Variable</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>
        {% for var in request.GET.items %}
          <tr>
            <td>{{ var.0 }}</td>
            <td class="code"><pre>{{ var.1|pprint }}</pre></td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% else %}
    <p>No GET data</p>
  {% endif %}

  <h3 id="post-info">POST</h3>
  {% if filtered_POST %}
    <table class="req">
      <thead>
        <tr>
          <th>Variable</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>
        {% for var in filtered_POST.items %}
          <tr>
            <td>{{ var.0 }}</td>
            <td class="code"><pre>{{ var.1|pprint }}</pre></td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% else %}
    <p>No POST data</p>
  {% endif %}
  <h3 id="files-info">FILES</h3>
  {% if request.FILES %}
    <table class="req">
        <thead>
            <tr>
                <th>Variable</th>
                <th>Value</th>
            </tr>
        </thead>
        <tbody>
            {% for var in request.FILES.items %}
                <tr>
                    <td>{{ var.0 }}</td>
                    <td class="code"><pre>{{ var.1|pprint }}</pre></td>
                </tr>
            {% endfor %}
        </tbody>
    </table>
  {% else %}
    <p>No FILES data</p>
  {% endif %}


  <h3 id="cookie-info">COOKIES</h3>
  {% if request.COOKIES %}
    <table class="req">
      <thead>
        <tr>
          <th>Variable</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>
        {% for var in request.COOKIES.items %}
          <tr>
            <td>{{ var.0 }}</td>
            <td class="code"><pre>{{ var.1|pprint }}</pre></td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% else %}
    <p>No cookie data</p>
  {% endif %}

  <h3 id="meta-info">META</h3>
  <table class="req">
    <thead>
      <tr>
        <th>Variable</th>
        <th>Value</th>
      </tr>
    </thead>
    <tbody>
      {% for var in request.META.items|dictsort:"0" %}
        <tr>
          <td>{{ var.0 }}</td>
          <td class="code"><pre>{{ var.1|pprint }}</pre></td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
{% else %}
  <p>Request data not supplied</p>
{% endif %}

  <h3 id="settings-info">Settings</h3>
  <h4>Using settings module <code>{{ settings.SETTINGS_MODULE }}</code></h4>
  <table class="req">
    <thead>
      <tr>
        <th>Setting</th>
        <th>Value</th>
      </tr>
    </thead>
    <tbody>
      {% for var in settings.items|dictsort:"0" %}
        <tr>
          <td>{{ var.0 }}</td>
          <td class="code"><pre>{{ var.1|pprint }}</pre></td>
        </tr>
      {% endfor %}
    </tbody>
  </table>

</div>
{% if not is_email %}
  <div id="explanation">
    <p>
      You're seeing this error because you have <code>DEBUG = True</code> in your
      Django settings file. Change that to <code>False</code>, and Django will
      display a standard page generated by the handler for this status code.
    </p>
  </div>
{% endif %}
</body>
</html>
"""

TECHNICAL_500_TEXT_TEMPLATE = """{% firstof exception_type 'Report' %}{% if request %} at {{ request.path_info }}{% endif %}
{% firstof exception_value 'No exception message supplied' %}
{% if request %}
Request Method: {{ request.META.REQUEST_METHOD }}
Request URL: {{ request.build_absolute_uri }}{% endif %}
Django Version: {{ django_version_info }}
Python Executable: {{ sys_executable }}
Python Version: {{ sys_version_info }}
Python Path: {{ sys_path }}
Server time: {{server_time|date:"r"}}
Installed Applications:
{{ settings.INSTALLED_APPS|pprint }}
Installed Middleware:
{{ settings.MIDDLEWARE_CLASSES|pprint }}
{% if template_does_not_exist %}Template loader Error:
{% if loader_debug_info %}Django tried loading these templates, in this order:
{% for loader in loader_debug_info %}Using loader {{ loader.loader }}:
{% for t in loader.templates %}{{ t.name }} ({{ t.status }})
{% endfor %}{% endfor %}
{% else %}Django couldn't find any templates because your TEMPLATE_LOADERS setting is empty!
{% endif %}
{% endif %}{% if template_info %}
Template error:
In template {{ template_info.name }}, error at line {{ template_info.line }}
   {{ template_info.message }}{% for source_line in template_info.source_lines %}{% ifequal source_line.0 template_info.line %}
   {{ source_line.0 }} : {{ template_info.before }} {{ template_info.during }} {{ template_info.after }}
{% else %}
   {{ source_line.0 }} : {{ source_line.1 }}
   {% endifequal %}{% endfor %}{% endif %}{% if frames %}
Traceback:
{% for frame in frames %}File "{{ frame.filename }}" in {{ frame.function }}
{% if frame.context_line %}  {{ frame.lineno }}. {{ frame.context_line }}{% endif %}
{% endfor %}
{% if exception_type %}Exception Type: {{ exception_type }}{% if request %} at {{ request.path_info }}{% endif %}
{% if exception_value %}Exception Value: {{ exception_value }}{% endif %}{% endif %}{% endif %}
{% if request %}Request information:
GET:{% for k, v in request.GET.items %}
{{ k }} = {{ v|stringformat:"r" }}{% empty %} No GET data{% endfor %}

POST:{% for k, v in filtered_POST.items %}
{{ k }} = {{ v|stringformat:"r" }}{% empty %} No POST data{% endfor %}

FILES:{% for k, v in request.FILES.items %}
{{ k }} = {{ v|stringformat:"r" }}{% empty %} No FILES data{% endfor %}

COOKIES:{% for k, v in request.COOKIES.items %}
{{ k }} = {{ v|stringformat:"r" }}{% empty %} No cookie data{% endfor %}

META:{% for k, v in request.META.items|dictsort:"0" %}
{{ k }} = {{ v|stringformat:"r" }}{% endfor %}
{% else %}Request data not supplied
{% endif %}
Settings:
Using settings module {{ settings.SETTINGS_MODULE }}{% for k, v in settings.items|dictsort:"0" %}
{{ k }} = {{ v|stringformat:"r" }}{% endfor %}

You're seeing this error because you have DEBUG = True in your
Django settings file. Change that to False, and Django will
display a standard page generated by the handler for this status code.
"""

TECHNICAL_404_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="content-type" content="text/html; charset=utf-8">
  <title>Page not found at {{ request.path_info|escape }}</title>
  <meta name="robots" content="NONE,NOARCHIVE">
  <style type="text/css">
    html * { padding:0; margin:0; }
    body * { padding:10px 20px; }
    body * * { padding:0; }
    body { font:small sans-serif; background:#eee; }
    body>div { border-bottom:1px solid #ddd; }
    h1 { font-weight:normal; margin-bottom:.4em; }
    h1 span { font-size:60%; color:#666; font-weight:normal; }
    table { border:none; border-collapse: collapse; width:100%; }
    td, th { vertical-align:top; padding:2px 3px; }
    th { width:12em; text-align:right; color:#666; padding-right:.5em; }
    #info { background:#f6f6f6; }
    #info ol { margin: 0.5em 4em; }
    #info ol li { font-family: monospace; }
    #summary { background: #ffc; }
    #explanation { background:#eee; border-bottom: 0px none; }
  </style>
</head>
<body>
  <div id="summary">
    <h1>Page not found <span>(404)</span></h1>
    <table class="meta">
      <tr>
        <th>Request Method:</th>
        <td>{{ request.META.REQUEST_METHOD }}</td>
      </tr>
      <tr>
        <th>Request URL:</th>
        <td>{{ request.build_absolute_uri|escape }}</td>
      </tr>
      {% if raising_view_name %}
      <tr>
        <th>Raised by:</th>
        <td>{{ raising_view_name }}</td>
      </tr>
      {% endif %}
    </table>
  </div>
  <div id="info">
    {% if urlpatterns %}
      <p>
      Using the URLconf defined in <code>{{ urlconf }}</code>,
      Django tried these URL patterns, in this order:
      </p>
      <ol>
        {% for pattern in urlpatterns %}
          <li>
            {% for pat in pattern %}
                {{ pat.regex.pattern }}
                {% if forloop.last and pat.name %}[name='{{ pat.name }}']{% endif %}
            {% endfor %}
          </li>
        {% endfor %}
      </ol>
      <p>The current URL, <code>{{ request_path|escape }}</code>, didn't match any of these.</p>
    {% else %}
      <p>{{ reason }}</p>
    {% endif %}
  </div>

  <div id="explanation">
    <p>
      You're seeing this error because you have <code>DEBUG = True</code> in
      your Django settings file. Change that to <code>False</code>, and Django
      will display a standard 404 page.
    </p>
  </div>
</body>
</html>
"""

DEFAULT_URLCONF_TEMPLATE = """
<!DOCTYPE html>
<html lang="en"><head>
  <meta http-equiv="content-type" content="text/html; charset=utf-8">
  <meta name="robots" content="NONE,NOARCHIVE"><title>{{ title }}</title>
  <style type="text/css">
    html * { padding:0; margin:0; }
    body * { padding:10px 20px; }
    body * * { padding:0; }
    body { font:small sans-serif; }
    body>div { border-bottom:1px solid #ddd; }
    h1 { font-weight:normal; }
    h2 { margin-bottom:.8em; }
    h2 span { font-size:80%; color:#666; font-weight:normal; }
    h3 { margin:1em 0 .5em 0; }
    h4 { margin:0 0 .5em 0; font-weight: normal; }
    table { border:1px solid #ccc; border-collapse: collapse; width:100%; background:white; }
    tbody td, tbody th { vertical-align:top; padding:2px 3px; }
    thead th { padding:1px 6px 1px 3px; background:#fefefe; text-align:left; font-weight:normal; font-size:11px; border:1px solid #ddd; }
    tbody th { width:12em; text-align:right; color:#666; padding-right:.5em; }
    #summary { background: #e0ebff; }
    #summary h2 { font-weight: normal; color: #666; }
    #explanation { background:#eee; }
    #instructions { background:#f6f6f6; }
    #summary table { border:none; background:transparent; }
  </style>
</head>

<body>
<div id="summary">
  <h1>{{ heading }}</h1>
  <h2>{{ subheading }}</h2>
</div>

<div id="instructions">
  <p>
    {{ instructions|safe }}
  </p>
</div>

<div id="explanation">
  <p>
    {{ explanation|safe }}
  </p>
</div>
</body></html>
"""
