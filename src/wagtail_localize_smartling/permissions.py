from wagtail.permission_policies import ModelPermissionPolicy

from .models import Job


class JobPermissionPolicy(ModelPermissionPolicy):
    def user_has_permission(self, user, action):
        if action in ["add", "change"]:
            return False

        return super().user_has_permission(user, action)


job_permission_policy = JobPermissionPolicy(Job)
