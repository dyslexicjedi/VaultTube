import os
from urllib.parse import urlparse, urlsplit
import re
import praw
from flask import current_app
from database import check_db_video, insert_not_found
from backend import get_video
from providers.base import dl_status_map


def provider_domains():
    return ['reddit.com', 'redd.it', 'redgifs.com']


def _get_reddit_client():
    return praw.Reddit(
        client_id=os.environ['VAULTTUBE_REDDIT_CLIENT_ID'],
        client_secret=os.environ['VAULTTUBE_REDDIT_CLIENT_SECRET'],
        user_agent=os.environ['VAULTTUBE_REDDIT_USER_AGENT'],
        username=os.environ['VAULTTUBE_REDDIT_USERNAME'],
        password=os.environ['VAULTTUBE_REDDIT_PASSWORD']
    )


def download(q, logger):
    url = q.url if hasattr(q, 'url') else q
    try:
        submission = None
        video_id = None
        video_url = None
        video_title = None
        channel_id = None
        is_image = False
        
        if 'redgifs.com' in url:
            video_id = extract_redgifs_id(url)
            if not video_id:
                logger.error("Could not extract RedGIF ID from URL: %s" % url)
                insert_not_found("redgifs_" + url.split('/')[-1][:50], logger)
                return False
            
            reddit = _get_reddit_client()
            submission = reddit.submission(id=video_id)
            
            if not submission:
                logger.error("Could not fetch RedGIF submission: %s" % url)
                insert_not_found(video_id, logger)
                return False
                
            video_url = submission.url
            video_title = submission.title
            channel_id = submission.author.name if submission.author else 'unknown'
            is_image = submission.url.endswith(('.jpg', '.jpeg', '.png'))
            
        else:
            parsed = urlparse(url)
            if parsed.hostname in ('redd.it',):
                vid = parsed.path.lstrip('/')
            else:
                vid = url.split('/comments/')[1].split('/')[0] if '/comments/' in url else None
            if not vid:
                logger.error("Could not extract video ID from URL: %s" % url)
                return False
            
            reddit = _get_reddit_client()
            submission = reddit.submission(id=vid)
            
            if not submission:
                logger.error("Could not fetch Reddit submission: %s" % url)
                insert_not_found(vid, logger)
                return False
            
            video_id = submission.id
            video_title = submission.title
            channel_id = submission.author.name if submission.author else 'unknown'
            
            if submission.is_video:
                video_url = submission.media['reddit_video']['fallback_url']
            elif submission.url:
                video_url = submission.url
                is_image = submission.url.endswith(('.jpg', '.jpeg', '.png'))
            else:
                logger.error("No media found in submission: %s" % url)
                insert_not_found(video_id, logger)
                return False
        
        logger.debug("Starting Reddit/RedGIF Download: %s" % url)
        logger.debug("Content URL: %s" % video_url)
        logger.debug("Is image: %s" % is_image)
        
        if is_image:
            return download_image(video_url, video_id, channel_id, logger)
        else:
            return download_video(video_url, video_id, video_title, channel_id, logger)
            
    except Exception as e:
        logger.error("Reddit Download Failed for %s: %s" % (url, e))
        return False


def download_video(video_url, video_id, video_title, channel_id, logger):
    ydl_opts = {
        'outtmpl': os.path.join(os.environ['VAULTTUBE_VAULTDIR'], "%(channel_id)s", "%(id)s.mp4"),
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        "progress_hooks": [dl_progress_hook],
    }
    
    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        dl_status_map[video_id] = {'progress': '0%', 'title': video_title, 'type': 'reddit'}
        ydl.download([video_url])
    
    filepath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id, video_id + ".mp4")
    result = get_video(filepath, current_app.logger)
    
    if video_id in dl_status_map:
        del dl_status_map[video_id]
    
    return True


def download_image(image_url, video_id, channel_id, logger):
    try:
        import requests
        response = requests.get(image_url)
        response.raise_for_status()
        
        ext = image_url.split('.')[-1]
        filepath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id, video_id + "." + ext)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(response.content)
        
        get_video(filepath, current_app.logger)
        
        return True
    except Exception as e:
        logger.error("Image download failed for %s: %s" % (image_url, e))
        return False


def extract_redgifs_id(url):
    if 'redgifs.com/watch/' in url:
        return url.split('/watch/')[1].split('/')[0]
    elif 'redgifs.com/gifs/' in url:
        return url.split('/gifs/')[1].split('.')[0]
    elif 'redgifs.com/i/' in url:
        return url.split('/i/')[1].split('.')[0]
    return None


def dl_progress_hook(d):
    try:
        video_id = d.get('info_dict', {}).get('id', None)
        if not video_id:
            video_id = d.get('filename', '').split('/')[-1].split('.')[0]
        status_obj = dl_status_map.setdefault(video_id, {})
        if d["status"] == "downloading":
            status_obj['progress'] = d['_percent_str']
            status_obj['title'] = d.get('info_dict', {}).get('title', "")
            status_obj['type'] = 'reddit'
        elif d["status"] == "finished":
            status_obj['progress'] = "100%"
    except Exception as e:
        current_app.logger.error("dl_progress_hook Failed: %s" % e)
