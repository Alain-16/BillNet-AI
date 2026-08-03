from django.db import models
from googleapiclient import model
from common.enums import CompanyStatus, UserRole
from common.models import UUIDModel, TimeStampedModel
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

# Create your models here.

class UserManager(BaseUserManager):

    use_in_migrations = True

    def create_user(self,email,password=None,**extra_fields):
        if not email:
            raise ValueError("User must have a valid email address")

        email = self.normalize_email(email)
        user = self.model(email=email,**extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self,email,password=None,**extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError("superuser must have is_staff=True")
        if extra_fields.get('is_superuser') is not True:
            raise ValueError("superuser must have is_superuser=True")

        return self.create_user(email,password,**extra_fields)


class User(UUIDModel, AbstractBaseUser, PermissionsMixin):

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255,blank=True)

    company = models.ForeignKey(
        "accounts.Company",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )

    role = models.CharField(
        max_length=30,
        choices=UserRole.choices,
        null=True,
        blank=True,

    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self) -> str:
        return self.email
    def get_full_name(self) -> str:
        return self.full_name or self.email
    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email




class Company(UUIDModel, TimeStampedModel):

    name = models.CharField(max_length=255)

    currency = models.CharField(max_length=3, default="CAD")

    status = models.CharField(
        max_length=30,
        choices=CompanyStatus.choices,
        default=CompanyStatus.ACTIVE,
    )
    timezone = models.CharField(max_length=100, default="America/vancouver")
    auto_post_enabled = models.BooleanField(default=False)

    tax_codes = models.JSONField(default=list)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self)->str:
        return self.name

