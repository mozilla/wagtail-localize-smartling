# from functools import cached_property

# from django.db import models
# from queryish.rest import APIModel, APIQuerySet


# class Project(APIModel):
#     class Meta:
#         fields = ["project_id", "name"]


# class Project(models.Model):
#     account = models.ForeignKey(Account, on_delete=models.CASCADE, editable=False)
#     project_id = models.CharField(max_length=16, unique=True, editable=False)
#     archived = models.BooleanField(default=False, editable=False)
#     name = models.CharField(max_length=255, editable=False)
#     type_code = models.CharField(max_length=16, editable=False)

#     source_locale_description = models.CharField(max_length=255, editable=False)
#     source_locale_id = models.CharField(max_length=16, editable=False)

#     def __str__(self):
#         return self.name


# class TargetLocale(models.Model):
#     project = models.ForeignKey(
#         Project,
#         on_delete=models.CASCADE,
#         editable=False,
#         related_name="target_locales",
#         related_query_name="target_locale",
#     )
#     enabled = models.BooleanField(default=True, editable=False)
#     locale_id = models.CharField(max_length=16, editable=False)

#     def __str__(self):
#         return self.locale_id
