from django.contrib import admin

from .models import LostItemReport, Notification, SOSAlert, SupportTicket, TicketMessage

admin.site.register([Notification, SupportTicket, TicketMessage, LostItemReport, SOSAlert])
