import logging,os,traceback,sys, threading, queue
from logging.handlers import TimedRotatingFileHandler
from flask import Flask,render_template,send_file,Blueprint,request
from api import api_bp
from backend import backend_thread
from database import checkdb
from scanner import start_scanner
from downloader import start_dl_queue
from dotenv import load_dotenv

load_dotenv()

#Logging
logging.getLogger('werkzeug').setLevel(logging.WARN)
global logger
logger = logging.getLogger('main')
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s.%(msecs)03d %(name)-14s %(levelname)-12s msg=%(message)s","%Y-%m-%d %H:%M:%S")

#StreamHandler
streamHandler = logging.StreamHandler()
streamHandler.setFormatter(formatter)
streamHandler.setLevel(logging.INFO)
logger.addHandler(streamHandler)

#File
logfile = os.path.abspath(os.curdir)+"VaultTube.log"
timedHandler = TimedRotatingFileHandler(logfile,when="d",interval=1,backupCount=7)
timedHandler.setFormatter(formatter)
timedHandler.setLevel(logging.INFO)
logger.addHandler(timedHandler)

#Exception Handling
def log_uncaught_exceptions(ex_cls,ex,tb):
    logger.critical(''.join(traceback.format_tb(tb)))
    logger.critical('{0}: {1}'.format(ex_cls,ex))
    logger.critical('END PROCESS')
    logger.handlers = []

sys.excepthook = log_uncaught_exceptions

#Flask Startup
app = Flask(__name__)
app.debug = True

#Video static
videos = Blueprint('videos',__name__,static_url_path='/videos',static_folder=os.environ['VAULTTUBE_VAULTDIR'])

#Register with Flask
app.register_blueprint(videos)
app.register_blueprint(api_bp,url_prefix='/api')

#Basic Routes
@app.route('/')
def home():
    return render_template('/index.html')

@app.route('/player.html')
def player():
    return render_template('/player.html')

@app.route("/download.html")
def download():
    return render_template('/download.html')

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

def start_background_threads():
    #Start Threads
    be = threading.Thread(target=backend_thread,args=(logger,app))
    be.start()
    sc = threading.Thread(target=start_scanner,args=(logger,app))
    sc.start()
    dl = threading.Thread(target=start_dl_queue,args=(logger,app))
    dl.start()

def startup():
    #Check Database
    dbpass = checkdb(logger)
    if(dbpass):
        q = queue.Queue()
        app.config['queue'] = q
        if("VAULTTUBE_DISABLEBACK" in os.environ):
            if(os.environ['VAULTTUBE_DISABLEBACK'] == "False"):
                start_background_threads()
        else:
            start_background_threads()
        #Begin
        logger.info("Starting VaultTube")
        app.run(host='0.0.0.0',use_reloader=False)
    else:
        logger.error("DB failed to start correctly, exiting")
        exit()

#Main
if __name__ == "__main__":
    startup()



    
  
