import glob
import os
import re
import sys
from functools import total_ordering
from itertools import dropwhile

import django
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.temp import NamedTemporaryFile
from django.core.management.base import BaseCommand, CommandError
from django.core.management.utils import (
    find_command, handle_extensions, is_ignored_path, popen_wrapper,
)
from django.utils.encoding import DEFAULT_LOCALE_ENCODING
from django.utils.functional import cached_property
from django.utils.jslex import prepare_js_for_gettext
from django.utils.regex_helper import _lazy_re_compile
from django.utils.text import get_text_list
from django.utils.translation import templatize

plural_forms_re = _lazy_re_compile(r'^(?P<value>"Plural-Forms.+?\\n")\s*$', re.MULTILINE | re.DOTALL)
STATUS_OK = 0
NO_LOCALE_DIR = object()


def check_programs(*programs):
    for program in programs:
        if find_command(program) is None:
            raise CommandError(
                "Can't find %s. Make sure you have GNU gettext tools 0.15 or "
                "newer installed." % program
            )


@total_ordering
class TranslatableFile:
    def __init__(self, dirpath, file_name, locale_dir):
        self.file = file_name
        self.dirpath = dirpath
        self.locale_dir = locale_dir

    def __repr__(self):
        return "<%s: %s>" % (
            self.__class__.__name__,
            os.sep.join([self.dirpath, self.file]),
        )

    def __eq__(self, other):
        return self.path == other.path

    def __lt__(self, other):
        return self.path < other.path

    @property
    def path(self):
        return os.path.join(self.dirpath, self.file)


class BuildFile:
    """
    Represent the state of a translatable file during the build process.
    """
    def __init__(self, command, domain, translatable):
        self.command = command
        self.domain = domain
        self.translatable = translatable

    @cached_property
    def is_templatized(self):
        if self.domain == 'djangojs':
            return self.command.gettext_version < (0, 18, 3)
        elif self.domain == 'django':
            file_ext = os.path.splitext(self.translatable.file)[1]
            return file_ext != '.py'
        return False

    @cached_property
    def path(self):
        return self.translatable.path

    @cached_property
    def work_path(self):
        """
        Path to a file which is being fed into GNU gettext pipeline. This may
        be either a translatable or its preprocessed version.
        """
        if not self.is_templatized:
            return self.path
        extension = {
            'djangojs': 'c',
            'django': 'py',
        }.get(self.domain)
        filename = '%s.%s' % (self.translatable.file, extension)
        return os.path.join(self.translatable.dirpath, filename)

    def preprocess(self):
        """
        Preprocess (if necessary) a translatable file before passing it to
        xgettext GNU gettext utility.
        """
        if not self.is_templatized:
            return

        with open(self.path, encoding='utf-8') as fp:
            src_data = fp.read()

        if self.domain == 'djangojs':
            content = prepare_js_for_gettext(src_data)
        elif self.domain == 'django':
            content = templatize(src_data, origin=self.path[2:])

        with open(self.work_path, 'w', encoding='utf-8') as fp:
            fp.write(content)

    def postprocess_messages(self, msgs):
        """
        Postprocess messages generated by xgettext GNU gettext utility.

        Transform paths as if these messages were generated from original
        translatable files rather than from preprocessed versions.
        """
        if not self.is_templatized:
            return msgs

        # Remove '.py' suffix
        if os.name == 'nt':
            # Preserve '.\' prefix on Windows to respect gettext behavior
            old_path = self.work_path
            new_path = self.path
        else:
            old_path = self.work_path[2:]
            new_path = self.path[2:]

        return re.sub(
            r'^(#: .*)(' + re.escape(old_path) + r')',
            lambda match: match.group().replace(old_path, new_path),
            msgs,
            flags=re.MULTILINE
        )

    def cleanup(self):
        """
        Remove a preprocessed copy of a translatable file (if any).
        """
        if self.is_templatized:
            # This check is needed for the case of a symlinked file and its
            # source being processed inside a single group (locale dir);
            # removing either of those two removes both.
            if os.path.exists(self.work_path):
                os.unlink(self.work_path)


def normalize_eols(raw_contents):
    """
    Take a block of raw text that will be passed through str.splitlines() to
    get universal newlines treatment.

    Return the resulting block of text with normalized `\n` EOL sequences ready
    to be written to disk using current platform's native EOLs.
    """
    lines_list = raw_contents.splitlines()
    # Ensure last line has its EOL
    if lines_list and lines_list[-1]:
        lines_list.append('')
    return '\n'.join(lines_list)


def write_pot_file(potfile, msgs):
    """
    Write the `potfile` with the `msgs` contents, making sure its format is
    valid.
    """
    pot_lines = msgs.splitlines()
    if os.path.exists(potfile):
        # Strip the header
        lines = dropwhile(len, pot_lines)
    else:
        lines = []
        found, header_read = False, False
        for line in pot_lines:
            if not found and not header_read:
                if 'charset=CHARSET' in line:
                    found = True
                    line = line.replace('charset=CHARSET', 'charset=UTF-8')
            if not line and not found:
                header_read = True
            lines.append(line)
    msgs = '\n'.join(lines)
    # Force newlines of POT files to '\n' to work around
    # https://savannah.gnu.org/bugs/index.php?52395
    with open(potfile, 'a', encoding='utf-8', newline='\n') as fp:
        fp.write(msgs)


class Command(BaseCommand):
    help = (
        "Runs over the entire source tree of the current directory and "
        "pulls out all strings marked for translation. It creates (or updates) a message "
        "file in the conf/locale (in the django tree) or locale (for projects and "
        "applications) directory.\n\nYou must run this command with one of either the "
        "--locale, --exclude, or --all options."
    )

    translatable_file_class = TranslatableFile
    build_file_class = BuildFile

    requires_system_checks = False

    msgmerge_options = ['-q', '--previous']
    msguniq_options = ['--to-code=utf-8']
    msgattrib_options = ['--no-obsolete']
    xgettext_options = ['--from-code=UTF-8', '--add-comments=Translators']

    def add_arguments(self, parser):
        parser.add_argument(
            '--locale', '-l', default=[], action='append',
            help='Creates or updates the message files for the given locale(s) (e.g. pt_BR). '
                 'Can be used multiple times.',
        )
        parser.add_argument(
            '--exclude', '-x', default=[], action='append',
            help='Locales to exclude. Default is none. Can be used multiple times.',
        )
        parser.add_argument(
            '--domain', '-d', default='django',
            help='The domain of the message files (default: "django").',
        )
        parser.add_argument(
            '--all', '-a', action='store_true',
            help='Updates the message files for all existing locales.',
        )
        parser.add_argument(
            '--extension', '-e', dest='extensions', action='append',
            help='The file extension(s) to examine (default: "html,txt,py", or "js" '
                 'if the domain is "djangojs"). Separate multiple extensions with '
                 'commas, or use -e multiple times.',
        )
        parser.add_argument(
            '--symlinks', '-s', action='store_true',
            help='Follows symlinks to directories when examining source code '
                 'and templates for translation strings.',
        )
        parser.add_argument(
            '--ignore', '-i', action='append', dest='ignore_patterns',
            default=[], metavar='PATTERN',
            help='Ignore files or directories matching this glob-style pattern. '
                 'Use multiple times to ignore more.',
        )
        parser.add_argument(
            '--no-default-ignore', action='store_false', dest='use_default_ignore_patterns',
            help="Don't ignore the common glob-style patterns 'CVS', '.*', '*~' and '*.pyc'.",
        )
        parser.add_argument(
            '--no-wrap', action='store_true',
            help="Don't break long message lines into several lines.",
        )
        parser.add_argument(
            '--no-location', action='store_true',
            help="Don't write '#: filename:line' lines.",
        )
        parser.add_argument(
            '--add-location',
            choices=('full', 'file', 'never'), const='full', nargs='?',
            help=(
                "Controls '#: filename:line' lines. If the option is 'full' "
                "(the default if not given), the lines  include both file name "
                "and line number. If it's 'file', the line number is omitted. If "
                "it's 'never', the lines are suppressed (same as --no-location). "
                "--add-location requires gettext 0.19 or newer."
            ),
        )
        parser.add_argument(
            '--no-obsolete', action='store_true',
            help="Remove obsolete message strings.",
        )
        parser.add_argument(
            '--keep-pot', action='store_true',
            help="Keep .pot file after making messages. Useful when debugging.",
        )

    def handle(self, *args, **options):
        locale = options['locale']
        exclude = options['exclude']
        self.domain = options['domain']
        self.verbosity = options['verbosity']
        process_all = options['all']
        extensions = options['extensions']
        self.symlinks = options['symlinks']

        ignore_patterns = options['ignore_patterns']
        if options['use_default_ignore_patterns']:
            ignore_patterns += ['CVS', '.*', '*~', '*.pyc']
        self.ignore_patterns = list(set(ignore_patterns))

        # Avoid messing with mutable class variables
        if options['no_wrap']:
            self.msgmerge_options = self.msgmerge_options[:] + ['--no-wrap']
            self.msguniq_options = self.msguniq_options[:] + ['--no-wrap']
            self.msgattrib_options = self.msgattrib_options[:] + ['--no-wrap']
            self.xgettext_options = self.xgettext_options[:] + ['--no-wrap']
        if options['no_location']:
            self.msgmerge_options = self.msgmerge_options[:] + ['--no-location']
            self.msguniq_options = self.msguniq_options[:] + ['--no-location']
            self.msgattrib_options = self.msgattrib_options[:] + ['--no-location']
            self.xgettext_options = self.xgettext_options[:] + ['--no-location']
        if options['add_location']:
            if self.gettext_version < (0, 19):
                raise CommandError(
                    "The --add-location option requires gettext 0.19 or later. "
                    "You have %s." % '.'.join(str(x) for x in self.gettext_version)
                )
            arg_add_location = "--add-location=%s" % options['add_location']
            self.msgmerge_options = self.msgmerge_options[:] + [arg_add_location]
            self.msguniq_options = self.msguniq_options[:] + [arg_add_location]
            self.msgattrib_options = self.msgattrib_options[:] + [arg_add_location]
            self.xgettext_options = self.xgettext_options[:] + [arg_add_location]

        self.no_obsolete = options['no_obsolete']
        self.keep_pot = options['keep_pot']

        if self.domain not in ('django', 'djangojs'):
            raise CommandError("currently makemessages only supports domains "
                               "'django' and 'djangojs'")
        if self.domain == 'djangojs':
            exts = extensions or ['js']
        else:
            exts = extensions or ['html', 'txt', 'py']
        self.extensions = handle_extensions(exts)

        if (not locale and not exclude and not process_all) or self.domain is None:
            raise CommandError(
                "Type '%s help %s' for usage information."
                % (os.path.basename(sys.argv[0]), sys.argv[1])
            )

        if self.verbosity > 1:
            self.stdout.write(
                'examining files with the extensions: %s'
                % get_text_list(list(self.extensions), 'and')
            )

        self.invoked_for_django = False
        self.locale_paths = []
        self.default_locale_path = None
        if os.path.isdir(os.path.join('conf', 'locale')):
            self.locale_paths = [os.path.abspath(os.path.join('conf', 'locale'))]
            self.default_locale_path = self.locale_paths[0]
            self.invoked_for_django = True
        else:
            if self.settings_available:
                self.locale_paths.extend(settings.LOCALE_PATHS)
            # Allow to run makemessages inside an app dir
            if os.path.isdir('locale'):
                self.locale_paths.append(os.path.abspath('locale'))
            if self.locale_paths:
                self.default_locale_path = self.locale_paths[0]
                os.makedirs(self.default_locale_path, exist_ok=True)

        # Build locale list
        looks_like_locale = re.compile(r'[a-z]{2}')
        locale_dirs = filter(os.path.isdir, glob.glob('%s/*' % self.default_locale_path))
        all_locales = [
            lang_code for lang_code in map(os.path.basename, locale_dirs)
            if looks_like_locale.match(lang_code)
        ]

        # Account for excluded locales
        if process_all:
            locales = all_locales
        else:
            locales = locale or all_locales
            locales = set(locales).difference(exclude)

        if locales:
            check_programs('msguniq', 'msgmerge', 'msgattrib')

        check_programs('xgettext')

        try:
            potfiles = self.build_potfiles()

            # Build po files for each selected locale
            for locale in locales:
                if self.verbosity > 0:
                    self.stdout.write('processing locale %s' % locale)
                for potfile in potfiles:
                    self.write_po_file(potfile, locale)
        finally:
            if not self.keep_pot:
                self.remove_potfiles()

    @cached_property
    def gettext_version(self):
        # Gettext tools will output system-encoded bytestrings instead of UTF-8,
        # when looking up the version. It's especially a problem on Windows.
        out, err, status = popen_wrapper(
            ['xgettext', '--version'],
            stdout_encoding=DEFAULT_LOCALE_ENCODING,
        )
        m = re.search(r'(\d+)\.(\d+)\.?(\d+)?', out)
        if m:
            return tuple(int(d) for d in m.groups() if d is not None)
        else:
            raise CommandError("Unable to get gettext version. Is it installed?")

    @cached_property
    def settings_available(self):
        try:
            settings.LOCALE_PATHS
        except ImproperlyConfigured:
            if self.verbosity > 1:
                self.stderr.write("Running without configured settings.")
            return False
        return True

    def build_potfiles(self):
        """
        Build pot files and apply msguniq to them.
        """
        file_list = self.find_files(".")
        self.remove_potfiles()
        self.process_files(file_list)
        potfiles = []
        for path in self.locale_paths:
            potfile = os.path.join(path, '%s.pot' % self.domain)
            if not os.path.exists(potfile):
                continue
            args = ['msguniq'] + self.msguniq_options + [potfile]
            msgs, errors, status = popen_wrapper(args)
            if errors:
                if status != STATUS_OK:
                    raise CommandError(
                        "errors happened while running msguniq\n%s" % errors)
                elif self.verbosity > 0:
                    self.stdout.write(errors)
            msgs = normalize_eols(msgs)
            with open(potfile, 'w', encoding='utf-8') as fp:
                fp.write(msgs)
            potfiles.append(potfile)
        return potfiles

    def remove_potfiles(self):
        for path in self.locale_paths:
            pot_path = os.path.join(path, '%s.pot' % self.domain)
            if os.path.exists(pot_path):
                os.unlink(pot_path)

    def find_files(self, root):
        """
        Get all files in the given root. Also check that there is a matching
        locale dir for each file.
        """
        all_files = []
        ignored_roots = []
        if self.settings_available:
            ignored_roots = [os.path.normpath(p) for p in (settings.MEDIA_ROOT, settings.STATIC_ROOT) if p]
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=self.symlinks):
            for dirname in dirnames[:]:
                if (is_ignored_path(os.path.normpath(os.path.join(dirpath, dirname)), self.ignore_patterns) or
                        os.path.join(os.path.abspath(dirpath), dirname) in ignored_roots):
                    dirnames.remove(dirname)
                    if self.verbosity > 1:
                        self.stdout.write('ignoring directory %s' % dirname)
                elif dirname == 'locale':
                    dirnames.remove(dirname)
                    self.locale_paths.insert(0, os.path.join(os.path.abspath(dirpath), dirname))
            for filename in filenames:
                file_path = os.path.normpath(os.path.join(dirpath, filename))
                file_ext = os.path.splitext(filename)[1]
                if file_ext not in self.extensions or is_ignored_path(file_path, self.ignore_patterns):
                    if self.verbosity > 1:
                        self.stdout.write('ignoring file %s in %s' % (filename, dirpath))
                else:
                    locale_dir = None
                    for path in self.locale_paths:
                        if os.path.abspath(dirpath).startswith(os.path.dirname(path)):
                            locale_dir = path
                            break
                    locale_dir = locale_dir or self.default_locale_path or NO_LOCALE_DIR
                    all_files.append(self.translatable_file_class(dirpath, filename, locale_dir))
        return sorted(all_files)

    def process_files(self, file_list):
        """
        Group translatable files by locale directory and run pot file build
        process for each group.
        """
        file_groups = {}
        for translatable in file_list:
            file_group = file_groups.setdefault(translatable.locale_dir, [])
            file_group.append(translatable)
        for locale_dir, files in file_groups.items():
            self.process_locale_dir(locale_dir, files)

    def process_locale_dir(self, locale_dir, files):
        """
        Extract translatable literals from the specified files, creating or
        updating the POT file for a given locale directory.

        Use the xgettext GNU gettext utility.
        """
        build_files = []
        for translatable in files:
            if self.verbosity > 1:
                self.stdout.write('processing file %s in %s' % (
                    translatable.file, translatable.dirpath
                ))
            if self.domain not in ('djangojs', 'django'):
                continue
            build_file = self.build_file_class(self, self.domain, translatable)
            try:
                build_file.preprocess()
            except UnicodeDecodeError as e:
                self.stdout.write(
                    'UnicodeDecodeError: skipped file %s in %s (reason: %s)' % (
                        translatable.file, translatable.dirpath, e,
                    )
                )
                continue
            build_files.append(build_file)

        if self.domain == 'djangojs':
            is_templatized = build_file.is_templatized
            args = [
                'xgettext',
                '-d', self.domain,
                '--language=%s' % ('C' if is_templatized else 'JavaScript',),
                '--keyword=gettext_noop',
                '--keyword=gettext_lazy',
                '--keyword=ngettext_lazy:1,2',
                '--keyword=pgettext:1c,2',
                '--keyword=npgettext:1c,2,3',
                '--output=-',
            ]
        elif self.domain == 'django':
            args = [
                'xgettext',
                '-d', self.domain,
                '--language=Python',
                '--keyword=gettext_noop',
                '--keyword=gettext_lazy',
                '--keyword=ngettext_lazy:1,2',
                '--keyword=ugettext_noop',
                '--keyword=ugettext_lazy',
                '--keyword=ungettext_lazy:1,2',
                '--keyword=pgettext:1c,2',
                '--keyword=npgettext:1c,2,3',
                '--keyword=pgettext_lazy:1c,2',
                '--keyword=npgettext_lazy:1c,2,3',
                '--output=-',
            ]
        else:
            return

        input_files = [bf.work_path for bf in build_files]
        with NamedTemporaryFile(mode='w+') as input_files_list:
            input_files_list.write('\n'.join(input_files))
            input_files_list.flush()
            args.extend(['--files-from', input_files_list.name])
            args.extend(self.xgettext_options)
            msgs, errors, status = popen_wrapper(args)

        if errors:
            if status != STATUS_OK:
                for build_file in build_files:
                    build_file.cleanup()
                raise CommandError(
                    'errors happened while running xgettext on %s\n%s' %
                    ('\n'.join(input_files), errors)
                )
            elif self.verbosity > 0:
                # Print warnings
                self.stdout.write(errors)

        if msgs:
            if locale_dir is NO_LOCALE_DIR:
                file_path = os.path.normpath(build_files[0].path)
                raise CommandError(
                    'Unable to find a locale path to store translations for '
                    'file %s' % file_path
                )
            for build_file in build_files:
                msgs = build_file.postprocess_messages(msgs)
            potfile = os.path.join(locale_dir, '%s.pot' % self.domain)
            write_pot_file(potfile, msgs)

        for build_file in build_files:
            build_file.cleanup()

    def write_po_file(self, potfile, locale):
        """
        Create or update the PO file for self.domain and `locale`.
        Use contents of the existing `potfile`.

        Use msgmerge and msgattrib GNU gettext utilities.
        """
        basedir = os.path.join(os.path.dirname(potfile), locale, 'LC_MESSAGES')
        os.makedirs(basedir, exist_ok=True)
        pofile = os.path.join(basedir, '%s.po' % self.domain)

        if os.path.exists(pofile):
            args = ['msgmerge'] + self.msgmerge_options + [pofile, potfile]
            msgs, errors, status = popen_wrapper(args)
            if errors:
                if status != STATUS_OK:
                    raise CommandError(
                        "errors happened while running msgmerge\n%s" % errors)
                elif self.verbosity > 0:
                    self.stdout.write(errors)
        else:
            with open(potfile, encoding='utf-8') as fp:
                msgs = fp.read()
            if not self.invoked_for_django:
                msgs = self.copy_plural_forms(msgs, locale)
        msgs = normalize_eols(msgs)
        msgs = msgs.replace(
            "#. #-#-#-#-#  %s.pot (PACKAGE VERSION)  #-#-#-#-#\n" % self.domain, "")
        with open(pofile, 'w', encoding='utf-8') as fp:
            fp.write(msgs)

        if self.no_obsolete:
            args = ['msgattrib'] + self.msgattrib_options + ['-o', pofile, pofile]
            msgs, errors, status = popen_wrapper(args)
            if errors:
                if status != STATUS_OK:
                    raise CommandError(
                        "errors happened while running msgattrib\n%s" % errors)
                elif self.verbosity > 0:
                    self.stdout.write(errors)

    def copy_plural_forms(self, msgs, locale):
        """
        Copy plural forms header contents from a Django catalog of locale to
        the msgs string, inserting it at the right place. msgs should be the
        contents of a newly created .po file.
        """
        django_dir = os.path.normpath(os.path.join(os.path.dirname(django.__file__)))
        if self.domain == 'djangojs':
            domains = ('djangojs', 'django')
        else:
            domains = ('django',)
        for domain in domains:
            django_po = os.path.join(django_dir, 'conf', 'locale', locale, 'LC_MESSAGES', '%s.po' % domain)
            if os.path.exists(django_po):
                with open(django_po, encoding='utf-8') as fp:
                    m = plural_forms_re.search(fp.read())
                if m:
                    plural_form_line = m.group('value')
                    if self.verbosity > 1:
                        self.stdout.write('copying plural forms: %s' % plural_form_line)
                    lines = []
                    found = False
                    for line in msgs.splitlines():
                        if not found and (not line or plural_forms_re.search(line)):
                            line = plural_form_line
                            found = True
                        lines.append(line)
                    msgs = '\n'.join(lines)
                    break
        return msgs
