from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base for every domain model going forward (Volumes 3-9)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
