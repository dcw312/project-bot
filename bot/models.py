from django.db import models


class DiscordUser(models.Model):
    discord_id = models.CharField(max_length=64, unique=True)
    username = models.CharField(max_length=128)
    is_authorized = models.BooleanField(default=False)
    authorized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.username} ({self.discord_id})"


class Project(models.Model):
    name = models.CharField(max_length=256)
    description = models.TextField(blank=True, null=True)
    creator = models.ForeignKey(DiscordUser, on_delete=models.PROTECT, related_name='created_projects')
    discord_channel_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ProjectMember(models.Model):
    OWNER = 'owner'
    COLLABORATOR = 'collaborator'
    ROLE_CHOICES = [(OWNER, 'Owner'), (COLLABORATOR, 'Collaborator')]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(DiscordUser, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)

    class Meta:
        unique_together = ('project', 'user')

    def __str__(self):
        return f"{self.user.username} — {self.project.name} ({self.role})"


class Task(models.Model):
    TODO = 'todo'
    DOING = 'doing'
    DONE = 'done'
    STATUS_CHOICES = [(TODO, 'Todo'), (DOING, 'Doing'), (DONE, 'Done')]

    HARD = 'hard'
    SOFT = 'soft'
    DEADLINE_CHOICES = [(HARD, 'Hard'), (SOFT, 'Soft')]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=512)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=TODO)
    priority = models.IntegerField(default=3)
    due_date = models.DateTimeField(null=True, blank=True)
    deadline_type = models.CharField(max_length=8, choices=DEADLINE_CHOICES, default=SOFT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['priority', 'created_at']

    def __str__(self):
        return f"[{self.status}] {self.title}"


class Activity(models.Model):
    user = models.ForeignKey(DiscordUser, on_delete=models.CASCADE, related_name='activities')
    action = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} — {self.action} at {self.created_at}"


class MessageLog(models.Model):
    user = models.ForeignKey(DiscordUser, on_delete=models.CASCADE, related_name='message_logs')
    discord_channel_id = models.CharField(max_length=64)
    message = models.TextField()
    llm_response = models.JSONField(default=dict)
    elapsed_ms = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} in {self.discord_channel_id} ({self.elapsed_ms}ms)"
