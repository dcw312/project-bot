from django.contrib import admin
from .models import DiscordUser, Project, ProjectMember, Task, Activity, MessageLog


@admin.register(DiscordUser)
class DiscordUserAdmin(admin.ModelAdmin):
    list_display = ('username', 'discord_id', 'is_authorized', 'authorized_at', 'created_at')
    list_filter = ('is_authorized',)
    search_fields = ('username', 'discord_id')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'creator', 'discord_channel_id', 'created_at')
    search_fields = ('name', 'creator__username')


@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = ('project', 'user', 'role')
    list_filter = ('role',)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'status', 'priority', 'deadline_type', 'due_date', 'created_at')
    list_filter = ('status', 'deadline_type', 'priority')
    search_fields = ('title', 'project__name')


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'created_at')
    list_filter = ('action',)
    search_fields = ('user__username', 'action')


@admin.register(MessageLog)
class MessageLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'discord_channel_id', 'elapsed_ms', 'created_at')
    search_fields = ('user__username', 'discord_channel_id')
