import logging

from django.contrib.auth.models import User
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import UserProfile

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User, dispatch_uid="user.profile.create")
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    else:
        instance.profile.save()

@receiver(post_delete, sender=UserProfile)
def delete_user_avatar(sender, instance, **kwargs):
    if not instance.avatar or not instance.avatar.name:
        return

    try:
        instance.avatar.storage.delete(instance.avatar.name)
    except Exception:
        logger.exception(
            "Failed to delete avatar for user %s",
            instance.user_id,
        )