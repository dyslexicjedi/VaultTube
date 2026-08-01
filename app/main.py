import logging,os,traceback,sys, threading, signal
from logging.handlers import TimedRotatingFileHandler
from flask import Flask,render_template,send_file,Blueprint,request,redirect
from api import api_bp
from sentinel_api import sentinel_bp
from backend import backend_thread,deleted_check_thread
from database import checkdb,get_resumable_queue_items
from scanner import start_scanner
from sentinel_inventory import start_census
from downloader import start_dl_queue
from transcoder import start_reaper_thread, start_cleanup_thread, shutdown_transcoder
from QueueObject import QueueObject
from queue_utils import PriorityDownloadQueue
from dotenv import load_dotenv

load_dotenv()

#Logging — handlers live on the root logger so every component-specific
#child logger (backend, downloader, youtube, ...) propagates to them and
#shows its own name in the source column instead of a generic 'main'.
logging.getLogger('werkzeug').setLevel(logging.WARN)
logger = logging.getLogger('main')           # app-level/startup messages
formatter = logging.Formatter("%(asctime)s.%(msecs)03d %(name)-14s %(levelname)-12s msg=%(message)s","%Y-%m-%d %H:%M:%S")

class ResilientStreamHandler(logging.StreamHandler):
    #The stderr pipe can break under docker (e.g. a `docker logs -f` detaches),
    #which makes flush() raise OSError and logging spew half-tracebacks. The
    #file handler still has the line, so just drop it instead of being noisy.
    def handleError(self, record):
        if isinstance(sys.exc_info()[1], OSError):
            return
        super().handleError(record)

#StreamHandler
streamHandler = ResilientStreamHandler()
streamHandler.setFormatter(formatter)
streamHandler.setLevel(logging.INFO)

#File
logfile = os.path.join(os.path.abspath(os.curdir), "VaultTube.log")
timedHandler = TimedRotatingFileHandler(logfile,when="d",interval=1,backupCount=7)
timedHandler.setFormatter(formatter)
timedHandler.setLevel(logging.INFO)

# Attach to root so all child loggers inherit them via propagation
root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(streamHandler)
root.addHandler(timedHandler)

required_vars = ['VAULTTUBE_VAULTDIR', 'VAULTTUBE_DBHOST', 'VAULTTUBE_DBUSER', 
                  'VAULTTUBE_DBPASS', 'VAULTTUBE_DBNAME', 'VAULTTUBE_YTKEY']
for var in required_vars:
    if var not in os.environ:
        logger.error(f"Required environment variable {var} not set")
        exit(1)


#Exception Handling
def log_uncaught_exceptions(ex_cls, ex, tb):
    logger.critical(''.join(traceback.format_tb(tb)))
    logger.critical('{0}: {1}'.format(ex_cls, ex))
    logger.critical('END PROCESS')
    # Also log system information for debugging
    logger.critical(f"System info - Python: {sys.version}, OS: {os.name}")
    logger.handlers = []

sys.excepthook = log_uncaught_exceptions

#As PID 1 in the container, Python ignores default-disposition signals, so
#docker stop's SIGTERM was dropped and every stop waited out the 10s SIGKILL
#timeout. Exit explicitly (worker threads are daemons; the queue is DB-backed).
def _graceful_exit(signum, frame):
    logger.info("Received signal %s, shutting down", signum)
    try:
        shutdown_transcoder()
    except Exception as e:
        logger.error("Transcoder shutdown error: %s", e)
    sys.exit(0)

signal.signal(signal.SIGTERM, _graceful_exit)


#Flask Startup
app = Flask(__name__)
app.debug = os.environ.get('VAULTTUBE_DEBUG', 'False').lower() in ('true', '1', 'yes')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB max upload size

#Video static
videos = Blueprint('videos',__name__,static_url_path='/videos',static_folder=os.environ['VAULTTUBE_VAULTDIR'])

#Register with Flask
app.register_blueprint(videos)
app.register_blueprint(api_bp,url_prefix='/api')
app.register_blueprint(sentinel_bp,url_prefix='/api/sentinel')

#Basic Routes
@app.route('/')
def home():
    return render_template('/index.html')

@app.route('/browse.html')
def browse():
    return render_template('/browse.html')

@app.route('/player.html')
def player():
    return render_template('/player.html')

@app.route("/queue.html")
def queue_page():
    return render_template('/queue.html')

#Old entry points for downloads/uploads; both live on the queue page now
@app.route("/download.html")
def download():
    return redirect('/queue.html', code=301)

@app.route("/channels.html")
def channels():
    return render_template("/channels.html")

@app.route("/creator.html")
def creator():
    return render_template("/creator.html")

@app.route("/search.html")
def search():
    return render_template("/search.html")

@app.route("/playlists.html")
def playlists():
    return render_template("/playlists.html")

@app.route("/playlist.html")
def playlist():
    return render_template("/playlist.html")

@app.route("/random.html")
def random():
    return render_template("/random.html")

@app.route("/stats.html")
def stats():
    return redirect('/storage.html', code=301)

@app.route("/storage.html")
def storage():
    return render_template("/storage.html")

@app.route("/observatory.html")
def observatory():
    return render_template("/observatory.html")

@app.route("/upload.html")
def upload():
    return redirect('/queue.html', code=301)

def start_background_threads():
    logger.info("Starting Background Threads")
    #Start Threads (daemons: the queue is DB-backed so nothing is lost on
    #shutdown, and docker stop terminates instantly instead of timing out)
    be = threading.Thread(target=backend_thread,args=(app,),daemon=True)
    be.start()
    sc = threading.Thread(target=start_scanner,args=(app,),daemon=True)
    sc.start()
    census = threading.Thread(target=start_census,args=(app,),daemon=True)
    census.start()
    dl = threading.Thread(target=start_dl_queue,args=(app,),daemon=True)
    dl.start()
    #Re-enabled: lookups are batched 50/call now and only changes are logged
    dc = threading.Thread(target=deleted_check_thread,args=(app,),daemon=True)
    dc.start()
    # HLS transcode reaper/cleanup threads
    start_reaper_thread()
    start_cleanup_thread()

def startup():
    #Check Database
    dbpass = checkdb()
    if(dbpass):
        q = PriorityDownloadQueue()
        app.config['queue'] = q
        #Restore downloads that were queued or in-flight at last shutdown
        for (rowid, url, source, channel_id, unsave, attempts, priority,
             origin, rescue_session_id, target_item_id,
             download_delay_seconds) in get_resumable_queue_items():
            qo = QueueObject(
                url, channel_id or "", source, 0, "", unsave=bool(unsave),
                priority=priority, origin=origin,
                rescue_session_id=rescue_session_id,
                target_item_id=target_item_id,
                download_delay_seconds=download_delay_seconds,
            )
            qo.row_id = rowid
            qo.attempts = attempts
            q.put(qo)
            logger.info("Restored queued download: %s" % url)
        if("VAULTTUBE_DISABLEBACK" in os.environ):
            logger.info("Found Disable Backend variable of %s",os.environ['VAULTTUBE_DISABLEBACK'])
            if(os.environ['VAULTTUBE_DISABLEBACK'] == "False"):
                start_background_threads()
        else:
            start_background_threads()
        #Begin
        logger.info("Starting VaultTube")
        if "VAULTTUBE_PORT" in os.environ:
            app.run(host='0.0.0.0',use_reloader=False,port=os.environ['VAULTTUBE_PORT'])
        else:
            app.run(host='0.0.0.0',use_reloader=False)
        
    else:
        logger.error("DB failed to start correctly, exiting")
        exit()

#Main
if __name__ == "__main__":
    startup()
