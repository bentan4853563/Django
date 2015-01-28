"""
Tests for select_related()

``select_related()`` follows all relationships and pre-caches any foreign key
values so that complex trees can be fetched in a single query. However, this
isn't always a good idea, so the ``depth`` argument control how many "levels"
the select-related behavior will traverse.
"""

from django.contrib.contenttypes.fields import (
    GenericForeignKey, GenericRelation,
)
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.encoding import python_2_unicode_compatible


# Who remembers high school biology?


@python_2_unicode_compatible
class Domain(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Kingdom(models.Model):
    name = models.CharField(max_length=50)
    domain = models.ForeignKey(Domain)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Phylum(models.Model):
    name = models.CharField(max_length=50)
    kingdom = models.ForeignKey(Kingdom)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Klass(models.Model):
    name = models.CharField(max_length=50)
    phylum = models.ForeignKey(Phylum)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Order(models.Model):
    name = models.CharField(max_length=50)
    klass = models.ForeignKey(Klass)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Family(models.Model):
    name = models.CharField(max_length=50)
    order = models.ForeignKey(Order)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Genus(models.Model):
    name = models.CharField(max_length=50)
    family = models.ForeignKey(Family)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Species(models.Model):
    name = models.CharField(max_length=50)
    genus = models.ForeignKey(Genus)

    def __str__(self):
        return self.name

# and we'll invent a new thing so we have a model with two foreign keys


@python_2_unicode_compatible
class HybridSpecies(models.Model):
    name = models.CharField(max_length=50)
    parent_1 = models.ForeignKey(Species, related_name='child_1')
    parent_2 = models.ForeignKey(Species, related_name='child_2')

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Topping(models.Model):
    name = models.CharField(max_length=30)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Pizza(models.Model):
    name = models.CharField(max_length=100)
    toppings = models.ManyToManyField(Topping)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class TaggedItem(models.Model):
    tag = models.CharField(max_length=30)

    content_type = models.ForeignKey(ContentType, related_name='select_related_tagged_items')
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    def __str__(self):
        return self.tag


@python_2_unicode_compatible
class Bookmark(models.Model):
    url = models.URLField()
    tags = GenericRelation(TaggedItem)

    def __str__(self):
        return self.url
