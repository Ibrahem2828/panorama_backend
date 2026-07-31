from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import FeatureFlag, MaintenanceMode, MobileAppReleasePolicy
from .services import FeatureFlagService, ProductConfigurationService


@receiver(post_save, sender=FeatureFlag)
@receiver(post_delete, sender=FeatureFlag)
def invalidate_feature_flag_cache(sender, instance, **kwargs) -> None:
    FeatureFlagService.invalidate(instance.key)


@receiver(post_save, sender=MobileAppReleasePolicy)
@receiver(post_delete, sender=MobileAppReleasePolicy)
def invalidate_release_policy_cache(sender, instance, **kwargs) -> None:
    ProductConfigurationService.invalidate_release(instance.platform)


@receiver(post_save, sender=MaintenanceMode)
@receiver(post_delete, sender=MaintenanceMode)
def invalidate_maintenance_cache(sender, instance, **kwargs) -> None:
    ProductConfigurationService.invalidate_maintenance()
