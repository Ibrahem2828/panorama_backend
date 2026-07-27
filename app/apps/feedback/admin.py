from django.contrib import admin

from .models import AppFeedback, FeedbackPromptEvent, FeedbackPromptPolicy, FeedbackVote


@admin.register(AppFeedback)
class AppFeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "kind", "context", "rating", "status", "priority", "created_at")
    list_filter = ("kind", "context", "status", "priority", "rating", "platform", "app_version")
    search_fields = ("user__email", "user__full_name", "title", "comment", "suggestion")
    readonly_fields = ("metadata", "created_at", "updated_at")


admin.site.register(FeedbackPromptPolicy)
admin.site.register(FeedbackPromptEvent)
admin.site.register(FeedbackVote)
