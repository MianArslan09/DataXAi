from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model, set as AUTH_USER_MODEL before the first migration
    (swapping it later is one of Django's classic footguns - see Volume 1,
    Section 1.6, ambiguity #3, and Volume 2's write-up of why this exists
    now instead of waiting for Volume 3).

    Full RBAC (Data Engineer / Business Analyst / Admin permission logic,
    not just this label) is implemented in Volume 3.
    """

    class Role(models.TextChoices):
        DATA_ENGINEER = "data_engineer", "Data Engineer"
        BUSINESS_ANALYST = "business_analyst", "Business Analyst"
        ADMIN = "admin", "Admin"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.BUSINESS_ANALYST)

    def __str__(self):
        return self.username
