import os
import datetime
from urllib.parse import urlparse
import praw
from flask import current_app
from database import insert_not_found
from backend import save_uploaded_video_metadata
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
            return download_video(url, video_id, video_id, 'redgifs', None, logger)

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
            video_id = submission.id
            video_title = submission.title
            channel_id = submission.author.name if submission.author else 'unknown'
            published_at = datetime.datetime.utcfromtimestamp(submission.created_utc)

            if submission.is_video:
                video_url = url  # pass the post URL so yt-dlp can merge audio+video
            elif submission.url:
                video_url = submission.url
                is_image = submission.url.lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))
            else:
                logger.error("No media found in submission: %s" % url)
                insert_not_found(video_id, logger)
                return False

        logger.debug("Starting Reddit/RedGIF Download: %s" % url)
        logger.debug("Content URL: %s" % video_url)
        logger.debug("Is image: %s" % is_image)

        if is_image:
            return download_image(video_url, video_id, channel_id, video_title, published_at, logger)
        else:
            return download_video(video_url, video_id, video_title, channel_id, published_at, logger)
            
    except Exception as e:
        logger.error("Reddit Download Failed for %s: %s" % (url, e))
        return False


def download_video(video_url, video_id, video_title, channel_id, published_at, logger):
    if published_at is None:
        published_at = datetime.datetime.utcnow()
    filepath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id, video_id + ".mp4")

    def make_progress_hook(vid_id):
        def hook(d):
            try:
                status_obj = dl_status_map.setdefault(vid_id, {})
                if d["status"] == "downloading":
                    status_obj['progress'] = d['_percent_str']
                    status_obj['title'] = d.get('info_dict', {}).get('title', "")
                    status_obj['type'] = 'reddit'
                elif d["status"] == "finished":
                    status_obj['progress'] = "100%"
            except Exception as e:
                current_app.logger.error("dl_progress_hook Failed: %s" % e)
        return hook

    ydl_opts = {
        'outtmpl': filepath,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        "progress_hooks": [make_progress_hook(video_id)],
    }

    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        dl_status_map[video_id] = {'progress': '0%', 'title': video_title, 'type': 'reddit'}
        ydl.download([video_url])

    try:
        save_uploaded_video_metadata(video_id, filepath, video_title, channel_id, published_at, filepath, 'reddit')
    finally:
        if video_id in dl_status_map:
            del dl_status_map[video_id]

    return True


def download_image(image_url, video_id, channel_id, video_title, published_at, logger):
    try:
        import requests
        if published_at is None:
            published_at = datetime.datetime.utcnow()
        response = requests.get(image_url)
        response.raise_for_status()

        ext = image_url.lower().split('?')[0].split('.')[-1]
        filepath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id, video_id + "." + ext)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(response.content)

        save_uploaded_video_metadata(video_id, filepath, video_title, channel_id, published_at, filepath, 'reddit')

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


