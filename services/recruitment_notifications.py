"""Internal notifications for recruitment detections using existing Web Push."""
from __future__ import annotations
import asyncio

def notify_detection(event:dict)->None:
    from core.recruitment_offer_visibility import should_show_in_selection_offer_review
    if not should_show_in_selection_offer_review(event):return
    try:
        from features.web_push import admin_usernames_with_subscriptions,send_to_user
        title=event['primary_status'].replace('_',' ').title();body=' · '.join(x for x in [event.get('company_name'),event.get('job_title'),f"{round(float(event.get('confidence') or 0)*100)}% confidence"] if x)
        for user in admin_usernames_with_subscriptions():send_to_user(user,title=title,body=body,tag=f"recruitment:{event['id']}")
    except Exception:pass

def notify_system(title:str,body:str,tag:str)->None:
    try:
        from features.web_push import admin_usernames_with_subscriptions,send_to_user
        for user in admin_usernames_with_subscriptions():send_to_user(user,title=title,body=body,tag=tag)
    except Exception:pass
