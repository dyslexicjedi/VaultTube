import os
import re
import logging
import datetime
from urllib.parse import urlparse
import praw
from database import insert_not_found
from backend import save_uploaded_video_metadata
from providers.base import set_status, del_status, dl_status_lock, dl_status_map

logger = logging.getLogger('reddit')


def provider_domains():
    return ['reddit.com', 'redd.it', 'redgifs.com']


def _get_reddit_client():
    _required = (
        'VAULTTUBE_REDDIT_CLIENT_ID', 'VAULTTUBE_REDDIT_CLIENT_SECRET',
        'VAULTTUBE_REDDIT_USER_AGENT', 'VAULTTUBE_REDDIT_USERNAME',
        'VAULTTUBE_REDDIT_PASSWORD',
    )
    missing = [v for v in _required if v not in os.environ]
    if missing:
        raise RuntimeError("Reddit credentials not configured: %s" % ', '.join(missing))
    return praw.Reddit(
        client_id=os.environ['VAULTTUBE_REDDIT_CLIENT_ID'],
        client_secret=os.environ['VAULTTUBE_REDDIT_CLIENT_SECRET'],
        user_agent=os.environ['VAULTTUBE_REDDIT_USER_AGENT'],
        username=os.environ['VAULTTUBE_REDDIT_USERNAME'],
        password=os.environ['VAULTTUBE_REDDIT_PASSWORD']
    )


def _reddit_video(submission):
    """The v.redd.it media dict for a post, following crossposts (a crosspost
    has is_video False and no media of its own; the video is on the parent)."""
    for media in (getattr(submission, 'secure_media', None), getattr(submission, 'media', None)):
        if isinstance(media, dict) and media.get('reddit_video'):
            return media['reddit_video']
    for parent in getattr(submission, 'crosspost_parent_list', None) or []:
        for key in ('secure_media', 'media'):
            media = parent.get(key)
            if isinstance(media, dict) and media.get('reddit_video'):
                return media['reddit_video']
    return None


def _media_url(submission, url):
    """Return (content_url, is_image) for what a post carries, or raise when
    there is nothing to download."""
    reddit_video = _reddit_video(submission)
    if reddit_video:
        # The HLS master has audio and video together, and comes straight from
        # the v.redd.it CDN, so yt-dlp doesn't have to fetch the post's JSON
        # itself (Reddit blocks that unauthenticated request with a 403)
        hls = reddit_video.get('hls_url')
        if hls:
            return hls, False
    if submission.is_video:
        return url, False  # pass the post URL so yt-dlp can merge audio+video
    if getattr(submission, 'is_self', False) or not submission.url:
        raise ValueError("Reddit post has no media (text post)")
    content_url = submission.url
    if content_url.startswith('/'):
        content_url = "https://www.reddit.com" + content_url
    is_image = content_url.lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))
    return content_url, is_image


def download(q):
    # Errors are raised, not swallowed, so the downloader records the real
    # reason in download_errors instead of "Download returned False"
    url = q.url if hasattr(q, 'url') else q
    try:
        if 'redgifs.com' in url:
            video_id = extract_redgifs_id(url)
            if not video_id:
                insert_not_found("redgifs_" + url.split('/')[-1][:50])
                raise ValueError("Could not extract RedGIF ID from URL")
            return download_video(url, video_id, video_id, 'redgifs', None, webpage_url=url)

        parsed = urlparse(url)
        if parsed.hostname in ('redd.it',):
            vid = parsed.path.strip('/')
        else:
            vid = url.split('/comments/')[1].split('/')[0] if '/comments/' in url else None
        if not vid:
            raise ValueError("Not a Reddit post URL (no /comments/<id>/)")

        reddit = _get_reddit_client()
        submission = reddit.submission(id=vid)
        video_id = submission.id
        video_title = submission.title
        channel_id = submission.author.name if submission.author else 'unknown'
        published_at = datetime.datetime.utcfromtimestamp(submission.created_utc)

        try:
            video_url, is_image = _media_url(submission, url)
        except ValueError:
            insert_not_found(video_id)
            raise

        logger.debug("Starting Reddit Download: %s" % url)
        logger.debug("Content URL: %s" % video_url)
        logger.debug("Is image: %s" % is_image)

        post_url = "https://www.reddit.com" + submission.permalink if submission.permalink else url
        # Stored in the video's json so posts can be traced to (and later
        # filtered by) the subreddit they came from
        extra = {'subreddit': submission.subreddit.display_name} if submission.subreddit else None
        if is_image:
            result = download_image(video_url, video_id, channel_id, video_title, published_at, webpage_url=post_url, extra=extra)
        else:
            result = download_video(video_url, video_id, video_title, channel_id, published_at, webpage_url=post_url, extra=extra)

        if result and getattr(q, 'unsave', False):
            try:
                submission.unsave()
                logger.info("Unsaved Reddit post %s" % submission.id)
            except Exception as e:
                logger.error("Failed to unsave %s: %s" % (submission.id, e))

        return result

    except Exception as e:
        logger.error("Reddit Download Failed for %s: %s" % (url, e))
        raise


def download_video(video_url, video_id, video_title, channel_id, published_at, webpage_url=None, extra=None):
    if published_at is None:
        published_at = datetime.datetime.utcnow()
    filepath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id, video_id + ".mp4")

    def make_progress_hook(vid_id):
        def hook(d):
            try:
                with dl_status_lock:
                    status_obj = dl_status_map.setdefault(vid_id, {})
                    if d["status"] == "downloading":
                        status_obj['progress'] = d['_percent_str']
                        status_obj['title'] = d.get('info_dict', {}).get('title', "")
                        status_obj['type'] = 'reddit'
                    elif d["status"] == "finished":
                        status_obj['progress'] = "100%"
            except Exception as e:
                logger.error("dl_progress_hook Failed: %s" % e)
        return hook

    ydl_opts = {
        'outtmpl': filepath,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        "progress_hooks": [make_progress_hook(video_id)],
    }

    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        set_status(video_id, {'progress': '0%', 'title': video_title, 'type': 'reddit'})
        try:
            ydl.download([video_url])
            save_uploaded_video_metadata(video_id, filepath, video_title, channel_id, published_at, filepath, 'reddit', webpage_url=webpage_url, extra=extra)
        finally:
            del_status(video_id)

    return True


def download_image(image_url, video_id, channel_id, video_title, published_at, webpage_url=None, extra=None):
    import requests
    if published_at is None:
        published_at = datetime.datetime.utcnow()
    response = requests.get(image_url, timeout=60)
    response.raise_for_status()

    ext = image_url.lower().split('?')[0].split('.')[-1]
    filepath = os.path.join(os.environ['VAULTTUBE_VAULTDIR'], channel_id, video_id + "." + ext)

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'wb') as f:
        f.write(response.content)

    save_uploaded_video_metadata(video_id, filepath, video_title, channel_id, published_at, filepath, 'reddit', webpage_url=webpage_url, extra=extra)

    return True


def extract_redgifs_id(url):
    if 'redgifs.com/watch/' in url:
        return url.split('/watch/')[1].split('/')[0]
    elif 'redgifs.com/gifs/' in url:
        return url.split('/gifs/')[1].split('.')[0]
    elif 'redgifs.com/i/' in url:
        return url.split('/i/')[1].split('.')[0]
    return None




def has_media(submission):
    """True when the post carries something the downloader can save: a
    Reddit-hosted video, an image/gif/mp4 link or a RedGifs/imgur link."""
    if submission.is_video:
        return True
    url = (submission.url or '').lower()
    if any(url.split('?')[0].endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4')):
        return True
    return any(host in url for host in ('redgifs.com', 'i.redd.it', 'i.imgur.com'))


_SUBREDDIT_RE = re.compile(r'^[A-Za-z0-9_]{2,21}$')


def normalize_subreddit(value):
    """Accept 'videos', 'r/videos', '/r/videos/' or a subreddit URL and
    return the bare name, or None if it isn't a valid subreddit name."""
    value = (value or '').strip()
    if '://' in value:
        value = urlparse(value).path
    value = value.strip('/')
    if value.lower().startswith('r/'):
        value = value[2:]
    value = value.split('/')[0]
    return value if _SUBREDDIT_RE.match(value) else None


def resolve_subreddit(name):
    """Look the subreddit up so typos and private/banned subs are caught when
    watching starts. Returns (display name, creation time of its newest post
    or None) so only posts after now are queued."""
    subreddit = _get_reddit_client().subreddit(name)
    subreddit.id  # forces the fetch; raises for missing/private subreddits
    display_name = subreddit.display_name
    newest = next(iter(subreddit.new(limit=1)), None)
    return display_name, (newest.created_utc if newest else None)


def scan_subreddit(name, last_seen_utc, q, limit=100):
    """Queue posts newer than last_seen_utc that have media and aren't in the
    vault yet. Each goes through the normal Reddit post download. Returns
    (number queued, newest creation time seen)."""
    from QueueObject import QueueObject
    from database import check_db_video
    from queue_utils import enqueue

    newest = last_seen_utc
    queued = 0
    for post in _get_reddit_client().subreddit(name).new(limit=limit):
        if last_seen_utc is not None and post.created_utc <= last_seen_utc:
            continue
        if newest is None or post.created_utc > newest:
            newest = post.created_utc
        if not post.permalink or not has_media(post) or check_db_video(post.id):
            continue
        qo = QueueObject("https://www.reddit.com" + post.permalink, "", "reddit", 0, "")
        if enqueue(qo, q):
            queued += 1
    return queued, newest
