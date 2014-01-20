# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.core import checks
from django.db import models


class ModelRaisingMessages(models.Model):
    @classmethod
    def check(self, **kwargs):
        return [
            checks.Warning(
                'First warning',
                hint='Hint',
                obj='obj'
            ),
            checks.Warning(
                'Second warning',
                hint=None,
                obj='a'
            ),
            checks.Error(
                'An error',
                hint='Error hint',
                obj=None,
            )
        ]
